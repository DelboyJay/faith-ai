"""
Description:
    Verify the FAITH event subscriber, stall detector, and completion batcher
    coordinate event delivery and timeout behaviour correctly.

Requirements:
    - Cover subscriber dispatch, wildcard routing, stall detection, and batch
      completion handling.
    - Verify timeout behaviour and immediate-event bypass rules remain stable.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from faith_shared.protocol.events import EventType, FaithEvent
from faith_shared.protocol.subscriber import CompletionBatcher, EventSubscriber, StallDetector


class FakePubSub:
    """
    Description:
        Provide a minimal asynchronous pubsub stand-in for subscriber tests.

    Requirements:
        - Capture subscribe and unsubscribe calls so the tests can verify
          lifecycle handling.
        - Return queued messages in the same shape as the real Redis pubsub
          client.

    :param messages: Initial queued pubsub messages to replay.
    """

    def __init__(self, messages: list[dict]):
        """
        Description:
            Initialise the fake pubsub with a fixed list of queued messages.

        Requirements:
            - Preserve the provided message order so the tests remain
              deterministic.
            - Track subscription, unsubscription, and close state for later
              assertions.

        :param messages: Queued pubsub payloads to return to the subscriber.
        """
        self.messages = list(messages)
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        """
        Description:
            Record the channels requested by the subscriber.

        Requirements:
            - Keep every subscribed channel in order for later assertions.
            - Behave asynchronously so the fake matches the production client.

        :param channels: Channel names requested by the subscriber.
        """
        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels: str) -> None:
        """
        Description:
            Record channel unsubscription requests.

        Requirements:
            - Keep every unsubscribed channel in order for later assertions.
            - Behave asynchronously so shutdown logic exercises the same path as
              production.

        :param channels: Channel names being unsubscribed.
        """
        self.unsubscribed.extend(channels)

    async def get_message(
        self,
        ignore_subscribe_messages: bool = True,
        timeout: float = 1.0,
    ) -> dict | None:
        """
        Description:
            Return the next queued message or `None` when the queue is empty.

        Requirements:
            - Consume queued messages in order.
            - Yield back to the event loop when no messages remain so polling
              logic behaves realistically.

        :param ignore_subscribe_messages: Unused compatibility flag that mirrors
            the real Redis client API.
        :param timeout: Poll timeout requested by the subscriber.
        :returns: Next queued message payload or `None` when no messages remain.
        """
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0)
        return None

    async def aclose(self) -> None:
        """
        Description:
            Mark the fake pubsub as closed.

        Requirements:
            - Let the tests verify subscriber shutdown closes the pubsub
              resource.
        """
        self.closed = True


class FakeRedis:
    """
    Description:
        Provide the minimal Redis interface required by the event subscriber.

    Requirements:
        - Return the fake pubsub object supplied by the test.

    :param pubsub: Fake pubsub instance that should be returned to callers.
    """

    def __init__(self, pubsub: FakePubSub):
        """
        Description:
            Store the fake pubsub dependency.

        Requirements:
            - Preserve the supplied pubsub object unchanged.

        :param pubsub: Fake pubsub instance used by the subscriber.
        """
        self._pubsub = pubsub

    def pubsub(self) -> FakePubSub:
        """
        Description:
            Return the fake pubsub used by the subscriber.

        Requirements:
            - Match the real Redis client's `pubsub()` shape.

        :returns: Fake pubsub instance injected by the test.
        """
        return self._pubsub


class FakePublisher:
    """
    Description:
        Capture published events for stall detector assertions.

    Requirements:
        - Preserve every event published during the test for later inspection.
    """

    def __init__(self):
        """
        Description:
            Initialise the published-event store.

        Requirements:
            - Start with an empty event list for deterministic assertions.
        """
        self.published: list[FaithEvent] = []

    async def publish(self, event: FaithEvent) -> None:
        """
        Description:
            Record a published event.

        Requirements:
            - Append events in the order they are published.

        :param event: Event emitted by the stall detector.
        """
        self.published.append(event)


@pytest.mark.asyncio
async def test_subscriber_dispatches_handlers() -> None:
    """
    Description:
        Verify the subscriber delivers matching events to registered handlers and
        performs clean startup and shutdown.

    Requirements:
        - This test is needed to prove event delivery reaches the correct async
          handler.
        - Verify the subscriber subscribes, unsubscribes, and closes the pubsub
          resource correctly.
    """
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
        """
        Description:
            Stop the subscriber after the first dispatched event.

        Requirements:
            - Ensure the test completes deterministically after one handler call.

        :param received: Event dispatched by the subscriber.
        """
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
async def test_subscriber_wildcard_dispatch_and_batcher_passthrough() -> None:
    """
    Description:
        Verify wildcard handlers receive dispatched events even when no
        event-specific handler exists.

    Requirements:
        - This test is needed to prove global observers still see protocol
          traffic.
        - Verify direct dispatch reaches the wildcard handler unchanged.
    """
    pubsub = FakePubSub([])
    subscriber = EventSubscriber(FakeRedis(pubsub))
    seen: list[str] = []

    async def wildcard(event: FaithEvent) -> None:
        """
        Description:
            Record wildcard-dispatched event names for assertions.

        Requirements:
            - Keep the event order unchanged.

        :param event: Event dispatched by the subscriber.
        """
        seen.append(event.event.value)

    subscriber.on_all(wildcard)
    await subscriber._dispatch(
        FaithEvent(event=EventType.FILE_CHANGED, source="pa", data={"path": "a"})
    )
    assert seen == ["file:changed"]


@pytest.mark.asyncio
async def test_stall_detector_flags_channel_and_agent() -> None:
    """
    Description:
        Verify the stall detector emits events for stalled channels and missing
        agent heartbeats.

    Requirements:
        - This test is needed to prove runtime supervision surfaces channel and
          agent failures promptly.
        - Verify both channel-stalled and agent-error events are published.
    """
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
async def test_completion_batcher_collects_and_fires() -> None:
    """
    Description:
        Verify the completion batcher publishes a completion event once every
        expected member has finished.

    Requirements:
        - This test is needed to prove batch completion waits for all expected
          participants.
        - Verify the emitted batch-complete event reports the final count.
    """
    batcher = CompletionBatcher(timeout_seconds=0.1)
    batch_events: list[FaithEvent] = []
    batcher.on_batch_ready(batch_events.append)
    batcher.expect("batch-1", {"a", "b"})

    handled1 = await batcher.on_event(
        FaithEvent(
            event=EventType.AGENT_TASK_COMPLETE,
            source="a",
            channel="ch-1",
            data={"task_id": "a"},
        )
    )
    handled2 = await batcher.on_event(
        FaithEvent(
            event=EventType.AGENT_TASK_COMPLETE,
            source="b",
            channel="ch-1",
            data={"task_id": "b"},
        )
    )

    assert handled1 is True
    assert handled2 is True
    assert batcher.active_batches == []
    assert batch_events[-1].event == EventType.BATCH_COMPLETE
    assert batch_events[-1].data["count"] == 2


@pytest.mark.asyncio
async def test_completion_batcher_timeout_path() -> None:
    """
    Description:
        Verify the completion batcher publishes a timeout event when some
        expected members never finish.

    Requirements:
        - This test is needed to prove incomplete batches surface a timeout
          rather than hanging indefinitely.
        - Verify the timeout payload reports the remaining pending member count.
    """
    batcher = CompletionBatcher(timeout_seconds=0.05)
    timeout_events: list[FaithEvent] = []
    batcher.on_batch_timeout(timeout_events.append)
    batcher.expect("batch-2", {"a", "b"})

    await batcher.on_event(
        FaithEvent(
            event=EventType.AGENT_TASK_COMPLETE,
            source="a",
            channel="ch-1",
            data={"task_id": "a"},
        )
    )
    await asyncio.sleep(0.08)

    assert timeout_events
    assert timeout_events[0].event == EventType.BATCH_TIMEOUT
    assert timeout_events[0].data["pending_count"] == 1


@pytest.mark.asyncio
async def test_completion_batcher_ignores_immediate_events() -> None:
    """
    Description:
        Verify immediate events bypass batch tracking and leave active batches
        intact.

    Requirements:
        - This test is needed to prove bypass events do not accidentally satisfy
          or clear tracked batches.
        - Verify the active batch remains registered until explicitly cancelled.
    """
    batcher = CompletionBatcher(timeout_seconds=0.05, immediate_events={EventType.FILE_CHANGED})
    batcher.expect("batch-3", {"file-a"})

    handled = await batcher.on_event(
        FaithEvent(event=EventType.FILE_CHANGED, source="pa", data={"path": "x"})
    )

    assert handled is False
    assert batcher.active_batches == ["batch-3"]
    batcher.cancel("batch-3")
