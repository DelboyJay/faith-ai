"""Description:
    Verify the FAITH event log writer primitives.

Requirements:
    - Prove event-log entries round-trip through JSON-lines persistence.
    - Prove the event log writer subscribes to the system event channel and persists published events.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from faith_pa.logging.event_log import EventLogEntry, EventLogWriter
from faith_shared.protocol.events import SYSTEM_EVENTS_CHANNEL, EventType, FaithEvent


class FakePubSub:
    """Description:
        Provide a minimal async pubsub stand-in for event-log tests.

    Requirements:
        - Record subscribe and unsubscribe calls.
        - Replay queued pubsub messages in order.
    """

    def __init__(self, messages: list[dict[str, str]] | None = None) -> None:
        """Description:
            Initialise the fake pubsub with optional queued messages.

        Requirements:
            - Preserve message order for later assertions.

        :param messages: Optional queued pubsub payloads.
        """

        self.messages = list(messages or [])
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        """Description:
            Record requested subscriptions.

        Requirements:
            - Preserve subscription order.

        :param channels: Channel names to subscribe.
        """

        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels: str) -> None:
        """Description:
            Record requested unsubscriptions.

        Requirements:
            - Preserve unsubscription order.

        :param channels: Channel names to unsubscribe.
        """

        self.unsubscribed.extend(channels)

    async def get_message(
        self,
        ignore_subscribe_messages: bool = True,
        timeout: float = 1.0,
    ) -> dict[str, str] | None:
        """Description:
            Return the next queued pubsub message or ``None`` when empty.

        Requirements:
            - Consume messages in order.
            - Yield to the event loop when the queue is empty.

        :param ignore_subscribe_messages: Compatibility flag matching Redis.
        :param timeout: Requested polling timeout.
        :returns: Next queued message or ``None``.
        """

        del ignore_subscribe_messages, timeout
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0)
        return None

    async def aclose(self) -> None:
        """Description:
            Mark the fake pubsub as closed.

        Requirements:
            - Allow shutdown tests to prove cleanup happened.
        """

        self.closed = True


class FakeRedis:
    """Description:
    Provide the minimal Redis interface needed by the event log writer.

    Requirements:
        - Return one stable fake pubsub instance.
    """

    def __init__(self, pubsub: FakePubSub) -> None:
        """Description:
            Initialise the fake Redis client.

        Requirements:
            - Preserve the injected pubsub for later calls.

        :param pubsub: Fake pubsub instance returned by ``pubsub()``.
        """

        self._pubsub = pubsub

    def pubsub(self) -> FakePubSub:
        """Description:
            Return the fake pubsub instance.

        Requirements:
            - Return the same instance on every call.

        :returns: Fake pubsub instance.
        """

        return self._pubsub


def test_event_log_entry_round_trip() -> None:
    """Description:
        Verify one event-log entry round-trips through JSON-lines serialisation.

    Requirements:
        - This test is needed to prove persisted event records keep the FRS-required fields.
        - Verify the restored entry preserves event, source, channel, and payload data.
    """

    entry = EventLogEntry(
        ts="2026-05-06T10:30:00Z",
        event="agent:task_complete",
        source="software-developer",
        channel="ch-auth-review",
        data={"task": "Implement refresh token flow", "msg_id": 42},
    )

    restored = EventLogEntry.from_json_line(entry.to_json_line())

    assert restored.event == "agent:task_complete"
    assert restored.source == "software-developer"
    assert restored.channel == "ch-auth-review"
    assert restored.data["msg_id"] == 42


@pytest.mark.asyncio
async def test_event_log_writer_subscribes_and_persists_system_events(tmp_path: Path) -> None:
    """Description:
        Verify the event log writer subscribes to the system event stream and persists events.

    Requirements:
        - This test is needed to prove FAITH can capture canonical runtime events in ``events.log``.
        - Verify subscription, persistence, cleanup, and stored payload fields all match expectations.

    :param tmp_path: Temporary pytest directory fixture.
    """

    event = FaithEvent(
        event=EventType.CHANNEL_STALLED,
        source="system",
        channel="ch-auth-review",
        data={"idle_seconds": 312},
    )
    pubsub = FakePubSub(
        [
            {
                "type": "message",
                "channel": SYSTEM_EVENTS_CHANNEL,
                "data": event.to_json(),
            }
        ]
    )
    writer = EventLogWriter(logs_dir=tmp_path / "logs")

    task = asyncio.create_task(writer.run(FakeRedis(pubsub)))
    await asyncio.sleep(0.03)
    await writer.stop()
    await asyncio.wait_for(task, timeout=1.0)

    log_text = (tmp_path / "logs" / "events.log").read_text(encoding="utf-8").splitlines()
    restored = EventLogEntry.from_json_line(log_text[0])

    assert pubsub.subscribed == [SYSTEM_EVENTS_CHANNEL]
    assert pubsub.unsubscribed == [SYSTEM_EVENTS_CHANNEL]
    assert pubsub.closed is True
    assert restored.event == "channel:stalled"
    assert restored.source == "system"
    assert restored.channel == "ch-auth-review"
    assert restored.data["idle_seconds"] == 312
