# FAITH-007 — Compact Protocol Data Models & Serialisation

**Phase:** 2 — Compact Protocol & Events
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-002
**FRS Reference:** Section 3.3, 3.4

---

## Objective

Define the Python data models for the compact inter-agent protocol, implement serialisation/deserialisation for Redis transport, implement tag-based message filtering, and implement `context_ref` resolution. This is the core communication layer between all FAITH agents.

---

## Architecture

```
faith/protocol/
├── __init__.py
├── compact.py        ← Message model, serialisation, filtering (this task)
└── events.py         ← Event model (FAITH-008)
```

---

## Files to Create

### 1. `faith/protocol/compact.py`

```python
"""FAITH Compact Inter-Agent Protocol.

Defines the structured, token-efficient message format used for all
agent-to-agent and PA-to-agent communication. Messages are serialised
to JSON for Redis transport and can be rendered as YAML for human
readability in logs.

FRS Reference: Section 3.3, 3.4
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """Types of compact protocol messages."""
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


class MessageStatus(str, Enum):
    """Status values for compact protocol messages."""
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"


class MessagePriority(str, Enum):
    """Priority levels for compact protocol messages."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class CompactMessage(BaseModel):
    """A single compact protocol message.

    This is the primary communication unit between agents in FAITH.
    All fields align with FRS Section 3.3.

    Attributes:
        from_agent: Sending agent short name (e.g. "dev", "qa").
        to_agent: Target agent(s), or "all" for broadcast.
        channel: Channel identifier (e.g. "ch-auth-feature").
        msg_id: Sequential message ID within the channel.
        type: Message type (task, review_request, feedback, etc.).
        tags: Role-relevance tags for context filtering.
        summary: Concise description of content.
        status: Current status (optional).
        files: Relevant file paths (optional).
        needs: What is required from the recipient (optional).
        context_ref: Reference to previous messages (optional).
        priority: Message priority (optional, default normal).
        disposable: If true, artifact can be purged during compaction
            once the related task is complete. Only the summary line
            is retained.
        ts: ISO 8601 timestamp (auto-generated if not provided).
    """

    from_agent: str = Field(alias="from")
    to_agent: str = Field(alias="to")
    channel: str
    msg_id: int
    type: MessageType
    tags: list[str]
    summary: str
    status: Optional[MessageStatus] = None
    files: Optional[list[str]] = None
    needs: Optional[str] = None
    context_ref: Optional[str] = None
    priority: MessagePriority = MessagePriority.NORMAL
    disposable: bool = False
    ts: str = Field(default_factory=lambda: _now_iso())

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "from": "dev",
                    "to": "qa",
                    "channel": "ch-auth-feature",
                    "msg_id": 47,
                    "type": "review_request",
                    "tags": ["code", "auth", "testing"],
                    "status": "complete",
                    "summary": "auth module done, 3 endpoints, JWT httponly cookies",
                    "needs": "test coverage for token expiry edge case",
                    "context_ref": "ch-auth-feature/msg-42-46",
                }
            ]
        },
    }

    def to_json(self) -> str:
        """Serialise to JSON string for Redis transport.

        Uses aliases ('from'/'to' instead of 'from_agent'/'to_agent')
        and excludes None values for compactness.
        """
        return self.model_dump_json(by_alias=True, exclude_none=True)

    @classmethod
    def from_json(cls, json_str: str) -> "CompactMessage":
        """Deserialise from JSON string received via Redis."""
        return cls.model_validate_json(json_str)

    def to_dict(self) -> dict:
        """Convert to dict with aliases, excluding None values."""
        return self.model_dump(by_alias=True, exclude_none=True)

    @classmethod
    def from_dict(cls, data: dict) -> "CompactMessage":
        """Create from a dict (e.g. parsed from YAML)."""
        return cls.model_validate(data)

    def to_log_format(self) -> str:
        """Render as a human-readable log entry for session logs.

        Format matches FRS Section 8.4 channel log format:
        **[HH:MM:SS] from → to**
        type: X | status: Y
        summary: "..."
        needs: "..."
        """
        ts_short = self.ts[11:19] if len(self.ts) >= 19 else self.ts
        lines = [
            f"**[{ts_short}] {self.from_agent} → {self.to_agent}**",
            f"type: {self.type.value}"
            + (f" | status: {self.status.value}" if self.status else ""),
            f'summary: "{self.summary}"',
        ]
        if self.needs:
            lines.append(f'needs: "{self.needs}"')
        if self.files:
            lines.append(f"files: {self.files}")
        if self.context_ref:
            lines.append(f"context_ref: {self.context_ref}")
        if self.disposable:
            lines.append("disposable: true")
        return "\n".join(lines)

    def to_compact_summary(self) -> str:
        """One-line summary for context compaction retention.

        When a disposable message is compacted, this is what remains
        in the rolling context summary.
        """
        status_str = f" [{self.status.value}]" if self.status else ""
        return (
            f"[{self.ts[:10]}] {self.from_agent}→{self.to_agent} "
            f"{self.type.value}{status_str}: {self.summary}"
        )


class MessageFilter:
    """Filters compact protocol messages by tag relevance.

    Each agent has a set of tags it listens to (configured in agents.yaml).
    Messages are added to the agent's context only if:
    1. Any of the message's tags match the agent's listen_tags, OR
    2. The message's 'to' field explicitly names the agent.

    FRS Reference: Section 3.4
    """

    def __init__(self, agent_id: str, listen_tags: list[str]):
        """
        Args:
            agent_id: This agent's short name.
            listen_tags: Tags this agent is interested in.
        """
        self.agent_id = agent_id
        self.listen_tags = set(listen_tags)

    def should_include(self, message: CompactMessage) -> bool:
        """Determine whether this message should enter the agent's context.

        Args:
            message: The incoming compact protocol message.

        Returns:
            True if the message is relevant to this agent.
        """
        # Always include messages addressed directly to this agent
        if message.to_agent == self.agent_id or message.to_agent == "all":
            return True

        # Check tag overlap
        message_tags = set(message.tags)
        return bool(self.listen_tags & message_tags)


class ChannelMessageStore:
    """In-memory message store for a single channel.

    Maintains an ordered list of messages for a channel, supports
    sequential msg_id generation, and provides context_ref resolution.

    This is used by agents to track recent messages on their active
    channels.
    """

    def __init__(self, channel: str):
        self.channel = channel
        self._messages: list[CompactMessage] = []
        self._next_id: int = 1

    @property
    def next_msg_id(self) -> int:
        """Get the next sequential message ID for this channel."""
        return self._next_id

    def add(self, message: CompactMessage) -> None:
        """Add a message to the store.

        Updates the next_id counter based on the message's msg_id.
        """
        self._messages.append(message)
        if message.msg_id >= self._next_id:
            self._next_id = message.msg_id + 1

    def get_recent(self, n: int = 20) -> list[CompactMessage]:
        """Get the N most recent messages.

        Args:
            n: Number of recent messages to return.

        Returns:
            List of messages, oldest first.
        """
        return self._messages[-n:]

    def get_by_id(self, msg_id: int) -> Optional[CompactMessage]:
        """Get a specific message by its msg_id.

        Args:
            msg_id: The message ID to look up.

        Returns:
            The message, or None if not found.
        """
        for msg in self._messages:
            if msg.msg_id == msg_id:
                return msg
        return None

    def resolve_context_ref(self, ref: str) -> list[CompactMessage]:
        """Resolve a context_ref string to actual messages.

        Supported ref formats:
        - "ch-auth-feature/msg-42" → single message #42
        - "ch-auth-feature/msg-42-46" → messages #42 through #46
        - "frs/REQ-011" → not resolvable here (handled by RAG tool)

        Args:
            ref: The context_ref string.

        Returns:
            List of resolved messages. Empty if ref doesn't match
            this channel or messages not found.
        """
        if "/" not in ref:
            return []

        parts = ref.split("/", 1)
        channel_part = parts[0]
        msg_part = parts[1]

        # Only resolve refs for this channel
        if channel_part != self.channel:
            return []

        if not msg_part.startswith("msg-"):
            return []

        id_part = msg_part[4:]  # strip "msg-"

        if "-" in id_part:
            # Range: msg-42-46
            try:
                start, end = id_part.split("-", 1)
                start_id, end_id = int(start), int(end)
            except ValueError:
                return []
            return [
                m for m in self._messages
                if start_id <= m.msg_id <= end_id
            ]
        else:
            # Single: msg-42
            try:
                target_id = int(id_part)
            except ValueError:
                return []
            msg = self.get_by_id(target_id)
            return [msg] if msg else []

    def get_all(self) -> list[CompactMessage]:
        """Get all messages in the store."""
        return list(self._messages)

    def count(self) -> int:
        """Get the total number of messages."""
        return len(self._messages)

    def clear(self) -> None:
        """Clear all messages (e.g. on channel close)."""
        self._messages.clear()
        self._next_id = 1


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
```

### 2. `faith/protocol/__init__.py`

```python
"""FAITH communication protocols — compact inter-agent and event system."""

from faith.protocol.compact import (
    CompactMessage,
    MessageType,
    MessageStatus,
    MessagePriority,
    MessageFilter,
    ChannelMessageStore,
)

__all__ = [
    "CompactMessage",
    "MessageType",
    "MessageStatus",
    "MessagePriority",
    "MessageFilter",
    "ChannelMessageStore",
]
```

### 3. `tests/test_compact_protocol.py`

```python
"""Tests for the compact inter-agent protocol."""

import json
import pytest
from faith.protocol.compact import (
    CompactMessage,
    MessageType,
    MessageStatus,
    MessagePriority,
    MessageFilter,
    ChannelMessageStore,
)


# --- CompactMessage tests ---

@pytest.fixture
def sample_message():
    return CompactMessage(
        **{
            "from": "dev",
            "to": "qa",
            "channel": "ch-auth-feature",
            "msg_id": 47,
            "type": MessageType.REVIEW_REQUEST,
            "tags": ["code", "auth", "testing"],
            "status": MessageStatus.COMPLETE,
            "summary": "auth module done, 3 endpoints, JWT httponly cookies",
            "needs": "test coverage for token expiry edge case",
            "context_ref": "ch-auth-feature/msg-42-46",
        }
    )


def test_create_message(sample_message):
    assert sample_message.from_agent == "dev"
    assert sample_message.to_agent == "qa"
    assert sample_message.type == MessageType.REVIEW_REQUEST
    assert sample_message.status == MessageStatus.COMPLETE
    assert len(sample_message.tags) == 3


def test_json_round_trip(sample_message):
    json_str = sample_message.to_json()
    parsed = json.loads(json_str)

    # Should use aliases in JSON output
    assert "from" in parsed
    assert "to" in parsed
    assert "from_agent" not in parsed
    assert "to_agent" not in parsed

    # None fields should be excluded
    assert "files" not in parsed

    # Round-trip
    restored = CompactMessage.from_json(json_str)
    assert restored.from_agent == "dev"
    assert restored.to_agent == "qa"
    assert restored.msg_id == 47
    assert restored.summary == sample_message.summary


def test_to_dict_uses_aliases(sample_message):
    d = sample_message.to_dict()
    assert "from" in d
    assert "to" in d


def test_log_format(sample_message):
    log = sample_message.to_log_format()
    assert "dev → qa" in log
    assert "review_request" in log
    assert "auth module done" in log
    assert "token expiry" in log


def test_compact_summary(sample_message):
    summary = sample_message.to_compact_summary()
    assert "dev→qa" in summary
    assert "review_request" in summary
    assert "auth module done" in summary


def test_disposable_flag():
    msg = CompactMessage(
        **{
            "from": "dev",
            "to": "qa",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.REVIEW_REQUEST,
            "tags": ["code"],
            "summary": "code review",
            "disposable": True,
        }
    )
    assert msg.disposable is True
    log = msg.to_log_format()
    assert "disposable: true" in log


def test_default_priority():
    msg = CompactMessage(
        **{
            "from": "pa",
            "to": "dev",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.TASK,
            "tags": ["code"],
            "summary": "implement feature",
        }
    )
    assert msg.priority == MessagePriority.NORMAL


def test_auto_timestamp():
    msg = CompactMessage(
        **{
            "from": "pa",
            "to": "dev",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.TASK,
            "tags": ["code"],
            "summary": "test",
        }
    )
    assert msg.ts is not None
    assert "T" in msg.ts  # ISO format


# --- MessageFilter tests ---

def test_filter_by_tag():
    f = MessageFilter("qa", listen_tags=["testing", "qa", "code"])
    msg = CompactMessage(
        **{
            "from": "dev",
            "to": "all",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.STATUS_UPDATE,
            "tags": ["code", "auth"],
            "summary": "done",
        }
    )
    assert f.should_include(msg) is True  # "code" matches


def test_filter_rejects_irrelevant():
    f = MessageFilter("qa", listen_tags=["testing", "qa"])
    msg = CompactMessage(
        **{
            "from": "dev",
            "to": "security",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.STATUS_UPDATE,
            "tags": ["architecture", "design"],
            "summary": "done",
        }
    )
    assert f.should_include(msg) is False


def test_filter_always_includes_direct():
    f = MessageFilter("qa", listen_tags=[])  # no tags at all
    msg = CompactMessage(
        **{
            "from": "dev",
            "to": "qa",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.QUESTION,
            "tags": ["unrelated"],
            "summary": "question for you",
        }
    )
    assert f.should_include(msg) is True  # direct address overrides tags


def test_filter_includes_broadcast():
    f = MessageFilter("qa", listen_tags=[])
    msg = CompactMessage(
        **{
            "from": "pa",
            "to": "all",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.STATUS_UPDATE,
            "tags": ["admin"],
            "summary": "broadcast",
        }
    )
    assert f.should_include(msg) is True


# --- ChannelMessageStore tests ---

def test_store_add_and_retrieve():
    store = ChannelMessageStore("ch-test")
    msg = CompactMessage(
        **{
            "from": "dev",
            "to": "qa",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.TASK,
            "tags": ["code"],
            "summary": "first",
        }
    )
    store.add(msg)
    assert store.count() == 1
    assert store.get_by_id(1) is not None
    assert store.next_msg_id == 2


def test_store_get_recent():
    store = ChannelMessageStore("ch-test")
    for i in range(1, 31):
        store.add(CompactMessage(
            **{
                "from": "dev",
                "to": "qa",
                "channel": "ch-test",
                "msg_id": i,
                "type": MessageType.STATUS_UPDATE,
                "tags": ["code"],
                "summary": f"msg {i}",
            }
        ))
    recent = store.get_recent(5)
    assert len(recent) == 5
    assert recent[0].msg_id == 26
    assert recent[-1].msg_id == 30


def test_resolve_single_ref():
    store = ChannelMessageStore("ch-auth")
    store.add(CompactMessage(
        **{
            "from": "dev",
            "to": "qa",
            "channel": "ch-auth",
            "msg_id": 42,
            "type": MessageType.TASK,
            "tags": ["code"],
            "summary": "target message",
        }
    ))
    result = store.resolve_context_ref("ch-auth/msg-42")
    assert len(result) == 1
    assert result[0].msg_id == 42


def test_resolve_range_ref():
    store = ChannelMessageStore("ch-auth")
    for i in range(40, 50):
        store.add(CompactMessage(
            **{
                "from": "dev",
                "to": "qa",
                "channel": "ch-auth",
                "msg_id": i,
                "type": MessageType.STATUS_UPDATE,
                "tags": ["code"],
                "summary": f"msg {i}",
            }
        ))
    result = store.resolve_context_ref("ch-auth/msg-42-46")
    assert len(result) == 5
    assert result[0].msg_id == 42
    assert result[-1].msg_id == 46


def test_resolve_wrong_channel():
    store = ChannelMessageStore("ch-auth")
    result = store.resolve_context_ref("ch-other/msg-1")
    assert len(result) == 0


def test_resolve_frs_ref_returns_empty():
    """FRS refs (frs/REQ-011) are not resolvable by the message store."""
    store = ChannelMessageStore("ch-auth")
    result = store.resolve_context_ref("frs/REQ-011")
    assert len(result) == 0


def test_store_clear():
    store = ChannelMessageStore("ch-test")
    store.add(CompactMessage(
        **{
            "from": "dev",
            "to": "qa",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.TASK,
            "tags": ["code"],
            "summary": "test",
        }
    ))
    store.clear()
    assert store.count() == 0
    assert store.next_msg_id == 1
```

---

## Redis Transport Integration

Messages are published to Redis channels as JSON strings:

```python
# Publishing a message to a channel
message = CompactMessage(
    from_agent="dev",
    to_agent="qa",
    channel="ch-auth-feature",
    msg_id=47,
    type=MessageType.REVIEW_REQUEST,
    tags=["code", "auth"],
    summary="auth module done",
)

await redis_client.publish("ch-auth-feature", message.to_json())
```

```python
# Receiving a message from a channel subscription
async for raw_message in pubsub.listen():
    if raw_message["type"] == "message":
        msg = CompactMessage.from_json(raw_message["data"])
        if message_filter.should_include(msg):
            channel_store.add(msg)
            # Process message...
```

This integration happens in the base agent runtime (FAITH-010), not in this module. This module provides the data models and utilities only.

---

## Acceptance Criteria

1. `CompactMessage` can be created with all FRS-defined fields and serialises to JSON with `from`/`to` aliases (not `from_agent`/`to_agent`).
2. JSON round-trip (serialise → deserialise) preserves all fields exactly.
3. `None` fields are excluded from JSON output for compactness.
4. `to_log_format()` produces human-readable markdown matching FRS Section 8.4 format.
5. `to_compact_summary()` produces a single-line summary suitable for context compaction retention.
6. `MessageFilter.should_include()` correctly filters by tag overlap and always includes direct-addressed and broadcast messages.
7. `ChannelMessageStore` maintains ordered messages, supports sequential ID generation, and resolves single and range `context_ref` strings.
8. FRS-style refs (`frs/REQ-011`) are gracefully returned as empty (handled by RAG tool, not message store).
9. All 22 tests pass.

---

## Notes for Implementer

- Pydantic v2 `model_config` with `populate_by_name = True` is required because `from` is a Python reserved word. The model uses `from_agent` internally but `from` in JSON/dict serialisation via the `alias` parameter.
- The `ChannelMessageStore` is an in-memory store per agent per channel. It is NOT persisted to Redis — Redis pub/sub is fire-and-forget. Persistence happens via the session log writer (FAITH-046).
- The `context_ref` resolution for `frs/REQ-XXX` style references is handled by the RAG tool (FAITH-028), not by this module. This module only resolves channel message references.
- `disposable: true` messages are handled during context compaction (FAITH-011). This module only carries the flag — it does not perform compaction.

