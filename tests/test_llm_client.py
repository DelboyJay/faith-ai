import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from faith_pa.agent.llm_client import (
    LLMClient,
    LLMPermanentError,
    LLMRetryableError,
    parse_model_string,
)


class DummyResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_parse_model_string_supports_both_backends():
    assert parse_model_string("ollama/llama3:8b") == ("ollama", "llama3:8b")
    assert parse_model_string("openrouter/openai/gpt-5") == ("openrouter", "openai/gpt-5")


def test_parse_model_string_rejects_unknown_prefix():
    with pytest.raises(ValueError):
        parse_model_string("claude-sonnet")


def test_call_openrouter_requires_key():
    client = LLMClient(model="openrouter/openai/gpt-5")
    with pytest.raises(LLMPermanentError):
        asyncio.run(
            client._dispatch("openrouter/openai/gpt-5", [], temperature=None, max_tokens=None)
        )


def test_chat_falls_back_after_retryable_failure():
    client = LLMClient(model="ollama/primary", fallback_model="ollama/fallback")
    side_effects = [LLMRetryableError("try again"), AsyncMock()]

    async def fake_dispatch(model, messages, temperature=None, max_tokens=None):
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
    client = LLMClient(model="ollama/test")
    with pytest.raises(LLMRetryableError):
        client._raise_for_status(DummyResponse(429, {}, "rate limit"))
    with pytest.raises(LLMPermanentError):
        client._raise_for_status(DummyResponse(401, {}, "unauthorized"))


def test_call_ollama_normalises_response_payload():
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

