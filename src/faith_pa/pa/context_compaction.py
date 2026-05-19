"""Description:
    Track Project Agent active-context usage and compact history safely.

Requirements:
    - Classify active-context usage into no-op, soft-compaction, or hard-compaction modes.
    - Keep deterministic must-retain history messages such as blockers and approvals.
    - Leave FAITH core rules, AGENTS.md content, and MCP tool information outside compaction scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from faith_pa.utils.tokens import count_message_tokens

DEFAULT_SOFT_COMPACTION_THRESHOLD_PCT = 80
DEFAULT_HARD_COMPACTION_THRESHOLD_PCT = 95
DEFAULT_RETAIN_RECENT_MESSAGES = 4
RETAIN_KEYWORDS = (
    "approval",
    "approve",
    "denied",
    "blocker",
    "blocked",
    "unresolved",
    "failure",
    "failed",
    "error",
    "warning",
    "todo",
    "next step",
    "follow up",
)


class ContextCompactionMode(str, Enum):
    """Description:
        Enumerate the supported Project Agent context-compaction modes.

    Requirements:
        - Preserve explicit labels for no compaction, soft compaction, and hard compaction.
    """

    NONE = "none"
    SOFT = "soft"
    HARD = "hard"


@dataclass(frozen=True, slots=True)
class ContextCompactionDecision:
    """Description:
        Represent one active-context compaction decision.

    Requirements:
        - Preserve the chosen mode, estimated usage percentage, and reliable context-window limit.

    :param mode: Chosen compaction mode.
    :param usage_percentage: Estimated active-context usage percentage when known.
    :param context_window_limit: Reliable context-window limit used for the estimate.
    """

    mode: ContextCompactionMode
    usage_percentage: int | None
    context_window_limit: int | None


@dataclass(frozen=True, slots=True)
class HistoryCompactionSelection:
    """Description:
        Represent the retained-versus-compacted history split for one compaction pass.

    Requirements:
        - Preserve both retained and compacted message groups for later inspection.

    :param retained_messages: Messages that must remain in active prompt history.
    :param compacted_messages: Older resolved messages selected for summarisation.
    """

    retained_messages: list[dict[str, Any]]
    compacted_messages: list[dict[str, Any]]


class ContextCompactionController:
    """Description:
        Decide when Project Agent active-context compaction should run and what history it may compact.

    Requirements:
        - Use only reliable model-window limits for percentage-based decisions.
        - Keep deterministic still-relevant history messages outside the compacted set.

    :param model_name: Model name used for token estimation.
    :param soft_threshold_pct: Percentage at which soft compaction becomes eligible.
    :param hard_threshold_pct: Percentage at which hard pre-turn compaction is required.
    :param retain_recent_messages: Number of newest history messages that always remain active.
    """

    def __init__(
        self,
        *,
        model_name: str,
        soft_threshold_pct: int = DEFAULT_SOFT_COMPACTION_THRESHOLD_PCT,
        hard_threshold_pct: int = DEFAULT_HARD_COMPACTION_THRESHOLD_PCT,
        retain_recent_messages: int = DEFAULT_RETAIN_RECENT_MESSAGES,
    ) -> None:
        """Description:
            Initialise the Project Agent context-compaction controller.

        Requirements:
            - Keep the thresholds configurable for deterministic tests.
            - Retain at least one recent message.

        :param model_name: Model name used for token estimation.
        :param soft_threshold_pct: Percentage at which soft compaction becomes eligible.
        :param hard_threshold_pct: Percentage at which hard pre-turn compaction is required.
        :param retain_recent_messages: Number of newest history messages that always remain active.
        """

        self.model_name = model_name
        self.soft_threshold_pct = soft_threshold_pct
        self.hard_threshold_pct = hard_threshold_pct
        self.retain_recent_messages = max(1, retain_recent_messages)

    def estimate_usage_percentage(
        self,
        messages: list[dict[str, Any]],
        *,
        context_window_limit: int | None,
    ) -> int | None:
        """Description:
            Estimate the active-context usage percentage for one assembled chat payload.

        Requirements:
            - Return ``None`` when FAITH does not know the reliable context-window limit.

        :param messages: Assembled chat payload ready for token counting.
        :param context_window_limit: Reliable context-window limit when known.
        :returns: Rounded usage percentage, or ``None`` when the limit is unknown.
        """

        if context_window_limit is None or context_window_limit <= 0:
            return None
        token_count = count_message_tokens(messages, self.model_name)
        return round((token_count / context_window_limit) * 100)

    def classify_usage(
        self,
        *,
        usage_percentage: int | None,
        context_window_limit: int | None,
    ) -> ContextCompactionDecision:
        """Description:
            Classify one active-context usage estimate into a compaction mode.

        Requirements:
            - Trigger hard compaction at or above the hard threshold.
            - Trigger soft compaction at or above the soft threshold but below the hard threshold.
            - Stay idle when no reliable usage estimate is available.

        :param usage_percentage: Estimated active-context usage percentage when known.
        :param context_window_limit: Reliable context-window limit used for the estimate.
        :returns: Deterministic compaction decision.
        """

        if usage_percentage is None or context_window_limit is None or context_window_limit <= 0:
            return ContextCompactionDecision(
                mode=ContextCompactionMode.NONE,
                usage_percentage=usage_percentage,
                context_window_limit=context_window_limit,
            )
        if usage_percentage >= self.hard_threshold_pct:
            mode = ContextCompactionMode.HARD
        elif usage_percentage >= self.soft_threshold_pct:
            mode = ContextCompactionMode.SOFT
        else:
            mode = ContextCompactionMode.NONE
        return ContextCompactionDecision(
            mode=mode,
            usage_percentage=usage_percentage,
            context_window_limit=context_window_limit,
        )

    def select_history_for_compaction(
        self,
        history: list[dict[str, Any]],
    ) -> HistoryCompactionSelection:
        """Description:
            Split Project Agent history into retained and compactable messages deterministically.

        Requirements:
            - Always retain the newest bounded suffix of history.
            - Retain explicitly pinned and still-relevant blocker or approval messages.

        :param history: Existing Project Agent prompt history.
        :returns: Deterministic retained-versus-compacted history split.
        """

        if not history:
            return HistoryCompactionSelection(retained_messages=[], compacted_messages=[])

        retained_indexes = set(
            range(max(0, len(history) - self.retain_recent_messages), len(history))
        )
        for index, message in enumerate(history):
            if self._should_retain_message(message):
                retained_indexes.add(index)

        retained_messages = [dict(history[index]) for index in sorted(retained_indexes)]
        compacted_messages = [
            dict(message) for index, message in enumerate(history) if index not in retained_indexes
        ]
        return HistoryCompactionSelection(
            retained_messages=retained_messages,
            compacted_messages=compacted_messages,
        )

    def build_summary_prompt(
        self,
        *,
        existing_summary: str,
        compacted_messages: list[dict[str, Any]],
    ) -> str:
        """Description:
            Build the deterministic local summariser prompt for compacted Project Agent history.

        Requirements:
            - Focus on done or decided outcomes for resolved older turns.
            - Preserve the prior working-memory summary when present.

        :param existing_summary: Existing compacted working-memory summary.
        :param compacted_messages: History messages selected for summarisation.
        :returns: Prompt text for the local compaction summariser.
        """

        rendered_messages = [
            f"[{message.get('role', 'unknown')}] {str(message.get('content', '')).strip()}"
            for message in compacted_messages
        ]
        summary_block = existing_summary.strip() or "(none)"
        return (
            "You are compacting older resolved Project Agent conversation history into short, inspectable working-memory notes.\n\n"
            "Summarise only older resolved turns. Keep any completed outcomes, decisions, or facts that could still matter later.\n"
            "Do not invent new work. Keep the result concise.\n\n"
            f"Existing working-memory summary:\n{summary_block}\n\n"
            "Older resolved history to compact:\n" + "\n".join(rendered_messages)
        )

    @staticmethod
    def build_compaction_note(*, compacted_messages: int) -> str:
        """Description:
            Build the synthetic retained history note inserted after one compaction pass.

        Requirements:
            - Describe how many earlier messages were compacted into the working summary.

        :param compacted_messages: Number of earlier history messages compacted.
        :returns: Synthetic retained history note text.
        """

        return f"Context compacted. {compacted_messages} earlier history messages were summarised."

    @staticmethod
    def _should_retain_message(message: dict[str, Any]) -> bool:
        """Description:
            Return whether one history message must remain active during compaction.

        Requirements:
            - Retain explicit pinned messages.
            - Retain messages that still look operationally relevant, such as blockers or approvals.

        :param message: Project Agent history message candidate.
        :returns: ``True`` when the message must remain in active history.
        """

        if bool(message.get("retain")):
            return True
        content = str(message.get("content", "")).casefold()
        return any(keyword in content for keyword in RETAIN_KEYWORDS)


__all__ = [
    "ContextCompactionController",
    "ContextCompactionDecision",
    "ContextCompactionMode",
    "HistoryCompactionSelection",
]
