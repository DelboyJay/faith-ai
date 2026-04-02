"""Tests for the compact inter-agent protocol."""

from __future__ import annotations

import json

from faith_shared.protocol.compact import (
    ChannelMessageStore,
    CompactMessage,
    MessageFilter,
    MessagePriority,
    MessageStatus,
    MessageType,
)


def sample_message() -> CompactMessage:
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


def test_message_round_trip_json():
    message = sample_message()
    parsed = json.loads(message.to_json())
    assert parsed["from"] == "dev"
    assert parsed["to"] == "qa"
    assert parsed["type"] == "review_request"
    assert "files" not in parsed

    restored = CompactMessage.from_json(message.to_json())
    assert restored.from_agent == "dev"
    assert restored.to_agent == "qa"
    assert restored.msg_id == 47
    assert restored.summary == message.summary
    assert restored.context_ref == "ch-auth-feature/msg-42-46"


def test_message_round_trip_yaml():
    message = sample_message()
    yaml_text = message.to_yaml()
    restored = CompactMessage.from_yaml(yaml_text)
    assert restored.from_agent == message.from_agent
    assert restored.context_ref == message.context_ref


def test_message_from_dict_and_aliases():
    message = CompactMessage.from_dict(
        {
            "from": "qa",
            "to": "pa",
            "channel": "ch-build",
            "msg_id": 12,
            "type": "instruction",
            "tags": ["ops"],
            "summary": "restart the service",
        }
    )
    assert message.from_agent == "qa"
    assert message.to_agent == "pa"
    assert message.type == MessageType.INSTRUCTION
    assert message.priority == MessagePriority.NORMAL
    assert message.to_dict()["from"] == "qa"


def test_message_log_and_summary():
    message = sample_message()
    log_line = message.to_log_format()
    compact_line = message.to_compact_summary()
    assert "dev → qa" in log_line
    assert "review_request" in log_line
    assert "dev→qa" in compact_line
    assert "review_request" in compact_line


def test_default_priority_and_disposable_flag():
    message = CompactMessage(
        **{
            "from": "dev",
            "to": "qa",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.TASK,
            "tags": ["code"],
            "summary": "implement feature",
            "disposable": True,
        }
    )
    assert message.priority == MessagePriority.NORMAL
    assert message.disposable is True
    assert "disposable: true" in message.to_log_format()


def test_filter_by_tags_and_recipient():
    message = sample_message()
    assert MessageFilter("qa", ["testing"]).should_include(message) is True
    assert MessageFilter("arch", ["testing"]).should_include(message) is True
    assert MessageFilter("arch", ["ops"]).should_include(message) is False


def test_channel_store_context_resolution():
    store = ChannelMessageStore("ch-auth-feature")
    for msg_id in range(42, 47):
        store.add(
            CompactMessage(
                **{
                    "from": "dev",
                    "to": "qa",
                    "channel": "ch-auth-feature",
                    "msg_id": msg_id,
                    "type": MessageType.STATUS_UPDATE,
                    "tags": ["code"],
                    "summary": f"message {msg_id}",
                }
            )
        )

    single = store.resolve_context_ref("ch-auth-feature/msg-42")
    span = store.resolve_context_ref("ch-auth-feature/msg-42-46")
    wrong_channel = store.resolve_context_ref("ch-other/msg-42")
    external = store.resolve_context_ref("frs/REQ-011")

    assert [m.msg_id for m in single] == [42]
    assert [m.msg_id for m in span] == [42, 43, 44, 45, 46]
    assert wrong_channel == []
    assert external == []
    assert store.next_msg_id == 47


def test_channel_store_clear_and_counts():
    store = ChannelMessageStore("ch-ops")
    store.add(
        CompactMessage(
            **{
                "from": "pa",
                "to": "dev",
                "channel": "ch-ops",
                "msg_id": 1,
                "type": MessageType.STATUS_UPDATE,
                "tags": ["ops"],
                "summary": "done",
            }
        )
    )
    assert store.count() == 1
    assert store.get_recent(1)[0].msg_id == 1
    assert store.get_by_id(1) is not None
    store.clear()
    assert store.count() == 0
    assert store.next_msg_id == 1

