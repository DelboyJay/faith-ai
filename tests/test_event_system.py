"""Tests for the FAITH event system publisher."""

from __future__ import annotations

import json

import pytest

from faith_shared.protocol.events import EventPublisher, EventType, FaithEvent


class FakeRedis:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> int:
        self.messages.append((channel, payload))
        return 1


@pytest.mark.asyncio
async def test_event_round_trip():
    event = FaithEvent(
        event=EventType.AGENT_TASK_COMPLETE,
        source="dev",
        channel="ch-auth",
        data={"task": "finish"},
    )
    parsed = json.loads(event.to_json())
    assert parsed["event"] == "agent:task_complete"
    restored = FaithEvent.from_json(event.to_json())
    assert restored.event == EventType.AGENT_TASK_COMPLETE
    assert restored.source == "dev"
    assert restored.event_type == EventType.AGENT_TASK_COMPLETE


@pytest.mark.asyncio
async def test_event_alias_round_trip_and_dict_output():
    event = FaithEvent(event_type=EventType.SYSTEM_CONFIG_CHANGED, source="pa", data={"file": "x"})
    parsed = event.to_dict()
    assert parsed["event"] == "system:config_changed"
    assert parsed["source"] == "pa"
    assert parsed["data"]["file"] == "x"


@pytest.mark.asyncio
async def test_publisher_emits_json_to_system_channel():
    redis = FakeRedis()
    publisher = EventPublisher(redis_client=redis, source="pa")

    await publisher.agent_task_complete(channel="ch-1", task="build", msg_id=7, files_written=2)

    assert len(redis.messages) == 1
    channel, payload = redis.messages[0]
    assert channel == "system-events"
    parsed = json.loads(payload)
    assert parsed["event"] == "agent:task_complete"
    assert parsed["source"] == "pa"
    assert parsed["data"]["task"] == "build"
    assert parsed["data"]["msg_id"] == 7
    assert parsed["data"]["files_written"] == 2


@pytest.mark.asyncio
async def test_publisher_handles_direct_event_publish():
    redis = FakeRedis()
    publisher = EventPublisher(redis_client=redis, source="pa")
    event = FaithEvent(event=EventType.SYSTEM_CONFIG_CHANGED, source="pa", data={"file": "x"})

    await publisher.publish(event)

    assert redis.messages[0][0] == "system-events"
    assert json.loads(redis.messages[0][1])["event"] == "system:config_changed"


@pytest.mark.asyncio
async def test_publisher_helpers_cover_common_event_shapes():
    redis = FakeRedis()
    publisher = EventPublisher(redis_client=redis, source="pa")

    await publisher.channel_stalled("ch-9", 42)
    await publisher.tool_call_started("filesystem", "read", "dev", channel="ch-9")
    await publisher.approval_requested(
        "req-1", "dev", "filesystem.read", "/tmp/file", channel="ch-9"
    )

    assert len(redis.messages) == 3
    first = json.loads(redis.messages[0][1])
    second = json.loads(redis.messages[1][1])
    third = json.loads(redis.messages[2][1])
    assert first["event"] == "channel:stalled"
    assert first["data"]["idle_seconds"] == 42
    assert second["event"] == "tool:call_started"
    assert second["source"] == "filesystem"
    assert third["event"] == "approval:requested"
    assert third["data"]["request_id"] == "req-1"

