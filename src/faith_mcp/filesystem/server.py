"""Practical filesystem server facade for the FAITH POC."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from faith_mcp.filesystem.history import FileHistoryManager, make_metadata
from faith_mcp.filesystem.mounts import MountRegistry
from faith_mcp.filesystem.operations import (
    delete_file,
    list_directory,
    make_directory,
    read_file,
    stat_file,
    write_file,
)


class FilesystemServer:
    def __init__(self, faith_dir: Path, config: dict[str, Any] | None = None):
        self.faith_dir = Path(faith_dir)
        self.mount_registry = MountRegistry()
        self._history_managers: dict[str, FileHistoryManager] = {}
        if config is not None:
            self.reload_config(config)

    def reload_config(self, config: dict[str, Any]) -> None:
        self.mount_registry.load_from_config(config)
        self._history_managers = {}
        for mount_name in self.mount_registry.list_mounts():
            mount = self.mount_registry.get(mount_name)
            if mount is None:
                continue
            self._history_managers[mount_name] = FileHistoryManager(
                mount_name,
                self.faith_dir,
                mount.host_path,
                depth=mount.history_depth,
                enabled=mount.history,
            )

    def _history(self, mount_name: str) -> FileHistoryManager | None:
        return self._history_managers.get(mount_name)

    def read(
        self, mount_name: str, relative_path: str, *, agent_id: str, agent_mounts: dict[str, str]
    ) -> dict[str, Any]:
        return read_file(self.mount_registry, mount_name, relative_path, agent_id, agent_mounts)

    def write(
        self,
        mount_name: str,
        relative_path: str,
        content: str,
        *,
        agent_id: str,
        agent_mounts: dict[str, str],
    ) -> dict[str, Any]:
        return write_file(
            self.mount_registry,
            mount_name,
            relative_path,
            content,
            agent_id,
            agent_mounts,
            history_manager=self._history(mount_name),
        )

    def list_dir(
        self, mount_name: str, relative_path: str, *, agent_id: str, agent_mounts: dict[str, str]
    ) -> dict[str, Any]:
        return list_directory(
            self.mount_registry, mount_name, relative_path, agent_id, agent_mounts
        )

    def stat(
        self, mount_name: str, relative_path: str, *, agent_id: str, agent_mounts: dict[str, str]
    ) -> dict[str, Any]:
        return stat_file(self.mount_registry, mount_name, relative_path, agent_id, agent_mounts)

    def delete(
        self, mount_name: str, relative_path: str, *, agent_id: str, agent_mounts: dict[str, str]
    ) -> dict[str, Any]:
        return delete_file(
            self.mount_registry,
            mount_name,
            relative_path,
            agent_id,
            agent_mounts,
            history_manager=self._history(mount_name),
        )

    def mkdir(
        self, mount_name: str, relative_path: str, *, agent_id: str, agent_mounts: dict[str, str]
    ) -> dict[str, Any]:
        return make_directory(
            self.mount_registry, mount_name, relative_path, agent_id, agent_mounts
        )

    def list_history(self, mount_name: str, relative_path: str) -> list[dict[str, Any]]:
        manager = self._history(mount_name)
        return manager.list_history(relative_path) if manager is not None else []

    def restore_version(
        self,
        mount_name: str,
        relative_path: str,
        version: int,
        *,
        agent_id: str,
        summary: str = "restore",
    ) -> bool:
        manager = self._history(mount_name)
        mount = self.mount_registry.get(mount_name)
        if manager is None or mount is None:
            return False
        destination = mount.host_path / relative_path
        return manager.restore_version(
            relative_path, version, destination, make_metadata(agent_id, summary)
        )

