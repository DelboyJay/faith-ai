"""Loop detection for PA channels."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from faith_shared.protocol.events import EventPublisher


class LoopType(str, Enum):
    DIRECT_REPETITION = "direct_repetition"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    OSCILLATION = "oscillation"


@dataclass(slots=True)
class StateSnapshot:
    msg_id: int = 0
    agent: str = "unknown"
    state_hash: str = ""
    file_hashes: dict[str, str] = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)
    raw_summary: str = ""


@dataclass(slots=True)
class _Snapshot:
    agent_id: str
    summary_hash: str
    state_hash: str


@dataclass(slots=True)
class LoopDetectionResult:
    detected: bool
    loop_type: LoopType | str | None = None
    description: str = ""
    agents_involved: list[str] = field(default_factory=list)
    channel: str = ""
    repeated_states: list[str] = field(default_factory=list)
    repetition_count: int = 0


@dataclass(slots=True)
class LoopDetectionConfig:
    enabled: bool = True
    window_messages: int = 10
    state_repeat_threshold: int = 2

    @classmethod
    def from_system_config(cls, system_config: dict[str, Any] | Any) -> LoopDetectionConfig:
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
    payload = json.dumps(
        {"files": dict(sorted(file_hashes.items())), "decisions": sorted(decisions)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_message_hash(summary: str, agent: str) -> str:
    payload = json.dumps(
        {"agent": agent, "summary": summary.strip()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ChannelStateTracker:
    def __init__(
        self, channel: str | LoopDetectionConfig, config: LoopDetectionConfig | None = None
    ) -> None:
        if isinstance(channel, LoopDetectionConfig) and config is None:
            self.channel = ""
            self.config = channel
        else:
            self.channel = str(channel)
            self.config = config or LoopDetectionConfig()
        self.snapshots: deque[_Snapshot] = deque(maxlen=self.config.window_messages)
        self._states: deque[StateSnapshot] = deque(maxlen=self.config.window_messages)

    def add(self, snapshot: _Snapshot) -> None:
        self.snapshots.append(snapshot)

    def record_snapshot(self, snapshot: StateSnapshot) -> None:
        self._states.append(snapshot)
        self.snapshots.append(
            _Snapshot(
                agent_id=snapshot.agent,
                summary_hash=_compute_message_hash(snapshot.raw_summary, snapshot.agent),
                state_hash=snapshot.state_hash,
            )
        )

    def clear(self) -> None:
        self.snapshots.clear()
        self._states.clear()

    def _check_direct_repetition(self) -> LoopDetectionResult:
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
            agents = sorted({agent for agent, _ in pattern})
            if len(agents) > 1 and matches > self.config.state_repeat_threshold:
                return LoopDetectionResult(
                    detected=True,
                    loop_type=LoopType.CIRCULAR_DEPENDENCY,
                    description=f"Circular dependency detected between {', '.join(agents)}.",
                    agents_involved=agents,
                    channel=self.channel,
                    repeated_states=[state_hash for _, state_hash in pattern],
                    repetition_count=matches,
                )
        return LoopDetectionResult(detected=False)

    def check_for_loops(self) -> LoopDetectionResult:
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
    def __init__(
        self, config: LoopDetectionConfig, event_publisher: EventPublisher | None = None
    ) -> None:
        self.config = config
        self.event_publisher = event_publisher
        self._channels: dict[str, ChannelStateTracker] = {}
        self._trackers = self._channels
        self.halted_channels: set[str] = set()
        self._halted = self.halted_channels

    @property
    def active_channels(self) -> set[str]:
        return set(self._channels)

    def _get_tracker(self, channel: str) -> ChannelStateTracker:
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
        self.halted_channels.discard(channel)
        if channel in self._channels:
            self._channels[channel].clear()

    def remove_channel(self, channel: str) -> None:
        self.halted_channels.discard(channel)
        self._channels.pop(channel, None)

    def reload_config(self, config: LoopDetectionConfig | dict[str, Any]) -> None:
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

