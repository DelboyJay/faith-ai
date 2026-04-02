"""
Description:
    Detect repeating channel state patterns so the PA can halt looping work.

Requirements:
    - Identify direct repetition, oscillation, and circular dependency loops.
    - Publish loop-detected events when configured to do so.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from faith_shared.protocol.events import EventPublisher


class LoopType(str, Enum):
    """
    Description:
        Define the loop categories recognised by the PA loop detector.

    Requirements:
        - Preserve the canonical loop type names used in loop-detection results.
    """

    DIRECT_REPETITION = "direct_repetition"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    OSCILLATION = "oscillation"


@dataclass(slots=True)
class StateSnapshot:
    """
    Description:
        Capture the high-level channel state used for loop detection.

    Requirements:
        - Preserve message, agent, state-hash, file-hash, decision, and summary
          data for one step in the channel history.
    """

    msg_id: int = 0
    agent: str = "unknown"
    state_hash: str = ""
    file_hashes: dict[str, str] = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)
    raw_summary: str = ""


@dataclass(slots=True)
class _Snapshot:
    """
    Description:
        Store the compact loop-detection fields kept in rolling history.

    Requirements:
        - Preserve agent, summary-hash, and state-hash data for one history item.
    """

    agent_id: str
    summary_hash: str
    state_hash: str


@dataclass(slots=True)
class LoopDetectionResult:
    """
    Description:
        Describe the outcome of one loop-detection pass.

    Requirements:
        - Report whether a loop was detected and, when applicable, include type,
          description, agents, repeated states, and repetition count.
    """

    detected: bool
    loop_type: LoopType | str | None = None
    description: str = ""
    agents_involved: list[str] = field(default_factory=list)
    channel: str = ""
    repeated_states: list[str] = field(default_factory=list)
    repetition_count: int = 0


@dataclass(slots=True)
class LoopDetectionConfig:
    """
    Description:
        Define the runtime settings for PA loop detection.

    Requirements:
        - Preserve enablement, history window size, and repeat threshold.
    """

    enabled: bool = True
    window_messages: int = 10
    state_repeat_threshold: int = 2

    @classmethod
    def from_system_config(cls, system_config: dict[str, Any] | Any) -> LoopDetectionConfig:
        """
        Description:
            Build loop-detection settings from the broader system config object.

        Requirements:
            - Accept either a plain mapping or an object exposing
              `loop_detection` attributes.
            - Fall back to defaults when the supplied structure is incomplete.

        :param system_config: System config mapping or object.
        :returns: Normalised loop-detection settings.
        """
        raw = system_config
        if hasattr(system_config, "loop_detection"):
            raw = getattr(system_config, "loop_detection")
        elif isinstance(system_config, dict):
            raw = system_config.get("loop_detection", {})
        if not isinstance(raw, dict):
            raw = {
                "enabled": getattr(raw, "enabled", True),
                "window_messages": getattr(raw, "window_messages", 10),
                "state_repeat_threshold": getattr(raw, "state_repeat_threshold", 2),
            }
        return cls(
            enabled=bool(raw.get("enabled", True)),
            window_messages=max(1, int(raw.get("window_messages", 10))),
            state_repeat_threshold=max(1, int(raw.get("state_repeat_threshold", 2))),
        )


def _compute_state_hash(file_hashes: dict[str, str], decisions: list[str]) -> str:
    """
    Description:
        Compute a stable hash for one channel state.

    Requirements:
        - Sort file hashes and decisions so logically equivalent states hash the
          same way.

    :param file_hashes: File-path to digest mapping for the channel state.
    :param decisions: Recorded decision labels for the channel state.
    :returns: Stable SHA-256 hash for the state payload.
    """
    payload = json.dumps(
        {"files": dict(sorted(file_hashes.items())), "decisions": sorted(decisions)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_message_hash(summary: str, agent: str) -> str:
    """
    Description:
        Compute a stable hash for one agent summary message.

    Requirements:
        - Combine agent identity and trimmed summary text so repeated outputs are
          detected reliably.

    :param summary: Raw summary text emitted by the agent.
    :param agent: Agent that emitted the summary.
    :returns: Stable SHA-256 hash for the message payload.
    """
    payload = json.dumps(
        {"agent": agent, "summary": summary.strip()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ChannelStateTracker:
    """
    Description:
        Track recent channel state so one channel can be checked for loops.

    Requirements:
        - Keep bounded rolling history based on the configured message window.
        - Detect direct repetition, oscillation, and circular dependency
          patterns.
    """

    def __init__(
        self,
        channel: str | LoopDetectionConfig,
        config: LoopDetectionConfig | None = None,
    ) -> None:
        """
        Description:
            Initialise the tracker for one channel or one detached config.

        Requirements:
            - Support the shorthand constructor used by older tests that pass
              only the config object.
            - Size the rolling history deques from the configured message
              window.

        :param channel: Channel name or the config object for shorthand usage.
        :param config: Explicit loop-detection config when the channel is passed
            separately.
        """
        if isinstance(channel, LoopDetectionConfig) and config is None:
            self.channel = ""
            self.config = channel
        else:
            self.channel = str(channel)
            self.config = config or LoopDetectionConfig()
        self.snapshots: deque[_Snapshot] = deque(maxlen=self.config.window_messages)
        self._states: deque[StateSnapshot] = deque(maxlen=self.config.window_messages)

    def add(self, snapshot: _Snapshot) -> None:
        """
        Description:
            Append one compact snapshot to the rolling history.

        Requirements:
            - Preserve insertion order inside the bounded deque.

        :param snapshot: Compact snapshot to record.
        """
        self.snapshots.append(snapshot)

    def record_snapshot(self, snapshot: StateSnapshot) -> None:
        """
        Description:
            Append one rich state snapshot and its derived compact snapshot.

        Requirements:
            - Preserve both the detailed state and the hashed compact state.

        :param snapshot: Rich state snapshot to record.
        """
        self._states.append(snapshot)
        self.snapshots.append(
            _Snapshot(
                agent_id=snapshot.agent,
                summary_hash=_compute_message_hash(snapshot.raw_summary, snapshot.agent),
                state_hash=snapshot.state_hash,
            )
        )

    def clear(self) -> None:
        """
        Description:
            Remove all stored history for the tracker.

        Requirements:
            - Clear both the compact and detailed history deques.
        """
        self.snapshots.clear()
        self._states.clear()

    def _check_direct_repetition(self) -> LoopDetectionResult:
        """
        Description:
            Detect repeated identical outputs from the same agent.

        Requirements:
            - Count repeated summary hashes per agent.
            - Return a populated detection result when the repeat threshold is
              exceeded.

        :returns: Loop-detection result for the direct-repetition check.
        """
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for snapshot in self.snapshots:
            counts[(snapshot.agent_id, snapshot.summary_hash)] += 1
        for (agent, summary_hash), count in counts.items():
            if count > self.config.state_repeat_threshold:
                return LoopDetectionResult(
                    detected=True,
                    loop_type=LoopType.DIRECT_REPETITION,
                    description=f"Agent {agent} repeated the same output {count} times.",
                    agents_involved=[agent],
                    channel=self.channel,
                    repeated_states=[summary_hash],
                    repetition_count=count,
                )
        return LoopDetectionResult(detected=False)

    def _identify_oscillating_files(self) -> list[str]:
        """
        Description:
            Identify files whose hashes repeat across tracked states.

        Requirements:
            - Return file paths whose recorded digests show repeated values.

        :returns: Sorted list of oscillating file paths.
        """
        histories: dict[str, list[str]] = defaultdict(list)
        for snapshot in self._states:
            for path, digest in snapshot.file_hashes.items():
                histories[path].append(digest)
        return sorted(
            path
            for path, values in histories.items()
            if len(values) >= 2 and len(set(values)) < len(values)
        )

    def _check_oscillation(self) -> LoopDetectionResult:
        """
        Description:
            Detect repeating channel states that indicate oscillation.

        Requirements:
            - Count repeated state hashes across the rolling history.
            - Include the participating agents and oscillating files in the
              detection result.

        :returns: Loop-detection result for the oscillation check.
        """
        counts: dict[str, int] = defaultdict(int)
        agents: dict[str, set[str]] = defaultdict(set)
        for snapshot in self.snapshots:
            counts[snapshot.state_hash] += 1
            agents[snapshot.state_hash].add(snapshot.agent_id)
        for state_hash, count in counts.items():
            if count > self.config.state_repeat_threshold:
                return LoopDetectionResult(
                    detected=True,
                    loop_type=LoopType.OSCILLATION,
                    description=(
                        f"State reverted {count} times on {self.channel or 'channel'}. "
                        f"Files involved: {', '.join(self._identify_oscillating_files()) or 'unknown'}."
                    ),
                    agents_involved=sorted(agents[state_hash]),
                    channel=self.channel,
                    repeated_states=[state_hash],
                    repetition_count=count,
                )
        return LoopDetectionResult(detected=False)

    def _check_circular_dependency(self) -> LoopDetectionResult:
        """
        Description:
            Detect repeating multi-agent state patterns that indicate a circular
            dependency.

        Requirements:
            - Require at least two agents in the repeated pattern.
            - Count how many times the same tail pattern appears in the tracked
              history.

        :returns: Loop-detection result for the circular-dependency check.
        """
        if len(self.snapshots) < 4:
            return LoopDetectionResult(detected=False)
        sequence = [(snapshot.agent_id, snapshot.state_hash) for snapshot in self.snapshots]
        max_pattern_len = len(sequence) // 2
        for pattern_len in range(2, max_pattern_len + 1):
            pattern = tuple(sequence[-pattern_len:])
            matches = 0
            for index in range(len(sequence) - pattern_len + 1):
                if tuple(sequence[index : index + pattern_len]) == pattern:
                    matches += 1
            agents = sorted({agent for agent, _state_hash in pattern})
            if len(agents) > 1 and matches > self.config.state_repeat_threshold:
                return LoopDetectionResult(
                    detected=True,
                    loop_type=LoopType.CIRCULAR_DEPENDENCY,
                    description=f"Circular dependency detected between {', '.join(agents)}.",
                    agents_involved=agents,
                    channel=self.channel,
                    repeated_states=[state_hash for _agent, state_hash in pattern],
                    repetition_count=matches,
                )
        return LoopDetectionResult(detected=False)

    def check_for_loops(self) -> LoopDetectionResult:
        """
        Description:
            Run all enabled loop checks against the current tracked history.

        Requirements:
            - Return no detection when loop detection is disabled or the history
              is too short.
            - Return the first matching loop result in priority order.

        :returns: Final loop-detection result.
        """
        if not self.config.enabled or len(self.snapshots) < 2:
            return LoopDetectionResult(detected=False)
        for checker in (
            self._check_direct_repetition,
            self._check_oscillation,
            self._check_circular_dependency,
        ):
            result = checker()
            if result.detected:
                return result
        return LoopDetectionResult(detected=False)


class LoopDetector:
    """
    Description:
        Coordinate loop detection across all active PA channels.

    Requirements:
        - Maintain one tracker per channel.
        - Halt looping channels and optionally publish loop-detected events.

    :param config: Loop-detection settings to apply.
    :param event_publisher: Optional event publisher used for loop notifications.
    """

    def __init__(
        self,
        config: LoopDetectionConfig,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        """
        Description:
            Initialise the multi-channel loop detector.

        Requirements:
            - Start with no tracked channels and no halted channels.

        :param config: Loop-detection settings to apply.
        :param event_publisher: Optional event publisher used for loop
            notifications.
        """
        self.config = config
        self.event_publisher = event_publisher
        self._channels: dict[str, ChannelStateTracker] = {}
        self._trackers = self._channels
        self.halted_channels: set[str] = set()
        self._halted = self.halted_channels

    @property
    def active_channels(self) -> set[str]:
        """
        Description:
            Return the set of currently tracked channels.

        Requirements:
            - Reflect the keys in the internal tracker map.

        :returns: Names of active tracked channels.
        """
        return set(self._channels)

    def _get_tracker(self, channel: str) -> ChannelStateTracker:
        """
        Description:
            Return the tracker for one channel, creating it when necessary.

        Requirements:
            - Lazily create a tracker the first time a channel is seen.

        :param channel: Channel whose tracker should be returned.
        :returns: Channel state tracker for the requested channel.
        """
        tracker = self._channels.get(channel)
        if tracker is None:
            tracker = ChannelStateTracker(channel, self.config)
            self._channels[channel] = tracker
        return tracker

    async def record_and_check(
        self,
        channel: str,
        *,
        msg_id: int = 0,
        agent: str | None = None,
        agent_id: str | None = None,
        file_hashes: dict[str, str] | None = None,
        decisions: list[str] | None = None,
        summary: str = "",
        message_summary: str | None = None,
    ) -> LoopDetectionResult:
        """
        Description:
            Record one channel state update and immediately evaluate it for
            loops.

        Requirements:
            - Ignore recording when loop detection is disabled or the channel is
              already halted.
            - Halt the channel and publish an event when a loop is detected.

        :param channel: Channel receiving the new state update.
        :param msg_id: Message identifier associated with the update.
        :param agent: Optional agent name.
        :param agent_id: Optional alternate agent identifier.
        :param file_hashes: Optional file-hash mapping for the channel state.
        :param decisions: Optional list of decision labels for the channel state.
        :param summary: Summary text for the channel state.
        :param message_summary: Optional alternate summary text.
        :returns: Loop-detection result for the recorded state.
        """
        if not self.config.enabled or channel in self.halted_channels:
            return LoopDetectionResult(detected=False)
        sender = agent or agent_id or "unknown"
        summary_text = message_summary if message_summary is not None else summary
        snapshot = StateSnapshot(
            msg_id=msg_id,
            agent=sender,
            state_hash=_compute_state_hash(file_hashes or {}, decisions or []),
            file_hashes=file_hashes or {},
            decisions=decisions or [],
            raw_summary=summary_text,
        )
        tracker = self._get_tracker(channel)
        tracker.record_snapshot(snapshot)
        result = tracker.check_for_loops()
        if result.detected:
            self.halted_channels.add(channel)
            if self.event_publisher is not None:
                await self.event_publisher.channel_loop_detected(
                    channel=channel,
                    description=result.description,
                    agents_involved=result.agents_involved,
                )
        return result

    def resume_channel(self, channel: str) -> None:
        """
        Description:
            Resume a halted channel and clear its tracked history.

        Requirements:
            - Remove the channel from the halted set.
            - Clear any stored tracker state when a tracker exists.

        :param channel: Channel that should be resumed.
        """
        self.halted_channels.discard(channel)
        if channel in self._channels:
            self._channels[channel].clear()

    def remove_channel(self, channel: str) -> None:
        """
        Description:
            Remove all tracking state for one channel.

        Requirements:
            - Remove the channel from both the halted set and the tracker map.

        :param channel: Channel that should be removed.
        """
        self.halted_channels.discard(channel)
        self._channels.pop(channel, None)

    def reload_config(self, config: LoopDetectionConfig | dict[str, Any]) -> None:
        """
        Description:
            Replace the loop-detection settings while preserving recent tracker
            history.

        Requirements:
            - Accept either a ready-made config object or a raw config mapping.
            - Truncate preserved history to the new message-window size.

        :param config: New loop-detection config object or raw mapping.
        """
        self.config = (
            config
            if isinstance(config, LoopDetectionConfig)
            else LoopDetectionConfig.from_system_config(config)
        )
        for channel, tracker in list(self._channels.items()):
            new_tracker = ChannelStateTracker(channel, self.config)
            old_states = list(tracker._states)[-self.config.window_messages :]
            old_snapshots = list(tracker.snapshots)[-self.config.window_messages :]
            for state in old_states:
                new_tracker._states.append(state)
            for snapshot in old_snapshots:
                new_tracker.snapshots.append(snapshot)
            self._channels[channel] = new_tracker
