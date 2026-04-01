"""Rolling context summary helpers for FAITH agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faith.config.models import AgentConfig
from faith.utils.tokens import count_message_tokens

DEFAULT_THRESHOLD_PCT = 50
DEFAULT_MAX_MESSAGES = 50
DEFAULT_RETAIN_RECENT_MESSAGES = 8

SummaryCallable = Callable[[str], Awaitable[Any]]


@dataclass(slots=True)
class SummaryResult:
    """Result of a context compaction pass."""

    summary: str
    remaining_messages: list[dict[str, Any]]
    compacted_messages: int


class ContextSummariser:
    """Adaptive summariser for agent conversation history."""

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
        return self.faith_dir / "agents" / self.agent_id / "context.md"

    def load_summary(self) -> str:
        path = self.context_md_path
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines and lines[0].startswith("<!-- updated:"):
            lines = lines[1:]
        return "\n".join(lines).strip()

    def persist_summary(self, summary: str) -> None:
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
