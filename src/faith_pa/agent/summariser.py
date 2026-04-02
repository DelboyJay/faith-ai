"""Description:
    Compact long-running agent conversation history into a durable summary.

Requirements:
    - Decide when context should be summarised based on message count and token usage.
    - Persist the rolling summary to the agent context file.
    - Keep a small set of recent non-disposable messages after compaction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faith_pa.config.models import AgentConfig
from faith_pa.utils.tokens import count_message_tokens

DEFAULT_THRESHOLD_PCT = 50
DEFAULT_MAX_MESSAGES = 50
DEFAULT_RETAIN_RECENT_MESSAGES = 8

SummaryCallable = Callable[[str], Awaitable[Any]]


@dataclass(slots=True)
class SummaryResult:
    """Description:
        Represent the result of one conversation compaction pass.

    Requirements:
        - Preserve the generated summary, retained messages, and compaction count together.

    :param summary: Newly generated summary text.
    :param remaining_messages: Messages retained after compaction.
    :param compacted_messages: Number of messages compacted into the summary.
    """

    summary: str
    remaining_messages: list[dict[str, Any]]
    compacted_messages: int


class ContextSummariser:
    """Description:
        Decide when to summarise agent history and persist the resulting context summary.

    Requirements:
        - Support both ``AgentConfig`` instances and plain dict configuration payloads.
        - Persist summaries under the agent's ``context.md`` file.
        - Retain a bounded tail of recent non-disposable messages after compaction.

    :param agent_id: Owning agent identifier.
    :param model_name: Model name used for token estimation.
    :param context_window_tokens: Total context window size for the model.
    :param context_config: Optional agent context configuration payload.
    :param faith_dir: Base FAITH directory containing agent context files.
    :param retain_recent_messages: Number of recent messages to preserve after compaction.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        model_name: str,
        context_window_tokens: int,
        context_config: AgentConfig | dict[str, Any] | None = None,
        faith_dir: Path | str = ".faith",
        retain_recent_messages: int = DEFAULT_RETAIN_RECENT_MESSAGES,
    ) -> None:
        """Description:
            Initialise the context summariser.

        Requirements:
            - Derive threshold and max-message settings from the supplied context config.
            - Enforce that at least one recent message is retained.

        :param agent_id: Owning agent identifier.
        :param model_name: Model name used for token estimation.
        :param context_window_tokens: Total context window size for the model.
        :param context_config: Optional agent context configuration payload.
        :param faith_dir: Base FAITH directory containing agent context files.
        :param retain_recent_messages: Number of recent messages to preserve after compaction.
        """

        self.agent_id = agent_id
        self.model_name = model_name
        self.context_window_tokens = context_window_tokens
        self.faith_dir = Path(faith_dir)
        self.retain_recent_messages = max(1, retain_recent_messages)

        if isinstance(context_config, AgentConfig):
            threshold_pct = context_config.context.summary_threshold_pct
            max_messages = context_config.context.max_messages
        elif isinstance(context_config, dict):
            threshold_pct = int(context_config.get("summary_threshold_pct", DEFAULT_THRESHOLD_PCT))
            max_messages = int(context_config.get("max_messages", DEFAULT_MAX_MESSAGES))
        else:
            threshold_pct = DEFAULT_THRESHOLD_PCT
            max_messages = DEFAULT_MAX_MESSAGES

        self.threshold_pct = threshold_pct
        self.max_messages = max_messages

    @property
    def context_md_path(self) -> Path:
        """Description:
            Return the on-disk path for the agent's rolling context summary.

        Requirements:
            - Store summaries under ``agents/<agent_id>/context.md`` inside the FAITH directory.

        :returns: Context summary file path.
        """

        return self.faith_dir / "agents" / self.agent_id / "context.md"

    def load_summary(self) -> str:
        """Description:
            Load the persisted summary text for the agent.

        Requirements:
            - Return an empty string when no summary file exists.
            - Strip the timestamp metadata line when one is present.

        :returns: Persisted summary text.
        """

        path = self.context_md_path
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines and lines[0].startswith("<!-- updated:"):
            lines = lines[1:]
        return "\n".join(lines).strip()

    def persist_summary(self, summary: str) -> None:
        """Description:
            Persist one summary text payload to the agent context file.

        Requirements:
            - Create the parent directory when needed.
            - Include an update timestamp comment when the summary is non-empty.
            - Write an empty file when the summary body is blank.

        :param summary: Summary text to persist.
        """

        path = self.context_md_path
        path.parent.mkdir(parents=True, exist_ok=True)
        body = summary.strip()
        if not body:
            path.write_text("", encoding="utf-8")
            return
        timestamp = datetime.now(timezone.utc).isoformat()
        rendered = f"<!-- updated: {timestamp} -->\n\n{body}\n"
        path.write_text(rendered, encoding="utf-8")

    def should_summarise(
        self,
        messages: Sequence[Any],
        *,
        current_task: str = "",
        model_name: str | None = None,
    ) -> bool:
        """Description:
            Return whether the supplied conversation history should be summarised.

        Requirements:
            - Trigger summarisation when the message count reaches the configured maximum.
            - Otherwise use token estimation against the configured threshold percentage.
            - Include the current task text in the token estimate when supplied.

        :param messages: Conversation history to evaluate.
        :param current_task: Optional active task description.
        :param model_name: Optional override model name for token estimation.
        :returns: ``True`` when summarisation should run.
        """

        normalised = self._normalise_messages(messages)
        if len(normalised) >= self.max_messages:
            return True
        payload = list(normalised)
        if current_task:
            payload.append({"role": "user", "content": current_task})
        total_tokens = count_message_tokens(payload, model_name or self.model_name)
        token_threshold = int(self.context_window_tokens * (self.threshold_pct / 100))
        return total_tokens >= token_threshold

    async def summarise(
        self,
        messages: Sequence[Any],
        *,
        existing_summary: str,
        llm_call: SummaryCallable,
    ) -> str:
        """Description:
            Generate an updated summary from the supplied message history.

        Requirements:
            - Build a summary prompt that includes the existing summary and recent messages.
            - Accept both object-style and dict-style LLM responses.

        :param messages: Conversation history to summarise.
        :param existing_summary: Existing persisted summary text.
        :param llm_call: Async callable used to generate the updated summary.
        :returns: Stripped summary text.
        """

        prompt = self.build_summary_prompt(messages, existing_summary)
        response = await llm_call(prompt)
        if hasattr(response, "content"):
            return str(getattr(response, "content", "")).strip()
        if isinstance(response, dict):
            message = response.get("message")
            if isinstance(message, dict):
                return str(message.get("content", "")).strip()
            return str(response.get("content", "")).strip()
        return str(response).strip()

    async def compact(
        self,
        messages: Sequence[Any],
        *,
        existing_summary: str,
        llm_call: SummaryCallable,
    ) -> SummaryResult:
        """Description:
            Summarise the supplied history and return the retained message tail.

        Requirements:
            - Return the existing summary unchanged when there are no messages.
            - Retain only the most recent non-disposable messages after compaction.
            - Insert a synthetic system note when earlier messages were compacted.
            - Persist the generated summary to disk.

        :param messages: Conversation history to compact.
        :param existing_summary: Existing persisted summary text.
        :param llm_call: Async callable used to generate the updated summary.
        :returns: Summary compaction result payload.
        """

        normalised = self._normalise_messages(messages)
        if not normalised:
            return SummaryResult(
                summary=existing_summary.strip(), remaining_messages=[], compacted_messages=0
            )

        summary = await self.summarise(
            normalised, existing_summary=existing_summary, llm_call=llm_call
        )

        disposable_count = sum(1 for message in normalised if message.get("disposable"))
        non_disposable = [message for message in normalised if not message.get("disposable")]
        retained_recent = non_disposable[-self.retain_recent_messages :]
        compacted_from_history = max(0, len(normalised) - len(retained_recent))
        compacted_messages = max(compacted_from_history, disposable_count)

        remaining_messages = [dict(message) for message in retained_recent]
        if compacted_messages:
            remaining_messages.insert(
                0,
                {
                    "role": "system",
                    "content": f"Context compacted. {compacted_messages} earlier messages were summarised.",
                    "disposable": True,
                    "name": "context-summariser",
                },
            )

        self.persist_summary(summary)
        return SummaryResult(
            summary=summary,
            remaining_messages=remaining_messages,
            compacted_messages=compacted_messages,
        )

    def build_summary_prompt(
        self,
        messages: Sequence[Any],
        existing_summary: str,
    ) -> str:
        """Description:
            Build the summary prompt sent to the language model.

        Requirements:
            - Render each recent message with its role and optional name.
            - Include the existing summary even when it is currently empty.

        :param messages: Conversation history to render.
        :param existing_summary: Existing persisted summary text.
        :returns: Prompt text for the summary LLM call.
        """

        rendered_messages = []
        for message in self._normalise_messages(messages):
            role = message.get("role", "unknown")
            name = message.get("name")
            prefix = f"[{role}]" if not name else f"[{role}:{name}]"
            rendered_messages.append(f"{prefix} {str(message.get('content', '')).strip()}")

        existing_block = existing_summary.strip() or "(none)"
        return (
            "You are compacting agent conversation history into a durable context summary.\n\n"
            "Update the existing summary with any new facts, decisions, blockers, file changes, "
            "and outstanding work. Keep it concise and structured.\n\n"
            f"Existing summary:\n{existing_block}\n\n"
            "Recent messages:\n" + "\n".join(rendered_messages)
        )

    @staticmethod
    def _normalise_messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
        """Description:
            Normalise supported message objects into plain chat-message mappings.

        Requirements:
            - Accept dict payloads, dataclasses, and objects exposing role/content fields.
            - Preserve optional ``disposable`` and ``name`` fields when available.
            - Reject unsupported message shapes clearly.

        :param messages: Message sequence to normalise.
        :returns: Normalised chat-message mappings.
        :raises TypeError: If any message object has an unsupported shape.
        """

        normalised: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, dict):
                normalised.append(dict(message))
                continue
            if hasattr(message, "role") and hasattr(message, "content"):
                if hasattr(message, "to_chat_message"):
                    payload = dict(message.to_chat_message())
                elif hasattr(message, "__dataclass_fields__"):
                    payload = asdict(message)
                else:
                    payload = {
                        "role": getattr(message, "role"),
                        "content": getattr(message, "content"),
                    }
                if hasattr(message, "disposable") and "disposable" not in payload:
                    payload["disposable"] = bool(getattr(message, "disposable"))
                if (
                    hasattr(message, "name")
                    and getattr(message, "name") is not None
                    and "name" not in payload
                ):
                    payload["name"] = getattr(message, "name")
                normalised.append(payload)
                continue
            raise TypeError(f"Unsupported message type for summariser: {type(message)!r}")
        return normalised


__all__ = ["ContextSummariser", "SummaryResult"]
