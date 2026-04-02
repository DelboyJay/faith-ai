"""FAITH compact inter-agent protocol models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class MessageType(str, Enum):
    TASK = "task"
    REVIEW_REQUEST = "review_request"
    FEEDBACK = "feedback"
    QUESTION = "question"
    STATUS_UPDATE = "status_update"
    STATUS_REQUEST = "status_request"
    DECISION = "decision"
    ESCALATE = "escalate"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    INSTRUCTION = "instruction"


class MessageStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"


class MessagePriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class CompactMessage(BaseModel):
    from_agent: str = Field(
        alias="from",
        validation_alias=AliasChoices("from", "from_agent"),
    )
    to_agent: str = Field(
        alias="to",
        validation_alias=AliasChoices("to", "to_agent"),
    )
    channel: str
    msg_id: int
    type: MessageType
    tags: list[str] = Field(default_factory=list)
    summary: str
    status: MessageStatus | None = None
    files: list[str] | None = None
    needs: str | None = None
    context_ref: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    priority: MessagePriority = MessagePriority.NORMAL
    disposable: bool = False
    data: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=lambda: _now_iso())

    model_config = ConfigDict(populate_by_name=True)

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, exclude_none=True)

    @classmethod
    def from_json(cls, json_str: str) -> CompactMessage:
        return cls.model_validate_json(json_str)

    def to_dict(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")

    @classmethod
    def from_dict(cls, data: dict) -> CompactMessage:
        return cls.model_validate(data)

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> CompactMessage:
        parsed = yaml.safe_load(yaml_str) or {}
        return cls.model_validate(parsed)

    def to_log_format(self) -> str:
        ts_short = self.ts[11:19] if len(self.ts) >= 19 else self.ts
        lines = [
            f"**[{ts_short}] {self.from_agent} → {self.to_agent}**",
            f"type: {self.type.value}" + (f" | status: {self.status.value}" if self.status else ""),
            f'summary: "{self.summary}"',
        ]
        if self.needs:
            lines.append(f'needs: "{self.needs}"')
        if self.files:
            lines.append(f"files: {self.files}")
        if self.context_ref:
            lines.append(f"context_ref: {self.context_ref}")
        if self.data:
            lines.append(f"data: {self.data}")
        if self.disposable:
            lines.append("disposable: true")
        return "\n".join(lines)

    def to_compact_summary(self) -> str:
        status_str = f" [{self.status.value}]" if self.status else ""
        return (
            f"[{self.ts[:10]}] {self.from_agent}→{self.to_agent} "
            f"{self.type.value}{status_str}: {self.summary}"
        )


class MessageFilter:
    def __init__(self, agent_id: str, listen_tags: list[str]):
        self.agent_id = agent_id
        self.listen_tags = set(listen_tags)

    def should_include(self, message: CompactMessage) -> bool:
        if message.to_agent == self.agent_id or message.to_agent == "all":
            return True
        return bool(self.listen_tags & set(message.tags))


class ChannelMessageStore:
    def __init__(self, channel: str):
        self.channel = channel
        self._messages: list[CompactMessage] = []
        self._next_id: int = 1

    @property
    def next_msg_id(self) -> int:
        return self._next_id

    def add(self, message: CompactMessage) -> None:
        self._messages.append(message)
        if message.msg_id >= self._next_id:
            self._next_id = message.msg_id + 1

    def get_recent(self, n: int = 20) -> list[CompactMessage]:
        return self._messages[-n:]

    def get_by_id(self, msg_id: int) -> CompactMessage | None:
        for msg in self._messages:
            if msg.msg_id == msg_id:
                return msg
        return None

    def resolve_context_ref(self, ref: str) -> list[CompactMessage]:
        if "/" not in ref:
            return []

        channel_part, msg_part = ref.split("/", 1)
        if channel_part != self.channel:
            return []
        if not msg_part.startswith("msg-"):
            return []

        id_part = msg_part[4:]
        if "-" in id_part:
            try:
                start, end = id_part.split("-", 1)
                start_id, end_id = int(start), int(end)
            except ValueError:
                return []
            return [m for m in self._messages if start_id <= m.msg_id <= end_id]

        try:
            target_id = int(id_part)
        except ValueError:
            return []
        msg = self.get_by_id(target_id)
        return [msg] if msg else []

    def get_all(self) -> list[CompactMessage]:
        return list(self._messages)

    def count(self) -> int:
        return len(self._messages)

    def clear(self) -> None:
        self._messages.clear()
        self._next_id = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
