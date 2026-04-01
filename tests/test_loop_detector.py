from __future__ import annotations

import pytest

from faith.pa.loop_detector import ChannelStateTracker, LoopDetectionConfig, LoopDetector, _Snapshot


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, list[str]]] = []

    async def channel_loop_detected(
        self,
        channel: str,
        description: str,
        agents_involved: list[str],
    ) -> None:
        self.events.append((channel, description, agents_involved))


@pytest.mark.asyncio
async def test_detect_direct_repetition() -> None:
    publisher = FakePublisher()
    detector = LoopDetector(
        config=LoopDetectionConfig(window_messages=5, state_repeat_threshold=1),
        event_publisher=publisher,
    )

    await detector.record_and_check(
        "chan", agent_id="dev", message_summary="same", file_hashes={"a": "1"}
    )
    result = await detector.record_and_check(
        "chan", agent_id="dev", message_summary="same", file_hashes={"a": "2"}
    )

    assert result.detected is True
    assert result.loop_type == "direct_repetition"
    assert publisher.events[0][0] == "chan"


@pytest.mark.asyncio
async def test_detect_oscillation() -> None:
    detector = LoopDetector(config=LoopDetectionConfig(window_messages=5, state_repeat_threshold=1))

    await detector.record_and_check(
        "chan", agent_id="dev", message_summary="one", file_hashes={"a": "1"}
    )
    result = await detector.record_and_check(
        "chan", agent_id="qa", message_summary="two", file_hashes={"a": "1"}
    )

    assert result.detected is True
    assert result.loop_type == "oscillation"


def test_reload_config_preserves_recent_snapshots() -> None:
    detector = LoopDetector(config=LoopDetectionConfig(window_messages=5, state_repeat_threshold=2))
    tracker = ChannelStateTracker(detector.config)
    tracker.add(_Snapshot("dev", "a", "b"))
    detector._channels["chan"] = tracker

    detector.reload_config(LoopDetectionConfig(window_messages=2, state_repeat_threshold=1))

    assert detector.config.window_messages == 2
    assert len(detector._channels["chan"].snapshots) == 1
