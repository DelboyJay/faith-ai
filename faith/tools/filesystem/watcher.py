"""Polling-based file watcher for subscribed filesystem paths."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(slots=True)
class FileSubscription:
    agent_id: str
    pattern: str
    events: list[str]
    mount_root: Path
    session_scoped: bool = False


@dataclass(slots=True)
class FileChangeEvent:
    agent_id: str
    event: str
    path: str


@dataclass(slots=True)
class FileSnapshot:
    path: str
    sha256: str
    size: int
    mtime: float


class FileWatcher:
    def __init__(self) -> None:
        self._subscriptions: list[FileSubscription] = []
        self._snapshots: dict[tuple[str, str], FileSnapshot] = {}

    def add_subscription(self, subscription: FileSubscription) -> None:
        self._subscriptions.append(subscription)

    def remove_session_subscriptions(self) -> None:
        self._subscriptions = [sub for sub in self._subscriptions if not sub.session_scoped]

    def _hash_file(self, path: Path) -> FileSnapshot | None:
        if not path.exists() or not path.is_file():
            return None
        data = path.read_bytes()
        stat = path.stat()
        return FileSnapshot(
            path=str(path),
            sha256=hashlib.sha256(data).hexdigest(),
            size=stat.st_size,
            mtime=stat.st_mtime,
        )

    def _collect_paths(self, subscription: FileSubscription) -> list[Path]:
        pattern = subscription.pattern.replace("\\", "/")
        parts = pattern.split("/", 1)
        relative = parts[1] if len(parts) > 1 else ""
        if not relative:
            return []
        return [path for path in subscription.mount_root.glob(relative) if path.is_file()]

    def poll_once(self) -> list[FileChangeEvent]:
        events: list[FileChangeEvent] = []
        seen_keys: set[tuple[str, str]] = set()
        for subscription in self._subscriptions:
            for path in self._collect_paths(subscription):
                key = (subscription.agent_id, str(path))
                seen_keys.add(key)
                new_snapshot = self._hash_file(path)
                old_snapshot = self._snapshots.get(key)
                if new_snapshot is None:
                    continue
                relative = PurePosixPath(
                    path.relative_to(subscription.mount_root).as_posix()
                ).as_posix()
                if old_snapshot is None:
                    self._snapshots[key] = new_snapshot
                    if "file:created" in subscription.events:
                        events.append(
                            FileChangeEvent(subscription.agent_id, "file:created", relative)
                        )
                    continue
                if old_snapshot.sha256 != new_snapshot.sha256:
                    self._snapshots[key] = new_snapshot
                    if "file:changed" in subscription.events:
                        events.append(
                            FileChangeEvent(subscription.agent_id, "file:changed", relative)
                        )
        removed_keys = [key for key in self._snapshots if key not in seen_keys]
        for key in removed_keys:
            agent_id, absolute = key
            relative = Path(absolute).name
            events.append(FileChangeEvent(agent_id, "file:deleted", relative))
            del self._snapshots[key]
        return events
