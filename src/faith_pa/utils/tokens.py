"""Token counting utilities for FAITH."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache

try:
    import tiktoken  # type: ignore
except ImportError:  # pragma: no cover - fallback path tested separately
    tiktoken = None

FALLBACK_CHARS_PER_TOKEN = 4
DEFAULT_TOKEN_SAFETY_MARGIN = 0.10


@lru_cache(maxsize=32)
def _encoding_for_model(model: str | None):
    if tiktoken is None:
        return None
    target = model or "gpt-4o-mini"
    try:
        return tiktoken.encoding_for_model(target)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_text_tokens(text: str, model: str | None = None) -> int:
    """Count tokens for plain text with a safe fallback when no tokenizer exists."""

    if not text:
        return 0

    encoding = _encoding_for_model(model)
    if encoding is None:
        return max(1, (len(text) + FALLBACK_CHARS_PER_TOKEN - 1) // FALLBACK_CHARS_PER_TOKEN)
    return len(encoding.encode(text))


def count_message_tokens(messages: Sequence[Mapping[str, object]], model: str | None = None) -> int:
    """Approximate token count for chat messages."""

    total = 0
    for message in messages:
        total += 4
        total += count_text_tokens(str(message.get("role", "")), model)
        total += count_text_tokens(str(message.get("content", "")), model)
        name = message.get("name")
        if name:
            total += count_text_tokens(str(name), model)
    return total + 2 if messages else 0


def context_threshold(
    context_window: int, pct: int, *, safety_margin: float = DEFAULT_TOKEN_SAFETY_MARGIN
) -> int:
    """Return the usable token threshold for a context window."""

    base = int(context_window * (pct / 100.0))
    adjusted = int(base * (1.0 - safety_margin))
    return max(1, adjusted)


def over_context_threshold(token_count: int, context_window: int, pct: int) -> bool:
    """Return whether token_count exceeds the configured threshold."""

    return token_count >= context_threshold(context_window, pct)


def truncate_text_to_token_limit(text: str, token_limit: int, model: str | None = None) -> str:
    """Truncate text to approximately token_limit tokens."""

    if token_limit <= 0 or not text:
        return ""

    encoding = _encoding_for_model(model)
    if encoding is None:
        char_limit = token_limit * FALLBACK_CHARS_PER_TOKEN
        return text[:char_limit]

    tokens = encoding.encode(text)
    if len(tokens) <= token_limit:
        return text
    return encoding.decode(tokens[:token_limit])


def summarize_token_usage(parts: Iterable[str], model: str | None = None) -> int:
    """Count the combined tokens for a sequence of text fragments."""

    return sum(count_text_tokens(part, model) for part in parts)
