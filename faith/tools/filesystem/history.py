"""File history manager for filesystem writes."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from faith.tools.filesystem.git_detect import is_git_managed


@dataclass(slots=True)
class VersionMetadata:
    ts: str
    agent: str
    channel: str
    msg_id: int
    audit_id: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VersionMetadata:
        return cls(**data)

    @classmethod
    def from_file(cls, path: Path) -> VersionMetadata:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


class FileHistoryManager:
    def __init__(
        self,
        mount_name: str,
        faith_dir: Path,
        host_path: Path,
        depth: int = 10,
        enabled: bool = False,
    ):
        self.mount_name = mount_name
        self.faith_dir = Path(faith_dir)
        self.host_path = Path(host_path).resolve()
        self.depth = max(1, int(depth))
        self.history_root = self.faith_dir / "history" / mount_name
        self.enabled = bool(enabled) and not is_git_managed(self.host_path)
        if self.enabled:
            self.history_root.mkdir(parents=True, exist_ok=True)

    def _version_dir(self, relative_path: str) -> Path:
        normalised = PurePosixPath(relative_path).as_posix().lstrip("/")
        return self.history_root / Path(normalised)

    def _version_filename(self, slot: int, suffix: str) -> str:
        return f"v{slot:02d}{suffix}"

    def _meta_filename(self, slot: int) -> str:
        return f"v{slot:02d}.meta.json"

    def _existing_slots(self, version_dir: Path) -> list[int]:
        slots: list[int] = []
        for meta_file in version_dir.glob("v*.meta.json"):
            name = meta_file.stem.split(".")[0]
            try:
                slots.append(int(name[1:]))
            except ValueError:
                continue
        return sorted(set(slots))

    def _find_next_slot(self, version_dir: Path, suffix: str) -> int:
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
        self, relative_path: str, source_file: Path, metadata: VersionMetadata
    ) -> int | None:
        if not self.enabled or not source_file.exists():
            return None
        version_dir = self._version_dir(relative_path)
        version_dir.mkdir(parents=True, exist_ok=True)
        suffix = source_file.suffix
        slot = self._find_next_slot(version_dir, suffix)
        shutil.copy2(source_file, version_dir / self._version_filename(slot, suffix))
        (version_dir / self._meta_filename(slot)).write_text(
            json.dumps(metadata.to_dict(), indent=2), encoding="utf-8"
        )
        return slot

    def list_history(self, relative_path: str) -> list[dict[str, Any]]:
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
        self, relative_path: str, version: int, dest_file: Path, restore_metadata: VersionMetadata
    ) -> bool:
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
    agent: str, summary: str, channel: str = "filesystem", msg_id: int = 0, audit_id: str = ""
) -> VersionMetadata:
    return VersionMetadata(
        ts=datetime.now(timezone.utc).isoformat(),
        agent=agent,
        channel=channel,
        msg_id=msg_id,
        audit_id=audit_id,
        summary=summary,
    )
