# FAITH-023 — Filesystem File History

**Phase:** 6 — Tool Servers
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-022
**FRS Reference:** Section 4.3.5

---

## Objective

Implement round-robin file versioning for the filesystem MCP server. Every file written through the filesystem tool gets a versioned copy stored under a `history/` directory, mirroring the workspace path structure. Each version has a `.meta.json` sidecar linking it to the audit log, agent, and task that produced it. Provide `list_history` and `restore_version` MCP commands for the PA to query and restore previous file states. Auto-skip versioning for git-managed mounts. Configurable per mount via `history: true/false` and `history_depth: N`.

---

## Architecture

```
faith/tools/filesystem/
├── __init__.py
├── server.py            ← existing filesystem MCP server (FAITH-022)
├── history.py           ← FileHistoryManager (this task)
└── git_detect.py        ← git repository detection (this task)

history/                  ← version storage root (in .faith project root)
└── {mount_name}/
    └── {relative/path/to/file}/
        ├── v01.{ext}
        ├── v01.meta.json
        ├── v02.{ext}
        ├── v02.meta.json
        ...
        └── v{NN}.{ext}
        └── v{NN}.meta.json
```

---

## Files to Create

### 1. `faith/tools/filesystem/git_detect.py`

```python
"""Git repository detection for file history auto-skip.

Checks whether a given host_path resides inside a git repository by
walking up the directory tree looking for a `.git` directory or file.
If found, file history is automatically skipped for that mount.

FRS Reference: Section 4.3.5
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("faith.tools.filesystem.git_detect")


def is_git_managed(path: Path) -> bool:
    """Check whether a path is inside a git repository.

    Walks up the directory tree from the given path looking for a
    `.git` directory or `.git` file (submodule worktrees use a file).

    Args:
        path: The path to check. Can be a file or directory.

    Returns:
        True if the path is inside a git repository, False otherwise.
    """
    resolved = path.resolve()

    # Start from the directory containing the path (or the path itself if it's a dir)
    check = resolved if resolved.is_dir() else resolved.parent

    while True:
        git_indicator = check / ".git"
        if git_indicator.exists():
            logger.info(
                f"Git repository detected at {check} — "
                f"file history will be skipped for paths under {path}"
            )
            return True

        parent = check.parent
        if parent == check:
            # Reached filesystem root
            break
        check = parent

    return False
```

### 2. `faith/tools/filesystem/history.py`

```python
"""File history manager — round-robin versioning for filesystem writes.

Maintains a lightweight version history for every file written through
the filesystem tool. Versions are stored under a `history/` directory
in the FAITH project root, mirroring the mount's path structure.

Each version has a metadata sidecar (.meta.json) linking it back to
the audit log entry and the agent task that produced it.

FRS Reference: Section 4.3.5
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from faith.tools.filesystem.git_detect import is_git_managed

logger = logging.getLogger("faith.tools.filesystem.history")


class VersionMetadata:
    """Metadata sidecar for a single file version.

    Attributes:
        ts: ISO 8601 timestamp of the version.
        agent: Agent ID that performed the write.
        channel: Channel where the write was initiated.
        msg_id: Compact protocol message ID that triggered the write.
        audit_id: Corresponding audit log entry ID.
        summary: Human-readable summary of the change.
    """

    def __init__(
        self,
        ts: str,
        agent: str,
        channel: str,
        msg_id: int,
        audit_id: str,
        summary: str,
    ):
        self.ts = ts
        self.agent = agent
        self.channel = channel
        self.msg_id = msg_id
        self.audit_id = audit_id
        self.summary = summary

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dictionary for JSON output."""
        return {
            "ts": self.ts,
            "agent": self.agent,
            "channel": self.channel,
            "msg_id": self.msg_id,
            "audit_id": self.audit_id,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VersionMetadata:
        """Deserialise from a dictionary."""
        return cls(
            ts=data["ts"],
            agent=data["agent"],
            channel=data["channel"],
            msg_id=data["msg_id"],
            audit_id=data["audit_id"],
            summary=data["summary"],
        )

    @classmethod
    def from_file(cls, path: Path) -> VersionMetadata:
        """Load metadata from a .meta.json file.

        Args:
            path: Path to the .meta.json file.

        Returns:
            Parsed VersionMetadata instance.

        Raises:
            FileNotFoundError: If the metadata file does not exist.
            json.JSONDecodeError: If the file contains invalid JSON.
            KeyError: If a required field is missing.
        """
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return cls.from_dict(data)


class FileHistoryManager:
    """Manages round-robin file versioning for a single mount.

    Each mount has its own FileHistoryManager instance, configured
    with the mount's history settings. The manager is responsible for:

    - Storing new versions when files are written
    - Listing available versions for a file
    - Restoring a specific version to the original path
    - Round-robin eviction when depth is exceeded

    Attributes:
        mount_name: The mount name (e.g. "workspace").
        history_root: Path to the history/ directory in the FAITH project root.
        depth: Maximum number of versions to retain per file.
        enabled: Whether file history is active for this mount.
    """

    def __init__(
        self,
        mount_name: str,
        faith_dir: Path,
        host_path: Path,
        depth: int = 10,
        enabled: bool = False,
    ):
        """Initialise the file history manager.

        Args:
            mount_name: The name of the mount (e.g. "workspace").
            faith_dir: Path to the .faith project directory.
            host_path: The mount's host_path (used for git detection).
            depth: Max versions to retain per file (round-robin).
            enabled: Whether history is enabled in config.
        """
        self.mount_name = mount_name
        self.faith_dir = faith_dir
        self.history_root = faith_dir / "history" / mount_name
        self.depth = max(1, depth)
        self.host_path = Path(host_path).resolve()

        # Auto-skip if git-managed
        if enabled and is_git_managed(self.host_path):
            logger.info(
                f"Mount '{mount_name}' is git-managed — "
                f"file history auto-skipped"
            )
            self.enabled = False
        else:
            self.enabled = enabled

        if self.enabled:
            self.history_root.mkdir(parents=True, exist_ok=True)
            logger.info(
                f"File history enabled for mount '{mount_name}' "
                f"(depth={self.depth})"
            )

    def _version_dir(self, relative_path: str) -> Path:
        """Get the history directory for a specific file.

        Args:
            relative_path: File path relative to the mount root
                (e.g. "src/auth.py").

        Returns:
            Path to the directory where versions of this file are stored.
        """
        return self.history_root / relative_path

    def _version_filename(self, slot: int, extension: str) -> str:
        """Build the versioned filename for a given slot number.

        Args:
            slot: Version slot number (1-based).
            extension: File extension including the dot (e.g. ".py").

        Returns:
            Filename like "v03.py".
        """
        return f"v{slot:02d}{extension}"

    def _meta_filename(self, slot: int) -> str:
        """Build the metadata sidecar filename for a given slot.

        Args:
            slot: Version slot number (1-based).

        Returns:
            Filename like "v03.meta.json".
        """
        return f"v{slot:02d}.meta.json"

    def _find_next_slot(self, version_dir: Path, extension: str) -> int:
        """Determine the next version slot to write to.

        If fewer than `depth` versions exist, appends to the next slot.
        If `depth` versions exist, returns the oldest slot (round-robin).

        Args:
            version_dir: The directory containing versions for this file.
            extension: File extension for matching existing versions.

        Returns:
            The 1-based slot number to write to.
        """
        existing_slots: list[int] = []
        for i in range(1, self.depth + 1):
            version_file = version_dir / self._version_filename(i, extension)
            if version_file.exists():
                existing_slots.append(i)

        if len(existing_slots) < self.depth:
            # Haven't filled all slots yet — use the next one
            return len(existing_slots) + 1

        # All slots full — find the oldest by timestamp
        oldest_slot = existing_slots[0]
        oldest_ts = None

        for slot in existing_slots:
            meta_path = version_dir / self._meta_filename(slot)
            try:
                meta = VersionMetadata.from_file(meta_path)
                ts = meta.ts
            except Exception:
                # If metadata is missing/corrupt, treat as oldest
                return slot

            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
                oldest_slot = slot

        return oldest_slot

    def store_version(
        self,
        relative_path: str,
        source_file: Path,
        metadata: VersionMetadata,
    ) -> Optional[int]:
        """Store a new version of a file.

        Copies the file to the history directory and writes the
        metadata sidecar. Uses round-robin eviction when depth
        is exceeded.

        Args:
            relative_path: File path relative to the mount root.
            source_file: Absolute path to the file to version.
            metadata: Version metadata to store.

        Returns:
            The version slot number that was written, or None if
            history is disabled.
        """
        if not self.enabled:
            return None

        if not source_file.exists():
            logger.warning(
                f"Cannot store version — source file does not exist: "
                f"{source_file}"
            )
            return None

        version_dir = self._version_dir(relative_path)
        version_dir.mkdir(parents=True, exist_ok=True)

        extension = source_file.suffix or ""
        slot = self._find_next_slot(version_dir, extension)

        # Copy file content
        version_file = version_dir / self._version_filename(slot, extension)
        shutil.copy2(str(source_file), str(version_file))

        # Write metadata sidecar
        meta_file = version_dir / self._meta_filename(slot)
        meta_file.write_text(
            json.dumps(metadata.to_dict(), indent=2),
            encoding="utf-8",
        )

        logger.info(
            f"Stored version v{slot:02d} for "
            f"{self.mount_name}/{relative_path} "
            f"(agent={metadata.agent}, audit={metadata.audit_id})"
        )
        return slot

    def list_history(self, relative_path: str) -> list[dict[str, Any]]:
        """List all available versions for a file.

        Returns versions sorted by timestamp (oldest first), each
        with the version number, timestamp, agent, and summary
        from the metadata sidecar.

        Args:
            relative_path: File path relative to the mount root.

        Returns:
            List of version info dicts, each containing:
            - version: int (slot number)
            - ts: str (ISO 8601 timestamp)
            - agent: str
            - channel: str
            - msg_id: int
            - audit_id: str
            - summary: str

            Returns an empty list if no history exists or history
            is disabled.
        """
        if not self.enabled:
            return []

        version_dir = self._version_dir(relative_path)
        if not version_dir.exists():
            return []

        versions: list[dict[str, Any]] = []

        for i in range(1, self.depth + 1):
            meta_path = version_dir / self._meta_filename(i)
            if not meta_path.exists():
                continue

            try:
                meta = VersionMetadata.from_file(meta_path)
                entry = meta.to_dict()
                entry["version"] = i
                versions.append(entry)
            except Exception as e:
                logger.warning(
                    f"Failed to read metadata for v{i:02d} of "
                    f"{relative_path}: {e}"
                )

        # Sort by timestamp (oldest first)
        versions.sort(key=lambda v: v["ts"])
        return versions

    def restore_version(
        self,
        relative_path: str,
        version: int,
        dest_file: Path,
        restore_metadata: VersionMetadata,
    ) -> bool:
        """Restore a specific version of a file to the original path.

        Copies the versioned file back to the destination path. The
        restoration itself creates a new history entry — the round-robin
        is never unwound, only appended to.

        Args:
            relative_path: File path relative to the mount root.
            version: The version slot number to restore (1-based).
            dest_file: Absolute path to restore the file to.
            restore_metadata: Metadata for the restoration event
                (recorded as a new history entry).

        Returns:
            True if the restore succeeded, False otherwise.
        """
        if not self.enabled:
            logger.warning("Cannot restore — file history is disabled")
            return False

        version_dir = self._version_dir(relative_path)

        # Find the versioned file by slot number
        matched_file: Optional[Path] = None
        for candidate in version_dir.iterdir():
            if candidate.name.startswith(f"v{version:02d}.") and not candidate.name.endswith(".meta.json"):
                matched_file = candidate
                break

        if matched_file is None:
            logger.warning(
                f"Version v{version:02d} not found for "
                f"{self.mount_name}/{relative_path}"
            )
            return False

        # Copy version back to original path
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(matched_file), str(dest_file))

        logger.info(
            f"Restored v{version:02d} of "
            f"{self.mount_name}/{relative_path} → {dest_file}"
        )

        # Store the restoration as a new history entry
        self.store_version(relative_path, dest_file, restore_metadata)

        return True

    def get_version_path(self, relative_path: str, version: int) -> Optional[Path]:
        """Get the filesystem path to a specific version file.

        Useful for diffing or reading version contents without restoring.

        Args:
            relative_path: File path relative to the mount root.
            version: The version slot number (1-based).

        Returns:
            Path to the version file, or None if it does not exist.
        """
        version_dir = self._version_dir(relative_path)
        if not version_dir.exists():
            return None

        for candidate in version_dir.iterdir():
            if candidate.name.startswith(f"v{version:02d}.") and not candidate.name.endswith(".meta.json"):
                return candidate

        return None
```

### 3. MCP Command Additions to `faith/tools/filesystem/server.py`

The following MCP commands are added to the existing filesystem MCP server (FAITH-022). They are registered as additional tools on the same server.

```python
# --- Add to existing imports in server.py ---
from faith.tools.filesystem.history import FileHistoryManager, VersionMetadata
from datetime import datetime, timezone


# --- Add to server initialisation (where mounts are configured) ---

# Build a FileHistoryManager for each mount
# self._history_managers: dict[str, FileHistoryManager] = {}
#
# for mount_name, mount_config in mounts.items():
#     self._history_managers[mount_name] = FileHistoryManager(
#         mount_name=mount_name,
#         faith_dir=self.faith_dir,
#         host_path=Path(mount_config["host_path"]).expanduser(),
#         depth=mount_config.get("history_depth", 10),
#         enabled=mount_config.get("history", False),
#     )


# --- Hook into existing write_file / edit_file handlers ---

# After every successful file write, call:
#
# def _record_file_history(
#     self,
#     mount_name: str,
#     relative_path: str,
#     absolute_path: Path,
#     agent: str,
#     channel: str,
#     msg_id: int,
#     audit_id: str,
#     summary: str,
# ) -> None:
#     """Record a file version after a write operation."""
#     manager = self._history_managers.get(mount_name)
#     if manager is None:
#         return
#     metadata = VersionMetadata(
#         ts=datetime.now(timezone.utc).isoformat(),
#         agent=agent,
#         channel=channel,
#         msg_id=msg_id,
#         audit_id=audit_id,
#         summary=summary,
#     )
#     manager.store_version(relative_path, absolute_path, metadata)


@server.tool()
async def list_history(path: str) -> dict:
    """List all available versions for a file.

    Args:
        path: File path in mount format (e.g. "workspace://src/auth.py").

    Returns:
        Dict with:
        - path: The requested path.
        - mount: The mount name.
        - versions: List of version info dicts (sorted oldest-first),
          each containing version, ts, agent, channel, msg_id,
          audit_id, summary.
        - count: Number of available versions.
    """
    mount_name, relative_path = self._parse_mount_path(path)
    manager = self._history_managers.get(mount_name)

    if manager is None or not manager.enabled:
        return {
            "path": path,
            "mount": mount_name,
            "versions": [],
            "count": 0,
            "error": "File history is not enabled for this mount",
        }

    versions = manager.list_history(relative_path)
    return {
        "path": path,
        "mount": mount_name,
        "versions": versions,
        "count": len(versions),
    }


@server.tool()
async def restore_version(path: str, version: int) -> dict:
    """Restore a specific version of a file to the original path.

    The restoration itself creates a new history entry — the round-robin
    is never unwound, only appended to.

    Args:
        path: File path in mount format (e.g. "workspace://src/auth.py").
        version: The version number to restore (from list_history output).

    Returns:
        Dict with:
        - path: The restored file path.
        - version: The version that was restored.
        - success: Boolean indicating success.
        - error: Error message if restoration failed (optional).
    """
    mount_name, relative_path = self._parse_mount_path(path)
    manager = self._history_managers.get(mount_name)

    if manager is None or not manager.enabled:
        return {
            "path": path,
            "version": version,
            "success": False,
            "error": "File history is not enabled for this mount",
        }

    # Resolve the destination file path
    dest_file = self._resolve_path(mount_name, relative_path)

    # Build metadata for the restoration event
    restore_metadata = VersionMetadata(
        ts=datetime.now(timezone.utc).isoformat(),
        agent="pa",
        channel="",
        msg_id=0,
        audit_id="",
        summary=f"Restored from version v{version:02d}",
    )

    success = manager.restore_version(
        relative_path, version, dest_file, restore_metadata
    )

    if success:
        return {
            "path": path,
            "version": version,
            "success": True,
        }
    else:
        return {
            "path": path,
            "version": version,
            "success": False,
            "error": f"Version v{version:02d} not found for {path}",
        }
```

### 4. `tests/test_file_history.py`

```python
"""Tests for the FAITH filesystem file history manager.

Covers version storage, round-robin eviction, metadata sidecars,
list_history, restore_version, git auto-skip, and edge cases.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from faith.tools.filesystem.git_detect import is_git_managed
from faith.tools.filesystem.history import (
    FileHistoryManager,
    VersionMetadata,
)


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


def _make_metadata(
    agent: str = "software-developer",
    channel: str = "ch-auth",
    msg_id: int = 1,
    audit_id: str = "aud-00001",
    summary: str = "Test change",
) -> VersionMetadata:
    """Helper to create a VersionMetadata instance."""
    return VersionMetadata(
        ts=datetime.now(timezone.utc).isoformat(),
        agent=agent,
        channel=channel,
        msg_id=msg_id,
        audit_id=audit_id,
        summary=summary,
    )


@pytest.fixture
def faith_dir(tmp_path):
    """Create a temporary .faith directory."""
    d = tmp_path / ".faith"
    d.mkdir()
    return d


@pytest.fixture
def workspace_dir(tmp_path):
    """Create a temporary workspace directory (not git-managed)."""
    d = tmp_path / "workspace"
    d.mkdir()
    return d


@pytest.fixture
def sample_file(workspace_dir):
    """Create a sample source file in the workspace."""
    src_dir = workspace_dir / "src"
    src_dir.mkdir()
    f = src_dir / "auth.py"
    f.write_text("def login():\n    pass\n", encoding="utf-8")
    return f


@pytest.fixture
def manager(faith_dir, workspace_dir):
    """Create a FileHistoryManager with history enabled."""
    return FileHistoryManager(
        mount_name="workspace",
        faith_dir=faith_dir,
        host_path=workspace_dir,
        depth=3,
        enabled=True,
    )


# ──────────────────────────────────────────────────
# Git detection tests
# ──────────────────────────────────────────────────


def test_git_detected_when_git_dir_present(tmp_path):
    """Detects git repository when .git directory exists."""
    (tmp_path / ".git").mkdir()
    subdir = tmp_path / "src" / "lib"
    subdir.mkdir(parents=True)
    assert is_git_managed(subdir) is True


def test_git_detected_when_git_file_present(tmp_path):
    """Detects git submodule worktree when .git is a file."""
    (tmp_path / ".git").write_text("gitdir: ../.git/modules/sub", encoding="utf-8")
    assert is_git_managed(tmp_path) is True


def test_git_not_detected_in_plain_directory(tmp_path):
    """Returns False for directories outside any git repository."""
    assert is_git_managed(tmp_path) is False


def test_git_auto_skip_disables_history(faith_dir, tmp_path):
    """History is disabled when mount is inside a git repository."""
    git_project = tmp_path / "git-project"
    git_project.mkdir()
    (git_project / ".git").mkdir()

    mgr = FileHistoryManager(
        mount_name="code",
        faith_dir=faith_dir,
        host_path=git_project,
        depth=10,
        enabled=True,
    )
    assert mgr.enabled is False


# ──────────────────────────────────────────────────
# Version storage tests
# ──────────────────────────────────────────────────


def test_store_version_creates_file_and_metadata(manager, sample_file):
    """Storing a version creates both the version file and .meta.json."""
    meta = _make_metadata()
    slot = manager.store_version("src/auth.py", sample_file, meta)

    assert slot == 1

    version_dir = manager.history_root / "src" / "auth.py"
    assert (version_dir / "v01.py").exists()
    assert (version_dir / "v01.meta.json").exists()

    # Verify file content was copied
    copied = (version_dir / "v01.py").read_text(encoding="utf-8")
    assert "def login" in copied

    # Verify metadata content
    meta_data = json.loads(
        (version_dir / "v01.meta.json").read_text(encoding="utf-8")
    )
    assert meta_data["agent"] == "software-developer"
    assert meta_data["channel"] == "ch-auth"
    assert meta_data["audit_id"] == "aud-00001"


def test_store_version_increments_slots(manager, sample_file):
    """Successive versions use incrementing slot numbers."""
    for i in range(1, 4):
        sample_file.write_text(f"version {i}", encoding="utf-8")
        slot = manager.store_version(
            "src/auth.py",
            sample_file,
            _make_metadata(msg_id=i, audit_id=f"aud-{i:05d}"),
        )
        assert slot == i


def test_store_version_round_robin_evicts_oldest(manager, sample_file):
    """After depth is reached, the oldest slot is overwritten."""
    # Fill all 3 slots
    for i in range(1, 4):
        sample_file.write_text(f"version {i}", encoding="utf-8")
        manager.store_version(
            "src/auth.py",
            sample_file,
            _make_metadata(
                msg_id=i,
                audit_id=f"aud-{i:05d}",
                summary=f"Change {i}",
            ),
        )

    # Store a 4th version — should overwrite slot 1 (oldest)
    sample_file.write_text("version 4", encoding="utf-8")
    slot = manager.store_version(
        "src/auth.py",
        sample_file,
        _make_metadata(msg_id=4, audit_id="aud-00004", summary="Change 4"),
    )
    assert slot == 1

    # Verify slot 1 now has the new content
    version_dir = manager.history_root / "src" / "auth.py"
    content = (version_dir / "v01.py").read_text(encoding="utf-8")
    assert content == "version 4"


def test_store_version_returns_none_when_disabled(faith_dir, workspace_dir, sample_file):
    """Returns None when history is disabled."""
    mgr = FileHistoryManager(
        mount_name="workspace",
        faith_dir=faith_dir,
        host_path=workspace_dir,
        depth=3,
        enabled=False,
    )
    result = mgr.store_version("src/auth.py", sample_file, _make_metadata())
    assert result is None


def test_store_version_handles_missing_source(manager, tmp_path):
    """Returns None when the source file does not exist."""
    fake_path = tmp_path / "nonexistent.py"
    result = manager.store_version("nonexistent.py", fake_path, _make_metadata())
    assert result is None


def test_store_version_handles_files_without_extension(manager, workspace_dir):
    """Files without extensions are versioned correctly."""
    f = workspace_dir / "Makefile"
    f.write_text("all:\n\techo hello\n", encoding="utf-8")
    slot = manager.store_version("Makefile", f, _make_metadata())
    assert slot == 1

    version_dir = manager.history_root / "Makefile"
    assert (version_dir / "v01").exists()
    assert (version_dir / "v01.meta.json").exists()


# ──────────────────────────────────────────────────
# list_history tests
# ──────────────────────────────────────────────────


def test_list_history_returns_all_versions(manager, sample_file):
    """list_history returns all stored versions sorted by timestamp."""
    for i in range(1, 4):
        sample_file.write_text(f"version {i}", encoding="utf-8")
        manager.store_version(
            "src/auth.py",
            sample_file,
            _make_metadata(msg_id=i, summary=f"Change {i}"),
        )

    versions = manager.list_history("src/auth.py")
    assert len(versions) == 3
    assert versions[0]["version"] in (1, 2, 3)
    assert all("ts" in v for v in versions)
    assert all("agent" in v for v in versions)
    assert all("summary" in v for v in versions)


def test_list_history_empty_when_no_versions(manager):
    """list_history returns empty list for files with no history."""
    versions = manager.list_history("src/nonexistent.py")
    assert versions == []


def test_list_history_empty_when_disabled(faith_dir, workspace_dir):
    """list_history returns empty list when history is disabled."""
    mgr = FileHistoryManager(
        mount_name="workspace",
        faith_dir=faith_dir,
        host_path=workspace_dir,
        depth=3,
        enabled=False,
    )
    versions = mgr.list_history("src/auth.py")
    assert versions == []


def test_list_history_sorted_by_timestamp(manager, sample_file):
    """Versions are sorted oldest-first by timestamp."""
    for i in range(1, 4):
        sample_file.write_text(f"version {i}", encoding="utf-8")
        manager.store_version(
            "src/auth.py",
            sample_file,
            _make_metadata(msg_id=i, summary=f"Change {i}"),
        )

    versions = manager.list_history("src/auth.py")
    timestamps = [v["ts"] for v in versions]
    assert timestamps == sorted(timestamps)


# ──────────────────────────────────────────────────
# restore_version tests
# ──────────────────────────────────────────────────


def test_restore_version_copies_file_to_destination(manager, sample_file):
    """restore_version copies the versioned file back to the original path."""
    # Store original
    sample_file.write_text("original content", encoding="utf-8")
    manager.store_version("src/auth.py", sample_file, _make_metadata(summary="Original"))

    # Overwrite the file
    sample_file.write_text("overwritten content", encoding="utf-8")
    manager.store_version("src/auth.py", sample_file, _make_metadata(summary="Overwrite"))

    # Restore version 1
    restore_meta = _make_metadata(agent="pa", summary="Restored from version v01")
    success = manager.restore_version("src/auth.py", 1, sample_file, restore_meta)

    assert success is True
    assert sample_file.read_text(encoding="utf-8") == "original content"


def test_restore_version_creates_new_history_entry(manager, sample_file):
    """Restoring a version creates a new history entry (never unwinds)."""
    sample_file.write_text("v1 content", encoding="utf-8")
    manager.store_version("src/auth.py", sample_file, _make_metadata(summary="V1"))

    sample_file.write_text("v2 content", encoding="utf-8")
    manager.store_version("src/auth.py", sample_file, _make_metadata(summary="V2"))

    # Restore version 1
    restore_meta = _make_metadata(agent="pa", summary="Restored v01")
    manager.restore_version("src/auth.py", 1, sample_file, restore_meta)

    # Should now have 3 versions (original v1, v2, and the restore)
    versions = manager.list_history("src/auth.py")
    assert len(versions) == 3


def test_restore_version_returns_false_for_missing_version(manager, sample_file):
    """Returns False when the requested version does not exist."""
    success = manager.restore_version(
        "src/auth.py", 99, sample_file, _make_metadata()
    )
    assert success is False


def test_restore_version_returns_false_when_disabled(faith_dir, workspace_dir, sample_file):
    """Returns False when history is disabled."""
    mgr = FileHistoryManager(
        mount_name="workspace",
        faith_dir=faith_dir,
        host_path=workspace_dir,
        depth=3,
        enabled=False,
    )
    success = mgr.restore_version(
        "src/auth.py", 1, sample_file, _make_metadata()
    )
    assert success is False


# ──────────────────────────────────────────────────
# VersionMetadata tests
# ──────────────────────────────────────────────────


def test_metadata_round_trip():
    """Metadata serialises and deserialises correctly."""
    meta = _make_metadata(
        agent="test-dev",
        channel="ch-test",
        msg_id=42,
        audit_id="aud-99999",
        summary="Round-trip test",
    )
    data = meta.to_dict()
    restored = VersionMetadata.from_dict(data)

    assert restored.agent == "test-dev"
    assert restored.channel == "ch-test"
    assert restored.msg_id == 42
    assert restored.audit_id == "aud-99999"
    assert restored.summary == "Round-trip test"


def test_metadata_from_file(tmp_path):
    """Metadata can be loaded from a .meta.json file."""
    meta = _make_metadata(agent="file-loader")
    meta_path = tmp_path / "v01.meta.json"
    meta_path.write_text(json.dumps(meta.to_dict()), encoding="utf-8")

    loaded = VersionMetadata.from_file(meta_path)
    assert loaded.agent == "file-loader"


def test_metadata_from_file_raises_on_missing(tmp_path):
    """from_file raises FileNotFoundError for missing files."""
    with pytest.raises(FileNotFoundError):
        VersionMetadata.from_file(tmp_path / "nonexistent.meta.json")


# ──────────────────────────────────────────────────
# get_version_path tests
# ──────────────────────────────────────────────────


def test_get_version_path_returns_path(manager, sample_file):
    """get_version_path returns the path to a stored version."""
    manager.store_version("src/auth.py", sample_file, _make_metadata())
    path = manager.get_version_path("src/auth.py", 1)
    assert path is not None
    assert path.exists()


def test_get_version_path_returns_none_for_missing(manager):
    """get_version_path returns None for non-existent versions."""
    path = manager.get_version_path("src/auth.py", 1)
    assert path is None


# ──────────────────────────────────────────────────
# Depth configuration tests
# ──────────────────────────────────────────────────


def test_depth_minimum_is_one(faith_dir, workspace_dir):
    """Depth is clamped to a minimum of 1."""
    mgr = FileHistoryManager(
        mount_name="workspace",
        faith_dir=faith_dir,
        host_path=workspace_dir,
        depth=0,
        enabled=True,
    )
    assert mgr.depth == 1
```

---

## Integration Points

The file history system integrates with the following FAITH components:

```python
# Filesystem MCP server (FAITH-022) hooks into every write operation:
# After write_file() or edit_file() completes, the server calls
# _record_file_history() with the write context from the MCP request.

# The PA (FAITH-014) uses list_history and restore_version in response
# to natural language requests from the user:
#
# User: "show me the history of auth.py"
# PA → filesystem tool: list_history("workspace://src/auth.py")
# PA → user: "I found 5 versions of auth.py: ..."
#
# User: "restore auth.py to two versions ago"
# PA → filesystem tool: restore_version("workspace://src/auth.py", 3)
# PA → user: "Restored auth.py to version 3 (Implemented JWT refresh)"

# The audit log (FAITH-020) records every restore_version call
# as a write operation, with the audit_id linking back to the
# version metadata sidecar.

# Configuration (FAITH-003) validates the history and history_depth
# fields in .faith/tools/filesystem.yaml mount definitions:
#
# filesystem:
#   mounts:
#     workspace:
#       host_path: ~/projects/my-project
#       access: readwrite
#       history: true
#       history_depth: 10
```

---

## Acceptance Criteria

1. `FileHistoryManager` stores a versioned copy of every file written through the filesystem tool, with the correct file extension preserved.
2. Each version has a `.meta.json` sidecar containing `ts`, `agent`, `channel`, `msg_id`, `audit_id`, and `summary` fields.
3. Version storage mirrors the workspace path structure under `history/{mount_name}/`.
4. Round-robin eviction overwrites the oldest version when `history_depth` is exceeded, determined by comparing metadata timestamps.
5. `is_git_managed()` correctly detects `.git` directories and `.git` files (submodule worktrees) by walking up the directory tree.
6. File history is automatically disabled for mounts whose `host_path` is inside a git repository, even if `history: true` is set in config.
7. `list_history(path)` MCP command returns all available versions sorted oldest-first with metadata from each sidecar.
8. `restore_version(path, version)` MCP command copies the specified version back to the original path and creates a new history entry for the restoration.
9. File history is disabled by default and must be explicitly enabled per mount via `history: true`.
10. `history_depth` defaults to 10 and is configurable per mount.
11. Binary files are versioned as-is with no diffing applied.
12. Files without extensions are versioned correctly (e.g. `Makefile` becomes `v01`, `v01.meta.json`).
13. All 24 tests in `tests/test_file_history.py` pass, covering git detection, version storage, round-robin eviction, list_history, restore_version, metadata serialisation, edge cases, and depth configuration.

---

## Notes for Implementer

- **Git detection uses filesystem only**: `is_git_managed()` walks the directory tree looking for `.git` — it does not shell out to `git` commands. This means it works even if `git` is not installed (the FRS lists git as optional).
- **Round-robin slot selection**: When all slots are full, the oldest is determined by comparing `ts` fields in the metadata sidecars, not by slot number. This is important because restoration creates new entries that may overwrite any slot.
- **Restoration creates history**: When `restore_version` is called, it first copies the version file to the destination, then calls `store_version` on the restored file. This means the round-robin always moves forward — the PA never needs to worry about "undoing" a restore.
- **MCP command context**: The `restore_version` MCP command receives `agent`, `channel`, `msg_id`, and `audit_id` from the MCP request context (populated by the PA or the approval system). The stub in this task uses placeholder values — the actual integration with the MCP request context is handled by FAITH-022's request pipeline.
- **No file locking**: The filesystem tool (FAITH-022) already serialises write operations per mount. File history piggybacks on this guarantee — there is no additional locking needed in the history manager.
- **Path normalisation**: `relative_path` arguments use forward slashes on all platforms (the filesystem MCP server normalises paths before passing them to the history manager).
- **History directory location**: Versions are stored under `.faith/history/`, not in the workspace itself. This keeps the workspace clean and avoids confusing user tools or git with version artefacts.

