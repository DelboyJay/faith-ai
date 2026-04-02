"""
Description:
    Provide a high-level filesystem server facade over the FAITH filesystem MCP
    helpers.

Requirements:
    - Load mount configuration and history settings.
    - Expose read, write, list, stat, delete, mkdir, history, and restore
      helpers through one object.
"""

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
    """
    Description:
        Coordinate mount-aware filesystem operations for the FAITH filesystem MCP
        server.

    Requirements:
        - Keep a mount registry and per-mount history managers in sync with the
          active config.
        - Delegate concrete file operations to the lower-level helper functions.

    :param faith_dir: Root FAITH data directory used for history storage.
    :param config: Optional filesystem config used to initialise the server.
    """

    def __init__(self, faith_dir: Path, config: dict[str, Any] | None = None):
        """
        Description:
            Initialise the filesystem server and optionally load its config.

        Requirements:
            - Always create an empty mount registry and history-manager map.
            - Load the supplied config immediately when present.

        :param faith_dir: Root FAITH data directory used for history storage.
        :param config: Optional filesystem config used to initialise the server.
        """
        self.faith_dir = Path(faith_dir)
        self.mount_registry = MountRegistry()
        self._history_managers: dict[str, FileHistoryManager] = {}
        if config is not None:
            self.reload_config(config)

    def reload_config(self, config: dict[str, Any]) -> None:
        """
        Description:
            Reload mount and history state from the supplied config mapping.

        Requirements:
            - Replace any previously registered mounts and history managers.
            - Build one history manager per configured mount.

        :param config: Filesystem config mapping containing the `mounts` block.
        """
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
        """
        Description:
            Return the history manager for one mount when history is configured.

        Requirements:
            - Return `None` for mounts without an active history manager.

        :param mount_name: Mount whose history manager should be returned.
        :returns: History manager for the mount or `None`.
        """
        return self._history_managers.get(mount_name)

    def read(
        self,
        mount_name: str,
        relative_path: str,
        *,
        agent_id: str,
        agent_mounts: dict[str, str],
    ) -> dict[str, Any]:
        """
        Description:
            Read one file through the configured mount registry.

        Requirements:
            - Delegate permission and path checks to the lower-level read helper.

        :param mount_name: Name of the configured mount.
        :param relative_path: Mount-relative path to read.
        :param agent_id: Agent performing the read.
        :param agent_mounts: Mount permissions granted to the agent.
        :returns: Structured read-file payload.
        """
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
        """
        Description:
            Write one file through the configured mount registry.

        Requirements:
            - Attach the mount history manager when history is enabled.

        :param mount_name: Name of the configured mount.
        :param relative_path: Mount-relative path to write.
        :param content: Text content to persist.
        :param agent_id: Agent performing the write.
        :param agent_mounts: Mount permissions granted to the agent.
        :returns: Structured write-file payload.
        """
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
        self,
        mount_name: str,
        relative_path: str,
        *,
        agent_id: str,
        agent_mounts: dict[str, str],
    ) -> dict[str, Any]:
        """
        Description:
            List one directory through the configured mount registry.

        Requirements:
            - Delegate permission and path checks to the lower-level directory
              listing helper.

        :param mount_name: Name of the configured mount.
        :param relative_path: Mount-relative directory path to list.
        :param agent_id: Agent performing the directory listing.
        :param agent_mounts: Mount permissions granted to the agent.
        :returns: Structured directory-listing payload.
        """
        return list_directory(self.mount_registry, mount_name, relative_path, agent_id, agent_mounts)

    def stat(
        self,
        mount_name: str,
        relative_path: str,
        *,
        agent_id: str,
        agent_mounts: dict[str, str],
    ) -> dict[str, Any]:
        """
        Description:
            Return metadata for one file or directory through the mount registry.

        Requirements:
            - Delegate permission and path checks to the lower-level stat helper.

        :param mount_name: Name of the configured mount.
        :param relative_path: Mount-relative path to inspect.
        :param agent_id: Agent performing the stat call.
        :param agent_mounts: Mount permissions granted to the agent.
        :returns: Structured stat payload.
        """
        return stat_file(self.mount_registry, mount_name, relative_path, agent_id, agent_mounts)

    def delete(
        self,
        mount_name: str,
        relative_path: str,
        *,
        agent_id: str,
        agent_mounts: dict[str, str],
    ) -> dict[str, Any]:
        """
        Description:
            Delete one file or directory through the configured mount registry.

        Requirements:
            - Attach the mount history manager when history is enabled.

        :param mount_name: Name of the configured mount.
        :param relative_path: Mount-relative path to delete.
        :param agent_id: Agent performing the delete.
        :param agent_mounts: Mount permissions granted to the agent.
        :returns: Structured delete payload.
        """
        return delete_file(
            self.mount_registry,
            mount_name,
            relative_path,
            agent_id,
            agent_mounts,
            history_manager=self._history(mount_name),
        )

    def mkdir(
        self,
        mount_name: str,
        relative_path: str,
        *,
        agent_id: str,
        agent_mounts: dict[str, str],
    ) -> dict[str, Any]:
        """
        Description:
            Create one directory through the configured mount registry.

        Requirements:
            - Delegate permission and path checks to the lower-level mkdir
              helper.

        :param mount_name: Name of the configured mount.
        :param relative_path: Mount-relative directory path to create.
        :param agent_id: Agent performing the mkdir operation.
        :param agent_mounts: Mount permissions granted to the agent.
        :returns: Structured mkdir payload.
        """
        return make_directory(self.mount_registry, mount_name, relative_path, agent_id, agent_mounts)

    def list_history(self, mount_name: str, relative_path: str) -> list[dict[str, Any]]:
        """
        Description:
            List stored history entries for one mounted path.

        Requirements:
            - Return an empty list when the mount has no history manager.

        :param mount_name: Name of the configured mount.
        :param relative_path: Mount-relative path whose history should be listed.
        :returns: Stored history entries for the path.
        """
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
        """
        Description:
            Restore one stored history version back into the mounted workspace.

        Requirements:
            - Return `False` when the mount or history manager does not exist.
            - Store restore metadata through the history manager.

        :param mount_name: Name of the configured mount.
        :param relative_path: Mount-relative path whose history should be restored.
        :param version: History version number to restore.
        :param agent_id: Agent performing the restore.
        :param summary: Human-readable restore summary.
        :returns: `True` when the restore succeeds, otherwise `False`.
        """
        manager = self._history(mount_name)
        mount = self.mount_registry.get(mount_name)
        if manager is None or mount is None:
            return False
        destination = mount.host_path / relative_path
        return manager.restore_version(
            relative_path,
            version,
            destination,
            make_metadata(agent_id, summary),
        )
