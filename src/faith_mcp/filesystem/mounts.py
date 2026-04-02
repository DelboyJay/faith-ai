"""
Description:
    Resolve named filesystem mounts from FAITH configuration data.

Requirements:
    - Build mount definitions from the current configuration schema.
    - Support mount-level overrides for subfolder permissions and history
      settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class MountConfig:
    """
    Description:
        Represent one configured host-path mount exposed through the filesystem
        MCP server.

    Requirements:
        - Preserve mount-wide access, recursion, size-limit, and history
          options.
        - Carry any subfolder-specific permission overrides used by the
          permission resolver.
    """

    name: str
    host_path: Path
    access: str = "readonly"
    recursive: bool = True
    max_file_size_mb: int = 50
    history: bool = False
    history_depth: int = 10
    subfolder_overrides: dict[str, str] = field(default_factory=dict)


class MountRegistry:
    """
    Description:
        Store and resolve named filesystem mounts for the filesystem MCP server.

    Requirements:
        - Load mounts from FAITH configuration data.
        - Resolve mount-relative paths onto host paths safely and consistently.
    """

    def __init__(self) -> None:
        """
        Description:
            Initialise the mount registry with no configured mounts.

        Requirements:
            - Start with an empty in-memory mount map.
        """
        self._mounts: dict[str, MountConfig] = {}

    def load_from_config(self, config: dict[str, Any]) -> None:
        """
        Description:
            Replace the registry contents from a FAITH configuration mapping.

        Requirements:
            - Ignore malformed mount records and nested names that are not valid
              top-level mount entries.
            - Capture subfolder access overrides for each mount.

        :param config: Configuration mapping that may contain a `mounts` block.
        """
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
        """
        Description:
            Add or replace one mount definition in the registry.

        Requirements:
            - Allow explicit registration for tests and dynamic setup paths.

        :param mount: Mount configuration that should be stored.
        """
        self._mounts[mount.name] = mount

    def get(self, name: str) -> MountConfig | None:
        """
        Description:
            Return one named mount configuration when it exists.

        Requirements:
            - Return `None` for unknown mount names rather than raising.

        :param name: Mount name to resolve.
        :returns: Matching mount configuration or `None`.
        """
        return self._mounts.get(name)

    def list_mounts(self) -> list[str]:
        """
        Description:
            Return the available mount names in deterministic order.

        Requirements:
            - Sort mount names so callers receive stable output.

        :returns: Sorted list of registered mount names.
        """
        return sorted(self._mounts)

    def resolve_path(self, mount_name: str, relative_path: str) -> Path | None:
        """
        Description:
            Resolve a mount-relative path onto the underlying host path.

        Requirements:
            - Return `None` when the mount does not exist.
            - Normalise the relative path to POSIX form before joining it to the
              host path.

        :param mount_name: Name of the mount to resolve.
        :param relative_path: Mount-relative path requested by the caller.
        :returns: Host path for the mounted file or `None` when the mount is unknown.
        """
        mount = self.get(mount_name)
        if mount is None:
            return None
        relative = PurePosixPath(relative_path).as_posix().lstrip("/")
        return mount.host_path / relative
