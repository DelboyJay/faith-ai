"""
Description:
    Provide a high-level filesystem server facade over the FAITH filesystem MCP
    helpers.

Requirements:
    - Load mount configuration and history settings.
    - Expose read, write, list, stat, delete, mkdir, history, and restore
      helpers through one object.
    - Support hot-reload and a stable MCP-facing dispatch surface.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from faith_mcp.filesystem.history import FileHistoryManager, make_metadata
from faith_mcp.filesystem.mounts import MountRegistry
from faith_mcp.filesystem.operations import (
    FilesystemError,
    delete_file,
    list_directory,
    make_directory,
    read_file,
    stat_file,
    write_file,
)
from faith_mcp.filesystem.watcher import FileSubscription, FileWatcher
from faith_shared.protocol.events import EventType, FaithEvent


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

    def __init__(
        self,
        faith_dir: Path,
        config: dict[str, Any] | None = None,
        *,
        event_publisher: Any | None = None,
    ):
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
        self.event_publisher = event_publisher
        self.mount_registry = MountRegistry()
        self._history_managers: dict[str, FileHistoryManager] = {}
        self.watcher = FileWatcher()
        if config is not None:
            self.reload_config(config)

    def mount_roots(self) -> dict[str, Path]:
        """
        Description:
            Return the resolved host roots for every configured mount.

        Requirements:
            - Expose only currently configured mounts.

        :returns: Mapping of mount names to resolved host paths.
        """

        roots: dict[str, Path] = {}
        for mount_name in self.mount_registry.list_mounts():
            mount = self.mount_registry.get(mount_name)
            if mount is not None:
                roots[mount_name] = mount.host_path
        return roots

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

    def load_static_subscriptions(self, agents_config: dict[str, dict[str, Any]]) -> None:
        """
        Description:
            Load static file-watch subscriptions from parsed agent config payloads.

        Requirements:
            - Replace static subscriptions while preserving existing session-scoped watches.

        :param agents_config: Mapping of agent IDs to parsed agent config payloads.
        """

        self.watcher.load_static_subscriptions(agents_config, self.mount_roots())

    def handle_config_changed(
        self, event: FaithEvent, config: dict[str, Any] | None = None
    ) -> bool:
        """
        Description:
            Apply a filesystem configuration reload when a matching config-changed event arrives.

        Requirements:
            - Ignore unrelated config-changed events.
            - Reload the supplied config mapping or read the on-disk filesystem config.

        :param event: Event that may describe a filesystem config change.
        :param config: Optional already-parsed filesystem config payload.
        :returns: ``True`` when the server reloaded its config, otherwise ``False``.
        """

        if event.event is not EventType.SYSTEM_CONFIG_CHANGED:
            return False
        payload = event.data or {}
        file_name = str(payload.get("file", ""))
        path_text = str(payload.get("path", ""))
        if file_name != "filesystem.yaml" and not path_text.replace("\\", "/").endswith(
            "/filesystem.yaml"
        ):
            return False
        self.reload_config(config or self._read_config_file())
        return True

    def _read_config_file(self) -> dict[str, Any]:
        """
        Description:
            Read the filesystem tool config from disk.

        Requirements:
            - Return an empty mount set when the config file does not exist.

        :returns: Parsed filesystem config payload.
        """

        config_path = self.faith_dir / "tools" / "filesystem.yaml"
        if not config_path.exists():
            return {"mounts": {}}
        return json.loads(json.dumps(yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}))

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

    def register_dynamic_subscription(
        self,
        agent_id: str,
        mount_name: str,
        pattern: str,
        events: list[str],
        *,
        session_scoped: bool = True,
    ) -> None:
        """
        Description:
            Register one dynamic file-watch subscription for the supplied mount.

        Requirements:
            - Resolve the mount root before storing the subscription.
            - Preserve the caller-supplied event list and session scope.

        :param agent_id: Agent that should receive change notifications.
        :param mount_name: Name of the configured mount being watched.
        :param pattern: Mount-relative watch pattern.
        :param events: Change event names to emit for the subscription.
        :param session_scoped: Whether the subscription should be removed at session end.
        :raises ValueError: If the mount name is unknown.
        """

        mount = self.mount_registry.get(mount_name)
        if mount is None:
            raise ValueError(f"Unknown mount '{mount_name}'")
        self.watcher.add_subscription(
            FileSubscription(
                agent_id=agent_id,
                pattern=f"{mount_name}/{pattern.lstrip('/')}",
                events=list(events),
                mount_root=mount.host_path,
                session_scoped=session_scoped,
            )
        )

    def remove_session_subscriptions(self) -> None:
        """
        Description:
            Remove session-scoped file-watch subscriptions from the watcher.

        Requirements:
            - Preserve non-session subscriptions unchanged.
        """

        self.watcher.remove_session_subscriptions()

    def poll_file_events(self) -> list[dict[str, str]]:
        """
        Description:
            Poll file-watch subscriptions once and publish any detected changes.

        Requirements:
            - Return a serialisable list of detected file-change events.
            - Publish detected events when an event publisher is configured.

        :returns: Serialisable file-change event payloads.
        """

        events = self.watcher.poll_once()
        payloads = [
            {"agent_id": event.agent_id, "event": event.event, "path": event.path}
            for event in events
        ]
        for payload in payloads:
            self._emit_event(
                EventType(payload["event"]),
                source="filesystem",
                channel=f"pa-{payload['agent_id']}",
                data={"agent": payload["agent_id"], "path": payload["path"]},
            )
        return payloads

    async def handle_tool_call(
        self,
        action: str,
        args: dict[str, Any],
        *,
        agent_id: str,
        agent_mounts: dict[str, str],
    ) -> dict[str, Any]:
        """
        Description:
            Dispatch one MCP-style filesystem tool call by action name.

        Requirements:
            - Support the canonical filesystem actions and history helpers.
            - Raise a structured error when the action is unknown.

        :param action: Filesystem action name to dispatch.
        :param args: Filesystem action arguments.
        :param agent_id: Agent performing the action.
        :param agent_mounts: Mount permissions granted to the agent.
        :returns: Structured filesystem response payload.
        :raises FilesystemError: If the action is unknown.
        """

        mount_name = str(args.get("mount", ""))
        relative_path = str(args.get("path", ""))
        if action == "read":
            return self.read(
                mount_name, relative_path, agent_id=agent_id, agent_mounts=agent_mounts
            )
        if action == "write":
            return self.write(
                mount_name,
                relative_path,
                str(args.get("content", "")),
                agent_id=agent_id,
                agent_mounts=agent_mounts,
            )
        if action == "list":
            return self.list_dir(
                mount_name, relative_path, agent_id=agent_id, agent_mounts=agent_mounts
            )
        if action == "stat":
            return self.stat(
                mount_name, relative_path, agent_id=agent_id, agent_mounts=agent_mounts
            )
        if action == "delete":
            return self.delete(
                mount_name, relative_path, agent_id=agent_id, agent_mounts=agent_mounts
            )
        if action == "mkdir":
            return self.mkdir(
                mount_name, relative_path, agent_id=agent_id, agent_mounts=agent_mounts
            )
        if action == "list_history":
            return {"versions": self.list_history(mount_name, relative_path)}
        if action == "restore_version":
            restored = self.restore_version(
                mount_name,
                relative_path,
                int(args["version"]),
                agent_id=agent_id,
                summary=str(args.get("summary", "restore")),
            )
            return {"restored": restored}
        if action == "register_watch":
            self.register_dynamic_subscription(
                agent_id,
                mount_name,
                str(args.get("pattern", "")),
                list(args.get("events", ["file:changed"])),
                session_scoped=bool(args.get("session_scoped", True)),
            )
            return {"registered": True}
        if action == "poll_events":
            return {"events": self.poll_file_events()}
        raise FilesystemError("UNKNOWN_ACTION", f"Unknown filesystem action '{action}'")

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
        self._emit_event(
            EventType.TOOL_CALL_STARTED,
            source="filesystem",
            data={"agent": agent_id, "action": "read", "path": relative_path},
        )
        try:
            payload = read_file(
                self.mount_registry, mount_name, relative_path, agent_id, agent_mounts
            )
        except FilesystemError as exc:
            self._emit_filesystem_error(agent_id, "read", relative_path, exc)
            raise
        self._emit_event(
            EventType.TOOL_CALL_COMPLETE,
            source="filesystem",
            data={"agent": agent_id, "action": "read", "path": payload["path"]},
        )
        return payload

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
        self._emit_event(
            EventType.TOOL_CALL_STARTED,
            source="filesystem",
            data={"agent": agent_id, "action": "write", "path": relative_path},
        )
        try:
            payload = write_file(
                self.mount_registry,
                mount_name,
                relative_path,
                content,
                agent_id,
                agent_mounts,
                history_manager=self._history(mount_name),
            )
        except FilesystemError as exc:
            self._emit_filesystem_error(agent_id, "write", relative_path, exc)
            raise
        self._emit_event(
            EventType.TOOL_CALL_COMPLETE,
            source="filesystem",
            data={"agent": agent_id, "action": "write", "path": payload["path"]},
        )
        return payload

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
        self._emit_event(
            EventType.TOOL_CALL_STARTED,
            source="filesystem",
            data={"agent": agent_id, "action": "list", "path": relative_path},
        )
        try:
            payload = list_directory(
                self.mount_registry, mount_name, relative_path, agent_id, agent_mounts
            )
        except FilesystemError as exc:
            self._emit_filesystem_error(agent_id, "list", relative_path, exc)
            raise
        self._emit_event(
            EventType.TOOL_CALL_COMPLETE,
            source="filesystem",
            data={"agent": agent_id, "action": "list", "path": payload["path"]},
        )
        return payload

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
        self._emit_event(
            EventType.TOOL_CALL_STARTED,
            source="filesystem",
            data={"agent": agent_id, "action": "stat", "path": relative_path},
        )
        try:
            payload = stat_file(
                self.mount_registry, mount_name, relative_path, agent_id, agent_mounts
            )
        except FilesystemError as exc:
            self._emit_filesystem_error(agent_id, "stat", relative_path, exc)
            raise
        self._emit_event(
            EventType.TOOL_CALL_COMPLETE,
            source="filesystem",
            data={"agent": agent_id, "action": "stat", "path": payload["path"]},
        )
        return payload

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
        self._emit_event(
            EventType.TOOL_CALL_STARTED,
            source="filesystem",
            data={"agent": agent_id, "action": "delete", "path": relative_path},
        )
        try:
            payload = delete_file(
                self.mount_registry,
                mount_name,
                relative_path,
                agent_id,
                agent_mounts,
                history_manager=self._history(mount_name),
            )
        except FilesystemError as exc:
            self._emit_filesystem_error(agent_id, "delete", relative_path, exc)
            raise
        self._emit_event(
            EventType.TOOL_CALL_COMPLETE,
            source="filesystem",
            data={"agent": agent_id, "action": "delete", "path": payload["path"]},
        )
        return payload

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
        self._emit_event(
            EventType.TOOL_CALL_STARTED,
            source="filesystem",
            data={"agent": agent_id, "action": "mkdir", "path": relative_path},
        )
        try:
            payload = make_directory(
                self.mount_registry, mount_name, relative_path, agent_id, agent_mounts
            )
        except FilesystemError as exc:
            self._emit_filesystem_error(agent_id, "mkdir", relative_path, exc)
            raise
        self._emit_event(
            EventType.TOOL_CALL_COMPLETE,
            source="filesystem",
            data={"agent": agent_id, "action": "mkdir", "path": payload["path"]},
        )
        return payload

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

    def _emit_filesystem_error(
        self,
        agent_id: str,
        action: str,
        relative_path: str,
        exc: FilesystemError,
    ) -> None:
        """
        Description:
            Publish one filesystem error or permission-denied event when possible.

        Requirements:
            - Use the dedicated permission-denied event for permission failures.
            - Publish the structured error code and path for other filesystem failures.

        :param agent_id: Agent invoking the failed operation.
        :param action: Filesystem action that failed.
        :param relative_path: Mount-relative target path involved in the failure.
        :param exc: Structured filesystem exception to report.
        """

        event_type = (
            EventType.TOOL_PERMISSION_DENIED
            if exc.code in {"PERMISSION_DENIED", "DENY_LIST"}
            else EventType.TOOL_ERROR
        )
        payload = {
            "agent": agent_id,
            "action": action,
            "path": relative_path,
            "code": exc.code,
            "error": str(exc),
        }
        self._emit_event(event_type, source="filesystem", data=payload)

    def _emit_event(
        self,
        event_type: EventType,
        *,
        source: str,
        data: dict[str, Any],
        channel: str | None = None,
    ) -> None:
        """
        Description:
            Publish one filesystem event through the configured event publisher.

        Requirements:
            - Support publishers exposing a generic async ``publish`` method.
            - Run immediately in sync call sites without leaking pending tasks.

        :param event_type: Event type to emit.
        :param source: Event source identifier.
        :param data: Structured event payload.
        :param channel: Optional agent or task channel associated with the event.
        """

        if self.event_publisher is None:
            return
        event = FaithEvent(event=event_type, source=source, channel=channel, data=data)
        publish = getattr(self.event_publisher, "publish", None)
        if not callable(publish):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(publish(event))
            return
        loop.create_task(publish(event))


def main() -> None:
    """
    Description:
        Start the filesystem MCP server entry point.

    Requirements:
        - Provide a stable line-oriented stdio process for the dedicated filesystem container.
        - Keep the request protocol simple: one JSON object per line in, one JSON object per line out.
    """
    faith_dir = Path(os.environ.get("FAITH_DIR", ".faith"))
    config_path = faith_dir / "tools" / "filesystem.yaml"
    config = {"mounts": {}}
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {"mounts": {}}
    server = FilesystemServer(faith_dir, config)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        try:
            result = asyncio.run(
                server.handle_tool_call(
                    str(request.get("action", "")),
                    dict(request.get("args", {})),
                    agent_id=str(request.get("agent_id", "unknown")),
                    agent_mounts=dict(request.get("agent_mounts", {})),
                )
            )
            response = {"ok": True, "result": result}
        except Exception as exc:  # pragma: no cover - stdio guard rail
            response = {"ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
