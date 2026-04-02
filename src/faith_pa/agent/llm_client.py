"""Unified async Ollama/OpenRouter LLM client."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

OLLAMA_DEFAULT_HOST = "http://host.docker.internal:11434"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE_STATUS_CODES = {429, 503}
PERMANENT_STATUS_CODES = {400, 401, 404}
RETRY_DELAYS = (2, 4, 8)
DEFAULT_TIMEOUT = 120.0


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"
    latency_ms: int = 0
    raw_response: dict[str, Any] | None = None


class LLMError(Exception):
    """Base error for the LLM client."""


class LLMRetryableError(LLMError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMPermanentError(LLMError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMFallbackTriggered(LLMError):
    def __init__(self, primary_model: str, fallback_model: str, cause: str) -> None:
        super().__init__(
            f"Primary model '{primary_model}' failed after retries ({cause}). Falling back to '{fallback_model}'."
        )
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.cause = cause


def parse_model_string(model: str) -> tuple[str, str]:
    if model.startswith("ollama/"):
        return "ollama", model[len("ollama/") :]
    if model.startswith("openrouter/"):
        return "openrouter", model[len("openrouter/") :]
    raise ValueError(f"Unknown model prefix in '{model}'")


class LLMClient:
    """Unified async client for Ollama and OpenRouter chat completions."""

    def __init__(
        self,
        *,
        model: str,
        fallback_model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        event_publisher: Any = None,
        ollama_host: str | None = None,
        openrouter_api_key: str | None = None,
    ) -> None:
        self.model = model
        self.fallback_model = fallback_model
        self.timeout = timeout
        self.event_publisher = event_publisher
        self.ollama_host = ollama_host or os.getenv("OLLAMA_HOST", OLLAMA_DEFAULT_HOST)
        self.openrouter_api_key = openrouter_api_key or os.getenv("OPENROUTER_API_KEY")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
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
        if response.status_code < 400:
            return
        message = response.text.strip() or f"HTTP {response.status_code}"
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise LLMRetryableError(message, status_code=response.status_code)
        if response.status_code in PERMANENT_STATUS_CODES:
            raise LLMPermanentError(message, status_code=response.status_code)
        raise LLMRetryableError(message, status_code=response.status_code)

    async def _publish_failure(self, model: str, error: str) -> None:
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
    "parse_model_string",
]
