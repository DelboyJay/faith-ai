# FAITH-017 — Loop Detection

**Phase:** 4 — PA Core
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-016
**FRS Reference:** Section 3.2.2

---

## Objective

Implement a `LoopDetector` class that the PA uses to monitor all active channels for circular behaviour patterns. The detector maintains a rolling hash of key state changes (file SHA256 digests, recorded decisions) per channel and detects three loop types: direct repetition, circular dependency loops, and oscillation. When a loop is detected the channel is halted, a `channel:loop_detected` event is published via the event system, and a human-readable summary is surfaced to the user via the Web UI. Configuration is loaded from `.faith/system.yaml` under the `loop_detection` key.

---

## Architecture

```
faith/pa/
├── __init__.py
└── loop_detector.py     ← LoopDetector class (this task)

tests/
└── test_loop_detector.py  ← Tests (this task)
```

---

## Files to Create

### 1. `faith/pa/loop_detector.py`

```python
"""Loop detection for FAITH PA channel monitoring.

Maintains a rolling window of state snapshots per channel and detects
three types of circular behaviour:

1. Direct repetition — an agent produces output substantively identical
   to a recent previous message on the channel.
2. Circular dependency loops — Agent A's output causes Agent B to make
   a change which causes Agent A to revert or re-change the same thing.
3. Oscillation — any tracked state (file content, decision, task status)
   reverts to a previous state within the configured window.

Detection is based on SHA256 hashes of key state changes. When the same
state hash appears more than `state_repeat_threshold` times within
`window_messages`, a loop is flagged.

FRS Reference: Section 3.2.2
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from faith.protocol.events import EventPublisher, EventType

logger = logging.getLogger("faith.pa.loop_detector")


class LoopType(str, Enum):
    """Classification of detected loop types."""

    DIRECT_REPETITION = "direct_repetition"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    OSCILLATION = "oscillation"


@dataclass
class StateSnapshot:
    """A snapshot of key state changes at a point in the channel timeline.

    Attributes:
        msg_id: The compact protocol message ID that triggered this snapshot.
        agent: The agent that produced this state change.
        state_hash: SHA256 hex digest of the normalised state.
        file_hashes: Mapping of file paths to their SHA256 digests at this point.
        decisions: List of decision strings recorded in this message.
        raw_summary: The message summary (for human-readable loop reports).
    """

    msg_id: int
    agent: str
    state_hash: str
    file_hashes: dict[str, str] = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)
    raw_summary: str = ""


@dataclass
class LoopDetectionResult:
    """Result of a loop detection check.

    Attributes:
        detected: Whether a loop was detected.
        loop_type: The type of loop detected (None if no loop).
        description: Human-readable description of the loop.
        agents_involved: List of agent IDs involved in the loop.
        channel: The channel where the loop was detected.
        repeated_states: The state hashes that were repeated.
        repetition_count: How many times the state was seen.
    """

    detected: bool
    loop_type: Optional[LoopType] = None
    description: str = ""
    agents_involved: list[str] = field(default_factory=list)
    channel: str = ""
    repeated_states: list[str] = field(default_factory=list)
    repetition_count: int = 0


@dataclass
class LoopDetectionConfig:
    """Configuration for loop detection, loaded from .faith/system.yaml.

    Corresponds to the `loop_detection` key in system.yaml:

    ```yaml
    loop_detection:
      enabled: true
      window_messages: 10
      state_repeat_threshold: 2
    ```

    Attributes:
        enabled: Whether loop detection is active.
        window_messages: Number of recent messages to check for loops.
        state_repeat_threshold: How many times a state can repeat
            before flagging a loop.
    """

    enabled: bool = True
    window_messages: int = 10
    state_repeat_threshold: int = 2

    @classmethod
    def from_system_config(cls, system_config: dict[str, Any]) -> LoopDetectionConfig:
        """Create config from the parsed .faith/system.yaml dict.

        Args:
            system_config: The full parsed system.yaml content.

        Returns:
            LoopDetectionConfig with values from the config,
            falling back to defaults for missing keys.
        """
        ld = system_config.get("loop_detection", {})
        if not isinstance(ld, dict):
            logger.warning(
                "loop_detection config is not a dict — using defaults"
            )
            return cls()

        return cls(
            enabled=ld.get("enabled", True),
            window_messages=ld.get("window_messages", 10),
            state_repeat_threshold=ld.get("state_repeat_threshold", 2),
        )


def _compute_state_hash(
    file_hashes: dict[str, str],
    decisions: list[str],
) -> str:
    """Compute a SHA256 hash representing the current state.

    The hash is derived from a deterministic JSON serialisation of
    file hashes and decisions, so identical states always produce
    the same hash regardless of message ordering or formatting.

    Args:
        file_hashes: Mapping of file paths to their SHA256 digests.
        decisions: List of decision strings.

    Returns:
        Hex-encoded SHA256 digest of the normalised state.
    """
    # Sort keys and values for deterministic serialisation
    normalised = json.dumps(
        {
            "files": dict(sorted(file_hashes.items())),
            "decisions": sorted(decisions),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _compute_message_hash(summary: str, agent: str) -> str:
    """Compute a SHA256 hash of a message for direct repetition detection.

    Args:
        summary: The message summary text.
        agent: The agent that produced the message.

    Returns:
        Hex-encoded SHA256 digest.
    """
    content = json.dumps(
        {"agent": agent, "summary": summary.strip()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ChannelStateTracker:
    """Tracks rolling state snapshots for a single channel.

    Maintains a bounded window of recent state snapshots and
    checks for all three loop types on each new snapshot.

    Attributes:
        channel: The channel name being tracked.
        config: Loop detection configuration.
        snapshots: Rolling window of recent state snapshots.
    """

    def __init__(self, channel: str, config: LoopDetectionConfig):
        self.channel = channel
        self.config = config
        self._snapshots: list[StateSnapshot] = []
        self._message_hashes: list[tuple[str, str]] = []  # (hash, agent)

    @property
    def snapshots(self) -> list[StateSnapshot]:
        """Read-only access to the snapshot window."""
        return list(self._snapshots)

    def record_snapshot(self, snapshot: StateSnapshot) -> None:
        """Record a new state snapshot, maintaining the rolling window.

        Args:
            snapshot: The state snapshot to record.
        """
        self._snapshots.append(snapshot)

        # Record message hash for direct repetition detection
        msg_hash = _compute_message_hash(snapshot.raw_summary, snapshot.agent)
        self._message_hashes.append((msg_hash, snapshot.agent))

        # Trim to window size
        if len(self._snapshots) > self.config.window_messages:
            self._snapshots = self._snapshots[-self.config.window_messages :]
        if len(self._message_hashes) > self.config.window_messages:
            self._message_hashes = self._message_hashes[-self.config.window_messages :]

    def check_for_loops(self) -> LoopDetectionResult:
        """Check the current window for all three loop types.

        Checks are performed in order of specificity:
        1. Direct repetition (same agent, same message)
        2. Oscillation (state reverts to a previous state)
        3. Circular dependency (multi-agent state cycling)

        Returns:
            LoopDetectionResult indicating whether a loop was found
            and its details.
        """
        if not self.config.enabled:
            return LoopDetectionResult(detected=False)

        if len(self._snapshots) < 2:
            return LoopDetectionResult(detected=False)

        # Check 1: Direct repetition
        result = self._check_direct_repetition()
        if result.detected:
            return result

        # Check 2: Oscillation (state hash repeats)
        result = self._check_oscillation()
        if result.detected:
            return result

        # Check 3: Circular dependency (multi-agent pattern)
        result = self._check_circular_dependency()
        if result.detected:
            return result

        return LoopDetectionResult(detected=False)

    def _check_direct_repetition(self) -> LoopDetectionResult:
        """Detect an agent producing substantively identical output.

        Looks for the same agent sending the same message content
        more than `state_repeat_threshold` times within the window.
        """
        threshold = self.config.state_repeat_threshold

        # Count message hashes per agent
        agent_hash_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for msg_hash, agent in self._message_hashes:
            agent_hash_counts[agent][msg_hash] += 1

        for agent, hash_counts in agent_hash_counts.items():
            for msg_hash, count in hash_counts.items():
                if count > threshold:
                    return LoopDetectionResult(
                        detected=True,
                        loop_type=LoopType.DIRECT_REPETITION,
                        description=(
                            f"Agent '{agent}' has produced substantively "
                            f"identical output {count} times within the "
                            f"last {len(self._message_hashes)} messages "
                            f"on channel {self.channel}."
                        ),
                        agents_involved=[agent],
                        channel=self.channel,
                        repeated_states=[msg_hash],
                        repetition_count=count,
                    )

        return LoopDetectionResult(detected=False)

    def _check_oscillation(self) -> LoopDetectionResult:
        """Detect state reverting to a previous state.

        Looks for any state hash appearing more than
        `state_repeat_threshold` times within the window.
        Files oscillating between two versions is the classic
        case (e.g. auth.py alternating between version A and B).
        """
        threshold = self.config.state_repeat_threshold

        # Count state hash occurrences
        hash_counts: dict[str, int] = defaultdict(int)
        hash_agents: dict[str, set[str]] = defaultdict(set)

        for snap in self._snapshots:
            hash_counts[snap.state_hash] += 1
            hash_agents[snap.state_hash].add(snap.agent)

        for state_hash, count in hash_counts.items():
            if count > threshold:
                agents = sorted(hash_agents[state_hash])

                # Identify which files are oscillating
                oscillating_files = self._identify_oscillating_files()
                file_desc = ""
                if oscillating_files:
                    file_list = ", ".join(oscillating_files[:5])
                    file_desc = f" Files involved: {file_list}."

                return LoopDetectionResult(
                    detected=True,
                    loop_type=LoopType.OSCILLATION,
                    description=(
                        f"State on channel {self.channel} has reverted to "
                        f"a previous state {count} times within the last "
                        f"{len(self._snapshots)} messages.{file_desc} "
                        f"Agents involved: {', '.join(agents)}."
                    ),
                    agents_involved=agents,
                    channel=self.channel,
                    repeated_states=[state_hash],
                    repetition_count=count,
                )

        return LoopDetectionResult(detected=False)

    def _check_circular_dependency(self) -> LoopDetectionResult:
        """Detect multi-agent circular dependency loops.

        Looks for a repeating sequence of (agent, state_hash) pairs
        that indicates agents are causing each other to undo and
        redo changes in a cycle.

        A circular dependency is identified when we see the same
        ordered sub-sequence of agent actions repeating. For example:
        A modifies file → B reverts → A modifies → B reverts.
        """
        threshold = self.config.state_repeat_threshold

        if len(self._snapshots) < 4:
            return LoopDetectionResult(detected=False)

        # Build sequence of (agent, state_hash) pairs
        sequence = [
            (snap.agent, snap.state_hash) for snap in self._snapshots
        ]

        # Check for repeating sub-sequences of length 2..n/2
        max_pattern_len = len(sequence) // 2
        for pattern_len in range(2, max_pattern_len + 1):
            pattern = tuple(sequence[-pattern_len:])

            # Count how many times this pattern appears in the sequence
            match_count = 0
            for i in range(len(sequence) - pattern_len + 1):
                candidate = tuple(sequence[i : i + pattern_len])
                if candidate == pattern:
                    match_count += 1

            if match_count > threshold:
                agents = sorted(set(agent for agent, _ in pattern))

                # Must involve multiple agents to be a circular dependency
                if len(agents) < 2:
                    continue

                return LoopDetectionResult(
                    detected=True,
                    loop_type=LoopType.CIRCULAR_DEPENDENCY,
                    description=(
                        f"Circular dependency detected on channel "
                        f"{self.channel}: agents {', '.join(agents)} "
                        f"are in a repeating cycle of {pattern_len} "
                        f"steps, seen {match_count} times within the "
                        f"last {len(self._snapshots)} messages."
                    ),
                    agents_involved=agents,
                    channel=self.channel,
                    repeated_states=[h for _, h in pattern],
                    repetition_count=match_count,
                )

        return LoopDetectionResult(detected=False)

    def _identify_oscillating_files(self) -> list[str]:
        """Identify files whose content oscillates between states.

        Returns:
            List of file paths that have reverted to a previous
            SHA256 digest within the window.
        """
        # Track per-file hash history
        file_histories: dict[str, list[str]] = defaultdict(list)
        for snap in self._snapshots:
            for path, digest in snap.file_hashes.items():
                file_histories[path].append(digest)

        oscillating = []
        for path, digests in file_histories.items():
            if len(digests) < 3:
                continue
            # A file oscillates if any hash appears more than once
            # and the sequence is not monotonically new
            seen: dict[str, int] = {}
            for d in digests:
                if d in seen:
                    oscillating.append(path)
                    break
                seen[d] = 1

        return sorted(oscillating)

    def clear(self) -> None:
        """Clear all tracked state for this channel.

        Called when a channel is resumed after user guidance.
        """
        self._snapshots.clear()
        self._message_hashes.clear()


class LoopDetector:
    """PA-level loop detector that monitors all active channels.

    The PA creates a single LoopDetector instance and calls
    `record_state()` after each message on any active channel.
    The detector maintains per-channel state trackers and returns
    detection results that the PA uses to halt channels and
    surface loop summaries to the user.

    Usage:
        detector = LoopDetector(config, event_publisher)

        # After each message on a channel:
        result = await detector.record_and_check(
            channel="ch-auth-phase3",
            msg_id=47,
            agent="software-developer",
            file_hashes={"auth.py": "abc123...", "test_refresh.py": "def456..."},
            decisions=["use JWT httponly cookies"],
            summary="Updated auth.py to pass new assertion",
        )

        if result.detected:
            # Channel is already halted and event published
            # Surface result.description to user via Web UI
            pass

    Attributes:
        config: Loop detection configuration.
        event_publisher: EventPublisher for system-events.
        halted_channels: Set of channel names that have been halted
            due to loop detection.
    """

    def __init__(
        self,
        config: LoopDetectionConfig,
        event_publisher: EventPublisher,
    ):
        self.config = config
        self.event_publisher = event_publisher
        self._trackers: dict[str, ChannelStateTracker] = {}
        self.halted_channels: set[str] = set()

    def _get_tracker(self, channel: str) -> ChannelStateTracker:
        """Get or create the state tracker for a channel.

        Args:
            channel: The channel name.

        Returns:
            The ChannelStateTracker for this channel.
        """
        if channel not in self._trackers:
            self._trackers[channel] = ChannelStateTracker(
                channel, self.config
            )
        return self._trackers[channel]

    async def record_and_check(
        self,
        channel: str,
        msg_id: int,
        agent: str,
        file_hashes: Optional[dict[str, str]] = None,
        decisions: Optional[list[str]] = None,
        summary: str = "",
    ) -> LoopDetectionResult:
        """Record a state snapshot and check for loops.

        This is the primary entry point called by the PA after
        processing each message on an active channel.

        Args:
            channel: The channel name.
            msg_id: The compact protocol message ID.
            agent: The agent that produced this state change.
            file_hashes: Mapping of file paths to SHA256 digests
                of their current content. Only include files that
                were modified or referenced in this message.
            decisions: List of decisions recorded in this message.
            summary: The message summary text.

        Returns:
            LoopDetectionResult. If a loop is detected, the channel
            is halted and a `channel:loop_detected` event is
            published before returning.
        """
        if not self.config.enabled:
            return LoopDetectionResult(detected=False)

        # Skip channels that are already halted
        if channel in self.halted_channels:
            logger.debug(
                f"Skipping loop check for halted channel {channel}"
            )
            return LoopDetectionResult(detected=False)

        file_hashes = file_hashes or {}
        decisions = decisions or []

        # Compute state hash
        state_hash = _compute_state_hash(file_hashes, decisions)

        # Record snapshot
        snapshot = StateSnapshot(
            msg_id=msg_id,
            agent=agent,
            state_hash=state_hash,
            file_hashes=dict(file_hashes),
            decisions=list(decisions),
            raw_summary=summary,
        )

        tracker = self._get_tracker(channel)
        tracker.record_snapshot(snapshot)

        # Check for loops
        result = tracker.check_for_loops()

        if result.detected:
            logger.warning(
                f"Loop detected on channel {channel}: "
                f"{result.loop_type.value} — {result.description}"
            )

            # Halt the channel
            self.halted_channels.add(channel)

            # Publish event
            await self.event_publisher.channel_loop_detected(
                channel=channel,
                description=result.description,
                agents_involved=result.agents_involved,
            )

        return result

    def resume_channel(self, channel: str) -> None:
        """Resume a halted channel after user provides guidance.

        Clears the halted state and resets the state tracker for
        the channel so that the loop detection window starts fresh.

        Args:
            channel: The channel to resume.
        """
        self.halted_channels.discard(channel)

        if channel in self._trackers:
            self._trackers[channel].clear()

        logger.info(f"Channel {channel} resumed — loop detection reset")

    def remove_channel(self, channel: str) -> None:
        """Stop tracking a channel entirely.

        Called when a channel is closed (task complete or session end).

        Args:
            channel: The channel to stop tracking.
        """
        self._trackers.pop(channel, None)
        self.halted_channels.discard(channel)
        logger.debug(f"Stopped tracking channel {channel}")

    def reload_config(self, system_config: dict[str, Any]) -> None:
        """Reload configuration from an updated .faith/system.yaml.

        Called by the config hot-reload watcher (FAITH-004) when
        system.yaml changes. Updates the config for all existing
        channel trackers.

        Args:
            system_config: The full parsed system.yaml content.
        """
        self.config = LoopDetectionConfig.from_system_config(system_config)

        # Update all existing trackers
        for tracker in self._trackers.values():
            tracker.config = self.config

        logger.info(
            f"Loop detection config reloaded: "
            f"enabled={self.config.enabled}, "
            f"window={self.config.window_messages}, "
            f"threshold={self.config.state_repeat_threshold}"
        )

    @property
    def active_channels(self) -> list[str]:
        """List of channels currently being tracked."""
        return sorted(self._trackers.keys())
```

### 2. `faith/pa/__init__.py`

```python
"""FAITH Project Agent — core PA modules."""

from faith.pa.loop_detector import LoopDetector, LoopDetectionConfig

__all__ = [
    "LoopDetector",
    "LoopDetectionConfig",
]
```

### 3. `tests/test_loop_detector.py`

```python
"""Tests for FAITH PA loop detection.

Covers all three loop types (direct repetition, circular dependency,
oscillation), configuration loading, channel lifecycle, config
hot-reload, and edge cases.
"""

import json
from unittest.mock import AsyncMock

import pytest

from faith.pa.loop_detector import (
    ChannelStateTracker,
    LoopDetectionConfig,
    LoopDetectionResult,
    LoopDetector,
    LoopType,
    StateSnapshot,
    _compute_message_hash,
    _compute_state_hash,
)


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def default_config():
    """Default loop detection config."""
    return LoopDetectionConfig(
        enabled=True,
        window_messages=10,
        state_repeat_threshold=2,
    )


@pytest.fixture
def strict_config():
    """Strict config with small window for easier testing."""
    return LoopDetectionConfig(
        enabled=True,
        window_messages=6,
        state_repeat_threshold=1,
    )


@pytest.fixture
def disabled_config():
    """Config with loop detection disabled."""
    return LoopDetectionConfig(enabled=False)


@pytest.fixture
def mock_event_publisher():
    """Mock EventPublisher with async methods."""
    publisher = AsyncMock()
    publisher.channel_loop_detected = AsyncMock()
    return publisher


@pytest.fixture
def detector(default_config, mock_event_publisher):
    """LoopDetector with default config and mock publisher."""
    return LoopDetector(default_config, mock_event_publisher)


@pytest.fixture
def strict_detector(strict_config, mock_event_publisher):
    """LoopDetector with strict config for easier testing."""
    return LoopDetector(strict_config, mock_event_publisher)


# ──────────────────────────────────────────────────
# State hash computation tests
# ──────────────────────────────────────────────────


def test_compute_state_hash_deterministic():
    """Same inputs always produce the same hash."""
    h1 = _compute_state_hash({"a.py": "abc"}, ["use JWT"])
    h2 = _compute_state_hash({"a.py": "abc"}, ["use JWT"])
    assert h1 == h2


def test_compute_state_hash_order_independent():
    """File hash ordering does not affect the state hash."""
    h1 = _compute_state_hash({"a.py": "abc", "b.py": "def"}, [])
    h2 = _compute_state_hash({"b.py": "def", "a.py": "abc"}, [])
    assert h1 == h2


def test_compute_state_hash_different_for_different_inputs():
    """Different inputs produce different hashes."""
    h1 = _compute_state_hash({"a.py": "abc"}, [])
    h2 = _compute_state_hash({"a.py": "xyz"}, [])
    assert h1 != h2


def test_compute_state_hash_includes_decisions():
    """Decisions affect the state hash."""
    h1 = _compute_state_hash({}, ["decision A"])
    h2 = _compute_state_hash({}, ["decision B"])
    assert h1 != h2


def test_compute_message_hash_deterministic():
    """Same message produces the same hash."""
    h1 = _compute_message_hash("auth module done", "dev")
    h2 = _compute_message_hash("auth module done", "dev")
    assert h1 == h2


def test_compute_message_hash_strips_whitespace():
    """Leading/trailing whitespace is normalised."""
    h1 = _compute_message_hash("auth module done", "dev")
    h2 = _compute_message_hash("  auth module done  ", "dev")
    assert h1 == h2


def test_compute_message_hash_different_agents():
    """Same message from different agents produces different hashes."""
    h1 = _compute_message_hash("auth module done", "dev")
    h2 = _compute_message_hash("auth module done", "qa")
    assert h1 != h2


# ──────────────────────────────────────────────────
# LoopDetectionConfig tests
# ──────────────────────────────────────────────────


def test_config_from_system_config_defaults():
    """Missing keys fall back to defaults."""
    config = LoopDetectionConfig.from_system_config({})
    assert config.enabled is True
    assert config.window_messages == 10
    assert config.state_repeat_threshold == 2


def test_config_from_system_config_custom():
    """Custom values are loaded from system.yaml structure."""
    config = LoopDetectionConfig.from_system_config({
        "loop_detection": {
            "enabled": False,
            "window_messages": 20,
            "state_repeat_threshold": 3,
        }
    })
    assert config.enabled is False
    assert config.window_messages == 20
    assert config.state_repeat_threshold == 3


def test_config_from_system_config_partial():
    """Partial config uses defaults for missing keys."""
    config = LoopDetectionConfig.from_system_config({
        "loop_detection": {
            "window_messages": 15,
        }
    })
    assert config.enabled is True
    assert config.window_messages == 15
    assert config.state_repeat_threshold == 2


def test_config_from_system_config_invalid_type():
    """Non-dict loop_detection value falls back to defaults."""
    config = LoopDetectionConfig.from_system_config({
        "loop_detection": "not a dict"
    })
    assert config.enabled is True
    assert config.window_messages == 10


# ──────────────────────────────────────────────────
# ChannelStateTracker tests
# ──────────────────────────────────────────────────


def test_tracker_records_snapshots(default_config):
    """Snapshots are recorded and accessible."""
    tracker = ChannelStateTracker("ch-test", default_config)
    snap = StateSnapshot(
        msg_id=1,
        agent="dev",
        state_hash="abc",
        file_hashes={"a.py": "123"},
        raw_summary="did something",
    )
    tracker.record_snapshot(snap)
    assert len(tracker.snapshots) == 1
    assert tracker.snapshots[0].agent == "dev"


def test_tracker_window_rolling(default_config):
    """Tracker trims snapshots beyond the window size."""
    config = LoopDetectionConfig(
        enabled=True, window_messages=3, state_repeat_threshold=2
    )
    tracker = ChannelStateTracker("ch-test", config)

    for i in range(5):
        tracker.record_snapshot(StateSnapshot(
            msg_id=i,
            agent="dev",
            state_hash=f"hash-{i}",
            raw_summary=f"msg {i}",
        ))

    assert len(tracker.snapshots) == 3
    assert tracker.snapshots[0].msg_id == 2  # oldest kept


def test_tracker_clear(default_config):
    """Clear resets all tracked state."""
    tracker = ChannelStateTracker("ch-test", default_config)
    tracker.record_snapshot(StateSnapshot(
        msg_id=1, agent="dev", state_hash="abc", raw_summary="test",
    ))
    tracker.clear()
    assert len(tracker.snapshots) == 0


# ──────────────────────────────────────────────────
# Direct repetition detection tests
# ──────────────────────────────────────────────────


def test_detect_direct_repetition(default_config):
    """Detects when an agent sends the same message repeatedly."""
    tracker = ChannelStateTracker("ch-test", default_config)

    # Same agent, same summary — 3 times (threshold is 2)
    for i in range(3):
        snap = StateSnapshot(
            msg_id=i,
            agent="dev",
            state_hash=f"unique-{i}",  # unique state hashes
            raw_summary="Implement auth module",
        )
        tracker.record_snapshot(snap)

    result = tracker.check_for_loops()
    assert result.detected is True
    assert result.loop_type == LoopType.DIRECT_REPETITION
    assert "dev" in result.agents_involved


def test_no_direct_repetition_different_messages(default_config):
    """Different messages from the same agent do not trigger."""
    tracker = ChannelStateTracker("ch-test", default_config)

    for i in range(5):
        tracker.record_snapshot(StateSnapshot(
            msg_id=i,
            agent="dev",
            state_hash=f"hash-{i}",
            raw_summary=f"Different message {i}",
        ))

    result = tracker.check_for_loops()
    assert result.detected is False


def test_no_direct_repetition_below_threshold(default_config):
    """Repetition at or below threshold does not trigger."""
    tracker = ChannelStateTracker("ch-test", default_config)

    # Exactly threshold (2) — should NOT trigger (need > threshold)
    for i in range(2):
        tracker.record_snapshot(StateSnapshot(
            msg_id=i,
            agent="dev",
            state_hash=f"unique-{i}",
            raw_summary="Same message",
        ))

    result = tracker.check_for_loops()
    assert result.detected is False


# ──────────────────────────────────────────────────
# Oscillation detection tests
# ──────────────────────────────────────────────────


def test_detect_oscillation(default_config):
    """Detects state oscillating between two values."""
    tracker = ChannelStateTracker("ch-test", default_config)

    file_v1 = {"auth.py": "version-A"}
    file_v2 = {"auth.py": "version-B"}

    # Oscillation: A → B → A (state A seen twice, exceeds threshold of 2)
    states = [file_v1, file_v2, file_v1]
    for i, files in enumerate(states):
        tracker.record_snapshot(StateSnapshot(
            msg_id=i,
            agent="dev" if i % 2 == 0 else "qa",
            state_hash=_compute_state_hash(files, []),
            file_hashes=files,
            raw_summary=f"Step {i}",
        ))

    result = tracker.check_for_loops()
    assert result.detected is True
    assert result.loop_type == LoopType.OSCILLATION
    assert "auth.py" in result.description


def test_detect_oscillation_frs_example(default_config):
    """Reproduces the FRS Section 11.6 example: auth.py and test_refresh.py oscillating."""
    tracker = ChannelStateTracker("ch-auth-phase3", default_config)

    # Simulate the FRS example:
    # qa modifies test_refresh.py → dev modifies auth.py →
    # qa modifies test_refresh.py again → dev modifies auth.py again
    states = [
        {"auth.py": "a1", "test_refresh.py": "t1"},  # initial
        {"auth.py": "a2", "test_refresh.py": "t1"},  # dev changes auth.py
        {"auth.py": "a2", "test_refresh.py": "t2"},  # qa changes test
        {"auth.py": "a1", "test_refresh.py": "t2"},  # dev reverts auth.py
        {"auth.py": "a1", "test_refresh.py": "t1"},  # qa reverts test
        {"auth.py": "a2", "test_refresh.py": "t1"},  # dev changes auth.py again
        {"auth.py": "a2", "test_refresh.py": "t2"},  # qa changes test again
        {"auth.py": "a1", "test_refresh.py": "t2"},  # dev reverts auth.py again
    ]
    agents = ["qa", "dev", "qa", "dev", "qa", "dev", "qa", "dev"]

    for i, (files, agent) in enumerate(zip(states, agents)):
        tracker.record_snapshot(StateSnapshot(
            msg_id=i,
            agent=agent,
            state_hash=_compute_state_hash(files, []),
            file_hashes=files,
            raw_summary=f"Modified files",
        ))

    result = tracker.check_for_loops()
    assert result.detected is True
    # Could be oscillation or circular dependency — both are valid
    assert result.loop_type in (
        LoopType.OSCILLATION,
        LoopType.CIRCULAR_DEPENDENCY,
    )
    assert len(result.agents_involved) >= 1


def test_no_oscillation_monotonic_progress(default_config):
    """Monotonically progressing state does not trigger oscillation."""
    tracker = ChannelStateTracker("ch-test", default_config)

    for i in range(5):
        tracker.record_snapshot(StateSnapshot(
            msg_id=i,
            agent="dev",
            state_hash=f"unique-state-{i}",
            file_hashes={f"file{i}.py": f"hash-{i}"},
            raw_summary=f"Step {i}",
        ))

    result = tracker.check_for_loops()
    assert result.detected is False


# ──────────────────────────────────────────────────
# Circular dependency detection tests
# ──────────────────────────────────────────────────


def test_detect_circular_dependency(strict_config):
    """Detects multi-agent circular dependency pattern."""
    tracker = ChannelStateTracker("ch-test", strict_config)

    # Pattern: dev produces state A, qa produces state B, repeat
    pattern = [
        ("dev", {"a.py": "v1"}, []),
        ("qa", {"a.py": "v2"}, []),
        ("dev", {"a.py": "v1"}, []),
        ("qa", {"a.py": "v2"}, []),
    ]

    for i, (agent, files, decisions) in enumerate(pattern):
        tracker.record_snapshot(StateSnapshot(
            msg_id=i,
            agent=agent,
            state_hash=_compute_state_hash(files, decisions),
            file_hashes=files,
            decisions=decisions,
            raw_summary=f"Agent {agent} step",
        ))

    result = tracker.check_for_loops()
    assert result.detected is True
    # Could detect as oscillation or circular dependency
    assert result.loop_type in (
        LoopType.OSCILLATION,
        LoopType.CIRCULAR_DEPENDENCY,
    )
    assert "dev" in result.agents_involved or "qa" in result.agents_involved


# ──────────────────────────────────────────────────
# LoopDetector integration tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detector_no_loop(detector):
    """Normal progress does not trigger loop detection."""
    for i in range(5):
        result = await detector.record_and_check(
            channel="ch-test",
            msg_id=i,
            agent="dev",
            file_hashes={f"file{i}.py": f"hash-{i}"},
            summary=f"Step {i}",
        )
        assert result.detected is False

    assert "ch-test" not in detector.halted_channels


@pytest.mark.asyncio
async def test_detector_halts_channel_on_loop(detector, mock_event_publisher):
    """Loop detection halts the channel and publishes an event."""
    # Create an oscillation
    file_v1 = {"auth.py": "version-A"}
    file_v2 = {"auth.py": "version-B"}

    await detector.record_and_check(
        channel="ch-test", msg_id=0, agent="dev",
        file_hashes=file_v1, summary="v1",
    )
    await detector.record_and_check(
        channel="ch-test", msg_id=1, agent="qa",
        file_hashes=file_v2, summary="v2",
    )
    result = await detector.record_and_check(
        channel="ch-test", msg_id=2, agent="dev",
        file_hashes=file_v1, summary="v1 again",
    )

    assert result.detected is True
    assert "ch-test" in detector.halted_channels
    mock_event_publisher.channel_loop_detected.assert_awaited_once()

    # Verify the event was called with correct args
    call_kwargs = mock_event_publisher.channel_loop_detected.call_args
    assert call_kwargs.kwargs["channel"] == "ch-test"
    assert len(call_kwargs.kwargs["agents_involved"]) >= 1


@pytest.mark.asyncio
async def test_detector_skips_halted_channel(detector, mock_event_publisher):
    """Once halted, further messages on the channel are not checked."""
    detector.halted_channels.add("ch-test")

    result = await detector.record_and_check(
        channel="ch-test", msg_id=99, agent="dev",
        file_hashes={"a.py": "abc"}, summary="ignored",
    )

    assert result.detected is False
    mock_event_publisher.channel_loop_detected.assert_not_awaited()


@pytest.mark.asyncio
async def test_detector_disabled(disabled_config, mock_event_publisher):
    """Disabled loop detection never flags loops."""
    detector = LoopDetector(disabled_config, mock_event_publisher)

    # Would normally trigger oscillation
    file_v1 = {"a.py": "v1"}
    for i in range(5):
        files = file_v1 if i % 2 == 0 else {"a.py": "v2"}
        result = await detector.record_and_check(
            channel="ch-test", msg_id=i, agent="dev",
            file_hashes=files, summary=f"step {i}",
        )
        assert result.detected is False

    mock_event_publisher.channel_loop_detected.assert_not_awaited()


@pytest.mark.asyncio
async def test_detector_resume_channel(detector, mock_event_publisher):
    """Resuming a channel clears halted state and resets tracking."""
    # Trigger a loop
    file_v1 = {"a.py": "v1"}
    file_v2 = {"a.py": "v2"}

    await detector.record_and_check(
        channel="ch-test", msg_id=0, agent="dev",
        file_hashes=file_v1, summary="v1",
    )
    await detector.record_and_check(
        channel="ch-test", msg_id=1, agent="qa",
        file_hashes=file_v2, summary="v2",
    )
    await detector.record_and_check(
        channel="ch-test", msg_id=2, agent="dev",
        file_hashes=file_v1, summary="v1 again",
    )

    assert "ch-test" in detector.halted_channels

    # Resume
    detector.resume_channel("ch-test")
    assert "ch-test" not in detector.halted_channels

    # New messages should be tracked fresh
    result = await detector.record_and_check(
        channel="ch-test", msg_id=3, agent="dev",
        file_hashes={"a.py": "v3"}, summary="fresh start",
    )
    assert result.detected is False


@pytest.mark.asyncio
async def test_detector_remove_channel(detector):
    """Removing a channel stops all tracking for it."""
    await detector.record_and_check(
        channel="ch-test", msg_id=0, agent="dev",
        file_hashes={"a.py": "v1"}, summary="test",
    )
    assert "ch-test" in detector.active_channels

    detector.remove_channel("ch-test")
    assert "ch-test" not in detector.active_channels
    assert "ch-test" not in detector.halted_channels


@pytest.mark.asyncio
async def test_detector_multiple_channels(detector):
    """Detector tracks multiple channels independently."""
    for i in range(3):
        await detector.record_and_check(
            channel="ch-auth", msg_id=i, agent="dev",
            file_hashes={f"auth{i}.py": f"h{i}"}, summary=f"auth {i}",
        )
        await detector.record_and_check(
            channel="ch-db", msg_id=i, agent="dev",
            file_hashes={f"db{i}.py": f"h{i}"}, summary=f"db {i}",
        )

    assert "ch-auth" in detector.active_channels
    assert "ch-db" in detector.active_channels


@pytest.mark.asyncio
async def test_detector_reload_config(detector):
    """Config reload updates the detector and all trackers."""
    # Create a tracker
    await detector.record_and_check(
        channel="ch-test", msg_id=0, agent="dev",
        file_hashes={"a.py": "v1"}, summary="test",
    )

    # Reload with new config
    detector.reload_config({
        "loop_detection": {
            "enabled": True,
            "window_messages": 20,
            "state_repeat_threshold": 5,
        }
    })

    assert detector.config.window_messages == 20
    assert detector.config.state_repeat_threshold == 5

    # Tracker should also have the updated config
    tracker = detector._get_tracker("ch-test")
    assert tracker.config.window_messages == 20


@pytest.mark.asyncio
async def test_detector_empty_file_hashes_and_decisions(detector):
    """Recording with no file hashes or decisions works correctly."""
    result = await detector.record_and_check(
        channel="ch-test",
        msg_id=0,
        agent="dev",
        summary="Just a status update",
    )
    assert result.detected is False


@pytest.mark.asyncio
async def test_direct_repetition_via_detector(detector, mock_event_publisher):
    """End-to-end: direct repetition triggers halt and event."""
    for i in range(3):
        result = await detector.record_and_check(
            channel="ch-test",
            msg_id=i,
            agent="dev",
            file_hashes={f"unique-{i}.py": f"hash-{i}"},  # unique states
            summary="Implement auth module",  # identical summary
        )

    assert result.detected is True
    assert result.loop_type == LoopType.DIRECT_REPETITION
    assert "ch-test" in detector.halted_channels
    mock_event_publisher.channel_loop_detected.assert_awaited_once()


# ──────────────────────────────────────────────────
# Oscillating file identification tests
# ──────────────────────────────────────────────────


def test_identify_oscillating_files(default_config):
    """Correctly identifies files that oscillate between states."""
    tracker = ChannelStateTracker("ch-test", default_config)

    tracker.record_snapshot(StateSnapshot(
        msg_id=0, agent="dev",
        state_hash="h0",
        file_hashes={"auth.py": "v1", "utils.py": "u1"},
        raw_summary="step 0",
    ))
    tracker.record_snapshot(StateSnapshot(
        msg_id=1, agent="qa",
        state_hash="h1",
        file_hashes={"auth.py": "v2", "utils.py": "u2"},
        raw_summary="step 1",
    ))
    tracker.record_snapshot(StateSnapshot(
        msg_id=2, agent="dev",
        state_hash="h2",
        file_hashes={"auth.py": "v1", "utils.py": "u3"},  # auth.py reverts
        raw_summary="step 2",
    ))

    oscillating = tracker._identify_oscillating_files()
    assert "auth.py" in oscillating
    assert "utils.py" not in oscillating  # u1 → u2 → u3, no repeat


# ──────────────────────────────────────────────────
# Edge case tests
# ──────────────────────────────────────────────────


def test_check_loops_with_single_snapshot(default_config):
    """A single snapshot never triggers a loop."""
    tracker = ChannelStateTracker("ch-test", default_config)
    tracker.record_snapshot(StateSnapshot(
        msg_id=0, agent="dev", state_hash="abc", raw_summary="hello",
    ))
    result = tracker.check_for_loops()
    assert result.detected is False


def test_check_loops_with_empty_tracker(default_config):
    """Empty tracker returns no loop."""
    tracker = ChannelStateTracker("ch-test", default_config)
    result = tracker.check_for_loops()
    assert result.detected is False


def test_resume_nonexistent_channel_no_error(detector):
    """Resuming a channel that was never tracked does not raise."""
    detector.resume_channel("ch-nonexistent")  # should not raise


def test_remove_nonexistent_channel_no_error(detector):
    """Removing a channel that was never tracked does not raise."""
    detector.remove_channel("ch-nonexistent")  # should not raise


def test_loop_detection_result_defaults():
    """LoopDetectionResult defaults are sensible."""
    result = LoopDetectionResult(detected=False)
    assert result.loop_type is None
    assert result.description == ""
    assert result.agents_involved == []
    assert result.channel == ""
    assert result.repeated_states == []
    assert result.repetition_count == 0
```

---

## Integration Points

The LoopDetector integrates with the PA's event dispatch loop (FAITH-016) and the event system (FAITH-008).

```python
# PA initialisation (FAITH-016 PAEventDispatcher):
from faith.pa.loop_detector import LoopDetector, LoopDetectionConfig

# Load config from .faith/system.yaml
system_config = yaml.safe_load(system_yaml_path.read_text())
loop_config = LoopDetectionConfig.from_system_config(system_config)
loop_detector = LoopDetector(loop_config, event_publisher)
```

```python
# PA event handler — called after each message on an active channel:
result = await loop_detector.record_and_check(
    channel=message.channel,
    msg_id=message.msg_id,
    agent=message.from_agent,
    file_hashes=extract_file_hashes(message),  # SHA256 of files in message
    decisions=extract_decisions(message),
    summary=message.summary,
)

if result.detected:
    # Channel is already halted and event published by the detector.
    # Surface the summary to the user via Web UI WebSocket.
    await websocket_manager.send_loop_alert(
        channel=result.channel,
        description=result.description,
        agents_involved=result.agents_involved,
        loop_type=result.loop_type.value,
    )
```

```python
# PA handles user "Resume with guidance" action:
loop_detector.resume_channel("ch-auth-phase3")
# PA injects user guidance into the channel and resumes agents
```

```python
# Config hot-reload handler (FAITH-004):
def on_system_config_changed(new_config: dict):
    loop_detector.reload_config(new_config)
```

---

## Acceptance Criteria

1. `LoopDetectionConfig.from_system_config()` correctly parses the `loop_detection` key from `.faith/system.yaml`, falling back to defaults for missing keys.
2. `_compute_state_hash()` produces deterministic hashes regardless of dict ordering; identical states always produce identical hashes.
3. `ChannelStateTracker` maintains a rolling window of snapshots bounded by `window_messages`, discarding the oldest entries when the window is exceeded.
4. **Direct repetition** is detected when an agent sends the same message summary more than `state_repeat_threshold` times within the window.
5. **Oscillation** is detected when the same state hash (derived from file SHA256 digests and decisions) appears more than `state_repeat_threshold` times within the window.
6. **Circular dependency** is detected when a repeating multi-agent sub-sequence of (agent, state_hash) pairs appears more than `state_repeat_threshold` times.
7. `LoopDetector.record_and_check()` halts the channel and publishes a `channel:loop_detected` event (via `EventPublisher.channel_loop_detected()`) when any loop type is detected.
8. Halted channels are skipped on subsequent `record_and_check()` calls until explicitly resumed.
9. `resume_channel()` clears the halted state and resets the channel tracker so detection starts fresh.
10. `remove_channel()` stops all tracking for a closed channel.
11. `reload_config()` updates the detector and all existing channel trackers with new configuration values.
12. When `enabled` is `false` in the config, no loop detection is performed and no events are published.
13. All 30 tests in `tests/test_loop_detector.py` pass, covering hash computation, config loading, all three loop types, channel lifecycle, config reload, and edge cases.

---

## Notes for Implementer

- **FAITH-016 dependency**: This task depends on FAITH-016 (PA Event Dispatcher & Intervention Logic) because the `LoopDetector` is instantiated and called from within the PA's event dispatch loop. The PA processes messages from `system-events`, extracts file hashes and decisions from compact protocol messages, and passes them to `record_and_check()`. The LoopDetector itself has no Redis dependency — it delegates event publishing to the `EventPublisher` injected at construction time.
- **File SHA256 extraction**: The PA is responsible for extracting file SHA256 digests from incoming messages. When an agent reports `files: [auth.py, test_refresh.py]` in a compact protocol message, the PA (or the Filesystem tool via `file:changed` events) provides the current SHA256 of each file. The LoopDetector receives pre-computed hashes — it does not read files itself.
- **Decision extraction**: Decisions are extracted from compact protocol messages by the PA. Messages with `type: decision` or those containing a `decisions` field in their data are parsed into string lists. The exact extraction logic lives in the PA's message processing pipeline (FAITH-016), not in the LoopDetector.
- **Web UI surfacing**: The LoopDetector publishes `channel:loop_detected` events. The Web UI (FAITH-036 onwards) subscribes to these events via WebSocket and renders the loop alert card shown in FRS Section 11.6. The detector's `LoopDetectionResult.description` field provides the human-readable text for the alert.
- **Agents are not informed**: Per FRS Section 3.2.2, agents are not notified of loop detection. Only the user sees the alert. The PA halts the channel (stops forwarding messages) but does not send a message to the agents explaining why.
- **Config hot-reload**: When `.faith/system.yaml` changes, the config watcher (FAITH-004) calls `reload_config()`. This updates the window size and threshold for all tracked channels immediately. Existing snapshot data is preserved — only the detection parameters change.
- **No persistence**: Loop detection state is entirely in-memory. If the PA restarts, tracking starts fresh. This is acceptable because a PA restart also resets channel state.
- **Thread safety**: The PA runs a single async event loop. All calls to `record_and_check()` happen sequentially within that loop, so no locking is required.
