"""
Description:
    Verify the FAITH event publisher emits stable payloads and helper-generated
    event shapes.

Requirements:
    - Cover event serialisation, direct publishing, and the common helper
      methods used by the PA.
    - Verify published payloads target the shared system event channel.
"""

from __future__ import annotations

import json

import pytest

from faith_shared.protocol.events import (
    EventPublisher,
    EventType,
    FaithEvent,
    SYSTEM_EVENTS_CHANNEL,
)


class FakeRedis:
    """
    Description:
        Provide a minimal Redis publisher double for event-system tests.

    Requirements:
        - Capture every published channel and payload for later assertions.
    """

    def __init__(self):
        """
        Description:
            Initialise the captured-message store.

        Requirements:
            - Start with an empty message list for deterministic assertions.
        """
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> int:
        """
        Description:
            Record a published payload and mimic Redis' integer return value.

        Requirements:
            - Preserve channel and payload order for later assertions.
            - Return a truthy publish count that matches Redis semantics.

        :param channel: Destination channel for the event payload.
        :param payload: JSON-encoded event payload.
        :returns: Integer publish count matching Redis' API shape.
        """
        self.messages.append((channel, payload))
        return 1


@pytest.mark.asyncio
async def test_event_round_trip() -> None:
    """
    Description:
        Verify events survive JSON serialisation and restoration.

    Requirements:
        - This test is needed to prove the event wire format keeps the canonical
          event name and source stable.
        - Verify round-tripped events preserve both `event` and `event_type`
          accessors.
    """
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
async def test_event_alias_round_trip_and_dict_output() -> None:
    """
    Description:
        Verify alias-based event construction and dictionary output remain
        stable.

    Requirements:
        - This test is needed to prove helper code can build events using the
          alternate `event_type` field.
        - Verify dictionary output exposes the canonical wire-format event key.
    """
    event = FaithEvent(event_type=EventType.SYSTEM_CONFIG_CHANGED, source="pa", data={"file": "x"})
    parsed = event.to_dict()
    assert parsed["event"] == "system:config_changed"
    assert parsed["source"] == "pa"
    assert parsed["data"]["file"] == "x"


@pytest.mark.asyncio
async def test_publisher_emits_json_to_system_channel() -> None:
    """
    Description:
        Verify publisher helpers emit JSON payloads to the shared system-events
        channel.

    Requirements:
        - This test is needed to prove PA helper methods route events to the
          expected channel.
        - Verify the helper-generated payload contains the expected source and
          task metadata.
    """
    redis = FakeRedis()
    publisher = EventPublisher(redis_client=redis, source="pa")

    await publisher.agent_task_complete(channel="ch-1", task="build", msg_id=7, files_written=2)

    assert len(redis.messages) == 1
    channel, payload = redis.messages[0]
    assert channel == SYSTEM_EVENTS_CHANNEL
    parsed = json.loads(payload)
    assert parsed["event"] == "agent:task_complete"
    assert parsed["source"] == "pa"
    assert parsed["data"]["task"] == "build"
    assert parsed["data"]["msg_id"] == 7
    assert parsed["data"]["files_written"] == 2


@pytest.mark.asyncio
async def test_publisher_handles_direct_event_publish() -> None:
    """
    Description:
        Verify the publisher forwards pre-built event objects unchanged.

    Requirements:
        - This test is needed to prove callers can bypass helper methods without
          changing routing semantics.
        - Verify direct publishes still target the system event channel.
    """
    redis = FakeRedis()
    publisher = EventPublisher(redis_client=redis, source="pa")
    event = FaithEvent(event=EventType.SYSTEM_CONFIG_CHANGED, source="pa", data={"file": "x"})

    await publisher.publish(event)

    assert redis.messages[0][0] == "system-events"
    assert json.loads(redis.messages[0][1])["event"] == "system:config_changed"


@pytest.mark.asyncio
async def test_publisher_helpers_cover_common_event_shapes() -> None:
    """
    Description:
        Verify the common helper methods emit the expected event types and
        payload structures.

    Requirements:
        - This test is needed to prove operational helpers for stalls, tool
          calls, and approvals remain stable.
        - Verify the emitted payloads include the expected event names and key
          metadata fields.
    """
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


def test_event_publisher_exposes_helpers_for_all_canonical_events() -> None:
    """
    Description:
        Verify the event publisher exposes helper methods for every canonical
        event shape used by the Phase 2 protocol layer.

    Requirements:
        - This test is needed to prove the publisher surface stays aligned with
          the shared event vocabulary.
        - Verify no helper is missing for the current canonical event set.
    """
    expected_helpers = {
        "agent_task_complete",
        "agent_task_blocked",
        "agent_needs_input",
        "agent_error",
        "agent_heartbeat",
        "agent_model_escalation",
        "agent_context_summary",
        "channel_stalled",
        "channel_goal_achieved",
        "channel_loop_detected",
        "tool_call_started",
        "tool_call_complete",
        "tool_permission_denied",
        "tool_error",
        "file_changed",
        "file_created",
        "file_deleted",
        "approval_requested",
        "approval_decision",
        "resource_token_threshold",
        "resource_token_critical",
        "system_config_changed",
        "system_config_error",
        "system_container_started",
        "system_container_stopped",
        "system_container_error",
        "batch_complete",
        "batch_timeout",
        "batch_partial",
    }
    publisher_members = set(dir(EventPublisher))
    assert expected_helpers - publisher_members == set()


@pytest.mark.asyncio
async def test_publisher_emits_config_error_and_batch_events() -> None:
    """
    Description:
        Verify the remaining Phase 2 publisher helpers emit stable payloads for
        config-error and batching workflows.

    Requirements:
        - This test is needed to prove the helper gap that previously existed in
          the event publisher stays closed.
        - Verify the config-error, batch-timeout, and batch-partial payloads use
          the expected event names and counts.
    """
    redis = FakeRedis()
    publisher = EventPublisher(redis_client=redis, source="pa")

    await publisher.system_config_error(
        "system.yaml",
        "validation failed",
        path=".faith/system.yaml",
    )
    await publisher.batch_timeout("batch-1", [{"task": "a"}], ["b", "c"])
    await publisher.batch_partial("batch-2", [{"task": "a"}, {"task": "b"}], ["c"])

    config_error = json.loads(redis.messages[0][1])
    timeout_event = json.loads(redis.messages[1][1])
    partial_event = json.loads(redis.messages[2][1])

    assert config_error["event"] == "system:config_error"
    assert config_error["data"]["file"] == "system.yaml"
    assert config_error["data"]["path"] == ".faith/system.yaml"
    assert timeout_event["event"] == "batch:timeout"
    assert timeout_event["data"]["completed_count"] == 1
    assert timeout_event["data"]["pending_count"] == 2
    assert partial_event["event"] == "batch:partial"
    assert partial_event["data"]["count"] == 2
