"""
Description:
    Manage file version history snapshots for the FAITH filesystem MCP server.

Requirements:
    - Store bounded version snapshots outside Git-managed workspaces.
    - Support listing and restoring historical versions with metadata.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from faith_mcp.filesystem.git_detect import is_git_managed


@dataclass(slots=True)
class VersionMetadata:
    """
    Description:
        Describe one stored filesystem history snapshot.

    Requirements:
        - Preserve timestamp, agent, channel, audit, and summary metadata for a
          stored version.
    """

    ts: str
    agent: str
    channel: str
    msg_id: int
    audit_id: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the metadata object into a serialisable dictionary.

        Requirements:
            - Preserve every dataclass field in the output.

        :returns: Serializable metadata dictionary.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VersionMetadata:
        """
        Description:
            Rebuild metadata from a persisted dictionary.

        Requirements:
            - Accept the same key structure produced by `to_dict()`.

        :param data: Persisted metadata dictionary.
        :returns: Reconstructed version metadata object.
        """
        return cls(**data)

    @classmethod
    def from_file(cls, path: Path) -> VersionMetadata:
        """
        Description:
            Load version metadata from a JSON file on disk.

        Requirements:
            - Parse the stored JSON payload and rebuild the metadata object.

        :param path: Metadata JSON file path.
        :returns: Reconstructed version metadata object.
        """
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


class FileHistoryManager:
    """
    Description:
        Store and restore bounded filesystem history snapshots for one mount.

    Requirements:
        - Keep a bounded number of historical versions per relative path.
        - Disable history automatically for Git-managed host paths.

    :param mount_name: Name of the mount whose history is being tracked.
    :param faith_dir: Root FAITH data directory.
    :param host_path: Host path mounted into the filesystem server.
    :param depth: Maximum number of versions to retain per file.
    :param enabled: Whether history should be active for this mount.
    """

    def __init__(
        self,
        mount_name: str,
        faith_dir: Path,
        host_path: Path,
        depth: int = 10,
        enabled: bool = False,
    ):
        """
        Description:
            Configure the history manager for one mount.

        Requirements:
            - Disable history automatically when the host path belongs to a Git
              repository.
            - Create the history root directory when history is enabled.

        :param mount_name: Name of the mount whose history is being tracked.
        :param faith_dir: Root FAITH data directory.
        :param host_path: Host path mounted into the filesystem server.
        :param depth: Maximum number of versions to retain per file.
        :param enabled: Whether history should be active for this mount.
        """
        self.mount_name = mount_name
        self.faith_dir = Path(faith_dir)
        self.host_path = Path(host_path).resolve()
        self.depth = max(1, int(depth))
        self.history_root = self.faith_dir / "history" / mount_name
        self.enabled = bool(enabled) and not is_git_managed(self.host_path)
        if self.enabled:
            self.history_root.mkdir(parents=True, exist_ok=True)

    def _version_dir(self, relative_path: str) -> Path:
        """
        Description:
            Resolve the on-disk directory used to store history for one relative
            path.

        Requirements:
            - Normalise the relative path before joining it onto the history
              root.

        :param relative_path: Mount-relative path whose history is being tracked.
        :returns: Directory used for stored versions of the path.
        """
        normalised = PurePosixPath(relative_path).as_posix().lstrip("/")
        return self.history_root / Path(normalised)

    def _version_filename(self, slot: int, suffix: str) -> str:
        """
        Description:
            Build the data-file name for a version slot.

        Requirements:
            - Use zero-padded slot numbers for predictable ordering.

        :param slot: Version slot number.
        :param suffix: Original file suffix to preserve.
        :returns: Stored version file name.
        """
        return f"v{slot:02d}{suffix}"

    def _meta_filename(self, slot: int) -> str:
        """
        Description:
            Build the metadata-file name for a version slot.

        Requirements:
            - Use zero-padded slot numbers for predictable ordering.

        :param slot: Version slot number.
        :returns: Stored metadata file name.
        """
        return f"v{slot:02d}.meta.json"

    def _existing_slots(self, version_dir: Path) -> list[int]:
        """
        Description:
            Return the version slots that already exist for one history
            directory.

        Requirements:
            - Ignore malformed metadata file names.
            - Return unique slot numbers in ascending order.

        :param version_dir: History directory to inspect.
        :returns: Sorted version slot numbers.
        """
        slots: list[int] = []
        for meta_file in version_dir.glob("v*.meta.json"):
            name = meta_file.stem.split(".")[0]
            try:
                slots.append(int(name[1:]))
            except ValueError:
                continue
        return sorted(set(slots))

    def _find_next_slot(self, version_dir: Path, suffix: str) -> int:
        """
        Description:
            Choose the slot that should receive the next stored version.

        Requirements:
            - Use an unused slot when the history depth has not been reached.
            - Reuse the oldest slot when the history depth is already full.

        :param version_dir: History directory to inspect.
        :param suffix: Original file suffix for the snapshot being stored.
        :returns: Slot number that should receive the next version.
        """
        slots = self._existing_slots(version_dir)
        if len(slots) < self.depth:
            return len(slots) + 1
        oldest_slot = slots[0]
        oldest_ts = None
        for slot in slots:
            meta_path = version_dir / self._meta_filename(slot)
            try:
                ts = VersionMetadata.from_file(meta_path).ts
            except Exception:
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
    ) -> int | None:
        """
        Description:
            Store one historical snapshot of a file.

        Requirements:
            - Return `None` when history is disabled or the source file is
              missing.
            - Copy both the file content and its metadata into the history store.

        :param relative_path: Mount-relative path whose history is being stored.
        :param source_file: Current file to snapshot.
        :param metadata: Metadata to persist with the snapshot.
        :returns: Slot number written, or `None` when nothing is stored.
        """
        if not self.enabled or not source_file.exists():
            return None
        version_dir = self._version_dir(relative_path)
        version_dir.mkdir(parents=True, exist_ok=True)
        suffix = source_file.suffix
        slot = self._find_next_slot(version_dir, suffix)
        shutil.copy2(source_file, version_dir / self._version_filename(slot, suffix))
        (version_dir / self._meta_filename(slot)).write_text(
            json.dumps(metadata.to_dict(), indent=2),
            encoding="utf-8",
        )
        return slot

    def list_history(self, relative_path: str) -> list[dict[str, Any]]:
        """
        Description:
            List the stored history metadata for one relative path.

        Requirements:
            - Return an empty list when history is disabled or no versions
              exist.
            - Ignore malformed metadata files rather than failing the whole
              listing.

        :param relative_path: Mount-relative path whose history should be listed.
        :returns: Sorted stored-version metadata dictionaries.
        """
        if not self.enabled:
            return []
        version_dir = self._version_dir(relative_path)
        if not version_dir.exists():
            return []
        versions: list[dict[str, Any]] = []
        for slot in self._existing_slots(version_dir):
            meta_path = version_dir / self._meta_filename(slot)
            if not meta_path.exists():
                continue
            try:
                entry = VersionMetadata.from_file(meta_path).to_dict()
            except Exception:
                continue
            entry["version"] = slot
            versions.append(entry)
        versions.sort(key=lambda item: item["ts"])
        return versions

    def get_version_path(self, relative_path: str, version: int) -> Path | None:
        """
        Description:
            Return the stored file path for a specific version slot.

        Requirements:
            - Return `None` when history is disabled, missing, or incomplete.

        :param relative_path: Mount-relative path whose version should be found.
        :param version: Version slot number to resolve.
        :returns: Stored version file path or `None`.
        """
        if not self.enabled:
            return None
        version_dir = self._version_dir(relative_path)
        if not version_dir.exists():
            return None
        for candidate in version_dir.iterdir():
            if candidate.name.startswith(f"v{version:02d}.") and not candidate.name.endswith(
                ".meta.json"
            ):
                return candidate
            if candidate.name == f"v{version:02d}":
                return candidate
        return None

    def restore_version(
        self,
        relative_path: str,
        version: int,
        dest_file: Path,
        restore_metadata: VersionMetadata,
    ) -> bool:
        """
        Description:
            Restore one stored version back to the destination file path.

        Requirements:
            - Return `False` when history is disabled or the requested version
              does not exist.
            - Store a fresh history snapshot after the restore operation.

        :param relative_path: Mount-relative path whose version should be restored.
        :param version: Version slot number to restore.
        :param dest_file: Destination file that should receive the restored content.
        :param restore_metadata: Metadata recorded for the restore action.
        :returns: `True` when the restore succeeds, otherwise `False`.
        """
        if not self.enabled:
            return False
        version_path = self.get_version_path(relative_path, version)
        if version_path is None:
            return False
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(version_path, dest_file)
        self.store_version(relative_path, dest_file, restore_metadata)
        return True


def make_metadata(
    agent: str,
    summary: str,
    channel: str = "filesystem",
    msg_id: int = 0,
    audit_id: str = "",
) -> VersionMetadata:
    """
    Description:
        Build version metadata for a filesystem history operation.

    Requirements:
        - Stamp metadata with the current UTC time.
        - Preserve the supplied agent, channel, message, audit, and summary data.

    :param agent: Agent performing the operation.
    :param summary: Human-readable summary of the operation.
    :param channel: Channel associated with the operation.
    :param msg_id: Message identifier associated with the operation.
    :param audit_id: Audit log identifier for the operation.
    :returns: Version metadata for the stored snapshot.
    """
    return VersionMetadata(
        ts=datetime.now(timezone.utc).isoformat(),
        agent=agent,
        channel=channel,
        msg_id=msg_id,
        audit_id=audit_id,
        summary=summary,
    )
