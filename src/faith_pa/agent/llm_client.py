"""Description:
    Provide a unified asynchronous chat client for Ollama and OpenRouter models.

Requirements:
    - Support both Ollama and OpenRouter model prefixes.
    - Retry transient failures with backoff.
    - Fall back to a secondary model when configured.
    - Publish failure and fallback events when an event publisher is available.
"""

from __future__ import annotations

import asyncio
import os
import platform
import time
from dataclasses import dataclass
from typing import Any

import httpx

OLLAMA_CONTAINER_HOST = "http://ollama:11434"
OLLAMA_HOST_BRIDGE = "http://host.docker.internal:11434"
OLLAMA_DEFAULT_HOST = OLLAMA_HOST_BRIDGE
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE_STATUS_CODES = {429, 503}
PERMANENT_STATUS_CODES = {400, 401, 404}
RETRY_DELAYS = (2, 4, 8)
DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True, slots=True)
class OllamaEndpointResolution:
    """Description:
        Describe the resolved Ollama endpoint selected for the current runtime.

    Requirements:
        - Preserve the endpoint URL, route kind, platform name, and whether the route came from an explicit override.

    :param endpoint: Resolved Ollama base URL.
    :param route_kind: Route category such as ``container``, ``host``, or ``explicit``.
    :param platform_name: Normalised platform name used during resolution.
    :param from_override: Whether the route came from an explicit override.
    """

    endpoint: str
    route_kind: str
    platform_name: str
    from_override: bool = False


@dataclass(frozen=True, slots=True)
class LocalModelCapability:
    """Description:
        Describe the observed capability of one resolved local-model route.

    Requirements:
        - Preserve whether inference succeeded, whether GPU acceleration is considered available, and the main resource signals used for recommendation.

    :param endpoint: Ollama endpoint that was probed.
    :param route_kind: Route category such as ``container`` or ``host``.
    :param inference_available: Whether the inference probe succeeded.
    :param gpu_acceleration: Whether GPU acceleration is available for the route.
    :param usable_vram_mb: Estimated usable VRAM in megabytes.
    :param system_ram_mb: Estimated system RAM in megabytes.
    :param probe_model: Model used for the inference probe.
    :param notes: Human-readable notes describing the probe result.
    """

    endpoint: str
    route_kind: str
    inference_available: bool
    gpu_acceleration: bool
    usable_vram_mb: int
    system_ram_mb: int | None
    probe_model: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Description:
        Represent the normalised result of one LLM chat completion request.

    Requirements:
        - Preserve content, model identity, token counts, finish reason, latency, and raw response payload.

    :param content: Assistant response text.
    :param model: Fully qualified model name used for the call.
    :param input_tokens: Input token count reported by the provider.
    :param output_tokens: Output token count reported by the provider.
    :param finish_reason: Provider finish reason.
    :param latency_ms: End-to-end request latency in milliseconds.
    :param raw_response: Raw provider response payload.
    """

    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"
    latency_ms: int = 0
    raw_response: dict[str, Any] | None = None


class LLMError(Exception):
    """Description:
        Provide the base exception type for LLM client failures.

    Requirements:
        - Act as the shared parent for retryable and permanent errors.
    """


class LLMRetryableError(LLMError):
    """Description:
        Represent a transient LLM failure that may succeed on retry.

    Requirements:
        - Preserve the optional HTTP status code for retry policy decisions.

    :param message: Error message.
    :param status_code: Optional HTTP status code.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Description:
            Initialise the retryable LLM error.

        Requirements:
            - Preserve the optional HTTP status code for callers.

        :param message: Error message.
        :param status_code: Optional HTTP status code.
        """

        super().__init__(message)
        self.status_code = status_code


class LLMPermanentError(LLMError):
    """Description:
        Represent an LLM failure that should not be retried automatically.

    Requirements:
        - Preserve the HTTP status code explaining the permanent failure.

    :param message: Error message.
    :param status_code: HTTP status code.
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        """Description:
            Initialise the permanent LLM error.

        Requirements:
            - Preserve the HTTP status code for callers.

        :param message: Error message.
        :param status_code: HTTP status code.
        """

        super().__init__(message)
        self.status_code = status_code


class LLMFallbackTriggered(LLMError):
    """Description:
        Represent a model-fallback transition triggered by repeated primary-model failures.

    Requirements:
        - Preserve the primary model, fallback model, and triggering cause.

    :param primary_model: Primary model that failed.
    :param fallback_model: Fallback model selected after failure.
    :param cause: Human-readable cause of the fallback.
    """

    def __init__(self, primary_model: str, fallback_model: str, cause: str) -> None:
        """Description:
            Initialise the fallback-triggered error.

        Requirements:
            - Build a human-readable message describing the fallback transition.

        :param primary_model: Primary model that failed.
        :param fallback_model: Fallback model selected after failure.
        :param cause: Human-readable cause of the fallback.
        """

        super().__init__(
            f"Primary model '{primary_model}' failed after retries ({cause}). Falling back to '{fallback_model}'."
        )
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.cause = cause


def parse_model_string(model: str) -> tuple[str, str]:
    """Description:
        Parse a fully qualified FAITH model string into provider and provider-model parts.

    Requirements:
        - Support the ``ollama/`` and ``openrouter/`` prefixes.
        - Reject unknown prefixes clearly.

    :param model: Fully qualified model string.
    :returns: Provider name and provider-specific model identifier.
    :raises ValueError: If the model prefix is unsupported.
    """

    if model.startswith("ollama/"):
        return "ollama", model[len("ollama/") :]
    if model.startswith("openrouter/"):
        return "openrouter", model[len("openrouter/") :]
    raise ValueError(f"Unknown model prefix in '{model}'")


def _normalise_platform_name(platform_name: str | None) -> str:
    """Description:
        Normalise platform names into the values used by the Ollama route policy.

    Requirements:
        - Convert Python platform names and common aliases into stable identifiers.

    :param platform_name: Raw platform name or alias.
    :returns: Normalised platform identifier.
    """

    raw = (platform_name or platform.system()).strip().lower()
    if raw in {"windows", "win32"}:
        return "windows"
    if raw in {"darwin", "mac", "macos", "osx"}:
        return "darwin"
    return "linux"


def _env_flag(name: str) -> bool:
    """Description:
        Parse a boolean environment flag used by FAITH runtime hints.

    Requirements:
        - Treat common truthy strings as ``True`` and all other values as ``False``.

    :param name: Environment variable name.
    :returns: Parsed boolean flag.
    """

    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str) -> int | None:
    """Description:
        Parse an integer environment hint used by FAITH runtime capability probing.

    Requirements:
        - Return ``None`` when the variable is unset or invalid.

    :param name: Environment variable name.
    :returns: Parsed integer value or ``None``.
    """

    value = os.getenv(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class LLMClient:
    """Description:
        Execute asynchronous chat completions against Ollama or OpenRouter.

    Requirements:
        - Support retry and fallback behaviour.
        - Normalise provider-specific responses into the shared ``LLMResponse`` model.
        - Publish failure and fallback events when configured.

    :param model: Primary model identifier.
    :param fallback_model: Optional fallback model identifier.
    :param timeout: Request timeout in seconds.
    :param event_publisher: Optional event publisher for failure notifications.
    :param ollama_host: Optional Ollama base URL override.
    :param openrouter_api_key: Optional explicit OpenRouter API key.
    :param platform_name: Optional explicit platform override for route selection.
    :param docker_gpu_supported: Optional Docker GPU availability hint for platform-aware Ollama routing.
    :param system_ram_mb: Optional system RAM hint used by capability reporting.
    """

    def __init__(
        self,
        *,
        model: str,
        fallback_model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        event_publisher: Any = None,
        ollama_host: str | None = None,
        openrouter_api_key: str | None = None,
        platform_name: str | None = None,
        docker_gpu_supported: bool | None = None,
        system_ram_mb: int | None = None,
    ) -> None:
        """Description:
            Initialise the LLM client.

        Requirements:
            - Default Ollama and OpenRouter credentials from the environment when not supplied explicitly.

        :param model: Primary model identifier.
        :param fallback_model: Optional fallback model identifier.
        :param timeout: Request timeout in seconds.
        :param event_publisher: Optional event publisher for failure notifications.
        :param ollama_host: Optional Ollama base URL override.
        :param openrouter_api_key: Optional explicit OpenRouter API key.
        :param platform_name: Optional explicit platform override for route selection.
        :param docker_gpu_supported: Optional Docker GPU availability hint for platform-aware Ollama routing.
        :param system_ram_mb: Optional system RAM hint used by capability reporting.
        """

        self.model = model
        self.fallback_model = fallback_model
        self.timeout = timeout
        self.event_publisher = event_publisher
        self.platform_name = _normalise_platform_name(platform_name)
        self.docker_gpu_supported = (
            docker_gpu_supported
            if docker_gpu_supported is not None
            else _env_flag("FAITH_DOCKER_GPU_SUPPORTED")
        )
        self.system_ram_mb = system_ram_mb or _env_int("FAITH_SYSTEM_RAM_MB")
        explicit_ollama_host = ollama_host or os.getenv("OLLAMA_HOST")
        self.ollama_resolution = self.resolve_ollama_endpoint(
            endpoint_override=explicit_ollama_host,
            platform_name=self.platform_name,
            docker_gpu_supported=self.docker_gpu_supported,
        )
        self.ollama_host = self.ollama_resolution.endpoint
        self.openrouter_api_key = openrouter_api_key or os.getenv("OPENROUTER_API_KEY")

    def resolve_ollama_endpoint(
        self,
        *,
        endpoint_override: str | None = None,
        platform_name: str | None = None,
        docker_gpu_supported: bool | None = None,
    ) -> OllamaEndpointResolution:
        """Description:
            Resolve the preferred Ollama endpoint for the current runtime.

        Requirements:
            - Honour an explicit endpoint override when one is provided.
            - Prefer the bundled container on Linux when Docker GPU support is confirmed.
            - Prefer the host route on Windows when Docker GPU support is not confirmed.
            - Prefer the host route on macOS by default.

        :param endpoint_override: Optional explicit endpoint override.
        :param platform_name: Optional explicit platform override.
        :param docker_gpu_supported: Optional Docker GPU availability hint.
        :returns: Resolved Ollama endpoint description.
        """

        resolved_platform = _normalise_platform_name(platform_name or self.platform_name)
        docker_gpu = (
            self.docker_gpu_supported
            if docker_gpu_supported is None
            else docker_gpu_supported
        )
        if endpoint_override:
            return OllamaEndpointResolution(
                endpoint=endpoint_override,
                route_kind="explicit",
                platform_name=resolved_platform,
                from_override=True,
            )
        if resolved_platform == "windows":
            if docker_gpu:
                return OllamaEndpointResolution(
                    endpoint=OLLAMA_CONTAINER_HOST,
                    route_kind="container",
                    platform_name=resolved_platform,
                )
            return OllamaEndpointResolution(
                endpoint=OLLAMA_HOST_BRIDGE,
                route_kind="host",
                platform_name=resolved_platform,
            )
        if resolved_platform == "darwin":
            return OllamaEndpointResolution(
                endpoint=OLLAMA_HOST_BRIDGE,
                route_kind="host",
                platform_name=resolved_platform,
            )
        if docker_gpu:
            return OllamaEndpointResolution(
                endpoint=OLLAMA_CONTAINER_HOST,
                route_kind="container",
                platform_name=resolved_platform,
            )
        return OllamaEndpointResolution(
            endpoint=OLLAMA_CONTAINER_HOST,
            route_kind="container",
            platform_name=resolved_platform,
        )

    async def probe_local_model_capability(
        self,
        *,
        probe_model: str | None = None,
        platform_name: str | None = None,
        docker_gpu_supported: bool | None = None,
        system_ram_mb: int | None = None,
    ) -> LocalModelCapability:
        """Description:
            Probe local-model availability and route capability for Ollama-backed models.

        Requirements:
            - Try candidate routes in platform-aware order until one succeeds.
            - Confirm actual inference works instead of relying on route assumptions alone.
            - Report GPU and memory hints for downstream recommendation logic.

        :param probe_model: Optional probe model override.
        :param platform_name: Optional explicit platform override.
        :param docker_gpu_supported: Optional Docker GPU availability hint.
        :param system_ram_mb: Optional system RAM override for the returned capability.
        :returns: Local-model capability description for the first successful route.
        :raises LLMRetryableError: If no candidate route passes the inference probe.
        """

        resolved_platform = _normalise_platform_name(platform_name or self.platform_name)
        docker_gpu = (
            self.docker_gpu_supported
            if docker_gpu_supported is None
            else docker_gpu_supported
        )
        effective_system_ram_mb = system_ram_mb or self.system_ram_mb
        provider, provider_model = parse_model_string(self.model)
        effective_probe_model = probe_model or (
            provider_model if provider == "ollama" else os.getenv("FAITH_OLLAMA_PROBE_MODEL", "llama3:8b")
        )
        last_error: LLMRetryableError | None = None
        for resolution in self._ollama_probe_candidates(
            platform_name=resolved_platform,
            docker_gpu_supported=docker_gpu,
        ):
            capability = await self._probe_ollama_endpoint(
                resolution.endpoint,
                resolution.route_kind,
                effective_probe_model,
                system_ram_mb=effective_system_ram_mb,
                docker_gpu_supported=docker_gpu,
            )
            if capability is not None and capability.inference_available:
                return capability
            last_error = LLMRetryableError(
                f"Ollama probe failed for {resolution.endpoint}",
            )
        raise last_error or LLMRetryableError("No Ollama endpoint passed the local capability probe")

    def _ollama_probe_candidates(
        self,
        *,
        platform_name: str,
        docker_gpu_supported: bool | None,
    ) -> list[OllamaEndpointResolution]:
        """Description:
            Build the ordered list of Ollama routes to probe for the current platform.

        Requirements:
            - Put the preferred platform route first.
            - Keep the secondary route as a fallback when available.

        :param platform_name: Normalised platform name.
        :param docker_gpu_supported: Docker GPU availability hint.
        :returns: Ordered candidate route list.
        """

        primary = self.resolve_ollama_endpoint(
            platform_name=platform_name,
            docker_gpu_supported=docker_gpu_supported,
        )
        secondary_route = (
            OllamaEndpointResolution(
                endpoint=OLLAMA_HOST_BRIDGE,
                route_kind="host",
                platform_name=platform_name,
            )
            if primary.route_kind != "host"
            else OllamaEndpointResolution(
                endpoint=OLLAMA_CONTAINER_HOST,
                route_kind="container",
                platform_name=platform_name,
            )
        )
        candidates = [primary]
        if secondary_route.endpoint != primary.endpoint:
            candidates.append(secondary_route)
        return candidates

    async def _probe_ollama_endpoint(
        self,
        endpoint: str,
        route_kind: str,
        probe_model: str,
        *,
        system_ram_mb: int | None,
        docker_gpu_supported: bool | None,
    ) -> LocalModelCapability | None:
        """Description:
            Run a lightweight inference probe against one Ollama endpoint.

        Requirements:
            - Use a minimal non-streaming chat request to confirm inference actually works.
            - Return ``None`` when the endpoint is unreachable or the probe fails.
            - Include GPU and memory hints in the returned capability.

        :param endpoint: Ollama endpoint to probe.
        :param route_kind: Route category such as ``container`` or ``host``.
        :param probe_model: Model name to use for the probe.
        :param system_ram_mb: Optional system RAM hint.
        :param docker_gpu_supported: Docker GPU availability hint.
        :returns: Local-model capability description when the probe succeeds, otherwise ``None``.
        """

        payload: dict[str, Any] = {
            "model": probe_model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 1},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{endpoint.rstrip('/')}/api/chat", json=payload)
            self._raise_for_status(response)
        except (httpx.HTTPError, LLMError):
            return None

        return LocalModelCapability(
            endpoint=endpoint,
            route_kind=route_kind,
            inference_available=True,
            gpu_acceleration=bool(
                _env_flag("FAITH_OLLAMA_GPU_ACCELERATION")
                or (route_kind == "container" and docker_gpu_supported)
            ),
            usable_vram_mb=_env_int("FAITH_OLLAMA_USABLE_VRAM_MB") or 0,
            system_ram_mb=system_ram_mb,
            probe_model=probe_model,
            notes=("inference probe succeeded",),
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Description:
            Execute one chat completion with retry and optional fallback behaviour.

        Requirements:
            - Use the supplied model override when present.
            - Retry transient failures on the primary model.
            - Fall back to the configured secondary model when retryable failures exhaust the primary path.

        :param messages: Chat message payload.
        :param model: Optional model override.
        :param fallback_model: Optional fallback model override.
        :param temperature: Optional sampling temperature.
        :param max_tokens: Optional maximum output-token limit.
        :returns: Normalised LLM response.
        :raises LLMRetryableError: If the primary path fails and no fallback is configured.
        """

        primary_model = model or self.model
        fallback = fallback_model if fallback_model is not None else self.fallback_model

        try:
            return await self._chat_with_retry(
                primary_model, messages, temperature=temperature, max_tokens=max_tokens
            )
        except LLMRetryableError as exc:
            if not fallback:
                await self._publish_failure(primary_model, str(exc))
                raise
            await self._publish_fallback(primary_model, fallback, str(exc))
            return await self._chat_with_retry(
                fallback, messages, temperature=temperature, max_tokens=max_tokens
            )

    async def _chat_with_retry(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Description:
            Execute one chat completion with retry backoff.

        Requirements:
            - Retry only retryable errors.
            - Stop retrying once the configured retry budget is exhausted.
            - Re-raise permanent errors immediately.

        :param model: Fully qualified model identifier.
        :param messages: Chat message payload.
        :param temperature: Optional sampling temperature.
        :param max_tokens: Optional maximum output-token limit.
        :returns: Normalised LLM response.
        :raises LLMRetryableError: If retryable failures exhaust the retry budget.
        :raises LLMPermanentError: If a permanent failure occurs.
        """

        last_error: Exception | None = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                return await self._dispatch(
                    model, messages, temperature=temperature, max_tokens=max_tokens
                )
            except LLMRetryableError as exc:
                last_error = exc
                if attempt >= len(RETRY_DELAYS):
                    break
                await asyncio.sleep(RETRY_DELAYS[attempt])
            except LLMPermanentError:
                raise
        raise LLMRetryableError(str(last_error or "retry budget exhausted"))

    async def _dispatch(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Description:
            Dispatch one request to the appropriate provider implementation.

        Requirements:
            - Route Ollama models to the Ollama provider path.
            - Route OpenRouter models to the OpenRouter provider path.

        :param model: Fully qualified model identifier.
        :param messages: Chat message payload.
        :param temperature: Optional sampling temperature.
        :param max_tokens: Optional maximum output-token limit.
        :returns: Normalised LLM response.
        """

        provider, provider_model = parse_model_string(model)
        if provider == "ollama":
            return await self._call_ollama(model, provider_model, messages, temperature=temperature)
        return await self._call_openrouter(
            model, provider_model, messages, temperature=temperature, max_tokens=max_tokens
        )

    async def _call_ollama(
        self,
        model: str,
        provider_model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None,
    ) -> LLMResponse:
        """Description:
            Execute one Ollama chat request and normalise the response.

        Requirements:
            - Send the request to the configured Ollama host.
            - Convert transport errors into retryable LLM errors.
            - Normalise token usage and finish-reason fields.

        :param model: Fully qualified model identifier.
        :param provider_model: Provider-specific model identifier.
        :param messages: Chat message payload.
        :param temperature: Optional sampling temperature.
        :returns: Normalised LLM response.
        :raises LLMRetryableError: If the request times out or the transport fails.
        """

        payload: dict[str, Any] = {"model": provider_model, "messages": messages, "stream": False}
        if temperature is not None:
            payload["options"] = {"temperature": temperature}
        endpoint = f"{self.ollama_host.rstrip('/')}/api/chat"
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(endpoint, json=payload)
            except httpx.TimeoutException as exc:
                raise LLMRetryableError("Ollama request timed out") from exc
            except httpx.HTTPError as exc:
                raise LLMRetryableError(f"Ollama request failed: {exc}") from exc

        self._raise_for_status(response)
        data = response.json()
        usage = data.get("usage", {})
        message = data.get("message", {}) if isinstance(data.get("message"), dict) else {}
        return LLMResponse(
            content=str(message.get("content", "")),
            model=model,
            input_tokens=int(usage.get("prompt_tokens", data.get("prompt_eval_count", 0)) or 0),
            output_tokens=int(usage.get("completion_tokens", data.get("eval_count", 0)) or 0),
            finish_reason=str(data.get("done_reason", "stop")),
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw_response=data,
        )

    async def _call_openrouter(
        self,
        model: str,
        provider_model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Description:
            Execute one OpenRouter chat request and normalise the response.

        Requirements:
            - Require an API key before sending the request.
            - Convert transport errors into retryable LLM errors.
            - Normalise token usage and finish-reason fields.

        :param model: Fully qualified model identifier.
        :param provider_model: Provider-specific model identifier.
        :param messages: Chat message payload.
        :param temperature: Optional sampling temperature.
        :param max_tokens: Optional maximum output-token limit.
        :returns: Normalised LLM response.
        :raises LLMPermanentError: If no OpenRouter API key is configured.
        :raises LLMRetryableError: If the request times out or the transport fails.
        """

        if not self.openrouter_api_key:
            raise LLMPermanentError("OPENROUTER_API_KEY is not configured", status_code=401)

        payload: dict[str, Any] = {"model": provider_model, "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {"Authorization": f"Bearer {self.openrouter_api_key}"}
        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(OPENROUTER_BASE_URL, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise LLMRetryableError("OpenRouter request timed out") from exc
            except httpx.HTTPError as exc:
                raise LLMRetryableError(f"OpenRouter request failed: {exc}") from exc

        self._raise_for_status(response)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) if isinstance(choice.get("message"), dict) else {}
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        return LLMResponse(
            content=str(message.get("content", "")),
            model=model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            finish_reason=str(choice.get("finish_reason", "stop")),
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw_response=data,
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Description:
            Raise the correct FAITH LLM exception for an HTTP error response.

        Requirements:
            - Treat configured retryable status codes as retryable errors.
            - Treat configured permanent status codes as permanent errors.
            - Treat all other HTTP errors as retryable by default.

        :param response: HTTP response to inspect.
        :raises LLMRetryableError: If the response represents a retryable error.
        :raises LLMPermanentError: If the response represents a permanent error.
        """

        if response.status_code < 400:
            return
        message = response.text.strip() or f"HTTP {response.status_code}"
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise LLMRetryableError(message, status_code=response.status_code)
        if response.status_code in PERMANENT_STATUS_CODES:
            raise LLMPermanentError(message, status_code=response.status_code)
        raise LLMRetryableError(message, status_code=response.status_code)

    async def _publish_failure(self, model: str, error: str) -> None:
        """Description:
            Publish an agent-error event for a failed model call when possible.

        Requirements:
            - Support both helper-style and generic publish-style event publishers.

        :param model: Model identifier associated with the failure.
        :param error: Human-readable failure message.
        """

        if not self.event_publisher:
            return
        publish = getattr(self.event_publisher, "agent_error", None)
        if callable(publish):
            result = publish(error=error, agent=model)
            if asyncio.iscoroutine(result):
                await result
            return
        publish = getattr(self.event_publisher, "publish", None)
        if callable(publish):
            result = publish({"event": "agent:error", "model": model, "error": error})
            if asyncio.iscoroutine(result):
                await result

    async def _publish_fallback(self, primary_model: str, fallback_model: str, cause: str) -> None:
        """Description:
            Publish a model-escalation event when the client falls back to another model.

        Requirements:
            - Use the publisher's generic ``publish`` method when available.

        :param primary_model: Primary model that failed.
        :param fallback_model: Fallback model selected after failure.
        :param cause: Human-readable fallback cause.
        """

        if not self.event_publisher:
            return
        publish = getattr(self.event_publisher, "publish", None)
        if callable(publish):
            payload = {
                "event": "agent:model_escalation_requested",
                "primary_model": primary_model,
                "fallback_model": fallback_model,
                "cause": cause,
            }
            result = publish(payload)
            if asyncio.iscoroutine(result):
                await result


__all__ = [
    "DEFAULT_TIMEOUT",
    "LLMClient",
    "LLMError",
    "LLMFallbackTriggered",
    "LLMPermanentError",
    "LLMResponse",
    "LLMRetryableError",
    "LocalModelCapability",
    "OLLAMA_CONTAINER_HOST",
    "parse_model_string",
]
