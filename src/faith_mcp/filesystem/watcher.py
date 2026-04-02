"""
Description:
    Poll subscribed filesystem paths for changes in the FAITH filesystem MCP
    server.

Requirements:
    - Track created, changed, and deleted file events for subscribed patterns.
    - Keep per-agent file snapshots between polls.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(slots=True)
class FileSubscription:
    """
    Description:
        Describe one agent file-watch subscription.

    Requirements:
        - Preserve the agent, pattern, subscribed events, mount root, and
          session-scoped flag.
    """

    agent_id: str
    pattern: str
    events: list[str]
    mount_root: Path
    session_scoped: bool = False


@dataclass(slots=True)
class FileChangeEvent:
    """
    Description:
        Describe one detected file change emitted by the watcher.

    Requirements:
        - Preserve the agent, event name, and mount-relative path.
    """

    agent_id: str
    event: str
    path: str


@dataclass(slots=True)
class FileSnapshot:
    """
    Description:
        Capture the file metadata used to detect changes between polls.

    Requirements:
        - Preserve the absolute path, content hash, size, and modification time.
    """

    path: str
    sha256: str
    size: int
    mtime: float


class FileWatcher:
    """
    Description:
        Poll subscribed filesystem paths and emit change events.

    Requirements:
        - Preserve subscriptions across polls until explicitly removed.
        - Track per-agent snapshots so changes can be detected incrementally.
    """

    def __init__(self) -> None:
        """
        Description:
            Initialise the watcher with no subscriptions and no snapshots.

        Requirements:
            - Start with empty subscription and snapshot stores.
        """
        self._subscriptions: list[FileSubscription] = []
        self._snapshots: dict[tuple[str, str], FileSnapshot] = {}

    def add_subscription(self, subscription: FileSubscription) -> None:
        """
        Description:
            Add one file-watch subscription.

        Requirements:
            - Preserve the subscription order used during polling.

        :param subscription: Subscription that should be tracked.
        """
        self._subscriptions.append(subscription)

    def remove_session_subscriptions(self) -> None:
        """
        Description:
            Remove subscriptions that are scoped only to the current session.

        Requirements:
            - Preserve non-session subscriptions unchanged.
        """
        self._subscriptions = [sub for sub in self._subscriptions if not sub.session_scoped]

    def _hash_file(self, path: Path) -> FileSnapshot | None:
        """
        Description:
            Build a file snapshot for one on-disk file.

        Requirements:
            - Return `None` when the path is missing or not a regular file.
            - Capture the file hash, size, and modification time.

        :param path: File path to hash.
        :returns: File snapshot or `None` when the file cannot be watched.
        """
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
        """
        Description:
            Expand one subscription pattern into the matching file paths.

        Requirements:
            - Interpret the pattern as `mount/pattern` and search relative to the
              mount root.
            - Return only regular files.

        :param subscription: Subscription whose pattern should be expanded.
        :returns: Matching file paths under the subscription mount root.
        """
        pattern = subscription.pattern.replace("\\", "/")
        parts = pattern.split("/", 1)
        relative = parts[1] if len(parts) > 1 else ""
        if not relative:
            return []
        return [path for path in subscription.mount_root.glob(relative) if path.is_file()]

    def poll_once(self) -> list[FileChangeEvent]:
        """
        Description:
            Poll all subscriptions once and return any detected file changes.

        Requirements:
            - Emit created, changed, and deleted events according to each
              subscription's event list.
            - Update the snapshot store to reflect the latest poll result.

        :returns: Detected file-change events for the current poll.
        """
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
