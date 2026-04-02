"""Description:
    Define the FAITH compact inter-agent message protocol and helper utilities.

Requirements:
    - Model compact messages with stable serialisation helpers.
    - Support JSON, YAML, log, and summary renderings.
    - Provide simple filtering and in-memory channel-history helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class MessageType(str, Enum):
    """Description:
        Enumerate the compact message types used by FAITH agents.

    Requirements:
        - Cover task, review, status, decision, escalation, and tool-oriented messages.
    """

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
    """Description:
        Enumerate the status markers used by compact messages.

    Requirements:
        - Distinguish active, completed, blocked, and input-waiting states.
    """

    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"


class MessagePriority(str, Enum):
    """Description:
        Enumerate the priority levels used by compact messages.

    Requirements:
        - Cover low through critical priority bands.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class CompactMessage(BaseModel):
    """Description:
        Represent one compact FAITH inter-agent message.

    Requirements:
        - Support ``from``/``to`` aliases for JSON compatibility while exposing ``from_agent`` and ``to_agent`` attributes.
        - Provide JSON, dict, YAML, log, and summary renderings.
    """

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
    ts: str = Field(default_factory=lambda: _now_iso())

    model_config = ConfigDict(populate_by_name=True)

    def to_json(self) -> str:
        """Description:
            Serialise the compact message as JSON.

        Requirements:
            - Use field aliases and omit ``None`` fields.

        :returns: JSON representation of the message.
        """

        return self.model_dump_json(by_alias=True, exclude_none=True)

    @classmethod
    def from_json(cls, json_str: str) -> CompactMessage:
        """Description:
            Parse a compact message from JSON text.

        Requirements:
            - Validate the JSON payload against the compact message schema.

        :param json_str: JSON text to parse.
        :returns: Parsed compact message.
        """

        return cls.model_validate_json(json_str)

    def to_dict(self) -> dict:
        """Description:
            Serialise the compact message as a plain Python mapping.

        Requirements:
            - Use field aliases and omit ``None`` fields.

        :returns: Dict representation of the message.
        """

        return self.model_dump(by_alias=True, exclude_none=True, mode="json")

    @classmethod
    def from_dict(cls, data: dict) -> CompactMessage:
        """Description:
            Parse a compact message from a plain Python mapping.

        Requirements:
            - Validate the mapping against the compact message schema.

        :param data: Mapping to parse.
        :returns: Parsed compact message.
        """

        return cls.model_validate(data)

    def to_yaml(self) -> str:
        """Description:
            Serialise the compact message as YAML.

        Requirements:
            - Preserve key order and allow Unicode content.

        :returns: YAML representation of the message.
        """

        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> CompactMessage:
        """Description:
            Parse a compact message from YAML text.

        Requirements:
            - Treat empty YAML input as an empty mapping before validation.

        :param yaml_str: YAML text to parse.
        :returns: Parsed compact message.
        """

        parsed = yaml.safe_load(yaml_str) or {}
        return cls.model_validate(parsed)

    def to_log_format(self) -> str:
        """Description:
            Render the compact message in a readable multiline log format.

        Requirements:
            - Include timestamp, routing, type, summary, and optional metadata when present.

        :returns: Human-readable log rendering.
        """

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
        """Description:
            Render the compact message as a single-line summary.

        Requirements:
            - Include the date, routing, type, optional status, and summary text.

        :returns: Single-line compact summary.
        """

        status_str = f" [{self.status.value}]" if self.status else ""
        return (
            f"[{self.ts[:10]}] {self.from_agent}→{self.to_agent} "
            f"{self.type.value}{status_str}: {self.summary}"
        )


class MessageFilter:
    """Description:
        Filter compact messages for one agent based on direct routing or subscribed tags.

    Requirements:
        - Include messages addressed directly to the agent or broadcast to ``all``.
        - Also include messages whose tags intersect with the listener tags.

    :param agent_id: Agent identifier receiving the filtered view.
    :param listen_tags: Tags the agent listens for.
    """

    def __init__(self, agent_id: str, listen_tags: list[str]):
        """Description:
            Initialise the message filter.

        Requirements:
            - Store the listen tags as a set for efficient overlap checks.

        :param agent_id: Agent identifier receiving the filtered view.
        :param listen_tags: Tags the agent listens for.
        """

        self.agent_id = agent_id
        self.listen_tags = set(listen_tags)

    def should_include(self, message: CompactMessage) -> bool:
        """Description:
            Return whether the supplied message should be visible to the agent.

        Requirements:
            - Include directly addressed and broadcast messages.
            - Include tagged messages when the tag sets intersect.

        :param message: Message to evaluate.
        :returns: ``True`` when the message should be included.
        """

        if message.to_agent == self.agent_id or message.to_agent == "all":
            return True
        return bool(self.listen_tags & set(message.tags))


class ChannelMessageStore:
    """Description:
        Store compact messages for one channel with simple lookup helpers.

    Requirements:
        - Preserve message order and track the next message identifier.
        - Support recent-message, by-id, and context-reference lookups.

    :param channel: Channel name represented by the store.
    """

    def __init__(self, channel: str):
        """Description:
            Initialise the in-memory message store.

        Requirements:
            - Start with no messages and ``1`` as the next message identifier.

        :param channel: Channel name represented by the store.
        """

        self.channel = channel
        self._messages: list[CompactMessage] = []
        self._next_id: int = 1

    @property
    def next_msg_id(self) -> int:
        """Description:
            Return the next message identifier for the channel.

        Requirements:
            - Reflect the highest seen message identifier plus one.

        :returns: Next message identifier.
        """

        return self._next_id

    def add(self, message: CompactMessage) -> None:
        """Description:
            Append one compact message to the channel store.

        Requirements:
            - Advance the next message identifier when the stored message uses a newer identifier.

        :param message: Message to append.
        """

        self._messages.append(message)
        if message.msg_id >= self._next_id:
            self._next_id = message.msg_id + 1

    def get_recent(self, n: int = 20) -> list[CompactMessage]:
        """Description:
            Return the most recent messages for the channel.

        Requirements:
            - Preserve the original message order.

        :param n: Maximum number of recent messages to return.
        :returns: Most recent channel messages.
        """

        return self._messages[-n:]

    def get_by_id(self, msg_id: int) -> CompactMessage | None:
        """Description:
            Return one message by its message identifier.

        Requirements:
            - Return ``None`` when the message identifier does not exist.

        :param msg_id: Message identifier to look up.
        :returns: Matching message, if any.
        """

        for msg in self._messages:
            if msg.msg_id == msg_id:
                return msg
        return None

    def resolve_context_ref(self, ref: str) -> list[CompactMessage]:
        """Description:
            Resolve a context reference into the matching channel messages.

        Requirements:
            - Support single-message references like ``channel/msg-7``.
            - Support range references like ``channel/msg-7-9``.
            - Return an empty list for malformed or cross-channel references.

        :param ref: Context reference string.
        :returns: Matching channel messages.
        """

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
        """Description:
            Return all stored channel messages.

        Requirements:
            - Preserve the stored message order.

        :returns: All stored channel messages.
        """

        return list(self._messages)

    def count(self) -> int:
        """Description:
            Return the number of stored channel messages.

        Requirements:
            - Reflect the full message count currently stored.

        :returns: Stored message count.
        """

        return len(self._messages)

    def clear(self) -> None:
        """Description:
            Remove all messages from the channel store.

        Requirements:
            - Reset the next message identifier back to ``1``.
        """

        self._messages.clear()
        self._next_id = 1


def _now_iso() -> str:
    """Description:
        Return the current UTC timestamp for compact messages.

    Requirements:
        - Use an ISO-8601 UTC string ending with ``Z``.

    :returns: Current UTC timestamp string.
    """

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
