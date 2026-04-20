"""Description:
    Provide provider-specific prompt-caching helpers for stable CAG-prefixed prompts.

Requirements:
    - Detect the effective provider from model or endpoint hints.
    - Apply Anthropic cache-control annotations when CAG content is present.
    - Leave OpenAI and Ollama payloads structurally unchanged.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class LLMProvider(str, Enum):
    """Description:
        Enumerate the provider categories relevant to CAG prompt caching.

    Requirements:
        - Preserve distinct Anthropic, OpenAI, Ollama, and unknown provider values.
    """

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA = "ollama"
    UNKNOWN = "unknown"


def detect_provider(model: str, api_base: str = "") -> LLMProvider:
    """Description:
        Detect the effective provider from the model name and optional API-base hint.

    Requirements:
        - Recognise Anthropic, OpenAI, and Ollama naming conventions.
        - Fall back to ``UNKNOWN`` when the provider cannot be inferred safely.

    :param model: Fully qualified or provider-native model name.
    :param api_base: Optional API base URL or endpoint hint.
    :returns: Detected provider enum value.
    """

    model_lower = model.lower()
    api_lower = api_base.lower()
    if "claude" in model_lower or "anthropic" in model_lower or "anthropic" in api_lower:
        return LLMProvider.ANTHROPIC
    if (
        any(token in model_lower for token in ("gpt-", "o1", "o3", "openai/"))
        or "openai" in api_lower
    ):
        return LLMProvider.OPENAI
    if "ollama/" in model_lower or "ollama" in api_lower or "11434" in api_lower:
        return LLMProvider.OLLAMA
    return LLMProvider.UNKNOWN


def apply_cache_hints(
    messages: list[dict[str, Any]],
    *,
    provider: LLMProvider,
    cag_present: bool,
) -> list[dict[str, Any]]:
    """Description:
        Apply provider-specific prompt-caching hints to one chat payload.

    Requirements:
        - Annotate Anthropic system prompts with an ephemeral cache-control block when CAG is present.
        - Leave OpenAI and Ollama payload shapes unchanged because caching is automatic or implicit.

    :param messages: Chat payload to annotate.
    :param provider: Detected provider category.
    :param cag_present: Whether stable CAG content is present in the prompt prefix.
    :returns: Potentially modified chat payload.
    """

    if not cag_present or not messages:
        return messages
    if provider is not LLMProvider.ANTHROPIC:
        return messages
    return _apply_anthropic_cache_hints(messages)


def _apply_anthropic_cache_hints(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Description:
        Apply Anthropic cache-control annotations to the leading system message.

    Requirements:
        - Convert a plain string system message to block format before annotating it.
        - Mark only the final system block as cacheable when block content already exists.

    :param messages: Chat payload to annotate.
    :returns: Modified chat payload.
    """

    if not messages or messages[0].get("role") != "system":
        return messages

    system_message = messages[0]
    content = system_message.get("content", "")
    if isinstance(content, str):
        system_message["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        return messages

    if isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1]["cache_control"] = {"type": "ephemeral"}
    return messages
