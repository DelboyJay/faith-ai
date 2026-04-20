"""Description:
    Verify model parsing, fallback behaviour, and response normalisation in the LLM client.

Requirements:
    - Prove the client recognises supported provider prefixes.
    - Prove OpenRouter calls require an API key.
    - Prove retryable failures can fall back to a secondary model.
    - Prove provider-specific responses are normalised into the shared response model.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from faith_pa.agent.llm_client import (
    OLLAMA_CONTAINER_HOST,
    LLMClient,
    LLMPermanentError,
    LLMRetryableError,
    LocalModelCapability,
    parse_model_string,
)


class DummyResponse:
    """Description:
        Provide a minimal HTTP response double for LLM client tests.

    Requirements:
        - Expose the attributes and methods used by the LLM client response handling code.

    :param status_code: HTTP status code to expose.
    :param payload: JSON payload returned by ``json()``.
    :param text: Response text used for error reporting.
    """

    def __init__(self, status_code: int, payload: dict, text: str = ""):
        """Description:
            Initialise the dummy HTTP response.

        Requirements:
            - Preserve the supplied status code, JSON payload, and text body.

        :param status_code: HTTP status code to expose.
        :param payload: JSON payload returned by ``json()``.
        :param text: Response text used for error reporting.
        """

        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        """Description:
            Return the dummy JSON payload.

        Requirements:
            - Mirror the interface used by ``httpx.Response`` in the client implementation.

        :returns: Dummy JSON payload.
        """

        return self._payload


def test_parse_model_string_supports_both_backends():
    """Description:
    Verify provider-prefixed model strings are parsed into provider and provider-model parts.

    Requirements:
        - This test is needed to prove the client can dispatch to both supported backends.
        - Verify both Ollama and OpenRouter model prefixes are handled correctly.
    """

    assert parse_model_string("ollama/llama3:8b") == ("ollama", "llama3:8b")
    assert parse_model_string("openrouter/openai/gpt-5") == ("openrouter", "openai/gpt-5")


def test_parse_model_string_rejects_unknown_prefix():
    """Description:
    Verify unknown model prefixes are rejected.

    Requirements:
        - This test is needed to prove unsupported model identifiers fail clearly.
        - Verify an unknown prefix raises ``ValueError``.
    """

    with pytest.raises(ValueError):
        parse_model_string("claude-sonnet")


def test_call_openrouter_requires_key():
    """Description:
    Verify OpenRouter dispatch fails when no API key is configured.

    Requirements:
        - This test is needed to prove the client does not attempt unauthenticated OpenRouter calls.
        - Verify the dispatch path raises ``LLMPermanentError`` without an API key.
    """

    client = LLMClient(model="openrouter/openai/gpt-5")
    with pytest.raises(LLMPermanentError):
        asyncio.run(
            client._dispatch("openrouter/openai/gpt-5", [], temperature=None, max_tokens=None)
        )


def test_chat_falls_back_after_retryable_failure():
    """Description:
    Verify retryable failures on the primary model fall back to the configured secondary model.

    Requirements:
        - This test is needed to prove the client can recover from transient primary-model failures.
        - Verify the returned response comes from the fallback model path.
    """

    client = LLMClient(model="ollama/primary", fallback_model="ollama/fallback")
    side_effects = [LLMRetryableError("try again"), AsyncMock()]

    async def fake_dispatch(model, messages, temperature=None, max_tokens=None):
        """Description:
            Simulate a retryable primary-model failure followed by fallback success.

        Requirements:
            - Raise a retryable error for the primary model.
            - Return the prepared fallback response for the fallback model.

        :param model: Target model name.
        :param messages: Chat message payload.
        :param temperature: Optional temperature value.
        :param max_tokens: Optional token limit.
        :returns: Mock fallback response object.
        """

        del messages, temperature, max_tokens
        if model == "ollama/primary":
            raise LLMRetryableError("try again")
        return side_effects[1]

    side_effects[1].content = "fallback ok"
    side_effects[1].input_tokens = 1
    side_effects[1].output_tokens = 2

    with patch.object(client, "_dispatch", side_effect=fake_dispatch):
        response = asyncio.run(client.chat([{"role": "user", "content": "hi"}]))

    assert response.content == "fallback ok"


def test_raise_for_status_classifies_errors():
    """Description:
    Verify HTTP status handling classifies retryable and permanent failures correctly.

    Requirements:
        - This test is needed to prove the retry logic receives the correct error type for each status class.
        - Verify rate-limit responses are retryable and authentication failures are permanent.
    """

    client = LLMClient(model="ollama/test")
    with pytest.raises(LLMRetryableError):
        client._raise_for_status(DummyResponse(429, {}, "rate limit"))
    with pytest.raises(LLMPermanentError):
        client._raise_for_status(DummyResponse(401, {}, "unauthorized"))


def test_call_ollama_normalises_response_payload():
    """Description:
    Verify Ollama responses are normalised into the shared LLM response format.

    Requirements:
        - This test is needed to prove provider-specific response payloads are translated consistently.
        - Verify content and token counts are extracted from the Ollama response payload.
    """

    client = LLMClient(model="ollama/test")
    response = DummyResponse(
        200,
        {
            "message": {"content": "hello"},
            "prompt_eval_count": 4,
            "eval_count": 6,
            "done_reason": "stop",
        },
    )
    post_mock = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient.post", post_mock):
        result = asyncio.run(
            client._call_ollama(
                "ollama/test", "test", [{"role": "user", "content": "hi"}], temperature=0.0
            )
        )

    assert result.content == "hello"
    assert result.input_tokens == 4
    assert result.output_tokens == 6


def test_resolve_ollama_endpoint_prefers_container_on_linux_with_gpu():
    """Description:
        Verify Linux defaults to the bundled Ollama container when accelerator support is confirmed.

    Requirements:
        - This test is needed to prove the local-model routing policy follows the updated FRS on Linux.
        - Verify container routing wins when Docker GPU support is confirmed.
    """

    client = LLMClient(model="ollama/llama3:8b")
    resolution = client.resolve_ollama_endpoint(platform_name="linux", docker_gpu_supported=True)
    assert resolution.endpoint == OLLAMA_CONTAINER_HOST
    assert resolution.route_kind == "container"


def test_resolve_ollama_endpoint_prefers_host_on_windows_without_docker_gpu():
    """Description:
        Verify Windows defaults to native host Ollama when Docker GPU support is not confirmed.

    Requirements:
        - This test is needed to prove Windows does not assume WSL2/Docker GPU support is available.
        - Verify host routing wins when Docker GPU support is absent.
    """

    client = LLMClient(model="ollama/llama3:8b")
    resolution = client.resolve_ollama_endpoint(
        platform_name="windows",
        docker_gpu_supported=False,
    )
    assert resolution.route_kind == "host"
    assert "host.docker.internal" in resolution.endpoint


def test_resolve_ollama_endpoint_prefers_host_on_macos():
    """Description:
        Verify macOS defaults to native host Ollama rather than the bundled container route.

    Requirements:
        - This test is needed to prove macOS follows the host-first policy from the FRS.
        - Verify host routing wins on macOS without requiring a container-specific override.
    """

    client = LLMClient(model="ollama/llama3:8b")
    resolution = client.resolve_ollama_endpoint(platform_name="darwin")
    assert resolution.route_kind == "host"
    assert "host.docker.internal" in resolution.endpoint


def test_probe_local_model_capability_reports_first_successful_endpoint(monkeypatch):
    """Description:
        Verify local-model capability probing reports the first endpoint that passes the inference probe.

    Requirements:
        - This test is needed to prove FAITH chooses a working Ollama route based on runtime evidence rather than a fixed assumption.
        - Verify the reported capability includes endpoint, route kind, inference readiness, and resource metadata.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    client = LLMClient(model="ollama/llama3:8b")

    async def fake_probe(
        endpoint: str,
        route_kind: str,
        probe_model: str,
        *,
        system_ram_mb: int | None,
        docker_gpu_supported: bool | None,
    ) -> LocalModelCapability | None:
        """Description:
            Simulate endpoint probing for the local-model capability test.

        Requirements:
            - Return a successful capability only for the host route.

        :param endpoint: Candidate Ollama endpoint.
        :param route_kind: Candidate route kind.
        :param probe_model: Model used for the probe.
        :param system_ram_mb: Reported system RAM budget.
        :param docker_gpu_supported: Docker GPU support hint.
        :returns: Capability description for the successful route, otherwise ``None``.
        """

        del docker_gpu_supported
        if route_kind != "host":
            return None
        return LocalModelCapability(
            endpoint=endpoint,
            route_kind=route_kind,
            inference_available=True,
            gpu_acceleration=False,
            usable_vram_mb=0,
            system_ram_mb=system_ram_mb,
            probe_model=probe_model,
            notes=("host probe succeeded",),
        )

    monkeypatch.setattr(client, "_probe_ollama_endpoint", fake_probe)
    capability = asyncio.run(
        client.probe_local_model_capability(
            platform_name="windows",
            docker_gpu_supported=False,
            system_ram_mb=32768,
        )
    )

    assert capability.inference_available is True
    assert capability.route_kind == "host"
    assert capability.system_ram_mb == 32768
