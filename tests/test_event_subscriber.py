"""Tests for the FAITH event subscriber, stall detector, and completion batcher."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from faith_shared.protocol.events import EventType, FaithEvent
from faith_shared.protocol.subscriber import CompletionBatcher, EventSubscriber, StallDetector


class FakePubSub:
    def __init__(self, messages: list[dict]):
        self.messages = list(messages)
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels: str) -> None:
        self.unsubscribed.extend(channels)

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0)
        return None

    async def aclose(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self, pubsub: FakePubSub):
        self._pubsub = pubsub

    def pubsub(self):
        return self._pubsub


class FakePublisher:
    def __init__(self):
        self.published: list[FaithEvent] = []

    async def publish(self, event: FaithEvent) -> None:
        self.published.append(event)


@pytest.mark.asyncio
async def test_subscriber_dispatches_handlers():
    event = FaithEvent(
        event=EventType.AGENT_TASK_COMPLETE,
        source="dev",
        channel="ch-1",
        data={"task": "done"},
    )
    pubsub = FakePubSub(
        [
            {"type": "message", "data": event.to_json()},
        ]
    )
    subscriber = EventSubscriber(FakeRedis(pubsub))
    handler = AsyncMock()

    async def stop_after_event(received: FaithEvent) -> None:
        await handler(received)
        subscriber._running = False

    subscriber.on(EventType.AGENT_TASK_COMPLETE, stop_after_event)
    await subscriber.start()
    await asyncio.sleep(0.05)
    await subscriber.stop()

    handler.assert_awaited_once()
    assert pubsub.subscribed == ["system-events"]
    assert pubsub.unsubscribed == ["system-events"]
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_subscriber_wildcard_dispatch_and_batcher_passthrough():
    pubsub = FakePubSub([])
    subscriber = EventSubscriber(FakeRedis(pubsub))
    seen: list[str] = []

    async def wildcard(event: FaithEvent) -> None:
        seen.append(event.event.value)

    subscriber.on_all(wildcard)
    await subscriber._dispatch(
        FaithEvent(event=EventType.FILE_CHANGED, source="pa", data={"path": "a"})
    )
    assert seen == ["file:changed"]


@pytest.mark.asyncio
async def test_stall_detector_flags_channel_and_agent():
    publisher = FakePublisher()
    detector = StallDetector(
        publisher=publisher,
        stall_timeout=0.1,
        heartbeat_interval=0.1,
        missed_heartbeat_limit=1,
        tick_interval=0.05,
    )
    detector.register_channel("ch-1")
    detector.register_agent("dev")
    detector._channel_activity["ch-1"] = time.monotonic() - 1
    detector._agent_heartbeats["dev"] = time.monotonic() - 1

    await detector._check_channels()
    await detector._check_heartbeats()

    assert any(event.event == EventType.CHANNEL_STALLED for event in publisher.published)
    assert any(event.event == EventType.AGENT_ERROR for event in publisher.published)


@pytest.mark.asyncio
async def test_completion_batcher_collects_and_fires():
    batcher = CompletionBatcher(timeout_seconds=0.1)
    batch_events: list[FaithEvent] = []
    batcher.on_batch_ready(batch_events.append)
    batcher.expect("batch-1", {"a", "b"})

    handled1 = await batcher.on_event(
        FaithEvent(
            event=EventType.AGENT_TASK_COMPLETE, source="a", channel="ch-1", data={"task_id": "a"}
        )
    )
    handled2 = await batcher.on_event(
        FaithEvent(
            event=EventType.AGENT_TASK_COMPLETE, source="b", channel="ch-1", data={"task_id": "b"}
        )
    )

    assert handled1 is True
    assert handled2 is True
    assert batcher.active_batches == []
    assert batch_events[-1].event == EventType.BATCH_COMPLETE
    assert batch_events[-1].data["count"] == 2


@pytest.mark.asyncio
async def test_completion_batcher_timeout_path():
    batcher = CompletionBatcher(timeout_seconds=0.05)
    timeout_events: list[FaithEvent] = []
    batcher.on_batch_timeout(timeout_events.append)
    batcher.expect("batch-2", {"a", "b"})

    await batcher.on_event(
        FaithEvent(
            event=EventType.AGENT_TASK_COMPLETE, source="a", channel="ch-1", data={"task_id": "a"}
        )
    )
    await asyncio.sleep(0.08)

    assert timeout_events
    assert timeout_events[0].event == EventType.BATCH_TIMEOUT
    assert timeout_events[0].data["pending_count"] == 1


@pytest.mark.asyncio
async def test_completion_batcher_ignores_immediate_events():
    batcher = CompletionBatcher(timeout_seconds=0.05, immediate_events={EventType.FILE_CHANGED})
    batcher.expect("batch-3", {"file-a"})

    handled = await batcher.on_event(
        FaithEvent(event=EventType.FILE_CHANGED, source="pa", data={"path": "x"})
    )

    assert handled is False
    assert batcher.active_batches == ["batch-3"]
    batcher.cancel("batch-3")

