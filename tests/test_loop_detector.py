"""
Description:
    Verify the PA loop detector catches repeated and oscillating channel state.

Requirements:
    - Cover direct repetition, oscillation, and config reload behaviour.
    - Verify loop-detected events are emitted when a publisher is present.
"""

from __future__ import annotations

import pytest

from faith_pa.pa.loop_detector import ChannelStateTracker, LoopDetectionConfig, LoopDetector, _Snapshot


class FakePublisher:
    """
    Description:
        Capture loop-detected events published by the loop detector.

    Requirements:
        - Preserve every published event tuple for later assertions.
    """

    def __init__(self) -> None:
        """
        Description:
            Initialise the captured event list.

        Requirements:
            - Start with no published events.
        """
        self.events: list[tuple[str, str, list[str]]] = []

    async def channel_loop_detected(
        self,
        channel: str,
        description: str,
        agents_involved: list[str],
    ) -> None:
        """
        Description:
            Record a published loop-detected event.

        Requirements:
            - Preserve the channel, description, and agent list for assertions.

        :param channel: Channel reported as looping.
        :param description: Human-readable loop description.
        :param agents_involved: Agents involved in the detected loop.
        """
        self.events.append((channel, description, agents_involved))


@pytest.mark.asyncio
async def test_detect_direct_repetition() -> None:
    """
    Description:
        Verify the loop detector halts a channel when the same agent repeats the
        same output.

    Requirements:
        - This test is needed to prove direct repetition triggers loop
          detection and event publishing.
        - Verify the published event references the looping channel.
    """
    publisher = FakePublisher()
    detector = LoopDetector(
        config=LoopDetectionConfig(window_messages=5, state_repeat_threshold=1),
        event_publisher=publisher,
    )

    await detector.record_and_check(
        "chan",
        agent_id="dev",
        message_summary="same",
        file_hashes={"a": "1"},
    )
    result = await detector.record_and_check(
        "chan",
        agent_id="dev",
        message_summary="same",
        file_hashes={"a": "2"},
    )

    assert result.detected is True
    assert result.loop_type == "direct_repetition"
    assert publisher.events[0][0] == "chan"


@pytest.mark.asyncio
async def test_detect_oscillation() -> None:
    """
    Description:
        Verify the loop detector flags repeated state hashes across agents as an
        oscillation.

    Requirements:
        - This test is needed to prove channel state reversions are detected as
          oscillation loops.
        - Verify the returned loop type is `oscillation`.
    """
    detector = LoopDetector(config=LoopDetectionConfig(window_messages=5, state_repeat_threshold=1))

    await detector.record_and_check(
        "chan",
        agent_id="dev",
        message_summary="one",
        file_hashes={"a": "1"},
    )
    result = await detector.record_and_check(
        "chan",
        agent_id="qa",
        message_summary="two",
        file_hashes={"a": "1"},
    )

    assert result.detected is True
    assert result.loop_type == "oscillation"


def test_reload_config_preserves_recent_snapshots() -> None:
    """
    Description:
        Verify loop-detector config reload preserves recent tracker history.

    Requirements:
        - This test is needed to prove config reload does not discard all recent
          channel state.
        - Verify the preserved snapshot count respects the new window size.
    """
    detector = LoopDetector(config=LoopDetectionConfig(window_messages=5, state_repeat_threshold=2))
    tracker = ChannelStateTracker(detector.config)
    tracker.add(_Snapshot("dev", "a", "b"))
    detector._channels["chan"] = tracker

    detector.reload_config(LoopDetectionConfig(window_messages=2, state_repeat_threshold=1))

    assert detector.config.window_messages == 2
    assert len(detector._channels["chan"].snapshots) == 1
