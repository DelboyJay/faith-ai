# FAITH-009 — Event Subscriber & Dispatcher

**Phase:** 2 — Compact Protocol & Events
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-008
**FRS Reference:** Section 3.7.5, 3.7.6, 3.7.8

---

## Objective

Implement the `EventSubscriber` class that listens on the `system-events` Redis channel and dispatches events to registered handlers by event type. This is the PA's primary input loop — it reacts to events rather than polling agent channels. Also implement stall detection: per-channel inactivity tracking and heartbeat absence detection, both of which publish events when thresholds are exceeded. Additionally, implement the `CompletionBatcher` class that accumulates `agent:task_complete` events when an orchestrator is waiting on multiple concurrent sub-tasks, only firing a batch-ready callback once all expected completions arrive or a configurable timeout expires. Urgent events always bypass batching.

---

## Architecture

```
src/faith_shared/protocol/
├── compact.py    ← (FAITH-007)
├── events.py     ← FaithEvent, EventPublisher, EventType (FAITH-008)
├── subscriber.py ← EventSubscriber, StallDetector, CompletionBatcher (this task)
└── __init__.py   ← updated exports
```

---

## Files to Create

### 1. `src/faith_shared/protocol/subscriber.py`

```python
"""FAITH Event Subscriber & Dispatcher.

Listens on the 'system-events' Redis channel and dispatches events
to registered handler functions by event type. Includes stall detection
for channel inactivity and heartbeat absence.

FRS Reference: Section 3.7.5, 3.7.6
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Optional

import redis.asyncio as aioredis

from faith.protocol.events import EventPublisher, EventType, FaithEvent
from faith_shared.protocol.events import SYSTEM_EVENTS_CHANNEL

logger = logging.getLogger("faith.protocol.subscriber")

# Type alias for event handler functions
EventHandler = Callable[[FaithEvent], Awaitable[None]]


class EventSubscriber:
    """Subscribes to system-events and dispatches to registered handlers.

    This is the PA's primary event loop. It maintains a registry of
    handler functions keyed by EventType. When an event arrives, all
    handlers registered for that event type are called concurrently.

    Attributes:
        redis: Async Redis client for pub/sub.
        stall_detector: Optional StallDetector for channel monitoring.
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        stall_detector: Optional[StallDetector] = None,
    ):
        self.redis = redis
        self.stall_detector = stall_detector
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._wildcard_handlers: list[EventHandler] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def on(self, event_type: EventType, handler: EventHandler) -> None:
        """Register a handler for a specific event type.

        Args:
            event_type: The event type to listen for.
            handler: Async function called with the FaithEvent.
        """
        self._handlers[event_type].append(handler)
        logger.debug(f"Registered handler for {event_type.value}: {handler.__name__}")

    def on_all(self, handler: EventHandler) -> None:
        """Register a handler that receives ALL events.

        Use sparingly — primarily for logging or audit purposes.

        Args:
            handler: Async function called with every FaithEvent.
        """
        self._wildcard_handlers.append(handler)
        logger.debug(f"Registered wildcard handler: {handler.__name__}")

    def remove(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove a previously registered handler.

        Args:
            event_type: The event type the handler was registered for.
            handler: The handler function to remove.
        """
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)
            logger.debug(f"Removed handler for {event_type.value}: {handler.__name__}")

    async def start(self) -> None:
        """Start listening for events on the system-events channel.

        This spawns a background asyncio task that subscribes to Redis
        pub/sub and dispatches events as they arrive. Call stop() to
        terminate.
        """
        if self._running:
            logger.warning("EventSubscriber is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._listen(), name="event-subscriber")
        logger.info("EventSubscriber started")

        # Start stall detector if configured
        if self.stall_detector:
            await self.stall_detector.start()

    async def stop(self) -> None:
        """Stop the event subscriber and stall detector."""
        self._running = False

        if self.stall_detector:
            await self.stall_detector.stop()

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("EventSubscriber stopped")

    async def _listen(self) -> None:
        """Internal loop: subscribe to Redis and dispatch events."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(SYSTEM_EVENTS_CHANNEL)
        logger.info(f"Subscribed to {SYSTEM_EVENTS_CHANNEL}")

        try:
            while self._running:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is None:
                    continue

                if message["type"] != "message":
                    continue

                try:
                    raw = message["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")

                    event = FaithEvent.from_json(raw)
                except Exception as e:
                    logger.warning(f"Failed to parse event: {e}")
                    continue

                # Feed event to stall detector (updates timestamps)
                if self.stall_detector:
                    self.stall_detector.record_event(event)

                # Dispatch to handlers
                await self._dispatch(event)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Event listener error: {e}")
        finally:
            await pubsub.unsubscribe(SYSTEM_EVENTS_CHANNEL)
            await pubsub.close()

    async def _dispatch(self, event: FaithEvent) -> None:
        """Dispatch an event to all matching handlers.

        Handlers for the specific event type and wildcard handlers are
        called concurrently via asyncio.gather. Exceptions in individual
        handlers are logged but do not affect other handlers.
        """
        handlers = list(self._handlers.get(event.event, []))
        handlers.extend(self._wildcard_handlers)

        if not handlers:
            return

        async def _safe_call(handler: EventHandler) -> None:
            try:
                await handler(event)
            except Exception as e:
                logger.error(
                    f"Handler {handler.__name__} failed for "
                    f"{event.event.value}: {e}"
                )

        await asyncio.gather(*[_safe_call(h) for h in handlers])


class StallDetector:
    """Detects channel inactivity and heartbeat absence.

    Maintains per-channel timestamps of last activity and per-agent
    heartbeat tracking. A background tick (every 60 seconds) checks
    all active channels and agents.

    FRS Reference: Section 3.7.6

    Stall detection combines two signals:
    1. Heartbeat absence: if an agent misses 3 consecutive heartbeats
       (configurable), an agent:error event is published.
    2. Channel inactivity: if no message activity on an active channel
       for the configured timeout (default 5 min), a channel:stalled
       event is published. Tool activity resets the stall timer.

    Attributes:
        publisher: EventPublisher for publishing stall/error events.
        stall_timeout: Seconds of inactivity before a channel is stalled.
        heartbeat_interval: Expected seconds between heartbeats.
        missed_heartbeat_limit: Number of missed beats before error.
        tick_interval: Seconds between background checks.
    """

    def __init__(
        self,
        publisher: EventPublisher,
        stall_timeout: float = 300.0,
        heartbeat_interval: float = 30.0,
        missed_heartbeat_limit: int = 3,
        tick_interval: float = 60.0,
    ):
        self.publisher = publisher
        self.stall_timeout = stall_timeout
        self.heartbeat_interval = heartbeat_interval
        self.missed_heartbeat_limit = missed_heartbeat_limit
        self.tick_interval = tick_interval

        # Per-channel: last activity timestamp (monotonic)
        self._channel_activity: dict[str, float] = {}

        # Per-agent: last heartbeat timestamp (monotonic)
        self._agent_heartbeats: dict[str, float] = {}

        # Channels that have already been flagged as stalled (avoid spam)
        self._stalled_channels: set[str] = set()

        # Agents that have already been flagged as errored (avoid spam)
        self._errored_agents: set[str] = set()

        self._running = False
        self._task: Optional[asyncio.Task] = None

    def register_channel(self, channel: str) -> None:
        """Register a channel for stall monitoring.

        Call this when a channel becomes active (e.g. agents assigned).

        Args:
            channel: The channel name (e.g. "ch-auth-feature").
        """
        self._channel_activity[channel] = time.monotonic()
        self._stalled_channels.discard(channel)
        logger.debug(f"Stall detector: tracking channel {channel}")

    def unregister_channel(self, channel: str) -> None:
        """Stop monitoring a channel (e.g. task complete, channel closed).

        Args:
            channel: The channel name to stop tracking.
        """
        self._channel_activity.pop(channel, None)
        self._stalled_channels.discard(channel)
        logger.debug(f"Stall detector: untracked channel {channel}")

    def register_agent(self, agent_name: str) -> None:
        """Register an agent for heartbeat monitoring.

        Args:
            agent_name: The agent's name (e.g. "software-developer").
        """
        self._agent_heartbeats[agent_name] = time.monotonic()
        self._errored_agents.discard(agent_name)
        logger.debug(f"Stall detector: tracking agent {agent_name}")

    def unregister_agent(self, agent_name: str) -> None:
        """Stop monitoring an agent (e.g. container stopped).

        Args:
            agent_name: The agent name to stop tracking.
        """
        self._agent_heartbeats.pop(agent_name, None)
        self._errored_agents.discard(agent_name)
        logger.debug(f"Stall detector: untracked agent {agent_name}")

    def record_event(self, event: FaithEvent) -> None:
        """Update tracking timestamps based on an incoming event.

        Called by EventSubscriber for every event received.

        - Any event with a channel field resets that channel's stall timer.
        - agent:heartbeat events reset the agent's heartbeat timer.
        - tool:call_started / tool:call_complete reset the associated
          channel's stall timer (an agent using a tool is not stalled).
        """
        now = time.monotonic()

        # Reset channel activity on any event with a channel
        if event.channel and event.channel in self._channel_activity:
            self._channel_activity[event.channel] = now
            # Clear stalled flag if activity resumes
            self._stalled_channels.discard(event.channel)

        # Heartbeat tracking
        if event.event == EventType.AGENT_HEARTBEAT:
            agent = event.source
            if agent in self._agent_heartbeats:
                self._agent_heartbeats[agent] = now
                self._errored_agents.discard(agent)

        # Tool activity resets the associated channel
        if event.event in (
            EventType.TOOL_CALL_STARTED,
            EventType.TOOL_CALL_COMPLETE,
        ):
            channel = event.channel or event.data.get("channel")
            if channel and channel in self._channel_activity:
                self._channel_activity[channel] = now
                self._stalled_channels.discard(channel)

    async def start(self) -> None:
        """Start the background tick loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop(), name="stall-detector")
        logger.info("StallDetector started")

    async def stop(self) -> None:
        """Stop the background tick loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("StallDetector stopped")

    async def _tick_loop(self) -> None:
        """Background loop that checks for stalls every tick_interval."""
        try:
            while self._running:
                await asyncio.sleep(self.tick_interval)
                if not self._running:
                    break
                await self._check_channels()
                await self._check_heartbeats()
        except asyncio.CancelledError:
            pass

    async def _check_channels(self) -> None:
        """Check all registered channels for inactivity."""
        now = time.monotonic()

        for channel, last_active in list(self._channel_activity.items()):
            elapsed = now - last_active
            if elapsed >= self.stall_timeout and channel not in self._stalled_channels:
                self._stalled_channels.add(channel)
                logger.warning(
                    f"Channel {channel} stalled: no activity for "
                    f"{elapsed:.0f}s (threshold: {self.stall_timeout:.0f}s)"
                )
                await self.publisher.channel_stalled(
                    source="pa",
                    channel=channel,
                    data={
                        "elapsed_seconds": round(elapsed),
                        "threshold_seconds": round(self.stall_timeout),
                    },
                )

    async def _check_heartbeats(self) -> None:
        """Check all registered agents for missed heartbeats."""
        now = time.monotonic()
        max_absence = self.heartbeat_interval * self.missed_heartbeat_limit

        for agent, last_beat in list(self._agent_heartbeats.items()):
            elapsed = now - last_beat
            if elapsed >= max_absence and agent not in self._errored_agents:
                self._errored_agents.add(agent)
                missed_count = int(elapsed // self.heartbeat_interval)
                logger.warning(
                    f"Agent {agent}: {missed_count} missed heartbeats "
                    f"({elapsed:.0f}s since last)"
                )
                await self.publisher.agent_error(
                    source=agent,
                    channel=None,
                    data={
                        "reason": "heartbeat_absence",
                        "missed_heartbeats": missed_count,
                        "elapsed_seconds": round(elapsed),
                        "threshold_seconds": round(max_absence),
                    },
                )
```

### 2. `CompletionBatcher` class (add to `src/faith_shared/protocol/subscriber.py`)

```python
class CompletionBatcher:
    """Accumulates task-completion events and fires a batch callback only when
    all expected completions have arrived (or a timeout expires).

    This avoids wasting LLM output tokens on intermediate completion events
    when an orchestrator is waiting on N concurrent sub-tasks.

    FRS Reference: Section 3.7.8 — Event Batching (Completion Accumulation)
    """

    def __init__(
        self,
        timeout_seconds: float = 600.0,  # 10 minutes default
        immediate_events: set[str] | None = None,
    ):
        self._timeout_seconds = timeout_seconds
        self._immediate_events: set[str] = immediate_events or set()
        self._pending: dict[str, set[str]] = {}      # batch_id -> set of pending task_ids
        self._results: dict[str, list[FaithEvent]] = {}  # batch_id -> buffered results
        self._batch_callback: EventHandler | None = None
        self._timeout_callback: EventHandler | None = None
        self._timeout_tasks: dict[str, asyncio.Task] = {}

    def on_batch_ready(self, callback: EventHandler) -> None:
        """Register callback invoked when ALL pending completions arrive."""
        self._batch_callback = callback

    def on_batch_timeout(self, callback: EventHandler) -> None:
        """Register callback invoked when timeout expires with tasks still pending."""
        self._timeout_callback = callback

    def expect(self, batch_id: str, task_ids: set[str]) -> None:
        """Declare that we expect completions for the given task IDs.

        Args:
            batch_id: Unique identifier for this batch (e.g., channel or session ID).
            task_ids: Set of task/agent IDs we expect `agent:task_complete` events from.
        """
        self._pending[batch_id] = set(task_ids)
        self._results[batch_id] = []
        # Start timeout watcher
        if batch_id in self._timeout_tasks:
            self._timeout_tasks[batch_id].cancel()
        self._timeout_tasks[batch_id] = asyncio.create_task(
            self._watch_timeout(batch_id)
        )

    async def on_event(self, event: FaithEvent) -> None:
        """Handle an incoming event. If it matches a pending completion, buffer it.
        If it's an urgent event, bypass batching entirely.
        """
        # Urgent events are never batched
        if event.event_type in self._immediate_events:
            return  # Let normal dispatch handle these

        # Find which batch this completion belongs to
        source = event.source or ""
        task_id = event.data.get("task_id", source) if event.data else source

        for batch_id, pending in self._pending.items():
            if task_id in pending:
                pending.discard(task_id)
                self._results[batch_id].append(event)

                # All done?
                if not pending:
                    await self._fire_batch(batch_id)
                return

    async def _fire_batch(self, batch_id: str) -> None:
        """All expected completions arrived — invoke the batch callback."""
        # Cancel timeout
        if batch_id in self._timeout_tasks:
            self._timeout_tasks[batch_id].cancel()
            del self._timeout_tasks[batch_id]

        results = self._results.pop(batch_id, [])
        self._pending.pop(batch_id, None)

        if self._batch_callback and results:
            # Create a synthetic batch event containing all results
            batch_event = FaithEvent(
                event_type="batch:complete",
                source="completion_batcher",
                data={
                    "batch_id": batch_id,
                    "results": [e.to_dict() for e in results],
                    "count": len(results),
                },
            )
            await self._batch_callback(batch_event)

    async def _watch_timeout(self, batch_id: str) -> None:
        """Wait for the timeout, then fire with whatever we have."""
        try:
            await asyncio.sleep(self._timeout_seconds)
        except asyncio.CancelledError:
            return  # Batch completed before timeout

        # Timeout expired — act on what we have
        still_pending = self._pending.pop(batch_id, set())
        results = self._results.pop(batch_id, [])
        del self._timeout_tasks[batch_id]

        if self._timeout_callback:
            timeout_event = FaithEvent(
                event_type="batch:timeout",
                source="completion_batcher",
                data={
                    "batch_id": batch_id,
                    "completed_results": [e.to_dict() for e in results],
                    "completed_count": len(results),
                    "still_pending": list(still_pending),
                    "pending_count": len(still_pending),
                },
            )
            await self._timeout_callback(timeout_event)
        elif self._batch_callback and results:
            # Fall back to batch callback with partial results
            batch_event = FaithEvent(
                event_type="batch:partial",
                source="completion_batcher",
                data={
                    "batch_id": batch_id,
                    "results": [e.to_dict() for e in results],
                    "count": len(results),
                    "still_pending": list(still_pending),
                },
            )
            await self._batch_callback(batch_event)

    def cancel(self, batch_id: str) -> None:
        """Cancel a pending batch without firing any callback."""
        self._pending.pop(batch_id, None)
        self._results.pop(batch_id, None)
        if batch_id in self._timeout_tasks:
            self._timeout_tasks[batch_id].cancel()
            del self._timeout_tasks[batch_id]

    @property
    def active_batches(self) -> list[str]:
        """Return IDs of currently active (pending) batches."""
        return list(self._pending.keys())
```

---

### 3. Update `src/faith_shared/protocol/__init__.py`

Add the subscriber exports to the existing `__init__.py`:

```python
"""FAITH Protocol — compact messaging and event system."""

from faith.protocol.compact import (
    ChannelMessageStore,
    CompactMessage,
    MessageFilter,
    MessagePriority,
    MessageStatus,
    MessageType,
)
from faith.protocol.events import (
    EventPublisher,
    EventType,
    FaithEvent,
)
from faith.protocol.subscriber import (
    CompletionBatcher,
    EventHandler,
    EventSubscriber,
    StallDetector,
)

__all__ = [
    # Compact protocol (FAITH-007)
    "CompactMessage",
    "MessageType",
    "MessageStatus",
    "MessagePriority",
    "MessageFilter",
    "ChannelMessageStore",
    # Events (FAITH-008)
    "FaithEvent",
    "EventType",
    "EventPublisher",
    # Subscriber & dispatcher (FAITH-009)
    "EventSubscriber",
    "EventHandler",
    "StallDetector",
    "CompletionBatcher",
]
```

### 3. `tests/test_event_subscriber.py`

```python
"""Tests for the event subscriber, dispatcher, and stall detector."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.protocol.events import EventPublisher, EventType, FaithEvent
from faith.protocol.subscriber import EventSubscriber, StallDetector


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def mock_redis():
    """Create a mock async Redis client."""
    return AsyncMock()


@pytest.fixture
def mock_publisher():
    """Create a mock EventPublisher with all methods as AsyncMock."""
    pub = AsyncMock(spec=EventPublisher)
    pub.channel_stalled = AsyncMock()
    pub.agent_error = AsyncMock()
    return pub


@pytest.fixture
def stall_detector(mock_publisher):
    """Create a StallDetector with short intervals for testing."""
    return StallDetector(
        publisher=mock_publisher,
        stall_timeout=2.0,          # 2 seconds for tests
        heartbeat_interval=1.0,     # 1 second for tests
        missed_heartbeat_limit=3,
        tick_interval=0.5,          # check every 0.5s for tests
    )


@pytest.fixture
def subscriber(mock_redis, stall_detector):
    """Create an EventSubscriber with mock Redis and stall detector."""
    return EventSubscriber(
        redis=mock_redis,
        stall_detector=stall_detector,
    )


# ──────────────────────────────────────────────────
# EventSubscriber tests
# ──────────────────────────────────────────────────


def test_register_handler(subscriber):
    """Handlers can be registered for specific event types."""
    handler = AsyncMock()
    subscriber.on(EventType.AGENT_TASK_COMPLETE, handler)
    assert handler in subscriber._handlers[EventType.AGENT_TASK_COMPLETE]


def test_register_wildcard_handler(subscriber):
    """Wildcard handlers receive all events."""
    handler = AsyncMock()
    subscriber.on_all(handler)
    assert handler in subscriber._wildcard_handlers


def test_remove_handler(subscriber):
    """Handlers can be removed after registration."""
    handler = AsyncMock()
    subscriber.on(EventType.AGENT_TASK_COMPLETE, handler)
    subscriber.remove(EventType.AGENT_TASK_COMPLETE, handler)
    assert handler not in subscriber._handlers[EventType.AGENT_TASK_COMPLETE]


@pytest.mark.asyncio
async def test_dispatch_calls_matching_handlers(subscriber):
    """Dispatch calls handlers registered for the event type."""
    handler1 = AsyncMock(__name__="handler1")
    handler2 = AsyncMock(__name__="handler2")
    unrelated = AsyncMock(__name__="unrelated")

    subscriber.on(EventType.AGENT_TASK_COMPLETE, handler1)
    subscriber.on(EventType.AGENT_TASK_COMPLETE, handler2)
    subscriber.on(EventType.AGENT_ERROR, unrelated)

    event = FaithEvent(
        event=EventType.AGENT_TASK_COMPLETE,
        source="dev-agent",
        channel="ch-test",
        data={"task": "test"},
    )

    await subscriber._dispatch(event)

    handler1.assert_awaited_once_with(event)
    handler2.assert_awaited_once_with(event)
    unrelated.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_calls_wildcard_handlers(subscriber):
    """Wildcard handlers are called for every event type."""
    wildcard = AsyncMock(__name__="wildcard")
    subscriber.on_all(wildcard)

    event = FaithEvent(
        event=EventType.FILE_CHANGED,
        source="filesystem",
        channel="ch-test",
        data={},
    )

    await subscriber._dispatch(event)
    wildcard.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_dispatch_handler_error_does_not_break_others(subscriber):
    """A failing handler should not prevent other handlers from running."""
    failing = AsyncMock(__name__="failing", side_effect=ValueError("boom"))
    working = AsyncMock(__name__="working")

    subscriber.on(EventType.AGENT_TASK_COMPLETE, failing)
    subscriber.on(EventType.AGENT_TASK_COMPLETE, working)

    event = FaithEvent(
        event=EventType.AGENT_TASK_COMPLETE,
        source="dev-agent",
        channel="ch-test",
        data={},
    )

    # Should not raise
    await subscriber._dispatch(event)

    failing.assert_awaited_once()
    working.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_no_handlers_is_noop(subscriber):
    """Dispatching an event with no handlers does nothing."""
    event = FaithEvent(
        event=EventType.SYSTEM_CONTAINER_STARTED,
        source="pa",
        channel=None,
        data={},
    )

    # Should not raise
    await subscriber._dispatch(event)


# ──────────────────────────────────────────────────
# StallDetector tests
# ──────────────────────────────────────────────────


def test_register_channel(stall_detector):
    """Registering a channel adds it to tracking."""
    stall_detector.register_channel("ch-test")
    assert "ch-test" in stall_detector._channel_activity


def test_unregister_channel(stall_detector):
    """Unregistering a channel removes it from tracking."""
    stall_detector.register_channel("ch-test")
    stall_detector.unregister_channel("ch-test")
    assert "ch-test" not in stall_detector._channel_activity


def test_register_agent(stall_detector):
    """Registering an agent adds it to heartbeat tracking."""
    stall_detector.register_agent("dev-agent")
    assert "dev-agent" in stall_detector._agent_heartbeats


def test_unregister_agent(stall_detector):
    """Unregistering an agent removes it from heartbeat tracking."""
    stall_detector.register_agent("dev-agent")
    stall_detector.unregister_agent("dev-agent")
    assert "dev-agent" not in stall_detector._agent_heartbeats


def test_record_event_resets_channel_activity(stall_detector):
    """An event with a channel field resets that channel's timer."""
    stall_detector.register_channel("ch-test")
    old_time = stall_detector._channel_activity["ch-test"]

    # Simulate time passing
    stall_detector._channel_activity["ch-test"] = time.monotonic() - 100

    event = FaithEvent(
        event=EventType.AGENT_TASK_COMPLETE,
        source="dev-agent",
        channel="ch-test",
        data={},
    )
    stall_detector.record_event(event)

    # Timer should be reset to approximately now
    assert stall_detector._channel_activity["ch-test"] > old_time


def test_record_heartbeat_resets_agent_timer(stall_detector):
    """A heartbeat event resets the agent's heartbeat timer."""
    stall_detector.register_agent("dev-agent")
    stall_detector._agent_heartbeats["dev-agent"] = time.monotonic() - 100

    event = FaithEvent(
        event=EventType.AGENT_HEARTBEAT,
        source="dev-agent",
        channel=None,
        data={},
    )
    stall_detector.record_event(event)

    # Timer should be recent
    assert time.monotonic() - stall_detector._agent_heartbeats["dev-agent"] < 1.0


def test_tool_activity_resets_channel(stall_detector):
    """Tool call events reset the associated channel's stall timer."""
    stall_detector.register_channel("ch-test")
    stall_detector._channel_activity["ch-test"] = time.monotonic() - 100

    event = FaithEvent(
        event=EventType.TOOL_CALL_STARTED,
        source="filesystem",
        channel="ch-test",
        data={"tool": "read_file"},
    )
    stall_detector.record_event(event)

    assert time.monotonic() - stall_detector._channel_activity["ch-test"] < 1.0


def test_record_event_clears_stalled_flag(stall_detector):
    """Activity on a stalled channel clears the stalled flag."""
    stall_detector.register_channel("ch-test")
    stall_detector._stalled_channels.add("ch-test")

    event = FaithEvent(
        event=EventType.AGENT_TASK_COMPLETE,
        source="dev-agent",
        channel="ch-test",
        data={},
    )
    stall_detector.record_event(event)

    assert "ch-test" not in stall_detector._stalled_channels


@pytest.mark.asyncio
async def test_check_channels_detects_stall(stall_detector, mock_publisher):
    """Channels inactive beyond the threshold trigger a stall event."""
    stall_detector.register_channel("ch-stale")
    # Set activity to 10 seconds ago (threshold is 2s for tests)
    stall_detector._channel_activity["ch-stale"] = time.monotonic() - 10

    await stall_detector._check_channels()

    mock_publisher.channel_stalled.assert_awaited_once()
    call_kwargs = mock_publisher.channel_stalled.call_args
    assert call_kwargs.kwargs["channel"] == "ch-stale"
    assert "ch-stale" in stall_detector._stalled_channels


@pytest.mark.asyncio
async def test_check_channels_does_not_double_report(stall_detector, mock_publisher):
    """A channel already flagged as stalled is not reported again."""
    stall_detector.register_channel("ch-stale")
    stall_detector._channel_activity["ch-stale"] = time.monotonic() - 10
    stall_detector._stalled_channels.add("ch-stale")

    await stall_detector._check_channels()

    mock_publisher.channel_stalled.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_channels_ignores_active(stall_detector, mock_publisher):
    """Active channels (recent activity) are not flagged."""
    stall_detector.register_channel("ch-active")
    # Activity just now — well within threshold
    stall_detector._channel_activity["ch-active"] = time.monotonic()

    await stall_detector._check_channels()

    mock_publisher.channel_stalled.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_heartbeats_detects_absence(stall_detector, mock_publisher):
    """Agents with missed heartbeats beyond the limit trigger an error event."""
    stall_detector.register_agent("dead-agent")
    # Last heartbeat was 10 seconds ago (limit is 3 * 1s = 3s for tests)
    stall_detector._agent_heartbeats["dead-agent"] = time.monotonic() - 10

    await stall_detector._check_heartbeats()

    mock_publisher.agent_error.assert_awaited_once()
    call_kwargs = mock_publisher.agent_error.call_args
    assert call_kwargs.kwargs["source"] == "dead-agent"
    assert call_kwargs.kwargs["data"]["reason"] == "heartbeat_absence"
    assert "dead-agent" in stall_detector._errored_agents


@pytest.mark.asyncio
async def test_check_heartbeats_does_not_double_report(stall_detector, mock_publisher):
    """An agent already flagged as errored is not reported again."""
    stall_detector.register_agent("dead-agent")
    stall_detector._agent_heartbeats["dead-agent"] = time.monotonic() - 10
    stall_detector._errored_agents.add("dead-agent")

    await stall_detector._check_heartbeats()

    mock_publisher.agent_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_heartbeats_ignores_healthy(stall_detector, mock_publisher):
    """Agents with recent heartbeats are not flagged."""
    stall_detector.register_agent("alive-agent")
    stall_detector._agent_heartbeats["alive-agent"] = time.monotonic()

    await stall_detector._check_heartbeats()

    mock_publisher.agent_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_stall_detector_start_stop(stall_detector):
    """StallDetector can be started and stopped cleanly."""
    await stall_detector.start()
    assert stall_detector._running is True
    assert stall_detector._task is not None

    await stall_detector.stop()
    assert stall_detector._running is False


@pytest.mark.asyncio
async def test_stall_detector_tick_loop_runs(stall_detector, mock_publisher):
    """The tick loop detects stalls within a few tick intervals."""
    stall_detector.register_channel("ch-stale")
    stall_detector._channel_activity["ch-stale"] = time.monotonic() - 10

    await stall_detector.start()
    # Wait for at least one tick
    await asyncio.sleep(0.8)
    await stall_detector.stop()

    mock_publisher.channel_stalled.assert_awaited()


# ── CompletionBatcher tests ──────────────────────────────────────────


@pytest.fixture
def batcher():
    return CompletionBatcher(timeout_seconds=2.0, immediate_events={"agent:error"})


def _make_complete_event(source: str, task_id: str = None) -> FaithEvent:
    return FaithEvent(
        event_type="agent:task_complete",
        source=source,
        data={"task_id": task_id or source, "result": f"{source} done"},
    )


@pytest.mark.asyncio
async def test_batcher_fires_when_all_complete(batcher):
    """Batch callback fires only when all expected tasks complete."""
    results = []

    async def on_batch(event):
        results.append(event)

    batcher.on_batch_ready(on_batch)
    batcher.expect("batch-1", {"agent-a", "agent-b", "agent-c"})

    await batcher.on_event(_make_complete_event("agent-a"))
    assert len(results) == 0  # Not yet

    await batcher.on_event(_make_complete_event("agent-b"))
    assert len(results) == 0  # Still waiting

    await batcher.on_event(_make_complete_event("agent-c"))
    assert len(results) == 1  # All done — batch fires
    assert results[0].data["count"] == 3


@pytest.mark.asyncio
async def test_batcher_does_not_fire_on_partial(batcher):
    """Batch callback does not fire until all tasks are done."""
    fired = []

    async def on_batch(event):
        fired.append(True)

    batcher.on_batch_ready(on_batch)
    batcher.expect("batch-1", {"agent-a", "agent-b"})

    await batcher.on_event(_make_complete_event("agent-a"))
    assert len(fired) == 0


@pytest.mark.asyncio
async def test_batcher_timeout_fires_with_partial_results(batcher):
    """On timeout, fires with completed results and lists pending."""
    timeout_results = []

    async def on_timeout(event):
        timeout_results.append(event)

    batcher.on_batch_timeout(on_timeout)
    batcher.expect("batch-1", {"agent-a", "agent-b"})

    await batcher.on_event(_make_complete_event("agent-a"))
    # Wait for timeout (batcher has 2s timeout in fixture)
    await asyncio.sleep(2.5)

    assert len(timeout_results) == 1
    assert timeout_results[0].data["completed_count"] == 1
    assert "agent-b" in timeout_results[0].data["still_pending"]


@pytest.mark.asyncio
async def test_batcher_cancel_removes_batch(batcher):
    """Cancelling a batch prevents any callback."""
    fired = []

    async def on_batch(event):
        fired.append(True)

    batcher.on_batch_ready(on_batch)
    batcher.expect("batch-1", {"agent-a"})
    batcher.cancel("batch-1")

    await batcher.on_event(_make_complete_event("agent-a"))
    assert len(fired) == 0
    assert "batch-1" not in batcher.active_batches


@pytest.mark.asyncio
async def test_batcher_multiple_independent_batches(batcher):
    """Multiple batches can run independently."""
    results = []

    async def on_batch(event):
        results.append(event.data["batch_id"])

    batcher.on_batch_ready(on_batch)
    batcher.expect("batch-1", {"agent-a"})
    batcher.expect("batch-2", {"agent-b", "agent-c"})

    await batcher.on_event(_make_complete_event("agent-a"))
    assert results == ["batch-1"]

    await batcher.on_event(_make_complete_event("agent-b"))
    assert results == ["batch-1"]  # batch-2 not done yet

    await batcher.on_event(_make_complete_event("agent-c"))
    assert results == ["batch-1", "batch-2"]


@pytest.mark.asyncio
async def test_batcher_ignores_unknown_events(batcher):
    """Events from unknown sources are silently ignored."""
    fired = []

    async def on_batch(event):
        fired.append(True)

    batcher.on_batch_ready(on_batch)
    batcher.expect("batch-1", {"agent-a"})

    await batcher.on_event(_make_complete_event("unknown-agent"))
    assert len(fired) == 0
```

---

## Integration with PA Startup

The `EventSubscriber` is the PA's main event loop. During startup (FAITH-014/FAITH-016), the PA:

```python
async def startup():
    redis = await get_async_client()
    publisher = EventPublisher(redis)

    # Create stall detector with config values
    stall_detector = StallDetector(
        publisher=publisher,
        stall_timeout=config.system.stall_timeout,         # default 300s
        heartbeat_interval=config.system.heartbeat_interval, # default 30s
        missed_heartbeat_limit=3,
    )

    # Create subscriber and register handlers
    subscriber = EventSubscriber(redis, stall_detector=stall_detector)

    # Create completion batcher for parallel task coordination
    batcher = CompletionBatcher(
        timeout_seconds=config.system.event_batching.batch_timeout_minutes * 60,
        immediate_events={
            EventType.AGENT_ERROR,
            EventType.CHANNEL_STALLED,
            EventType.CHANNEL_LOOP_DETECTED,
            EventType.APPROVAL_REQUESTED,
        },
    )

    # Register PA event handlers
    # Note: task_complete goes through batcher, not directly to handler
    subscriber.on(EventType.AGENT_TASK_COMPLETE, batcher.on_event)
    batcher.on_batch_ready(handle_all_tasks_complete)  # called once ALL pending tasks done
    subscriber.on(EventType.AGENT_TASK_BLOCKED, handle_task_blocked)
    subscriber.on(EventType.AGENT_NEEDS_INPUT, handle_needs_input)
    subscriber.on(EventType.AGENT_ERROR, handle_agent_error)
    subscriber.on(EventType.CHANNEL_STALLED, handle_stalled)
    subscriber.on(EventType.CHANNEL_LOOP_DETECTED, handle_loop)
    subscriber.on(EventType.APPROVAL_REQUESTED, handle_approval)
    subscriber.on(EventType.RESOURCE_TOKEN_THRESHOLD, handle_token_warning)
    subscriber.on(EventType.RESOURCE_TOKEN_CRITICAL, handle_token_critical)
    subscriber.on(EventType.SYSTEM_CONTAINER_ERROR, handle_container_error)

    # Wildcard handler for audit logging
    subscriber.on_all(log_event_to_audit)

    # Start listening
    await subscriber.start()

    # Register active channels and agents for stall detection
    for channel in active_channels:
        stall_detector.register_channel(channel)
    for agent in active_agents:
        stall_detector.register_agent(agent.name)
```

---

## Acceptance Criteria

1. `EventSubscriber.on()` registers handlers for specific event types.
2. `EventSubscriber.on_all()` registers wildcard handlers that receive all events.
3. `EventSubscriber.remove()` removes a previously registered handler.
4. `_dispatch()` calls all matching handlers (type-specific + wildcard) concurrently.
5. A failing handler does not prevent other handlers from executing.
6. `StallDetector.register_channel()` / `unregister_channel()` correctly manage tracked channels.
7. `StallDetector.register_agent()` / `unregister_agent()` correctly manage tracked agents.
8. `record_event()` resets channel activity on any event with a channel field.
9. `record_event()` resets agent heartbeat timer on `agent:heartbeat` events.
10. `record_event()` resets channel timer on `tool:call_started` / `tool:call_complete` events.
11. `_check_channels()` publishes `channel:stalled` when inactivity exceeds the threshold.
12. `_check_channels()` does not double-report already-stalled channels.
13. `_check_heartbeats()` publishes `agent:error` when heartbeats are missed beyond the limit.
14. `_check_heartbeats()` does not double-report already-errored agents.
15. Activity on a stalled channel clears the stalled flag, allowing re-detection if it stalls again.
16. `CompletionBatcher.expect()` registers a batch with a set of expected task IDs.
17. `CompletionBatcher.on_event()` buffers matching completions without invoking any callback.
18. Batch callback fires only when all expected completions arrive, with a synthetic `batch:complete` event containing all results.
19. Timeout fires a `batch:timeout` event listing completed results and still-pending task IDs.
20. `CompletionBatcher.cancel()` removes a batch without firing any callback.
21. Multiple independent batches can run concurrently without interference.
22. Events from unknown sources are silently ignored by the batcher.
23. All tests in `tests/test_event_subscriber.py` pass.

---

## Notes for Implementer

- `StallDetector` uses `time.monotonic()` rather than wall-clock time. Monotonic time is immune to system clock changes and is the correct choice for interval measurement.
- The `_stalled_channels` and `_errored_agents` sets prevent duplicate event spam. Once an agent responds to a stall query or a heartbeat resumes, the flag is cleared via `record_event()`.
- The `EventSubscriber._listen()` method uses `pubsub.get_message(timeout=1.0)` rather than blocking indefinitely. This allows the `_running` flag to be checked periodically for clean shutdown.
- Test fixtures use short intervals (0.5s tick, 2s stall timeout, 1s heartbeat) to keep tests fast. Production defaults are in the FRS: 60s tick, 300s stall timeout, 30s heartbeat.
- The `EventSubscriber` expects `FaithEvent.from_json()` to exist on the model. This was defined in FAITH-008's `FaithEvent` Pydantic model — ensure it includes a `from_json(cls, raw: str) -> FaithEvent` classmethod.
- Handlers are called with `asyncio.gather()` for concurrency. The `_safe_call` wrapper ensures individual handler failures are isolated.
- `CompletionBatcher` is a generic building block used by the PA (FAITH-016) to avoid wasting LLM output tokens on intermediate completion events. The PA calls `batcher.expect()` when dispatching parallel sub-tasks, and the batcher only fires the batch callback when all results are in. See FRS Section 3.7.8.
- Urgent events listed in `immediate_events` are never held by the batcher — they pass through to normal dispatch. The batcher's `on_event()` returns immediately for these, and the subscriber's separate handler handles them.
- The batcher's timeout task uses `asyncio.create_task()`. Ensure the event loop is running when `expect()` is called. In tests, use short timeouts (e.g., 2s) to keep tests fast.

