"""Description:
    Provide token estimation helpers used by the FAITH agent runtime.

Requirements:
    - Support model-aware token counting when ``tiktoken`` is available.
    - Fall back to conservative character-based estimates when a tokenizer is unavailable.
    - Expose helpers for context-threshold checks and prompt truncation.
"""

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
    """Description:
        Resolve the tokenizer encoding for one model name.

    Requirements:
        - Return ``None`` when ``tiktoken`` is unavailable.
        - Fall back to the ``cl100k_base`` encoding for unknown model names.

    :param model: Optional model name.
    :returns: Matching tokenizer encoding or ``None``.
    """

    if tiktoken is None:
        return None
    target = model or "gpt-4o-mini"
    try:
        return tiktoken.encoding_for_model(target)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_text_tokens(text: str, model: str | None = None) -> int:
    """Description:
        Estimate the token count for one plain-text fragment.

    Requirements:
        - Return ``0`` for empty input.
        - Use the tokenizer when available.
        - Fall back to a conservative character-per-token estimate otherwise.

    :param text: Text to measure.
    :param model: Optional model name used for tokenizer selection.
    :returns: Estimated token count.
    """

    if not text:
        return 0

    encoding = _encoding_for_model(model)
    if encoding is None:
        return max(1, (len(text) + FALLBACK_CHARS_PER_TOKEN - 1) // FALLBACK_CHARS_PER_TOKEN)
    return len(encoding.encode(text))


def count_message_tokens(messages: Sequence[Mapping[str, object]], model: str | None = None) -> int:
    """Description:
        Estimate the token count for a sequence of chat messages.

    Requirements:
        - Include a small per-message overhead in the estimate.
        - Count the ``role``, ``content``, and optional ``name`` fields.

    :param messages: Chat message payloads to measure.
    :param model: Optional model name used for tokenizer selection.
    :returns: Estimated token count for the message sequence.
    """

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
    """Description:
        Return the usable token threshold for one model context window.

    Requirements:
        - Apply the configured percentage limit before the safety margin.
        - Never return a threshold below ``1``.

    :param context_window: Total context window for the model.
    :param pct: Percentage of the context window available for prompt content.
    :param safety_margin: Additional margin reserved for response generation and overhead.
    :returns: Safe usable token threshold.
    """

    base = int(context_window * (pct / 100.0))
    adjusted = int(base * (1.0 - safety_margin))
    return max(1, adjusted)


def over_context_threshold(token_count: int, context_window: int, pct: int) -> bool:
    """Description:
        Return whether the current token count exceeds the safe threshold.

    Requirements:
        - Compare against the threshold returned by ``context_threshold``.

    :param token_count: Observed token count.
    :param context_window: Total context window for the model.
    :param pct: Percentage of the context window available for prompt content.
    :returns: ``True`` when the token count is at or above the threshold.
    """

    return token_count >= context_threshold(context_window, pct)


def truncate_text_to_token_limit(text: str, token_limit: int, model: str | None = None) -> str:
    """Description:
        Truncate text so it fits within an approximate token limit.

    Requirements:
        - Return an empty string for empty input or non-positive limits.
        - Use tokenizer-aware truncation when a tokenizer is available.
        - Fall back to a conservative character-based truncation otherwise.

    :param text: Text to truncate.
    :param token_limit: Maximum allowed token count.
    :param model: Optional model name used for tokenizer selection.
    :returns: Truncated text that fits within the approximate token budget.
    """

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
    """Description:
        Count the combined token usage for multiple text fragments.

    Requirements:
        - Reuse the plain-text token counting helper for each fragment.

    :param parts: Text fragments to measure.
    :param model: Optional model name used for tokenizer selection.
    :returns: Combined token count estimate.
    """

    return sum(count_text_tokens(part, model) for part in parts)
