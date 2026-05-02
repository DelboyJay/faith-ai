# FAITH-008 — Event System Data Models & Publisher

**Phase:** 2 — Compact Protocol & Events
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-002
**FRS Reference:** Section 3.7.1–3.7.4

---

## Objective

Define the Python data models for the FAITH event system and implement the `EventPublisher` class that publishes structured JSON events to the `system-events` Redis channel. Every component in FAITH (agents, tools, PA, filesystem watcher) uses this publisher to communicate state changes.

---

## Architecture

```
src/faith_shared/protocol/
├── compact.py    ← (FAITH-007)
└── events.py     ← FaithEvent model, EventPublisher, typed helpers (this task)
```

---

## Files to Create

### 1. `src/faith_shared/protocol/events.py`

```python
"""FAITH Event System — structured state-change events.

All FAITH components publish events to the 'system-events' Redis channel.
The PA subscribes to this channel and reacts when intervention is needed.
Events are lightweight JSON — they carry state and references, not content.

FRS Reference: Section 3.7
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import redis.asyncio as aioredis
from pydantic import BaseModel, Field

logger = logging.getLogger("faith.events")

SYSTEM_EVENTS_CHANNEL = "system-events"


class EventType(str, Enum):
    """All FAITH event types from the FRS event catalogue."""

    # Agent state events
    AGENT_TASK_COMPLETE = "agent:task_complete"
    AGENT_TASK_BLOCKED = "agent:task_blocked"
    AGENT_NEEDS_INPUT = "agent:needs_input"
    AGENT_ERROR = "agent:error"
    AGENT_HEARTBEAT = "agent:heartbeat"
    AGENT_MODEL_ESCALATION = "agent:model_escalation_requested"
    AGENT_CONTEXT_SUMMARY = "agent:context_summary_triggered"

    # Channel events
    CHANNEL_STALLED = "channel:stalled"
    CHANNEL_GOAL_ACHIEVED = "channel:goal_achieved"
    CHANNEL_LOOP_DETECTED = "channel:loop_detected"

    # Tool events
    TOOL_CALL_STARTED = "tool:call_started"
    TOOL_CALL_COMPLETE = "tool:call_complete"
    TOOL_PERMISSION_DENIED = "tool:permission_denied"
    TOOL_ERROR = "tool:error"

    # File events
    FILE_CHANGED = "file:changed"
    FILE_CREATED = "file:created"
    FILE_DELETED = "file:deleted"

    # Approval events
    APPROVAL_REQUESTED = "approval:requested"
    APPROVAL_DECISION = "approval:decision"

    # Resource events
    RESOURCE_TOKEN_THRESHOLD = "resource:token_threshold"
    RESOURCE_TOKEN_CRITICAL = "resource:token_critical"

    # System events
    SYSTEM_CONFIG_CHANGED = "system:config_changed"
    SYSTEM_CONFIG_ERROR = "system:config_error"
    SYSTEM_CONTAINER_STARTED = "system:container_started"
    SYSTEM_CONTAINER_STOPPED = "system:container_stopped"
    SYSTEM_CONTAINER_ERROR = "system:container_error"


class FaithEvent(BaseModel):
    """A single FAITH event.

    Events are lightweight state-change notifications published to
    the system-events Redis channel. They carry minimal data — just
    enough for the PA to decide whether intervention is needed.

    Attributes:
        event: The event type (from EventType enum).
        source: Agent ID, tool ID, or "system".
        channel: Associated channel (if applicable).
        ts: ISO 8601 timestamp.
        data: Event-specific payload (varies by event type).
    """

    event: EventType
    source: str
    channel: Optional[str] = None
    ts: str = Field(default_factory=lambda: _now_iso())
    data: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        """Serialise to JSON string for Redis transport."""
        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_json(cls, json_str: str) -> "FaithEvent":
        """Deserialise from JSON string received via Redis."""
        return cls.model_validate_json(json_str)

    def to_dict(self) -> dict:
        """Convert to dict, excluding None values."""
        return self.model_dump(exclude_none=True)


class EventPublisher:
    """Publishes FAITH events to the system-events Redis channel.

    Every FAITH component instantiates one of these. Typed helper
    methods ensure events are well-formed without manual dict construction.

    Attributes:
        redis_client: Async Redis client.
        source: Default source ID for events from this publisher
            (e.g. agent ID, tool name, "pa", "system").
    """

    def __init__(self, redis_client: aioredis.Redis, source: str):
        self.redis_client = redis_client
        self.source = source

    async def publish(self, event: FaithEvent) -> None:
        """Publish an event to the system-events channel.

        This is fire-and-forget — publishing errors are logged but
        do not raise exceptions to the caller.

        Args:
            event: The event to publish.
        """
        try:
            await self.redis_client.publish(
                SYSTEM_EVENTS_CHANNEL, event.to_json()
            )
        except Exception:
            logger.exception(f"Failed to publish event: {event.event}")

    # --- Agent state event helpers ---

    async def agent_task_complete(
        self,
        channel: str,
        task: str,
        msg_id: Optional[int] = None,
        files_written: int = 0,
    ) -> None:
        """Agent has completed its current task."""
        await self.publish(FaithEvent(
            event=EventType.AGENT_TASK_COMPLETE,
            source=self.source,
            channel=channel,
            data={
                "task": task,
                **({"msg_id": msg_id} if msg_id else {}),
                "files_written": files_written,
            },
        ))

    async def agent_task_blocked(
        self,
        channel: str,
        reason: str,
        waiting_for: Optional[str] = None,
    ) -> None:
        """Agent cannot proceed — waiting on something."""
        await self.publish(FaithEvent(
            event=EventType.AGENT_TASK_BLOCKED,
            source=self.source,
            channel=channel,
            data={
                "reason": reason,
                **({"waiting_for": waiting_for} if waiting_for else {}),
            },
        ))

    async def agent_needs_input(
        self,
        channel: str,
        question: str,
    ) -> None:
        """Agent needs specific information before continuing."""
        await self.publish(FaithEvent(
            event=EventType.AGENT_NEEDS_INPUT,
            source=self.source,
            channel=channel,
            data={"question": question},
        ))

    async def agent_error(
        self,
        error: str,
        channel: Optional[str] = None,
        recoverable: bool = False,
    ) -> None:
        """Agent has encountered an error."""
        await self.publish(FaithEvent(
            event=EventType.AGENT_ERROR,
            source=self.source,
            channel=channel,
            data={"error": error, "recoverable": recoverable},
        ))

    async def agent_heartbeat(self) -> None:
        """Agent liveness ping — published every N seconds."""
        await self.publish(FaithEvent(
            event=EventType.AGENT_HEARTBEAT,
            source=self.source,
            data={},
        ))

    async def agent_model_escalation(
        self,
        channel: str,
        reason: str,
        current_model: str,
    ) -> None:
        """Agent signals its current model is insufficient."""
        await self.publish(FaithEvent(
            event=EventType.AGENT_MODEL_ESCALATION,
            source=self.source,
            channel=channel,
            data={"reason": reason, "current_model": current_model},
        ))

    async def agent_context_summary(self) -> None:
        """Agent's rolling context summary has fired."""
        await self.publish(FaithEvent(
            event=EventType.AGENT_CONTEXT_SUMMARY,
            source=self.source,
            data={},
        ))

    # --- Channel event helpers ---

    async def channel_stalled(
        self,
        channel: str,
        idle_seconds: int,
    ) -> None:
        """No activity on a channel for the configured timeout."""
        await self.publish(FaithEvent(
            event=EventType.CHANNEL_STALLED,
            source=self.source,
            channel=channel,
            data={"idle_seconds": idle_seconds},
        ))

    async def channel_goal_achieved(self, channel: str) -> None:
        """Session objective on this channel is complete."""
        await self.publish(FaithEvent(
            event=EventType.CHANNEL_GOAL_ACHIEVED,
            source=self.source,
            channel=channel,
            data={},
        ))

    async def channel_loop_detected(
        self,
        channel: str,
        description: str,
        agents_involved: list[str],
    ) -> None:
        """Circular behaviour pattern detected on a channel."""
        await self.publish(FaithEvent(
            event=EventType.CHANNEL_LOOP_DETECTED,
            source=self.source,
            channel=channel,
            data={
                "description": description,
                "agents_involved": agents_involved,
            },
        ))

    # --- Tool event helpers ---

    async def tool_call_started(
        self,
        tool: str,
        action: str,
        agent: str,
        channel: Optional[str] = None,
    ) -> None:
        """A tool invocation has begun."""
        await self.publish(FaithEvent(
            event=EventType.TOOL_CALL_STARTED,
            source=tool,
            channel=channel,
            data={"action": action, "agent": agent},
        ))

    async def tool_call_complete(
        self,
        tool: str,
        action: str,
        agent: str,
        success: bool = True,
        channel: Optional[str] = None,
    ) -> None:
        """A tool call has finished."""
        await self.publish(FaithEvent(
            event=EventType.TOOL_CALL_COMPLETE,
            source=tool,
            channel=channel,
            data={"action": action, "agent": agent, "success": success},
        ))

    async def tool_permission_denied(
        self,
        tool: str,
        action: str,
        agent: str,
        reason: str,
    ) -> None:
        """A tool action was blocked by approval rules."""
        await self.publish(FaithEvent(
            event=EventType.TOOL_PERMISSION_DENIED,
            source=tool,
            data={"action": action, "agent": agent, "reason": reason},
        ))

    async def tool_error(
        self,
        tool: str,
        error: str,
        agent: Optional[str] = None,
        raw_content_available: bool = False,
    ) -> None:
        """A tool call failed."""
        await self.publish(FaithEvent(
            event=EventType.TOOL_ERROR,
            source=tool,
            data={
                "error": error,
                **({"agent": agent} if agent else {}),
                "raw_content_available": raw_content_available,
            },
        ))

    # --- File event helpers ---

    async def file_changed(
        self,
        path: str,
        sha256_before: str,
        sha256_after: str,
        agent: Optional[str] = None,
    ) -> None:
        """A watched file has been modified."""
        await self.publish(FaithEvent(
            event=EventType.FILE_CHANGED,
            source=self.source,
            data={
                "path": path,
                "sha256_before": sha256_before,
                "sha256_after": sha256_after,
                **({"agent": agent} if agent else {}),
            },
        ))

    async def file_created(self, path: str) -> None:
        """A new file was created in a watched path."""
        await self.publish(FaithEvent(
            event=EventType.FILE_CREATED,
            source=self.source,
            data={"path": path},
        ))

    async def file_deleted(self, path: str) -> None:
        """A file was deleted from a watched path."""
        await self.publish(FaithEvent(
            event=EventType.FILE_DELETED,
            source=self.source,
            data={"path": path},
        ))

    # --- Approval event helpers ---

    async def approval_requested(
        self,
        request_id: str,
        agent: str,
        action: str,
        detail: str,
        channel: Optional[str] = None,
    ) -> None:
        """An action requires user approval."""
        await self.publish(FaithEvent(
            event=EventType.APPROVAL_REQUESTED,
            source=self.source,
            channel=channel,
            data={
                "request_id": request_id,
                "agent": agent,
                "action": action,
                "detail": detail,
            },
        ))

    async def approval_decision(
        self,
        request_id: str,
        decision: str,
        agent: str,
    ) -> None:
        """User has responded to an approval request."""
        await self.publish(FaithEvent(
            event=EventType.APPROVAL_DECISION,
            source=self.source,
            data={
                "request_id": request_id,
                "decision": decision,
                "agent": agent,
            },
        ))

    # --- Resource event helpers ---

    async def resource_token_threshold(
        self,
        pct_used: float,
        tokens_used: int,
        model: str,
    ) -> None:
        """Token usage has crossed a warning level."""
        await self.publish(FaithEvent(
            event=EventType.RESOURCE_TOKEN_THRESHOLD,
            source=self.source,
            data={
                "pct_used": pct_used,
                "tokens_used": tokens_used,
                "model": model,
            },
        ))

    async def resource_token_critical(
        self,
        pct_used: float,
        tokens_used: int,
        model: str,
    ) -> None:
        """Token usage approaching model context limit."""
        await self.publish(FaithEvent(
            event=EventType.RESOURCE_TOKEN_CRITICAL,
            source=self.source,
            data={
                "pct_used": pct_used,
                "tokens_used": tokens_used,
                "model": model,
            },
        ))

    # --- System event helpers ---

    async def system_config_changed(
        self,
        file: str,
        path: str,
    ) -> None:
        """A config file change has been detected and validated."""
        await self.publish(FaithEvent(
            event=EventType.SYSTEM_CONFIG_CHANGED,
            source=self.source,
            data={"file": file, "path": path},
        ))

    async def system_container_started(
        self,
        container_name: str,
        container_type: str,
    ) -> None:
        """A managed container has started."""
        await self.publish(FaithEvent(
            event=EventType.SYSTEM_CONTAINER_STARTED,
            source=self.source,
            data={
                "container_name": container_name,
                "container_type": container_type,
            },
        ))

    async def system_container_stopped(
        self,
        container_name: str,
        reason: str = "normal",
    ) -> None:
        """A managed container has stopped."""
        await self.publish(FaithEvent(
            event=EventType.SYSTEM_CONTAINER_STOPPED,
            source=self.source,
            data={"container_name": container_name, "reason": reason},
        ))

    async def system_container_error(
        self,
        container_name: str,
        error: str,
    ) -> None:
        """A managed container has crashed or failed to start."""
        await self.publish(FaithEvent(
            event=EventType.SYSTEM_CONTAINER_ERROR,
            source=self.source,
            data={"container_name": container_name, "error": error},
        ))


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
```

### 2. Update `src/faith_shared/protocol/__init__.py`

Add event exports:

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
from faith.protocol.events import (
    FaithEvent,
    EventType,
    EventPublisher,
    SYSTEM_EVENTS_CHANNEL,
)

__all__ = [
    "CompactMessage",
    "MessageType",
    "MessageStatus",
    "MessagePriority",
    "MessageFilter",
    "ChannelMessageStore",
    "FaithEvent",
    "EventType",
    "EventPublisher",
    "SYSTEM_EVENTS_CHANNEL",
]
```

### 3. `tests/test_events.py`

```python
"""Tests for the FAITH event system."""

import json
import pytest
from faith.protocol.events import (
    FaithEvent,
    EventType,
    EventPublisher,
    SYSTEM_EVENTS_CHANNEL,
)


class FakeRedis:
    """Minimal fake async Redis for testing."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def publisher(fake_redis):
    return EventPublisher(fake_redis, source="test-agent")


# --- FaithEvent model tests ---

def test_event_create():
    event = FaithEvent(
        event=EventType.AGENT_TASK_COMPLETE,
        source="dev",
        channel="ch-auth",
        data={"task": "implement auth", "msg_id": 47},
    )
    assert event.event == EventType.AGENT_TASK_COMPLETE
    assert event.source == "dev"
    assert event.ts is not None


def test_event_json_round_trip():
    event = FaithEvent(
        event=EventType.FILE_CHANGED,
        source="filesystem",
        data={"path": "workspace/src/auth.py"},
    )
    json_str = event.to_json()
    restored = FaithEvent.from_json(json_str)
    assert restored.event == EventType.FILE_CHANGED
    assert restored.data["path"] == "workspace/src/auth.py"


def test_event_excludes_none_channel():
    event = FaithEvent(
        event=EventType.AGENT_HEARTBEAT,
        source="dev",
        data={},
    )
    d = event.to_dict()
    assert "channel" not in d


# --- EventPublisher tests ---

@pytest.mark.asyncio
async def test_publish_sends_to_system_events(publisher, fake_redis):
    await publisher.agent_heartbeat()
    assert len(fake_redis.published) == 1
    channel, msg = fake_redis.published[0]
    assert channel == SYSTEM_EVENTS_CHANNEL
    event = json.loads(msg)
    assert event["event"] == "agent:heartbeat"
    assert event["source"] == "test-agent"


@pytest.mark.asyncio
async def test_agent_task_complete(publisher, fake_redis):
    await publisher.agent_task_complete(
        channel="ch-auth",
        task="implement JWT",
        msg_id=47,
        files_written=3,
    )
    event = json.loads(fake_redis.published[0][1])
    assert event["event"] == "agent:task_complete"
    assert event["channel"] == "ch-auth"
    assert event["data"]["task"] == "implement JWT"
    assert event["data"]["files_written"] == 3


@pytest.mark.asyncio
async def test_agent_task_blocked(publisher, fake_redis):
    await publisher.agent_task_blocked(
        channel="ch-auth",
        reason="Waiting for security review",
        waiting_for="security-expert",
    )
    event = json.loads(fake_redis.published[0][1])
    assert event["data"]["reason"] == "Waiting for security review"
    assert event["data"]["waiting_for"] == "security-expert"


@pytest.mark.asyncio
async def test_agent_error(publisher, fake_redis):
    await publisher.agent_error(
        error="LLM API timeout after 3 retries",
        channel="ch-auth",
        recoverable=False,
    )
    event = json.loads(fake_redis.published[0][1])
    assert event["event"] == "agent:error"
    assert event["data"]["recoverable"] is False


@pytest.mark.asyncio
async def test_tool_call_lifecycle(publisher, fake_redis):
    await publisher.tool_call_started(
        tool="filesystem",
        action="write",
        agent="dev",
        channel="ch-auth",
    )
    await publisher.tool_call_complete(
        tool="filesystem",
        action="write",
        agent="dev",
        success=True,
        channel="ch-auth",
    )
    assert len(fake_redis.published) == 2
    started = json.loads(fake_redis.published[0][1])
    complete = json.loads(fake_redis.published[1][1])
    assert started["event"] == "tool:call_started"
    assert complete["event"] == "tool:call_complete"
    assert complete["data"]["success"] is True


@pytest.mark.asyncio
async def test_file_changed(publisher, fake_redis):
    await publisher.file_changed(
        path="workspace/src/auth.py",
        sha256_before="aaa",
        sha256_after="bbb",
        agent="dev",
    )
    event = json.loads(fake_redis.published[0][1])
    assert event["event"] == "file:changed"
    assert event["data"]["sha256_before"] == "aaa"
    assert event["data"]["agent"] == "dev"


@pytest.mark.asyncio
async def test_approval_requested(publisher, fake_redis):
    await publisher.approval_requested(
        request_id="apr-042",
        agent="dev",
        action="run_command",
        detail="pytest tests/auth/",
        channel="ch-auth",
    )
    event = json.loads(fake_redis.published[0][1])
    assert event["event"] == "approval:requested"
    assert event["data"]["request_id"] == "apr-042"


@pytest.mark.asyncio
async def test_channel_loop_detected(publisher, fake_redis):
    await publisher.channel_loop_detected(
        channel="ch-auth",
        description="auth.py oscillating between two versions",
        agents_involved=["dev", "qa"],
    )
    event = json.loads(fake_redis.published[0][1])
    assert event["event"] == "channel:loop_detected"
    assert "dev" in event["data"]["agents_involved"]


@pytest.mark.asyncio
async def test_publish_error_does_not_raise(fake_redis):
    """Publishing errors should be logged, not raised."""

    class BrokenRedis:
        async def publish(self, channel, message):
            raise ConnectionError("Redis down")

    publisher = EventPublisher(BrokenRedis(), source="test")
    # Should not raise
    await publisher.agent_heartbeat()


@pytest.mark.asyncio
async def test_system_container_events(publisher, fake_redis):
    await publisher.system_container_started("faith-dev", "agent")
    await publisher.system_container_stopped("faith-dev", "normal")
    await publisher.system_container_error("faith-dev", "OOM killed")

    events = [json.loads(msg) for _, msg in fake_redis.published]
    assert events[0]["event"] == "system:container_started"
    assert events[1]["event"] == "system:container_stopped"
    assert events[2]["event"] == "system:container_error"
    assert events[2]["data"]["error"] == "OOM killed"
```

---

## Acceptance Criteria

1. `FaithEvent` model covers all 26 event types from the FRS event catalogue.
2. JSON round-trip preserves all fields.
3. `EventPublisher` publishes to the `system-events` channel only.
4. All 26 typed helper methods on `EventPublisher` produce correctly structured events.
5. Publishing errors are caught and logged — they never propagate to the caller.
6. `None` optional fields are excluded from JSON output.
7. All 13 tests pass.

---

## Notes for Implementer

- The `EventPublisher.publish()` method is fire-and-forget. Events are observability, not control flow — a failed publish should never block agent work. The `try/except` in `publish()` is intentional.
- The `source` on tool events is set to the tool name (not the publisher's default source) because tool events come from the tool process, not the agent that triggered them. The helper methods accept an explicit `tool` parameter for this.
- Helper methods use `**` dict unpacking with conditionals to omit optional fields — this keeps the event payloads minimal.
- The `EventType` enum values match the FRS exactly (e.g. `"agent:task_complete"`, `"file:changed"`). Do not change the string values.

