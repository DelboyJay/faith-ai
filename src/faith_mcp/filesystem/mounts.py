"""Named mount resolution for the filesystem layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class MountConfig:
    name: str
    host_path: Path
    access: str = "readonly"
    recursive: bool = True
    max_file_size_mb: int = 50
    history: bool = False
    history_depth: int = 10
    subfolder_overrides: dict[str, str] = field(default_factory=dict)


class MountRegistry:
    def __init__(self) -> None:
        self._mounts: dict[str, MountConfig] = {}

    def load_from_config(self, config: dict[str, Any]) -> None:
        self._mounts.clear()
        mounts_raw = config.get("mounts", {}) or {}
        for name, mount_def in mounts_raw.items():
            if not isinstance(mount_def, dict) or "/" in name:
                continue
            overrides: dict[str, str] = {}
            for key, override in mounts_raw.items():
                if key.startswith(f"{name}/") and isinstance(override, dict):
                    subfolder = key[len(name) + 1 :]
                    overrides[PurePosixPath(subfolder).as_posix().lstrip("/")] = override.get(
                        "access", "readonly"
                    )
            self._mounts[name] = MountConfig(
                name=name,
                host_path=Path(mount_def["host_path"]).expanduser().resolve(),
                access=mount_def.get("access", "readonly"),
                recursive=mount_def.get("recursive", True),
                max_file_size_mb=int(mount_def.get("max_file_size_mb", 50)),
                history=bool(mount_def.get("history", False)),
                history_depth=int(mount_def.get("history_depth", 10)),
                subfolder_overrides=overrides,
            )

    def register(self, mount: MountConfig) -> None:
        self._mounts[mount.name] = mount

    def get(self, name: str) -> MountConfig | None:
        return self._mounts.get(name)

    def list_mounts(self) -> list[str]:
        return sorted(self._mounts)

    def resolve_path(self, mount_name: str, relative_path: str) -> Path | None:
        mount = self.get(mount_name)
        if mount is None:
            return None
        relative = PurePosixPath(relative_path).as_posix().lstrip("/")
        return mount.host_path / relative
