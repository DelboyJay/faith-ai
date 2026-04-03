# FAITH-022 — Filesystem MCP Server

**Phase:** 6 — MCP Tool Servers (Built-in)
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** IN PROGRESS
**Dependencies:** FAITH-003, FAITH-008
**FRS Reference:** Section 4.3, 5.4

---

## Objective

Implement the filesystem MCP server that runs in a dedicated Docker container (`tool-fs-container`). This is a security-first project-workspace boundary, not a general-purpose filesystem platform. It provides the file operations FAITH needs via named mount points with layered permission resolution, a hardcoded deny list for secrets, symlink escape prevention, file size enforcement, and SHA256-based file watching that publishes change events to subscribing agents. File history (versioning) is deferred to FAITH-023.

---

## Architecture

```
containers/tool-fs/
├── Dockerfile               ← Alpine + Python 3.12, MCP SDK
└── entrypoint.py            ← Starts the MCP server process

faith/tools/filesystem/
├── __init__.py
├── server.py                ← MCP server setup, tool registration
├── mounts.py                ← Mount configuration loading & path resolution
├── permissions.py           ← Permission resolution engine
├── deny_list.py             ← Hardcoded deny list (secrets protection)
├── symlinks.py              ← Symlink escape detection
├── watcher.py               ← File watch poller (SHA256, 5s interval)
└── operations.py            ← read, write, list, stat, delete, mkdir MCP tools

tests/test_filesystem_server.py
```

---

## Files to Create

### 1. `faith/tools/filesystem/deny_list.py`

```python
"""Hardcoded deny list — defence-in-depth secrets protection.

These paths can never be read, written, listed, or stat'd by any agent,
regardless of mount configuration. Even if a mount misconfiguration
exposes the path, the tool refuses and logs the attempt.

FRS Reference: Section 5.4
"""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath

logger = logging.getLogger("faith.tools.filesystem.deny_list")

# Exact paths (relative to any mount root) that are always blocked.
_EXACT_DENY: set[str] = {
    "config/secrets.yaml",
    "config/.env",
}

# Glob-style patterns matched against any path component or full path.
# These catch secrets files regardless of where they appear.
_PATTERN_DENY: list[re.Pattern] = [
    re.compile(r"(^|/)secrets\.yaml$"),
    re.compile(r"(^|/)\.env$"),
]


def is_denied(relative_path: str) -> bool:
    """Check whether a path is on the hardcoded deny list.

    Args:
        relative_path: Path relative to the mount root, using forward
            slashes (e.g. "config/secrets.yaml", "nested/.env").

    Returns:
        True if the path must be blocked.
    """
    # Normalise to forward slashes and strip leading slash
    normalised = PurePosixPath(relative_path).as_posix().lstrip("/")

    # Exact match
    if normalised in _EXACT_DENY:
        return True

    # Pattern match
    for pattern in _PATTERN_DENY:
        if pattern.search(normalised):
            return True

    return False


def check_and_log(relative_path: str, agent_id: str, action: str) -> bool:
    """Check deny list and log the attempt if blocked.

    Args:
        relative_path: Path being accessed.
        agent_id: The requesting agent's ID.
        action: The operation (read, write, list, stat, delete).

    Returns:
        True if the path is DENIED (caller must abort the operation).
    """
    if is_denied(relative_path):
        logger.warning(
            f"DENY LIST BLOCKED: agent='{agent_id}' action='{action}' "
            f"path='{relative_path}' — hardcoded deny list hit"
        )
        return True
    return False
```

### 2. `faith/tools/filesystem/mounts.py`

```python
"""Named mount resolution — maps logical mount names to host paths.

Loads mount configuration from .faith/tools/filesystem.yaml and resolves
agent requests by mount name. Agents never see raw host paths.

FRS Reference: Section 4.3.1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("faith.tools.filesystem.mounts")


@dataclass(frozen=True)
class MountConfig:
    """Configuration for a single named mount point."""

    name: str
    host_path: Path
    access: str  # "readonly" | "readwrite"
    recursive: bool = True
    max_file_size_mb: int = 50
    history: bool = False
    history_depth: int = 10
    subfolder_overrides: dict[str, str] = field(default_factory=dict)
    # subfolder_overrides: {"src/config": "readonly", ...}


class MountRegistry:
    """Registry of all configured mount points.

    Loaded from .faith/tools/filesystem.yaml at server startup and
    refreshed on config hot-reload events.
    """

    def __init__(self) -> None:
        self._mounts: dict[str, MountConfig] = {}

    def load_from_config(self, config: dict) -> None:
        """Parse mount definitions from the filesystem tool config.

        Args:
            config: Parsed YAML dict from .faith/tools/filesystem.yaml.
                Expected structure: {"mounts": {"name": {...}, ...}}
        """
        self._mounts.clear()
        mounts_raw = config.get("mounts", {})

        for name, mount_def in mounts_raw.items():
            # Skip subfolder override entries at the top level
            # (they contain "/" and are handled as overrides of their parent)
            if "/" in name:
                continue

            host_path = Path(mount_def["host_path"]).expanduser().resolve()
            subfolder_overrides: dict[str, str] = {}

            # Collect subfolder overrides (keys like "workspace/config")
            for key, value in mounts_raw.items():
                if key.startswith(f"{name}/") and isinstance(value, dict):
                    subfolder = key[len(name) + 1 :]
                    subfolder_overrides[subfolder] = value.get("access", "readonly")

            mount = MountConfig(
                name=name,
                host_path=host_path,
                access=mount_def.get("access", "readonly"),
                recursive=mount_def.get("recursive", True),
                max_file_size_mb=mount_def.get("max_file_size_mb", 50),
                history=mount_def.get("history", False),
                history_depth=mount_def.get("history_depth", 10),
                subfolder_overrides=subfolder_overrides,
            )
            self._mounts[name] = mount
            logger.info(
                f"Registered mount '{name}': {host_path} "
                f"(access={mount.access}, recursive={mount.recursive})"
            )

    def get(self, name: str) -> Optional[MountConfig]:
        """Look up a mount by name.

        Args:
            name: The logical mount name (e.g. "workspace").

        Returns:
            MountConfig or None if not found.
        """
        return self._mounts.get(name)

    def resolve_path(self, mount_name: str, relative_path: str) -> Optional[Path]:
        """Resolve a mount-relative path to an absolute host path.

        Args:
            mount_name: The logical mount name.
            relative_path: Path within the mount (e.g. "src/auth.py").

        Returns:
            Absolute host path, or None if mount not found.
        """
        mount = self._mounts.get(mount_name)
        if mount is None:
            return None
        return mount.host_path / relative_path

    def list_mounts(self) -> list[str]:
        """Return all registered mount names."""
        return list(self._mounts.keys())

    def reload(self, config: dict) -> None:
        """Reload mount configuration (called on hot-reload events).

        Args:
            config: Fresh parsed YAML dict.
        """
        logger.info("Reloading mount configuration")
        self.load_from_config(config)
```

### 3. `faith/tools/filesystem/permissions.py`

```python
"""Permission resolution engine — layered, most-restrictive-wins.

Resolution order (FRS Section 4.3.2):
1. Specificity override — subfolder config overrides parent mount.
2. Recursive default — permissions apply recursively unless recursive=false.
3. Agent cap — agent permission cannot exceed mount-level permission.
4. Most restrictive wins — when mount and agent disagree, stricter applies.

FRS Reference: Section 4.3.2, 4.3.3
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Optional

from faith.tools.filesystem.mounts import MountConfig

logger = logging.getLogger("faith.tools.filesystem.permissions")

# Permission hierarchy: higher index = more permissive
_PERMISSION_RANK = {"none": 0, "readonly": 1, "readwrite": 2}


def _rank(permission: str) -> int:
    """Convert a permission string to a numeric rank."""
    return _PERMISSION_RANK.get(permission, 0)


def _most_restrictive(a: str, b: str) -> str:
    """Return the more restrictive of two permission levels."""
    return a if _rank(a) <= _rank(b) else b


def resolve_mount_permission(
    mount: MountConfig, relative_path: str
) -> str:
    """Resolve the mount-level permission for a given path.

    Applies specificity override and recursive rules.

    Args:
        mount: The mount configuration.
        relative_path: Path within the mount (forward slashes).

    Returns:
        "readonly", "readwrite", or "none".
    """
    normalised = PurePosixPath(relative_path).as_posix().lstrip("/")

    # Rule 1: Check subfolder overrides (most specific path wins)
    # Sort overrides by specificity (longest path first)
    best_match: Optional[str] = None
    best_length = -1

    for subfolder, access in mount.subfolder_overrides.items():
        sub_norm = PurePosixPath(subfolder).as_posix().lstrip("/")
        if normalised == sub_norm or normalised.startswith(sub_norm + "/"):
            if len(sub_norm) > best_length:
                best_match = access
                best_length = len(sub_norm)

    if best_match is not None:
        return best_match

    # Rule 2: Recursive default
    if not mount.recursive and normalised and "/" in normalised:
        # Non-recursive mount — only top-level files are accessible
        return "none"

    return mount.access


def resolve_effective_permission(
    mount: MountConfig,
    relative_path: str,
    agent_mount_access: Optional[str],
) -> str:
    """Resolve the effective permission for an agent on a specific path.

    Combines mount-level resolution with agent cap (Rules 3 & 4).

    Args:
        mount: The mount configuration.
        relative_path: Path within the mount.
        agent_mount_access: The agent's declared access for this mount
            from their config.yaml ("readonly" or "readwrite").
            None means the agent has no access to this mount.

    Returns:
        "readonly", "readwrite", or "none".
    """
    # Agent not assigned to this mount at all
    if agent_mount_access is None:
        return "none"

    # Get mount-level permission for this specific path
    mount_permission = resolve_mount_permission(mount, relative_path)

    if mount_permission == "none":
        return "none"

    # Rule 3 & 4: Agent cap + most restrictive wins
    effective = _most_restrictive(mount_permission, agent_mount_access)

    return effective


def check_permission(
    mount: MountConfig,
    relative_path: str,
    agent_mount_access: Optional[str],
    required: str,
) -> tuple[bool, str]:
    """Check whether an operation is permitted.

    Args:
        mount: The mount configuration.
        relative_path: Path within the mount.
        agent_mount_access: Agent's declared access for this mount.
        required: Required permission level — "readonly" for reads,
            "readwrite" for writes/deletes.

    Returns:
        (allowed, effective_permission) tuple.
    """
    effective = resolve_effective_permission(
        mount, relative_path, agent_mount_access
    )
    allowed = _rank(effective) >= _rank(required)

    if not allowed:
        logger.warning(
            f"Permission denied: required={required} effective={effective} "
            f"mount={mount.name} path={relative_path}"
        )

    return allowed, effective
```

### 4. `faith/tools/filesystem/symlinks.py`

```python
"""Symlink escape prevention — blocks symlinks that resolve outside mount.

FRS Reference: Section 4.3.4
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("faith.tools.filesystem.symlinks")


class SymlinkEscapeError(Exception):
    """Raised when a symlink resolves outside the mount boundary."""

    def __init__(self, symlink_path: Path, resolved_path: Path, mount_root: Path):
        self.symlink_path = symlink_path
        self.resolved_path = resolved_path
        self.mount_root = mount_root
        super().__init__(
            f"Symlink escape blocked: '{symlink_path}' resolves to "
            f"'{resolved_path}' which is outside mount root '{mount_root}'"
        )


class BrokenSymlinkError(Exception):
    """Raised when a symlink target does not exist."""

    def __init__(self, symlink_path: Path):
        self.symlink_path = symlink_path
        super().__init__(f"Broken symlink: '{symlink_path}' — target does not exist")


def validate_path(path: Path, mount_root: Path) -> Path:
    """Validate that a path does not escape the mount boundary via symlinks.

    Resolves the path fully (following all symlinks) and verifies
    the resolved path is still within mount_root.

    Args:
        path: The path to validate (may or may not be a symlink).
        mount_root: The root directory of the mount.

    Returns:
        The fully resolved absolute path.

    Raises:
        SymlinkEscapeError: If the resolved path is outside mount_root.
        BrokenSymlinkError: If the path is a symlink whose target
            does not exist.
    """
    mount_resolved = mount_root.resolve()

    # Check if the path is a symlink with a broken target
    if path.is_symlink() and not path.exists():
        raise BrokenSymlinkError(path)

    # Resolve the full path (follows all symlinks)
    try:
        resolved = path.resolve()
    except OSError as e:
        raise BrokenSymlinkError(path) from e

    # Verify the resolved path is within the mount root
    try:
        resolved.relative_to(mount_resolved)
    except ValueError:
        raise SymlinkEscapeError(path, resolved, mount_resolved)

    return resolved


def validate_path_components(path: Path, mount_root: Path) -> Path:
    """Validate every component of a path for symlink escapes.

    Walks the path from mount_root downward, resolving each
    component individually. This catches intermediate symlinks
    that escape even if the final resolved path appears valid.

    Args:
        path: The full path to validate.
        mount_root: The mount root directory.

    Returns:
        The fully resolved path.

    Raises:
        SymlinkEscapeError: If any component escapes the mount.
        BrokenSymlinkError: If any symlink is broken.
    """
    mount_resolved = mount_root.resolve()
    current = mount_resolved

    try:
        relative = path.relative_to(mount_root)
    except ValueError:
        # path is not under mount_root at all
        raise SymlinkEscapeError(path, path, mount_root)

    for part in relative.parts:
        current = current / part

        if current.is_symlink():
            if not current.exists():
                raise BrokenSymlinkError(current)

            resolved_component = current.resolve()
            try:
                resolved_component.relative_to(mount_resolved)
            except ValueError:
                raise SymlinkEscapeError(current, resolved_component, mount_resolved)

    return current.resolve()
```

### 5. `faith/tools/filesystem/watcher.py`

```python
"""File watch poller — SHA256-based change detection.

Polls subscribed paths every 5 seconds, computes SHA256 checksums,
and publishes file:changed, file:created, and file:deleted events
when differences are detected. Only paths with active subscriptions
are polled.

Subscriptions come from two sources:
- Static: .faith/agents/{id}/config.yaml file_watches field
- Dynamic: Registered by PA at session start (session-scoped)

FRS Reference: Section 3.7.7, 4.3
"""

from __future__ import annotations

import asyncio
import glob
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from faith.protocol.events import EventPublisher, EventType

logger = logging.getLogger("faith.tools.filesystem.watcher")

POLL_INTERVAL_SECONDS = 5


@dataclass
class FileSubscription:
    """A single file watch subscription."""

    agent_id: str
    pattern: str  # Glob pattern, e.g. "workspace/src/**/*.py"
    events: list[str]  # e.g. ["file:changed", "file:created"]
    mount_root: Path  # Resolved host path for the mount
    session_scoped: bool = False  # True = dynamic, cleared on session end


@dataclass
class FileSnapshot:
    """SHA256 snapshot of a file at a point in time."""

    path: str  # Relative to mount root
    sha256: str
    size: int
    mtime: float


class FileWatcher:
    """Polls subscribed paths for changes using SHA256 checksums.

    Attributes:
        event_publisher: EventPublisher for sending file events.
        poll_interval: Seconds between poll cycles (default 5).
    """

    def __init__(
        self,
        event_publisher: EventPublisher,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._event_publisher = event_publisher
        self._poll_interval = poll_interval
        self._subscriptions: list[FileSubscription] = []
        self._snapshots: dict[str, FileSnapshot] = {}
        # _snapshots key = absolute path string
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None

    def add_subscription(self, subscription: FileSubscription) -> None:
        """Register a new file watch subscription.

        Args:
            subscription: The subscription to add.
        """
        self._subscriptions.append(subscription)
        logger.info(
            f"Added file watch: agent={subscription.agent_id} "
            f"pattern='{subscription.pattern}' "
            f"events={subscription.events} "
            f"session_scoped={subscription.session_scoped}"
        )

    def remove_session_subscriptions(self) -> None:
        """Remove all session-scoped (dynamic) subscriptions.

        Called on session end.
        """
        before = len(self._subscriptions)
        self._subscriptions = [
            s for s in self._subscriptions if not s.session_scoped
        ]
        removed = before - len(self._subscriptions)
        if removed > 0:
            logger.info(f"Cleared {removed} session-scoped file subscriptions")

    def load_static_subscriptions(
        self, agents_config: dict[str, dict], mount_roots: dict[str, Path]
    ) -> None:
        """Load static subscriptions from agent config files.

        Args:
            agents_config: Mapping of agent_id -> parsed config.yaml dict.
            mount_roots: Mapping of mount_name -> resolved host path.
        """
        for agent_id, config in agents_config.items():
            watches = config.get("file_watches", [])
            for watch in watches:
                pattern = watch.get("pattern", "")
                events = watch.get("events", ["file:changed"])

                # Determine mount from pattern prefix
                # Pattern format: "mount_name/path/glob" e.g. "workspace/src/**/*.py"
                parts = pattern.split("/", 1)
                mount_name = parts[0]
                mount_root = mount_roots.get(mount_name)

                if mount_root is None:
                    logger.warning(
                        f"Agent '{agent_id}' file_watch references unknown "
                        f"mount '{mount_name}': {pattern}"
                    )
                    continue

                self.add_subscription(
                    FileSubscription(
                        agent_id=agent_id,
                        pattern=pattern,
                        events=events,
                        mount_root=mount_root,
                        session_scoped=False,
                    )
                )

    async def start(self) -> None:
        """Start the background poll loop."""
        if self._running:
            return
        self._running = True
        # Take initial snapshot
        self._take_initial_snapshots()
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="filesystem-watcher"
        )
        logger.info(
            f"File watcher started (interval={self._poll_interval}s, "
            f"subscriptions={len(self._subscriptions)})"
        )

    async def stop(self) -> None:
        """Stop the background poll loop."""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("File watcher stopped")

    def _take_initial_snapshots(self) -> None:
        """Build initial SHA256 snapshot for all subscribed paths."""
        self._snapshots.clear()
        for path_str in self._collect_watched_paths():
            snapshot = self._hash_file(path_str)
            if snapshot is not None:
                self._snapshots[path_str] = snapshot

    def _collect_watched_paths(self) -> set[str]:
        """Expand all subscription glob patterns to concrete file paths.

        Returns:
            Set of absolute path strings.
        """
        paths: set[str] = set()
        for sub in self._subscriptions:
            # Pattern format: "mount_name/rest/of/glob"
            parts = sub.pattern.split("/", 1)
            if len(parts) < 2:
                continue
            glob_suffix = parts[1]
            full_pattern = str(sub.mount_root / glob_suffix)
            matched = glob.glob(full_pattern, recursive=True)
            for m in matched:
                p = Path(m)
                if p.is_file():
                    paths.add(str(p))
        return paths

    def _hash_file(self, path_str: str) -> Optional[FileSnapshot]:
        """Compute SHA256 hash of a file.

        Args:
            path_str: Absolute path to the file.

        Returns:
            FileSnapshot or None if the file cannot be read.
        """
        try:
            p = Path(path_str)
            stat = p.stat()
            sha256 = hashlib.sha256(p.read_bytes()).hexdigest()
            return FileSnapshot(
                path=path_str,
                sha256=sha256,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        except (OSError, PermissionError) as e:
            logger.debug(f"Cannot hash file '{path_str}': {e}")
            return None

    async def _poll_loop(self) -> None:
        """Background loop: poll every interval, detect changes."""
        while self._running:
            try:
                await asyncio.sleep(self._poll_interval)
                if not self._running:
                    break
                await self._poll_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in file watcher poll cycle: {e}", exc_info=True)

    async def _poll_cycle(self) -> None:
        """Single poll cycle: check all subscribed paths for changes."""
        current_paths = self._collect_watched_paths()
        previous_paths = set(self._snapshots.keys())

        # Detect new files (file:created)
        created = current_paths - previous_paths
        for path_str in created:
            snapshot = self._hash_file(path_str)
            if snapshot is not None:
                self._snapshots[path_str] = snapshot
                await self._publish_event(path_str, "file:created")

        # Detect deleted files (file:deleted)
        deleted = previous_paths - current_paths
        for path_str in deleted:
            del self._snapshots[path_str]
            await self._publish_event(path_str, "file:deleted")

        # Detect changed files (file:changed) — SHA256 comparison
        for path_str in current_paths & previous_paths:
            new_snapshot = self._hash_file(path_str)
            if new_snapshot is None:
                # File became unreadable — treat as deleted
                del self._snapshots[path_str]
                await self._publish_event(path_str, "file:deleted")
                continue

            old_snapshot = self._snapshots[path_str]
            if new_snapshot.sha256 != old_snapshot.sha256:
                self._snapshots[path_str] = new_snapshot
                await self._publish_event(path_str, "file:changed")

    async def _publish_event(self, path_str: str, event_type_str: str) -> None:
        """Publish a file event to all matching subscribers.

        The event is published to the subscribing agent's channel
        (not system-events) per FRS Section 3.7.7.

        Args:
            path_str: Absolute path of the affected file.
            event_type_str: "file:changed", "file:created", or "file:deleted".
        """
        event_type_map = {
            "file:changed": EventType.FILE_CHANGED,
            "file:created": EventType.FILE_CREATED,
            "file:deleted": EventType.FILE_DELETED,
        }
        event_type = event_type_map.get(event_type_str)
        if event_type is None:
            return

        path = Path(path_str)

        for sub in self._subscriptions:
            if event_type_str not in sub.events:
                continue

            # Check if this path matches the subscription's glob
            parts = sub.pattern.split("/", 1)
            if len(parts) < 2:
                continue
            glob_suffix = parts[1]
            full_pattern = str(sub.mount_root / glob_suffix)

            if path_str in glob.glob(full_pattern, recursive=True):
                # Compute relative path for the event payload
                try:
                    rel_path = path.relative_to(sub.mount_root).as_posix()
                except ValueError:
                    rel_path = path_str

                mount_name = parts[0]

                from faith.protocol.events import FaithEvent

                event = FaithEvent(
                    event=event_type,
                    source="filesystem",
                    data={
                        "path": rel_path,
                        "mount": mount_name,
                        "absolute_path": path_str,
                        "agent": sub.agent_id,
                    },
                )
                await self._event_publisher.publish(event)
                logger.info(
                    f"Published {event_type_str}: {rel_path} -> "
                    f"agent={sub.agent_id}"
                )
```

### 6. `faith/tools/filesystem/operations.py`

```python
"""MCP tool operations — read, write, list, stat, delete, mkdir.

Each function validates permissions, checks the deny list, validates
symlinks, enforces file size limits, and then performs the operation.
All operations log to the audit trail via event publishing.

FRS Reference: Section 4.3
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from faith.tools.filesystem.deny_list import check_and_log as check_deny_list
from faith.tools.filesystem.mounts import MountConfig, MountRegistry
from faith.tools.filesystem.permissions import check_permission
from faith.tools.filesystem.symlinks import (
    SymlinkEscapeError,
    BrokenSymlinkError,
    validate_path_components,
)

logger = logging.getLogger("faith.tools.filesystem.operations")

# Maximum file size in bytes (derived from mount config's max_file_size_mb)
_MB = 1024 * 1024


class FilesystemError(Exception):
    """Base error for filesystem operations."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class PermissionDeniedError(FilesystemError):
    """Operation blocked by permission rules."""

    def __init__(self, message: str):
        super().__init__("PERMISSION_DENIED", message)


class DenyListError(FilesystemError):
    """Operation blocked by hardcoded deny list."""

    def __init__(self, path: str):
        super().__init__(
            "DENY_LIST_BLOCKED",
            f"Access to '{path}' is permanently denied — "
            f"this path is on the hardcoded deny list",
        )


class FileSizeLimitError(FilesystemError):
    """File exceeds the configured size limit."""

    def __init__(self, path: str, size_mb: float, limit_mb: int):
        super().__init__(
            "FILE_SIZE_EXCEEDED",
            f"File '{path}' is {size_mb:.1f}MB — exceeds limit of "
            f"{limit_mb}MB. Use Code Index or RAG tools for large files.",
        )


def _validate_request(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
    required_permission: str,
) -> tuple[MountConfig, Path]:
    """Common validation for all operations.

    Checks: mount exists, deny list, agent permission, symlink safety.

    Args:
        mount_registry: The active mount registry.
        mount_name: Logical mount name.
        relative_path: Path within the mount.
        agent_id: Requesting agent's ID.
        agent_mounts: Agent's mount assignments {mount_name: access}.
        required_permission: "readonly" or "readwrite".

    Returns:
        (mount_config, resolved_absolute_path) tuple.

    Raises:
        FilesystemError: On any validation failure.
    """
    # 1. Mount exists?
    mount = mount_registry.get(mount_name)
    if mount is None:
        raise FilesystemError(
            "MOUNT_NOT_FOUND",
            f"Mount '{mount_name}' does not exist. "
            f"Available: {mount_registry.list_mounts()}",
        )

    # 2. Deny list check
    if check_deny_list(relative_path, agent_id, required_permission):
        raise DenyListError(relative_path)

    # 3. Permission check
    agent_access = agent_mounts.get(mount_name)
    allowed, effective = check_permission(
        mount, relative_path, agent_access, required_permission
    )
    if not allowed:
        raise PermissionDeniedError(
            f"Agent '{agent_id}' has '{effective}' access on "
            f"mount '{mount_name}' path '{relative_path}' — "
            f"'{required_permission}' required"
        )

    # 4. Resolve path and check symlinks
    absolute_path = mount.host_path / relative_path
    try:
        resolved = validate_path_components(absolute_path, mount.host_path)
    except SymlinkEscapeError as e:
        raise PermissionDeniedError(str(e))
    except BrokenSymlinkError as e:
        raise FilesystemError("BROKEN_SYMLINK", str(e))

    return mount, resolved


def read_file(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    """Read a file from a named mount.

    Args:
        mount_registry: Active mount registry.
        mount_name: Logical mount name.
        relative_path: Path within the mount.
        agent_id: Requesting agent's ID.
        agent_mounts: Agent's mount assignments.

    Returns:
        {"content": str, "size": int, "path": str, "mount": str}

    Raises:
        FilesystemError: On validation or I/O failure.
    """
    mount, resolved = _validate_request(
        mount_registry, mount_name, relative_path,
        agent_id, agent_mounts, "readonly",
    )

    # File size limit check
    if not resolved.exists():
        raise FilesystemError("NOT_FOUND", f"File not found: {relative_path}")

    if not resolved.is_file():
        raise FilesystemError("NOT_A_FILE", f"Path is not a file: {relative_path}")

    size = resolved.stat().st_size
    limit_bytes = mount.max_file_size_mb * _MB
    if size > limit_bytes:
        raise FileSizeLimitError(
            relative_path, size / _MB, mount.max_file_size_mb
        )

    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Binary file — read as bytes and return base64
        import base64
        content = base64.b64encode(resolved.read_bytes()).decode("ascii")

    return {
        "content": content,
        "size": size,
        "path": relative_path,
        "mount": mount_name,
    }


def write_file(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    content: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    """Write content to a file on a named mount.

    Creates parent directories as needed. Rejects writes that
    exceed the file size limit before writing begins.

    Args:
        mount_registry: Active mount registry.
        mount_name: Logical mount name.
        relative_path: Path within the mount.
        content: File content to write.
        agent_id: Requesting agent's ID.
        agent_mounts: Agent's mount assignments.

    Returns:
        {"path": str, "mount": str, "size": int, "created": bool}

    Raises:
        FilesystemError: On validation or I/O failure.
    """
    mount, resolved = _validate_request(
        mount_registry, mount_name, relative_path,
        agent_id, agent_mounts, "readwrite",
    )

    # Pre-write size check
    content_bytes = content.encode("utf-8")
    limit_bytes = mount.max_file_size_mb * _MB
    if len(content_bytes) > limit_bytes:
        raise FileSizeLimitError(
            relative_path,
            len(content_bytes) / _MB,
            mount.max_file_size_mb,
        )

    created = not resolved.exists()

    # Create parent directories
    resolved.parent.mkdir(parents=True, exist_ok=True)

    resolved.write_bytes(content_bytes)

    return {
        "path": relative_path,
        "mount": mount_name,
        "size": len(content_bytes),
        "created": created,
    }


def list_directory(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    """List contents of a directory on a named mount.

    Args:
        mount_registry: Active mount registry.
        mount_name: Logical mount name.
        relative_path: Path within the mount (empty string for root).
        agent_id: Requesting agent's ID.
        agent_mounts: Agent's mount assignments.

    Returns:
        {"entries": [{"name": str, "type": "file"|"dir", "size": int}],
         "path": str, "mount": str}

    Raises:
        FilesystemError: On validation failure.
    """
    mount, resolved = _validate_request(
        mount_registry, mount_name, relative_path,
        agent_id, agent_mounts, "readonly",
    )

    if not resolved.exists():
        raise FilesystemError("NOT_FOUND", f"Directory not found: {relative_path}")

    if not resolved.is_dir():
        raise FilesystemError("NOT_A_DIRECTORY", f"Path is not a directory: {relative_path}")

    entries = []
    for item in sorted(resolved.iterdir()):
        # Skip deny-listed files from directory listings
        item_rel = (Path(relative_path) / item.name).as_posix()
        from faith.tools.filesystem.deny_list import is_denied
        if is_denied(item_rel):
            continue

        entry = {
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
        }
        if item.is_file():
            try:
                entry["size"] = item.stat().st_size
            except OSError:
                entry["size"] = -1
        entries.append(entry)

    return {
        "entries": entries,
        "path": relative_path,
        "mount": mount_name,
    }


def stat_file(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    """Get file/directory metadata from a named mount.

    Args:
        mount_registry: Active mount registry.
        mount_name: Logical mount name.
        relative_path: Path within the mount.
        agent_id: Requesting agent's ID.
        agent_mounts: Agent's mount assignments.

    Returns:
        {"path": str, "mount": str, "type": str, "size": int,
         "modified": float, "exists": bool}
    """
    mount, resolved = _validate_request(
        mount_registry, mount_name, relative_path,
        agent_id, agent_mounts, "readonly",
    )

    if not resolved.exists():
        return {
            "path": relative_path,
            "mount": mount_name,
            "exists": False,
        }

    stat = resolved.stat()
    return {
        "path": relative_path,
        "mount": mount_name,
        "exists": True,
        "type": "dir" if resolved.is_dir() else "file",
        "size": stat.st_size,
        "modified": stat.st_mtime,
    }


def delete_file(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    """Delete a file from a named mount.

    Only files can be deleted — directory deletion is not supported
    for safety reasons.

    Args:
        mount_registry: Active mount registry.
        mount_name: Logical mount name.
        relative_path: Path within the mount.
        agent_id: Requesting agent's ID.
        agent_mounts: Agent's mount assignments.

    Returns:
        {"path": str, "mount": str, "deleted": bool}
    """
    mount, resolved = _validate_request(
        mount_registry, mount_name, relative_path,
        agent_id, agent_mounts, "readwrite",
    )

    if not resolved.exists():
        raise FilesystemError("NOT_FOUND", f"File not found: {relative_path}")

    if not resolved.is_file():
        raise FilesystemError(
            "NOT_A_FILE",
            f"Cannot delete directories — only files: {relative_path}",
        )

    resolved.unlink()

    return {
        "path": relative_path,
        "mount": mount_name,
        "deleted": True,
    }


def make_directory(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    """Create a directory on a named mount.

    Creates parent directories as needed.

    Args:
        mount_registry: Active mount registry.
        mount_name: Logical mount name.
        relative_path: Path within the mount.
        agent_id: Requesting agent's ID.
        agent_mounts: Agent's mount assignments.

    Returns:
        {"path": str, "mount": str, "created": bool}
    """
    mount, resolved = _validate_request(
        mount_registry, mount_name, relative_path,
        agent_id, agent_mounts, "readwrite",
    )

    created = not resolved.exists()
    resolved.mkdir(parents=True, exist_ok=True)

    return {
        "path": relative_path,
        "mount": mount_name,
        "created": created,
    }
```

### 7. `faith/tools/filesystem/server.py`

```python
"""Filesystem MCP server — tool registration and request dispatch.

Registers all filesystem operations as MCP tools and handles
incoming requests from agents via the PA's MCP adapter layer.

FRS Reference: Section 4.3
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

import yaml
import redis.asyncio as aioredis

from faith.tools.filesystem.mounts import MountRegistry
from faith.tools.filesystem.operations import (
    read_file,
    write_file,
    list_directory,
    stat_file,
    delete_file,
    make_directory,
    FilesystemError,
)
from faith.tools.filesystem.watcher import FileWatcher, FileSubscription
from faith.protocol.events import EventPublisher, EventType

logger = logging.getLogger("faith.tools.filesystem.server")


class FilesystemMCPServer:
    """MCP server for filesystem operations.

    Manages mount configuration, permission enforcement, file watching,
    and event publishing. Runs as a long-lived process inside the
    tool-fs Docker container.

    Attributes:
        mount_registry: Registry of named mount points.
        watcher: File change detection poller.
        event_publisher: EventPublisher for tool and file events.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        config_path: Path,
        agents_dir: Path,
    ) -> None:
        """Initialise the filesystem MCP server.

        Args:
            redis_client: Connected async Redis client.
            config_path: Path to .faith/tools/filesystem.yaml.
            agents_dir: Path to .faith/agents/ directory.
        """
        self.redis = redis_client
        self.config_path = config_path
        self.agents_dir = agents_dir

        self.event_publisher = EventPublisher(redis_client, source="filesystem")
        self.mount_registry = MountRegistry()
        self.watcher: Optional[FileWatcher] = None

        self._agent_mounts_cache: dict[str, dict[str, str]] = {}
        # {agent_id: {mount_name: access_level}}

    async def start(self) -> None:
        """Start the server: load config, register mounts, start watcher."""
        # Load filesystem tool config
        config = self._load_config()
        self.mount_registry.load_from_config(config)

        # Load agent mount assignments
        self._load_agent_mounts()

        # Initialise file watcher
        self.watcher = FileWatcher(self.event_publisher)

        # Load static file watch subscriptions from agent configs
        mount_roots = {
            name: self.mount_registry.get(name).host_path
            for name in self.mount_registry.list_mounts()
        }
        agents_config = self._load_all_agent_configs()
        self.watcher.load_static_subscriptions(agents_config, mount_roots)

        # Start the watcher
        await self.watcher.start()

        # Subscribe to system-events for config reload signals
        # and session lifecycle events
        await self._subscribe_system_events()

        logger.info("Filesystem MCP server started")

    async def stop(self) -> None:
        """Stop the server and file watcher."""
        if self.watcher:
            await self.watcher.stop()
        logger.info("Filesystem MCP server stopped")

    def _load_config(self) -> dict:
        """Load filesystem tool config from YAML."""
        try:
            raw = self.config_path.read_text(encoding="utf-8")
            return yaml.safe_load(raw) or {}
        except Exception as e:
            logger.error(f"Failed to load config from {self.config_path}: {e}")
            return {}

    def _load_agent_mounts(self) -> None:
        """Load agent mount assignments from all agent config files."""
        self._agent_mounts_cache.clear()

        if not self.agents_dir.exists():
            return

        for agent_dir in self.agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            config_file = agent_dir / "config.yaml"
            if not config_file.exists():
                continue
            try:
                config = yaml.safe_load(
                    config_file.read_text(encoding="utf-8")
                ) or {}
                mounts = config.get("mounts", {})
                self._agent_mounts_cache[agent_dir.name] = mounts
            except Exception as e:
                logger.warning(
                    f"Failed to load agent config {config_file}: {e}"
                )

    def _load_all_agent_configs(self) -> dict[str, dict]:
        """Load all agent configs for file watch subscription loading."""
        configs: dict[str, dict] = {}
        if not self.agents_dir.exists():
            return configs

        for agent_dir in self.agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            config_file = agent_dir / "config.yaml"
            if config_file.exists():
                try:
                    configs[agent_dir.name] = yaml.safe_load(
                        config_file.read_text(encoding="utf-8")
                    ) or {}
                except Exception as e:
                    logger.warning(f"Failed to load {config_file}: {e}")
        return configs

    def get_agent_mounts(self, agent_id: str) -> dict[str, str]:
        """Get an agent's mount assignments.

        Args:
            agent_id: The agent's identifier.

        Returns:
            Dict of {mount_name: access_level}.
        """
        return self._agent_mounts_cache.get(agent_id, {})

    async def handle_tool_call(
        self,
        action: str,
        args: dict[str, Any],
        agent_id: str,
    ) -> dict[str, Any]:
        """Dispatch an MCP tool call to the appropriate operation.

        Args:
            action: The tool action (read, write, list, stat, delete, mkdir).
            args: Action-specific arguments.
            agent_id: The calling agent's ID.

        Returns:
            Operation result dict.

        Raises:
            FilesystemError: On any operation failure.
        """
        agent_mounts = self.get_agent_mounts(agent_id)

        # Publish tool:call_started
        await self.event_publisher.publish_raw(
            EventType.TOOL_CALL_STARTED,
            {
                "tool": "filesystem",
                "action": action,
                "agent": agent_id,
                "args": {k: v for k, v in args.items() if k != "content"},
            },
        )

        try:
            result = self._dispatch(action, args, agent_id, agent_mounts)

            # Publish tool:call_complete
            await self.event_publisher.publish_raw(
                EventType.TOOL_CALL_COMPLETE,
                {
                    "tool": "filesystem",
                    "action": action,
                    "agent": agent_id,
                    "result_summary": {
                        k: v for k, v in result.items() if k != "content"
                    },
                },
            )

            return result

        except FilesystemError as e:
            # Publish tool:permission_denied or tool:error
            event_type = (
                EventType.TOOL_PERMISSION_DENIED
                if e.code in ("PERMISSION_DENIED", "DENY_LIST_BLOCKED")
                else EventType.TOOL_ERROR
            )
            await self.event_publisher.publish_raw(
                event_type,
                {
                    "tool": "filesystem",
                    "action": action,
                    "agent": agent_id,
                    "error_code": e.code,
                    "error_message": e.message,
                },
            )
            raise

    def _dispatch(
        self,
        action: str,
        args: dict[str, Any],
        agent_id: str,
        agent_mounts: dict[str, str],
    ) -> dict[str, Any]:
        """Route to the correct operation function.

        Args:
            action: The tool action name.
            args: Action arguments.
            agent_id: Calling agent's ID.
            agent_mounts: Agent's mount assignments.

        Returns:
            Operation result dict.
        """
        dispatch_table = {
            "read": read_file,
            "write": write_file,
            "list": list_directory,
            "stat": stat_file,
            "delete": delete_file,
            "mkdir": make_directory,
        }

        handler = dispatch_table.get(action)
        if handler is None:
            raise FilesystemError(
                "UNKNOWN_ACTION",
                f"Unknown filesystem action: '{action}'. "
                f"Available: {list(dispatch_table.keys())}",
            )

        # Build common kwargs
        kwargs: dict[str, Any] = {
            "mount_registry": self.mount_registry,
            "mount_name": args.get("mount", ""),
            "relative_path": args.get("path", ""),
            "agent_id": agent_id,
            "agent_mounts": agent_mounts,
        }

        # Add write-specific args
        if action == "write":
            kwargs["content"] = args.get("content", "")

        return handler(**kwargs)

    async def register_dynamic_subscription(
        self,
        agent_id: str,
        pattern: str,
        events: list[str],
    ) -> None:
        """Register a session-scoped file watch subscription.

        Called by the PA at session start for dynamic subscriptions.

        Args:
            agent_id: The agent to notify.
            pattern: Glob pattern (e.g. "workspace/src/**/*.py").
            events: Event types to subscribe to.
        """
        if self.watcher is None:
            logger.warning("Cannot register subscription — watcher not started")
            return

        parts = pattern.split("/", 1)
        mount_name = parts[0]
        mount = self.mount_registry.get(mount_name)
        if mount is None:
            logger.warning(
                f"Dynamic subscription references unknown mount "
                f"'{mount_name}': {pattern}"
            )
            return

        self.watcher.add_subscription(
            FileSubscription(
                agent_id=agent_id,
                pattern=pattern,
                events=events,
                mount_root=mount.host_path,
                session_scoped=True,
            )
        )

    async def _subscribe_system_events(self) -> None:
        """Subscribe to system-events for config changes and session end.

        Listens for:
        - system:config_changed (filesystem.yaml) -> reload mount config
        - session:ended -> clear dynamic subscriptions
        """
        pubsub = self.redis.pubsub()
        await pubsub.subscribe("system-events")

        async def _listener() -> None:
            while True:
                try:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                    if msg is None:
                        continue
                    if msg["type"] != "message":
                        continue

                    raw = msg["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")

                    data = json.loads(raw)
                    event = data.get("event", "")

                    if event == "system:config_changed":
                        file_changed = data.get("data", {}).get("file", "")
                        if "filesystem" in file_changed:
                            logger.info("Reloading filesystem config")
                            config = self._load_config()
                            self.mount_registry.reload(config)
                            self._load_agent_mounts()

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in system-events listener: {e}")

        asyncio.create_task(_listener(), name="fs-system-events")
```

### 8. `faith/tools/filesystem/__init__.py`

```python
"""FAITH Filesystem MCP Server — secure file I/O for agents."""

from faith.tools.filesystem.server import FilesystemMCPServer

__all__ = ["FilesystemMCPServer"]
```

### 9. `containers/tool-fs/Dockerfile`

```dockerfile
FROM python:3.12-alpine

LABEL maintainer="FAITH Framework"
LABEL description="Filesystem MCP server for FAITH agents"

WORKDIR /app

# Install the FAITH package (mounted or copied at build time)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY faith/ /app/faith/
COPY containers/tool-fs/entrypoint.py /app/entrypoint.py

# Non-root user for security
RUN adduser -D -u 1000 faith
USER faith

ENTRYPOINT ["python", "/app/entrypoint.py"]
```

### 10. `containers/tool-fs/entrypoint.py`

```python
"""Entrypoint for the filesystem MCP server container."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import redis.asyncio as aioredis

from faith.tools.filesystem.server import FilesystemMCPServer

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("faith.tools.filesystem")


async def main() -> None:
    """Start the filesystem MCP server."""
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    faith_dir = Path(os.environ.get("FAITH_DIR", "/project/.faith"))

    config_path = faith_dir / "tools" / "filesystem.yaml"
    agents_dir = faith_dir / "agents"

    redis_client = aioredis.from_url(redis_url, decode_responses=False)

    server = FilesystemMCPServer(
        redis_client=redis_client,
        config_path=config_path,
        agents_dir=agents_dir,
    )

    # Graceful shutdown on SIGTERM
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    await server.start()
    logger.info("Filesystem MCP server ready")

    await shutdown_event.wait()
    await server.stop()
    await redis_client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

### 11. `tests/test_filesystem_server.py`

```python
"""Tests for the FAITH filesystem MCP server.

Covers deny list, mount resolution, permission resolution, symlink
detection, file size limits, file operations, and file watching.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.tools.filesystem.deny_list import is_denied, check_and_log
from faith.tools.filesystem.mounts import MountConfig, MountRegistry
from faith.tools.filesystem.permissions import (
    resolve_mount_permission,
    resolve_effective_permission,
    check_permission,
)
from faith.tools.filesystem.symlinks import (
    validate_path,
    validate_path_components,
    SymlinkEscapeError,
    BrokenSymlinkError,
)
from faith.tools.filesystem.operations import (
    read_file,
    write_file,
    list_directory,
    stat_file,
    delete_file,
    make_directory,
    FilesystemError,
    PermissionDeniedError,
    DenyListError,
    FileSizeLimitError,
)
from faith.tools.filesystem.watcher import (
    FileWatcher,
    FileSubscription,
    FileSnapshot,
    POLL_INTERVAL_SECONDS,
)


# ──────────────────────────────────────────────────
# Deny list tests
# ──────────────────────────────────────────────────


class TestDenyList:
    """Hardcoded deny list — these must ALWAYS be blocked."""

    def test_exact_secrets_yaml(self):
        assert is_denied("config/secrets.yaml") is True

    def test_exact_dot_env(self):
        assert is_denied("config/.env") is True

    def test_pattern_secrets_yaml_nested(self):
        assert is_denied("some/nested/secrets.yaml") is True

    def test_pattern_dot_env_nested(self):
        assert is_denied("deeply/nested/.env") is True

    def test_pattern_secrets_yaml_root(self):
        assert is_denied("secrets.yaml") is True

    def test_pattern_dot_env_root(self):
        assert is_denied(".env") is True

    def test_safe_path_allowed(self):
        assert is_denied("src/main.py") is False

    def test_partial_name_not_blocked(self):
        """Files containing 'secrets' in the name but not matching
        the exact pattern should not be blocked."""
        assert is_denied("src/secrets_manager.py") is False

    def test_dot_env_partial_not_blocked(self):
        assert is_denied("src/.env.example") is False

    def test_leading_slash_normalised(self):
        assert is_denied("/config/secrets.yaml") is True

    def test_check_and_log_returns_true_on_deny(self):
        assert check_and_log("config/.env", "test-agent", "read") is True

    def test_check_and_log_returns_false_on_allow(self):
        assert check_and_log("src/app.py", "test-agent", "read") is False


# ──────────────────────────────────────────────────
# Mount resolution tests
# ──────────────────────────────────────────────────


@pytest.fixture
def mount_registry(tmp_path):
    """Create a MountRegistry with test mounts."""
    registry = MountRegistry()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    config = {
        "mounts": {
            "workspace": {
                "host_path": str(workspace),
                "access": "readwrite",
                "recursive": True,
                "max_file_size_mb": 10,
            },
            "workspace/config": {
                "access": "readonly",
            },
            "outputs": {
                "host_path": str(outputs),
                "access": "readwrite",
            },
            "docs": {
                "host_path": str(tmp_path / "docs"),
                "access": "readonly",
                "recursive": False,
            },
        }
    }
    registry.load_from_config(config)
    return registry


class TestMountRegistry:

    def test_get_existing_mount(self, mount_registry):
        mount = mount_registry.get("workspace")
        assert mount is not None
        assert mount.access == "readwrite"

    def test_get_nonexistent_mount(self, mount_registry):
        assert mount_registry.get("nonexistent") is None

    def test_list_mounts(self, mount_registry):
        names = mount_registry.list_mounts()
        assert "workspace" in names
        assert "outputs" in names

    def test_subfolder_override_loaded(self, mount_registry):
        mount = mount_registry.get("workspace")
        assert "config" in mount.subfolder_overrides
        assert mount.subfolder_overrides["config"] == "readonly"

    def test_resolve_path(self, mount_registry):
        path = mount_registry.resolve_path("workspace", "src/main.py")
        assert path is not None
        assert path.name == "main.py"

    def test_resolve_path_unknown_mount(self, mount_registry):
        assert mount_registry.resolve_path("unknown", "foo.py") is None


# ──────────────────────────────────────────────────
# Permission resolution tests
# ──────────────────────────────────────────────────


class TestPermissions:

    def test_basic_readwrite(self, mount_registry):
        mount = mount_registry.get("workspace")
        perm = resolve_mount_permission(mount, "src/main.py")
        assert perm == "readwrite"

    def test_subfolder_override_to_readonly(self, mount_registry):
        """Subfolder override makes workspace/config readonly."""
        mount = mount_registry.get("workspace")
        perm = resolve_mount_permission(mount, "config/settings.yaml")
        assert perm == "readonly"

    def test_non_recursive_blocks_subfolders(self, mount_registry):
        """Non-recursive mount blocks access to child folders."""
        mount = mount_registry.get("docs")
        perm = resolve_mount_permission(mount, "nested/file.md")
        assert perm == "none"

    def test_non_recursive_allows_root(self, mount_registry):
        """Non-recursive mount allows root-level files."""
        mount = mount_registry.get("docs")
        perm = resolve_mount_permission(mount, "readme.md")
        assert perm == "readonly"

    def test_agent_cap_readonly(self, mount_registry):
        """Agent with readonly cannot write even if mount is readwrite."""
        mount = mount_registry.get("workspace")
        effective = resolve_effective_permission(mount, "src/main.py", "readonly")
        assert effective == "readonly"

    def test_agent_cap_readwrite(self, mount_registry):
        """Agent with readwrite on readwrite mount gets readwrite."""
        mount = mount_registry.get("workspace")
        effective = resolve_effective_permission(mount, "src/main.py", "readwrite")
        assert effective == "readwrite"

    def test_agent_no_access(self, mount_registry):
        """Agent not assigned to mount gets none."""
        mount = mount_registry.get("workspace")
        effective = resolve_effective_permission(mount, "src/main.py", None)
        assert effective == "none"

    def test_most_restrictive_wins(self, mount_registry):
        """Mount readonly + agent readwrite = readonly."""
        mount = mount_registry.get("docs")
        effective = resolve_effective_permission(mount, "readme.md", "readwrite")
        assert effective == "readonly"

    def test_check_permission_allowed(self, mount_registry):
        mount = mount_registry.get("workspace")
        allowed, _ = check_permission(mount, "src/main.py", "readwrite", "readwrite")
        assert allowed is True

    def test_check_permission_denied(self, mount_registry):
        mount = mount_registry.get("workspace")
        allowed, _ = check_permission(mount, "src/main.py", "readonly", "readwrite")
        assert allowed is False


# ──────────────────────────────────────────────────
# Symlink tests
# ──────────────────────────────────────────────────


class TestSymlinks:

    def test_normal_file_passes(self, tmp_path):
        """Non-symlink files pass validation."""
        mount_root = tmp_path / "mount"
        mount_root.mkdir()
        f = mount_root / "file.txt"
        f.write_text("hello")
        result = validate_path(f, mount_root)
        assert result == f.resolve()

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Symlink creation requires elevated privileges on Windows",
    )
    def test_internal_symlink_passes(self, tmp_path):
        """Symlink within mount boundary is allowed."""
        mount_root = tmp_path / "mount"
        mount_root.mkdir()
        target = mount_root / "real.txt"
        target.write_text("content")
        link = mount_root / "link.txt"
        link.symlink_to(target)
        result = validate_path(link, mount_root)
        assert result == target.resolve()

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Symlink creation requires elevated privileges on Windows",
    )
    def test_escape_symlink_blocked(self, tmp_path):
        """Symlink escaping mount boundary is blocked."""
        mount_root = tmp_path / "mount"
        mount_root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        link = mount_root / "escape.txt"
        link.symlink_to(outside)
        with pytest.raises(SymlinkEscapeError):
            validate_path(link, mount_root)

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Symlink creation requires elevated privileges on Windows",
    )
    def test_broken_symlink_raises(self, tmp_path):
        """Broken symlink raises BrokenSymlinkError."""
        mount_root = tmp_path / "mount"
        mount_root.mkdir()
        link = mount_root / "broken.txt"
        link.symlink_to(mount_root / "nonexistent.txt")
        with pytest.raises(BrokenSymlinkError):
            validate_path(link, mount_root)


# ──────────────────────────────────────────────────
# File operation tests
# ──────────────────────────────────────────────────


@pytest.fixture
def workspace_mount(tmp_path, mount_registry):
    """Create workspace with test files and return mount + registry."""
    mount = mount_registry.get("workspace")
    src = mount.host_path / "src"
    src.mkdir(parents=True)
    (src / "main.py").write_text("print('hello')", encoding="utf-8")
    return mount_registry


@pytest.fixture
def agent_mounts():
    """Standard agent mount assignments."""
    return {"workspace": "readwrite", "outputs": "readwrite"}


class TestFileOperations:

    def test_read_file(self, workspace_mount, agent_mounts):
        result = read_file(
            workspace_mount, "workspace", "src/main.py",
            "test-agent", agent_mounts,
        )
        assert result["content"] == "print('hello')"
        assert result["mount"] == "workspace"

    def test_read_file_not_found(self, workspace_mount, agent_mounts):
        with pytest.raises(FilesystemError) as exc_info:
            read_file(
                workspace_mount, "workspace", "nonexistent.py",
                "test-agent", agent_mounts,
            )
        assert exc_info.value.code == "NOT_FOUND"

    def test_read_denied_secrets(self, workspace_mount, agent_mounts):
        with pytest.raises(DenyListError):
            read_file(
                workspace_mount, "workspace", "config/secrets.yaml",
                "test-agent", agent_mounts,
            )

    def test_read_mount_not_found(self, workspace_mount, agent_mounts):
        with pytest.raises(FilesystemError) as exc_info:
            read_file(
                workspace_mount, "nonexistent", "file.py",
                "test-agent", agent_mounts,
            )
        assert exc_info.value.code == "MOUNT_NOT_FOUND"

    def test_write_file(self, workspace_mount, agent_mounts):
        result = write_file(
            workspace_mount, "workspace", "src/new.py",
            "# new file", "test-agent", agent_mounts,
        )
        assert result["created"] is True
        # Verify file was written
        mount = workspace_mount.get("workspace")
        assert (mount.host_path / "src" / "new.py").read_text() == "# new file"

    def test_write_file_size_limit(self, workspace_mount, agent_mounts):
        """Write exceeding size limit is rejected before writing."""
        # Mount has max_file_size_mb=10
        large_content = "x" * (11 * 1024 * 1024)  # 11MB
        with pytest.raises(FileSizeLimitError):
            write_file(
                workspace_mount, "workspace", "large.bin",
                large_content, "test-agent", agent_mounts,
            )

    def test_write_permission_denied(self, workspace_mount):
        """Agent with readonly access cannot write."""
        with pytest.raises(PermissionDeniedError):
            write_file(
                workspace_mount, "workspace", "src/hack.py",
                "malicious", "readonly-agent",
                {"workspace": "readonly"},
            )

    def test_list_directory(self, workspace_mount, agent_mounts):
        result = list_directory(
            workspace_mount, "workspace", "src",
            "test-agent", agent_mounts,
        )
        names = [e["name"] for e in result["entries"]]
        assert "main.py" in names

    def test_stat_file(self, workspace_mount, agent_mounts):
        result = stat_file(
            workspace_mount, "workspace", "src/main.py",
            "test-agent", agent_mounts,
        )
        assert result["exists"] is True
        assert result["type"] == "file"
        assert result["size"] > 0

    def test_stat_nonexistent(self, workspace_mount, agent_mounts):
        result = stat_file(
            workspace_mount, "workspace", "ghost.py",
            "test-agent", agent_mounts,
        )
        assert result["exists"] is False

    def test_delete_file(self, workspace_mount, agent_mounts):
        # Create a file to delete
        mount = workspace_mount.get("workspace")
        target = mount.host_path / "to_delete.txt"
        target.write_text("bye")

        result = delete_file(
            workspace_mount, "workspace", "to_delete.txt",
            "test-agent", agent_mounts,
        )
        assert result["deleted"] is True
        assert not target.exists()

    def test_mkdir(self, workspace_mount, agent_mounts):
        result = make_directory(
            workspace_mount, "workspace", "new_dir/sub",
            "test-agent", agent_mounts,
        )
        assert result["created"] is True
        mount = workspace_mount.get("workspace")
        assert (mount.host_path / "new_dir" / "sub").is_dir()


# ──────────────────────────────────────────────────
# File watcher tests
# ──────────────────────────────────────────────────


class TestFileWatcher:

    @pytest.fixture
    def mock_publisher(self):
        publisher = AsyncMock(spec=["publish", "publish_raw"])
        return publisher

    @pytest.fixture
    def watched_dir(self, tmp_path):
        d = tmp_path / "watched"
        d.mkdir()
        (d / "file1.py").write_text("original")
        return d

    def test_subscription_added(self, mock_publisher, watched_dir):
        watcher = FileWatcher(mock_publisher, poll_interval=0.1)
        sub = FileSubscription(
            agent_id="dev",
            pattern="workspace/file1.py",
            events=["file:changed"],
            mount_root=watched_dir,
        )
        watcher.add_subscription(sub)
        assert len(watcher._subscriptions) == 1

    def test_session_subscription_cleared(self, mock_publisher, watched_dir):
        watcher = FileWatcher(mock_publisher, poll_interval=0.1)
        watcher.add_subscription(FileSubscription(
            agent_id="dev", pattern="workspace/**/*.py",
            events=["file:changed"], mount_root=watched_dir,
            session_scoped=True,
        ))
        watcher.add_subscription(FileSubscription(
            agent_id="dev", pattern="workspace/config.py",
            events=["file:changed"], mount_root=watched_dir,
            session_scoped=False,
        ))
        assert len(watcher._subscriptions) == 2
        watcher.remove_session_subscriptions()
        assert len(watcher._subscriptions) == 1
        assert watcher._subscriptions[0].session_scoped is False

    def test_hash_file(self, mock_publisher, watched_dir):
        watcher = FileWatcher(mock_publisher)
        snapshot = watcher._hash_file(str(watched_dir / "file1.py"))
        assert snapshot is not None
        expected = hashlib.sha256(b"original").hexdigest()
        assert snapshot.sha256 == expected

    def test_hash_nonexistent_returns_none(self, mock_publisher):
        watcher = FileWatcher(mock_publisher)
        assert watcher._hash_file("/nonexistent/path.txt") is None
```

---

## Integration Points

The filesystem MCP server integrates with several other FAITH components:

```python
# PA registers a dynamic file watch subscription at session start (FAITH-015)
await fs_server.register_dynamic_subscription(
    agent_id="test-engineer",
    pattern="workspace/src/auth/**/*.py",
    events=["file:changed", "file:created"],
)

# Agent makes a tool call via compact protocol (FAITH-012 MCP adapter)
# The PA translates this to a handle_tool_call invocation:
result = await fs_server.handle_tool_call(
    action="read",
    args={"mount": "workspace", "path": "src/auth.py"},
    agent_id="software-developer",
)

# Config hot-reload (FAITH-004) publishes system:config_changed
# The filesystem server listens and reloads mount configuration.

# File history (FAITH-023) hooks into write_file to version files
# before overwriting — that integration is added in FAITH-023.
```

```yaml
# Agent config.yaml — static file watch subscriptions (FAITH-003)
file_watches:
  - pattern: "workspace/tests/**/*.py"
    events: [file:changed, file:created]
  - pattern: "workspace/src/**/*.py"
    events: [file:changed]
```

---

## Acceptance Criteria

1. **Deny list enforcement:** `config/secrets.yaml`, `config/.env`, `**/secrets.yaml`, and `**/.env` are blocked for all operations (read, write, list, stat, delete) regardless of mount config or agent permissions. Blocked attempts are logged with agent ID, action, and path.
2. **Named mount resolution:** Agents reference mounts by logical name only. `MountRegistry` loads mounts from `.faith/tools/filesystem.yaml` and resolves paths to absolute host paths. Unknown mount names return a clear error.
3. **Permission resolution — specificity override:** Subfolder overrides in mount config take precedence over parent mount access (e.g. `workspace/config: readonly` overrides `workspace: readwrite`).
4. **Permission resolution — recursive default:** Mounts with `recursive: false` block access to child directories while permitting root-level files.
5. **Permission resolution — agent cap:** An agent's declared access for a mount cannot exceed the mount-level access. Agent `readwrite` on a `readonly` mount resolves to `readonly`.
6. **Permission resolution — most restrictive wins:** When mount-level and agent-level permissions both apply, the more restrictive of the two is enforced.
7. **Symlink escape prevention:** Symlinks resolving outside the mount boundary raise `SymlinkEscapeError`. Broken symlinks raise `BrokenSymlinkError`. Internal symlinks (within the same mount) are followed normally.
8. **File size limits:** Reads exceeding `max_file_size_mb` return an error with the file size and limit. Writes exceeding the limit are rejected before writing begins (no partial writes).
9. **File watching — poll cycle:** `FileWatcher` polls all subscribed paths every 5 seconds using SHA256 checksums. Only paths with active subscriptions are polled.
10. **File watching — event detection:** `file:changed` published when SHA256 differs from previous snapshot. `file:created` published for new files matching a subscription. `file:deleted` published when a previously tracked file disappears.
11. **File watching — static subscriptions:** Loaded from `.faith/agents/{id}/config.yaml` `file_watches` field at server startup.
12. **File watching — dynamic subscriptions:** Registered by PA via `register_dynamic_subscription()`. Session-scoped subscriptions cleared on `remove_session_subscriptions()`.
13. **Event publishing:** All operations publish `tool:call_started` and `tool:call_complete` (or `tool:permission_denied` / `tool:error`) events to `system-events`.
14. **Config hot-reload:** Server listens on `system-events` for `system:config_changed` affecting `filesystem.yaml` and reloads mount config without restart.
15. **All tests pass:** The 40+ tests in `tests/test_filesystem_server.py` cover deny list, mount resolution, permissions (all four rules), symlinks, file operations (read, write, list, stat, delete, mkdir), file size limits, and file watcher subscription management.

---

## Notes for Implementer

- **File history is FAITH-023:** This task does not implement file versioning. The `write_file` operation writes directly to disk. FAITH-023 adds a pre-write hook that creates a history snapshot before overwriting.
- **Deny list is hardcoded, not configurable:** The deny list in `deny_list.py` is intentionally not loaded from config. This is a defence-in-depth measure — even if an attacker compromises config files, they cannot whitelist secrets paths. The list is code, not configuration.
- **`.env.example` is not blocked:** The deny list patterns match exact filenames (`.env`, `secrets.yaml`), not substrings. Files like `.env.example`, `.env.local`, or `secrets_manager.py` are not blocked. Verify this in tests.
- **Mount paths use forward slashes internally:** All path handling normalises to forward slashes (POSIX-style) regardless of OS. The Docker container runs Linux, so this is the natural path separator.
- **Glob expansion in watcher:** The `_collect_watched_paths()` method uses Python's `glob.glob(recursive=True)` which supports `**` patterns. This is called every poll cycle, which is acceptable for the 5-second interval, but may need optimisation for very large workspaces. Consider caching glob results and only re-expanding on `file:created` or `file:deleted` events.
- **Event delivery target:** Per FRS Section 3.7.7, file events are published to the subscribing agent's channel, not to `system-events`. The current implementation publishes via `EventPublisher` which targets `system-events`. The implementer should either extend `EventPublisher` with a channel-targeted publish method or publish directly to the agent's Redis channel (`pa-{agent_id}`).
- **MCP SDK integration:** The `server.py` is structured as a dispatcher. The actual MCP protocol framing (JSON-RPC over stdio) should be wired in using the MCP Python SDK (`mcp` package). The `handle_tool_call` method maps directly to MCP tool handler registrations.
- **FakeRedis in tests:** Follow the pattern established in FAITH-010 — use lightweight fake Redis/PubSub classes rather than external `fakeredis` dependency.
- **Thread safety:** The `FileWatcher` runs as an asyncio task alongside the MCP request handler. Both access `_subscriptions` but writes happen infrequently (subscription registration). If race conditions arise, protect `_subscriptions` with an `asyncio.Lock`.

