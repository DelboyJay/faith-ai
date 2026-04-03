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
from typing import Any


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

    def load_static_subscriptions(
        self,
        agents_config: dict[str, dict[str, Any]],
        mount_roots: dict[str, Path],
    ) -> None:
        """
        Description:
            Load file-watch subscriptions declared in agent configuration.

        Requirements:
            - Replace only the non-session subscriptions already tracked by the watcher.
            - Ignore watches referencing unknown mounts.

        :param agents_config: Mapping of agent IDs to parsed agent config payloads.
        :param mount_roots: Mapping of mount names to their resolved host roots.
        """

        dynamic_subscriptions = [sub for sub in self._subscriptions if sub.session_scoped]
        static_subscriptions: list[FileSubscription] = []
        for agent_id, config in agents_config.items():
            for watch in config.get("file_watches", []):
                pattern = str(watch.get("pattern", "")).replace("\\", "/").lstrip("/")
                if not pattern:
                    continue
                mount_name = pattern.split("/", 1)[0]
                mount_root = mount_roots.get(mount_name)
                if mount_root is None:
                    continue
                static_subscriptions.append(
                    FileSubscription(
                        agent_id=agent_id,
                        pattern=pattern,
                        events=list(watch.get("events", ["file:changed"])),
                        mount_root=mount_root,
                        session_scoped=False,
                    )
                )
        self._subscriptions = static_subscriptions + dynamic_subscriptions

    def remove_session_subscriptions(self) -> None:
        """
        Description:
            Remove subscriptions that are scoped only to the current session.

        Requirements:
            - Preserve non-session subscriptions unchanged.
        """
        self._subscriptions = [sub for sub in self._subscriptions if not sub.session_scoped]
        self._snapshots = {
            key: snapshot
            for key, snapshot in self._snapshots.items()
            if any(sub.agent_id == key[0] for sub in self._subscriptions)
        }

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

    def _snapshot_relative_path(self, absolute_path: str, mount_root: Path) -> str:
        """
        Description:
            Convert one stored absolute file path back into a mount-relative path.

        Requirements:
            - Preserve nested relative paths rather than truncating to the basename.

        :param absolute_path: Absolute path recorded in the snapshot store.
        :param mount_root: Mount root used to relativise the stored path.
        :returns: Mount-relative POSIX path.
        """

        return PurePosixPath(Path(absolute_path).resolve().relative_to(mount_root.resolve()).as_posix()).as_posix()

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
        subscription_roots: dict[tuple[str, str], Path] = {}
        for subscription in self._subscriptions:
            for path in self._collect_paths(subscription):
                key = (subscription.agent_id, str(path))
                seen_keys.add(key)
                subscription_roots[key] = subscription.mount_root
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
            root = subscription_roots.get(key)
            if root is None:
                matching_root = None
                for subscription in self._subscriptions:
                    if subscription.agent_id != agent_id:
                        continue
                    candidate = Path(absolute)
                    try:
                        candidate.resolve().relative_to(subscription.mount_root.resolve())
                    except ValueError:
                        continue
                    matching_root = subscription.mount_root
                    break
                root = matching_root or Path(absolute).parent
            relative = self._snapshot_relative_path(absolute, root)
            subscribed_delete = any(
                sub.agent_id == agent_id and "file:deleted" in sub.events
                for sub in self._subscriptions
            )
            if subscribed_delete:
                events.append(FileChangeEvent(agent_id, "file:deleted", relative))
            del self._snapshots[key]
        return events
