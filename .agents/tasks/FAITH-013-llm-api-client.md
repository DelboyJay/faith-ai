# FAITH-013 — LLM API Client (Ollama + OpenRouter)

**Phase:** 3 — Base Agent Runtime
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-010
**FRS Reference:** Section 3.6.4, 3.6.5

---

## Objective

Implement a unified async LLM API client that supports both Ollama (local) and OpenRouter (cloud) endpoints. The client handles model string routing, structured responses, retry with exponential backoff, error classification, automatic fallback, platform-aware Ollama endpoint resolution, and local-model capability probing. Used by the PA and all specialist agents via `BaseAgent._call_llm`.

---

## Architecture

```
src/faith_pa/agent/
├── llm_client.py    ← LLMClient class, LLMResponse, error types (this task)
└── base.py          ← (FAITH-010 — wire LLMClient into BaseAgent._call_llm)
```

---

## Additional Required Scope

1. Support both bundled-container and external-host Ollama endpoints.
2. Resolve the default Ollama route in a platform-aware way:
- Linux: prefer bundled container Ollama when accelerator support is confirmed.
- Windows: use bundled container Ollama only when Docker Desktop GPU support is confirmed under WSL2; otherwise prefer native host Ollama.
- macOS: prefer native host Ollama by default.
3. Implement local-model capability probing that records:
- whether inference succeeds at all
- whether GPU acceleration is actually working
- usable GPU memory when available
- RAM fallback suitability when GPU execution is unavailable
4. Expose enough capability information for the wizard and PA to recommend an appropriate local model instead of relying on a hard-coded “best” model list.

---

## Files to Create

### 1. `src/faith_pa/agent/llm_client.py`

```python
"""FAITH LLM API Client — unified Ollama + OpenRouter interface.

Routes LLM calls to the correct backend based on model string prefix.
Handles retry with exponential backoff, error classification, and
automatic fallback to a secondary model when the primary is unavailable.

Model string format:
  - ollama/llama3:8b          → Ollama local API
  - openrouter/anthropic/claude-sonnet-4-6 → OpenRouter cloud API

FRS Reference: Section 3.6.4, 3.6.5
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

import httpx

logger = logging.getLogger("faith.llm_client")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLLAMA_DEFAULT_HOST = "http://host.docker.internal:11434"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]  # seconds — exponential backoff

RETRYABLE_STATUS_CODES = {429, 503}
PERMANENT_ERROR_CODES = {400, 401, 404}

DEFAULT_TIMEOUT = 120.0  # seconds


# ---------------------------------------------------------------------------
# Response dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMResponse:
    """Structured response from an LLM API call.

    Attributes:
        content: The generated text.
        model: Actual model used (may differ from requested if fallback fired).
        input_tokens: Token count for the input (from API or estimated).
        output_tokens: Token count for the output.
        finish_reason: Why generation stopped — "stop", "length", etc.
        latency_ms: Round-trip time in milliseconds.
    """

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    finish_reason: str
    latency_ms: int


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base class for all LLM client errors."""
    pass


class LLMRetryableError(LLMError):
    """Transient error — 429 rate-limit, 503 unavailable, or timeout.

    These will be retried automatically with exponential backoff.
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class LLMPermanentError(LLMError):
    """Non-recoverable error — 400 bad request, 401 unauthorized, 404 not found.

    These are never retried. They indicate a configuration problem.
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class LLMFallbackTriggered(LLMError):
    """All retries exhausted on the primary model.

    If a fallback model is configured, the client switches to it
    automatically. This exception is informational — it is logged
    but does not propagate to the caller when fallback succeeds.
    """

    def __init__(self, primary_model: str, fallback_model: str, cause: str):
        super().__init__(
            f"Primary model '{primary_model}' failed after {MAX_RETRIES} retries "
            f"({cause}). Falling back to '{fallback_model}'."
        )
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.cause = cause


# ---------------------------------------------------------------------------
# Model string parsing
# ---------------------------------------------------------------------------

def parse_model_string(model: str) -> tuple[str, str]:
    """Parse a model string into (provider, model_name).

    Args:
        model: Prefixed model string, e.g. "ollama/llama3:8b" or
            "openrouter/anthropic/claude-sonnet-4-6".

    Returns:
        Tuple of (provider, model_name) where provider is "ollama"
        or "openrouter" and model_name is the remainder of the string.

    Raises:
        ValueError: If the model string does not start with a known prefix.
    """
    if model.startswith("ollama/"):
        return "ollama", model[len("ollama/"):]
    elif model.startswith("openrouter/"):
        return "openrouter", model[len("openrouter/"):]
    else:
        raise ValueError(
            f"Unknown model prefix in '{model}'. "
            f"Expected 'ollama/...' or 'openrouter/...'."
        )


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

class LLMClient:
    """Unified async LLM client for Ollama and OpenRouter.

    Routes calls to the correct backend based on the model string prefix.
    Supports retry with exponential backoff and automatic fallback.

    Args:
        model: Primary model string (e.g. "ollama/llama3:8b").
        fallback_model: Optional fallback model string. Used when the
            primary exhausts all retries.
        event_publisher: Optional EventPublisher instance for emitting
            agent:error events after retry exhaustion.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        model: str,
        fallback_model: Optional[str] = None,
        event_publisher: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.model = model
        self.fallback_model = fallback_model
        self.event_publisher = event_publisher
        self.timeout = timeout

        # Validate model strings at construction time
        self._primary_provider, self._primary_model_name = parse_model_string(model)
        if fallback_model:
            self._fallback_provider, self._fallback_model_name = parse_model_string(
                fallback_model
            )

    # --- Public API ---------------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Send a chat completion request to the configured LLM.

        Routes to Ollama or OpenRouter based on the model prefix.
        Retries transient failures with exponential backoff. Falls back
        to the secondary model if all retries are exhausted.

        Args:
            messages: List of message dicts with "role" and "content" keys.
            temperature: Sampling temperature (0.0–1.0).

        Returns:
            LLMResponse with generated text, token counts, and metadata.

        Raises:
            LLMPermanentError: On non-recoverable API errors (400, 401, 404).
            LLMRetryableError: If all retries AND fallback are exhausted.
        """
        try:
            return await self._retry_with_backoff(
                self._dispatch,
                messages,
                self._primary_provider,
                self._primary_model_name,
                temperature,
            )
        except LLMRetryableError as exc:
            # Primary model exhausted all retries
            logger.error(
                "Primary model '%s' failed after %d retries: %s",
                self.model, MAX_RETRIES, exc,
            )

            # Publish agent:error event
            if self.event_publisher:
                await self.event_publisher.agent_error(
                    error=f"LLM API failed for {self.model}: {exc}",
                    recoverable=self.fallback_model is not None,
                )

            # Try fallback if configured
            if self.fallback_model:
                logger.warning(
                    "Switching to fallback model '%s'", self.fallback_model,
                )
                fallback_info = LLMFallbackTriggered(
                    primary_model=self.model,
                    fallback_model=self.fallback_model,
                    cause=str(exc),
                )
                logger.info(str(fallback_info))

                return await self._retry_with_backoff(
                    self._dispatch,
                    messages,
                    self._fallback_provider,
                    self._fallback_model_name,
                    temperature,
                )

            # No fallback — re-raise
            raise

    # --- Dispatch -----------------------------------------------------------

    async def _dispatch(
        self,
        messages: list[dict],
        provider: str,
        model_name: str,
        temperature: float,
    ) -> LLMResponse:
        """Route to the correct provider backend."""
        if provider == "ollama":
            return await self._call_ollama(messages, model_name, temperature)
        elif provider == "openrouter":
            return await self._call_openrouter(messages, model_name, temperature)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    # --- Ollama backend -----------------------------------------------------

    async def _call_ollama(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
    ) -> LLMResponse:
        """POST to the Ollama /api/chat endpoint.

        Ollama runs locally on the Docker network. The host is
        configurable via the OLLAMA_HOST environment variable.

        Args:
            messages: Chat messages.
            model: Ollama model name (e.g. "llama3:8b").
            temperature: Sampling temperature.

        Returns:
            LLMResponse parsed from the Ollama response.

        Raises:
            LLMRetryableError: On 429, 503, or timeout.
            LLMPermanentError: On 400, 401, 404.
        """
        host = os.environ.get("OLLAMA_HOST", OLLAMA_DEFAULT_HOST)
        url = f"{host}/api/chat"

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }

        start_ms = _now_ms()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                raise LLMRetryableError(
                    f"Ollama request failed: {exc}", status_code=None
                ) from exc

        latency = _now_ms() - start_ms
        _check_status(response, model)

        body = response.json()
        message_content = body.get("message", {}).get("content", "")

        # Ollama token counts — may not always be present
        input_tokens = body.get("prompt_eval_count", 0)
        output_tokens = body.get("eval_count", 0)
        finish_reason = body.get("done_reason", "stop")

        return LLMResponse(
            content=message_content,
            model=f"ollama/{model}",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            latency_ms=latency,
        )

    # --- OpenRouter backend -------------------------------------------------

    async def _call_openrouter(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
    ) -> LLMResponse:
        """POST to the OpenRouter /api/v1/chat/completions endpoint.

        Requires the OPENROUTER_API_KEY environment variable.

        Args:
            messages: Chat messages.
            model: OpenRouter model name (e.g. "anthropic/claude-sonnet-4-6").
            temperature: Sampling temperature.

        Returns:
            LLMResponse parsed from the OpenRouter response.

        Raises:
            LLMPermanentError: If API key is missing, or on 400/401/404.
            LLMRetryableError: On 429, 503, or timeout.
        """
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise LLMPermanentError(
                "OPENROUTER_API_KEY environment variable is not set.",
                status_code=401,
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        start_ms = _now_ms()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    OPENROUTER_BASE_URL, headers=headers, json=payload
                )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                raise LLMRetryableError(
                    f"OpenRouter request failed: {exc}", status_code=None
                ) from exc

        latency = _now_ms() - start_ms
        _check_status(response, model)

        body = response.json()
        choice = body.get("choices", [{}])[0]
        message_content = choice.get("message", {}).get("content", "")
        finish_reason = choice.get("finish_reason", "stop")

        # Token usage from OpenRouter response
        usage = body.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        return LLMResponse(
            content=message_content,
            model=f"openrouter/{model}",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            latency_ms=latency,
        )

    # --- Retry logic --------------------------------------------------------

    async def _retry_with_backoff(
        self,
        call_fn: Callable[..., Coroutine],
        *args: Any,
    ) -> LLMResponse:
        """Execute call_fn with exponential backoff on retryable errors.

        Retry schedule: 2s → 4s → 8s (3 attempts max).
        Retries on: HTTP 429, 503, network timeout.
        Does NOT retry: HTTP 400, 401, 404 (permanent errors).

        Args:
            call_fn: Async callable that returns LLMResponse.
            *args: Arguments forwarded to call_fn.

        Returns:
            LLMResponse on success.

        Raises:
            LLMPermanentError: Immediately on non-recoverable errors.
            LLMRetryableError: After all retries are exhausted.
        """
        last_error: Optional[LLMRetryableError] = None

        for attempt in range(MAX_RETRIES):
            try:
                return await call_fn(*args)
            except LLMPermanentError:
                # Do not retry — propagate immediately
                raise
            except LLMRetryableError as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        "Retryable LLM error (attempt %d/%d), "
                        "retrying in %ds: %s",
                        attempt + 1, MAX_RETRIES, delay, exc,
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted
        assert last_error is not None
        raise last_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_status(response: httpx.Response, model: str) -> None:
    """Classify HTTP status code and raise the appropriate error.

    Args:
        response: The httpx response object.
        model: Model name for error messages.

    Raises:
        LLMPermanentError: On 400, 401, 404.
        LLMRetryableError: On 429, 503.
    """
    code = response.status_code
    if 200 <= code < 300:
        return

    body_text = response.text[:500]  # Truncate for logging

    if code in PERMANENT_ERROR_CODES:
        raise LLMPermanentError(
            f"HTTP {code} from model '{model}': {body_text}",
            status_code=code,
        )
    elif code in RETRYABLE_STATUS_CODES:
        raise LLMRetryableError(
            f"HTTP {code} from model '{model}': {body_text}",
            status_code=code,
        )
    else:
        # Unknown error codes are treated as retryable to be safe
        raise LLMRetryableError(
            f"HTTP {code} from model '{model}': {body_text}",
            status_code=code,
        )


def _now_ms() -> int:
    """Return current time in milliseconds."""
    return int(time.monotonic() * 1000)
```

### 2. `tests/test_llm_client.py`

```python
"""Tests for the FAITH LLM API client.

Tests cover model string parsing, Ollama/OpenRouter call formatting,
retry with exponential backoff, permanent error handling, fallback
model switching, LLMResponse creation, error classification, and
timeout handling.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from faith.agent.llm_client import (
    LLMClient,
    LLMResponse,
    LLMRetryableError,
    LLMPermanentError,
    LLMFallbackTriggered,
    parse_model_string,
    MAX_RETRIES,
    RETRY_DELAYS,
    OLLAMA_DEFAULT_HOST,
    OPENROUTER_BASE_URL,
)


# ---------------------------------------------------------------------------
# Test: Model string parsing
# ---------------------------------------------------------------------------

class TestParseModelString:

    def test_ollama_prefix(self):
        provider, name = parse_model_string("ollama/llama3:8b")
        assert provider == "ollama"
        assert name == "llama3:8b"

    def test_openrouter_prefix(self):
        provider, name = parse_model_string(
            "openrouter/anthropic/claude-sonnet-4-6"
        )
        assert provider == "openrouter"
        assert name == "anthropic/claude-sonnet-4-6"

    def test_unknown_prefix_raises(self):
        with pytest.raises(ValueError, match="Unknown model prefix"):
            parse_model_string("huggingface/bert-base")

    def test_ollama_preserves_tag(self):
        _, name = parse_model_string("ollama/codellama:13b-instruct")
        assert name == "codellama:13b-instruct"

    def test_openrouter_preserves_full_path(self):
        _, name = parse_model_string("openrouter/google/gemini-pro-1.5")
        assert name == "google/gemini-pro-1.5"


# ---------------------------------------------------------------------------
# Test: LLMResponse creation
# ---------------------------------------------------------------------------

class TestLLMResponse:

    def test_create_response(self):
        resp = LLMResponse(
            content="Hello, world!",
            model="ollama/llama3:8b",
            input_tokens=42,
            output_tokens=5,
            finish_reason="stop",
            latency_ms=320,
        )
        assert resp.content == "Hello, world!"
        assert resp.model == "ollama/llama3:8b"
        assert resp.input_tokens == 42
        assert resp.output_tokens == 5
        assert resp.finish_reason == "stop"
        assert resp.latency_ms == 320

    def test_response_is_immutable(self):
        resp = LLMResponse(
            content="test",
            model="ollama/llama3:8b",
            input_tokens=10,
            output_tokens=5,
            finish_reason="stop",
            latency_ms=100,
        )
        with pytest.raises(AttributeError):
            resp.content = "changed"


# ---------------------------------------------------------------------------
# Test: Ollama call format
# ---------------------------------------------------------------------------

class TestOllamaCallFormat:

    @pytest.mark.asyncio
    async def test_ollama_request_body(self):
        """Verify the exact payload sent to Ollama."""
        captured_request = {}

        async def mock_post(url, json=None, **kwargs):
            captured_request["url"] = str(url)
            captured_request["body"] = json
            resp = httpx.Response(
                200,
                json={
                    "message": {"content": "Hi there"},
                    "prompt_eval_count": 15,
                    "eval_count": 3,
                    "done_reason": "stop",
                },
                request=httpx.Request("POST", url),
            )
            return resp

        client = LLMClient("ollama/llama3:8b")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = mock_post
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await client.chat(messages, temperature=0.5)

        assert captured_request["url"] == f"{OLLAMA_DEFAULT_HOST}/api/chat"
        assert captured_request["body"]["model"] == "llama3:8b"
        assert captured_request["body"]["stream"] is False
        assert captured_request["body"]["options"]["temperature"] == 0.5
        assert captured_request["body"]["messages"] == messages
        assert result.content == "Hi there"
        assert result.model == "ollama/llama3:8b"
        assert result.input_tokens == 15
        assert result.output_tokens == 3


# ---------------------------------------------------------------------------
# Test: OpenRouter call format
# ---------------------------------------------------------------------------

class TestOpenRouterCallFormat:

    @pytest.mark.asyncio
    async def test_openrouter_request_body(self):
        """Verify the exact payload and headers sent to OpenRouter."""
        captured_request = {}

        async def mock_post(url, json=None, headers=None, **kwargs):
            captured_request["url"] = str(url)
            captured_request["body"] = json
            captured_request["headers"] = headers
            resp = httpx.Response(
                200,
                json={
                    "choices": [{
                        "message": {"content": "Response from Claude"},
                        "finish_reason": "stop",
                    }],
                    "usage": {
                        "prompt_tokens": 30,
                        "completion_tokens": 10,
                    },
                },
                request=httpx.Request("POST", url),
            )
            return resp

        client = LLMClient("openrouter/anthropic/claude-sonnet-4-6")
        messages = [{"role": "user", "content": "Hello"}]

        with patch("httpx.AsyncClient") as MockClient, \
             patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test123"}):
            mock_instance = AsyncMock()
            mock_instance.post = mock_post
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            result = await client.chat(messages, temperature=0.3)

        assert captured_request["url"] == OPENROUTER_BASE_URL
        assert captured_request["headers"]["Authorization"] == "Bearer sk-or-test123"
        assert captured_request["body"]["model"] == "anthropic/claude-sonnet-4-6"
        assert captured_request["body"]["temperature"] == 0.3
        assert result.content == "Response from Claude"
        assert result.model == "openrouter/anthropic/claude-sonnet-4-6"
        assert result.input_tokens == 30
        assert result.output_tokens == 10

    @pytest.mark.asyncio
    async def test_openrouter_missing_api_key(self):
        """Should raise LLMPermanentError when API key is missing."""
        client = LLMClient("openrouter/anthropic/claude-sonnet-4-6")

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(LLMPermanentError, match="OPENROUTER_API_KEY"):
                await client.chat([{"role": "user", "content": "test"}])


# ---------------------------------------------------------------------------
# Test: Error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:

    def test_retryable_error_has_status_code(self):
        err = LLMRetryableError("rate limited", status_code=429)
        assert err.status_code == 429
        assert "rate limited" in str(err)

    def test_permanent_error_has_status_code(self):
        err = LLMPermanentError("bad request", status_code=400)
        assert err.status_code == 400
        assert "bad request" in str(err)

    def test_fallback_triggered_contains_models(self):
        err = LLMFallbackTriggered(
            primary_model="ollama/llama3:8b",
            fallback_model="openrouter/anthropic/claude-sonnet-4-6",
            cause="HTTP 503",
        )
        assert "ollama/llama3:8b" in str(err)
        assert "openrouter/anthropic/claude-sonnet-4-6" in err.fallback_model


# ---------------------------------------------------------------------------
# Test: Retry with backoff (mock httpx)
# ---------------------------------------------------------------------------

class TestRetryWithBackoff:

    @pytest.mark.asyncio
    async def test_retries_on_503_then_succeeds(self):
        """Should retry on 503 and succeed on the second attempt."""
        call_count = 0

        async def flaky_dispatch(messages, provider, model, temp):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMRetryableError("503 Service Unavailable", status_code=503)
            return LLMResponse(
                content="Success after retry",
                model="ollama/llama3:8b",
                input_tokens=10,
                output_tokens=5,
                finish_reason="stop",
                latency_ms=200,
            )

        client = LLMClient("ollama/llama3:8b")
        client._dispatch = flaky_dispatch

        # Patch asyncio.sleep to avoid actual delay
        with patch("faith.agent.llm_client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.chat([{"role": "user", "content": "test"}])

        assert call_count == 2
        assert result.content == "Success after retry"

    @pytest.mark.asyncio
    async def test_exhausts_all_retries(self):
        """Should raise after MAX_RETRIES attempts."""
        call_count = 0

        async def always_fail(messages, provider, model, temp):
            nonlocal call_count
            call_count += 1
            raise LLMRetryableError("429 Too Many Requests", status_code=429)

        client = LLMClient("ollama/llama3:8b")
        client._dispatch = always_fail

        with patch("faith.agent.llm_client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(LLMRetryableError, match="429"):
                await client.chat([{"role": "user", "content": "test"}])

        assert call_count == MAX_RETRIES


# ---------------------------------------------------------------------------
# Test: Permanent error handling (no retry)
# ---------------------------------------------------------------------------

class TestPermanentError:

    @pytest.mark.asyncio
    async def test_permanent_error_not_retried(self):
        """400/401/404 errors should NOT be retried."""
        call_count = 0

        async def bad_request(messages, provider, model, temp):
            nonlocal call_count
            call_count += 1
            raise LLMPermanentError("400 Bad Request", status_code=400)

        client = LLMClient("ollama/llama3:8b")
        client._dispatch = bad_request

        with pytest.raises(LLMPermanentError, match="400"):
            await client.chat([{"role": "user", "content": "test"}])

        assert call_count == 1  # No retries


# ---------------------------------------------------------------------------
# Test: Fallback model switching
# ---------------------------------------------------------------------------

class TestFallbackModelSwitching:

    @pytest.mark.asyncio
    async def test_falls_back_to_secondary_model(self):
        """When primary exhausts retries, fallback model should be tried."""
        models_called = []

        async def dispatch_with_tracking(messages, provider, model, temp):
            models_called.append(f"{provider}/{model}")
            if provider == "ollama":
                raise LLMRetryableError("503", status_code=503)
            return LLMResponse(
                content="Fallback response",
                model=f"openrouter/{model}",
                input_tokens=20,
                output_tokens=8,
                finish_reason="stop",
                latency_ms=500,
            )

        publisher = AsyncMock()
        publisher.agent_error = AsyncMock()

        client = LLMClient(
            model="ollama/llama3:8b",
            fallback_model="openrouter/anthropic/claude-sonnet-4-6",
            event_publisher=publisher,
        )
        client._dispatch = dispatch_with_tracking

        with patch("faith.agent.llm_client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.chat([{"role": "user", "content": "test"}])

        # Primary was tried MAX_RETRIES times
        primary_calls = [m for m in models_called if m.startswith("ollama/")]
        assert len(primary_calls) == MAX_RETRIES

        # Fallback succeeded
        assert result.content == "Fallback response"
        assert "openrouter/" in result.model

        # agent:error event was published
        publisher.agent_error.assert_called_once()


# ---------------------------------------------------------------------------
# Test: Timeout handling
# ---------------------------------------------------------------------------

class TestTimeoutHandling:

    @pytest.mark.asyncio
    async def test_timeout_is_retryable(self):
        """Network timeouts should raise LLMRetryableError."""
        call_count = 0

        async def timeout_then_succeed(messages, provider, model, temp):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMRetryableError("Ollama request failed: timed out")
            return LLMResponse(
                content="OK",
                model="ollama/llama3:8b",
                input_tokens=5,
                output_tokens=1,
                finish_reason="stop",
                latency_ms=100,
            )

        client = LLMClient("ollama/llama3:8b")
        client._dispatch = timeout_then_succeed

        with patch("faith.agent.llm_client.asyncio.sleep", new_callable=AsyncMock):
            result = await client.chat([{"role": "user", "content": "test"}])

        assert call_count == 2
        assert result.content == "OK"
```

---

## Integration Points

- **BaseAgent (FAITH-010):** `BaseAgent._call_llm` instantiates `LLMClient` using the agent's configured model string and delegates all LLM calls through it.
- **EventPublisher (FAITH-008):** `LLMClient` accepts an optional `EventPublisher` and calls `agent_error()` after retry exhaustion. This lets the PA observe LLM failures system-wide.
- **Configuration (FAITH-003):** Model strings come from agent config (`model` field in `.faith/agents/{id}/config.yaml`) or system defaults (`.faith/system.yaml` → `default_agent_model`, `pa.model`, `pa.fallback_model`).
- **Secrets (FAITH-003):** `OPENROUTER_API_KEY` is injected as an environment variable by the PA from framework-level `config/secrets.yaml` before agent containers start.

---

## Acceptance Criteria

1. `parse_model_string` correctly splits `ollama/` and `openrouter/` prefixes, and raises `ValueError` on unknown prefixes.
2. `LLMClient.chat()` routes to `_call_ollama` for `ollama/` models and `_call_openrouter` for `openrouter/` models.
3. Ollama requests are sent to the resolved Ollama endpoint rather than a hard-coded single-host assumption, with the correct payload format (`model`, `messages`, `stream: false`, `options.temperature`).
4. OpenRouter requests are sent to `https://openrouter.ai/api/v1/chat/completions` with `Authorization: Bearer` header and correct payload (`model`, `messages`, `temperature`).
5. Platform-aware Ollama default routing is implemented for Linux, Windows, and macOS.
6. Local-model capability probing records whether inference works, whether GPU acceleration is active, and what local resource budget is available for model selection.
7. Retry logic executes up to 3 attempts with 2s/4s/8s delays on HTTP 429, 503, and network timeouts.
8. HTTP 400, 401, and 404 errors raise `LLMPermanentError` immediately without any retry attempts.
9. When all retries are exhausted and a `fallback_model` is configured, the client automatically switches to the fallback model and retries.
10. After retry exhaustion, an `agent:error` event is published via the `EventPublisher` (if one is provided).
11. `LLMResponse` dataclass contains all six fields (`content`, `model`, `input_tokens`, `output_tokens`, `finish_reason`, `latency_ms`) and is immutable.
12. All tests in `tests/test_llm_client.py` and any new capability-probe tests pass.

---

## Notes for Implementer

- The `LLMClient` validates model strings at construction time, not at call time. If someone passes a bad model string, they get a `ValueError` immediately rather than at the first `.chat()` call. This is intentional — fail fast.
- `_check_status` classifies errors by HTTP status code ONLY. Do not inspect response bodies to determine retryability. Status codes are the contract.
- Unknown HTTP error codes (e.g. 500, 502) are treated as retryable. Only the codes explicitly listed in `PERMANENT_ERROR_CODES` are non-retryable. This is a deliberate safety choice — when in doubt, retry.
- The `httpx.AsyncClient` is created per-request (inside `async with`). This is fine for agent workloads where calls are infrequent. If performance profiling later shows connection overhead, this can be changed to a shared client with connection pooling.
- `latency_ms` is measured using `time.monotonic()` to avoid clock drift issues. It includes network round-trip and response parsing — it is wall-clock latency, not server processing time.
- Ollama token counts come from `prompt_eval_count` and `eval_count` fields, which may not always be present (some Ollama models omit them). The code defaults to 0 in that case.
- The `event_publisher` parameter is typed as `Any` rather than `EventPublisher` to avoid a circular import between `faith.agent` and `faith.protocol`. The duck-typing contract is: it must have an async `agent_error(error: str, recoverable: bool)` method.
