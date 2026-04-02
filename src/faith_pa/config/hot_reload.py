"""Description:
    Watch FAITH configuration files for changes and publish validated reload events.

Requirements:
    - Poll framework and project configuration files for changes.
    - Validate changed files before notifying the wider runtime.
    - Publish structured Redis events for both successful reloads and validation failures.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from faith_pa.config.loader import (
    ConfigLoadError,
    config_dir,
    load_agent_config,
    load_secrets,
    load_security_config,
    load_system_config,
    load_tool_config,
    project_config_dir,
)
from faith_pa.utils import SYSTEM_EVENTS_CHANNEL

Handler = Callable[[Path], Awaitable[None]]


@dataclass(slots=True)
class WatchedFile:
    """Description:
        Represent one configuration file being monitored by the watcher.

    Requirements:
        - Preserve the file path and logical configuration kind together.

    :param path: Absolute path to the watched file.
    :param kind: Logical configuration kind used for validation and handlers.
    """

    path: Path
    kind: str


class ConfigWatcher:
    """Description:
        Poll FAITH configuration files and react to validated changes.

    Requirements:
        - Keep an in-memory hash snapshot of watched files.
        - Validate changed files before invoking handlers or publishing events.
        - Publish failure events instead of applying invalid configuration.

    :param project_root: Project root containing the local ``.faith`` directory.
    :param poll_interval: Polling interval in seconds.
    :param redis_client: Optional Redis client used for publishing events.
    :param handlers: Optional per-kind async handler mapping.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        poll_interval: float = 5.0,
        redis_client=None,
        handlers: dict[str, Handler] | None = None,
    ) -> None:
        """Description:
            Initialise the configuration watcher state.

        Requirements:
            - Normalise the project root to a ``Path`` instance.
            - Start with an empty file hash snapshot.

        :param project_root: Project root containing the local ``.faith`` directory.
        :param poll_interval: Polling interval in seconds.
        :param redis_client: Optional Redis client used for publishing events.
        :param handlers: Optional per-kind async handler mapping.
        """

        self.project_root = Path(project_root)
        self.poll_interval = poll_interval
        self.redis_client = redis_client
        self.handlers = handlers or {}
        self._hashes: dict[Path, str] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    def discover_files(self) -> list[WatchedFile]:
        """Description:
            Discover the configuration files that should be monitored.

        Requirements:
            - Include framework secrets when present.
            - Include project YAML, prompts, and skill definition files when available.

        :returns: Ordered list of watched configuration files.
        """

        watched: list[WatchedFile] = []

        secrets = config_dir() / "secrets.yaml"
        if secrets.exists():
            watched.append(WatchedFile(secrets, "framework.secrets"))

        project_config = project_config_dir(self.project_root)
        if not project_config.exists():
            return watched

        for path in sorted(project_config.rglob("*.yaml")):
            watched.append(WatchedFile(path, self.classify_path(path)))
        for path in sorted(project_config.rglob("prompt.md")):
            watched.append(WatchedFile(path, "agent.prompt"))
        skills_dir = project_config / "skills"
        if skills_dir.exists():
            for path in sorted(skills_dir.glob("*.md")):
                watched.append(WatchedFile(path, "skill.definition"))

        return watched

    def classify_path(self, path: Path) -> str:
        """Description:
            Classify one watched path into its logical configuration kind.

        Requirements:
            - Return specific kinds for secrets, system, security, agent, and tool files.
            - Fall back to a generic YAML kind for other project YAML files.

        :param path: Configuration file path to classify.
        :returns: Logical configuration kind for the file.
        """

        project_config = project_config_dir(self.project_root)
        if path == config_dir() / "secrets.yaml":
            return "framework.secrets"
        if path == project_config / "system.yaml":
            return "project.system"
        if path == project_config / "security.yaml":
            return "project.security"
        if path.parent == project_config / "tools":
            return "tool.config"
        if path.name == "config.yaml" and path.parent.parent == project_config / "agents":
            return "agent.config"
        return "generic.yaml"

    def _compute_hash(self, path: Path) -> str:
        """Description:
            Compute the current content hash for one watched file.

        Requirements:
            - Return an empty marker when the file no longer exists.

        :param path: File path to hash.
        :returns: SHA-256 hash digest or an empty string for missing files.
        """

        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return ""

    def refresh_snapshot(self) -> None:
        """Description:
            Rebuild the in-memory hash snapshot for all watched files.

        Requirements:
            - Replace the entire snapshot with hashes from the current discovery set.
        """

        self._hashes = {item.path: self._compute_hash(item.path) for item in self.discover_files()}

    async def start(self) -> None:
        """Description:
            Start the background polling task when it is not already running.

        Requirements:
            - Refresh the hash snapshot before polling begins.
            - Avoid creating duplicate background tasks.
        """

        if self._running:
            return
        self.refresh_snapshot()
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Description:
            Stop the background polling task.

        Requirements:
            - Cancel the active task cleanly when one exists.
            - Reset the stored task handle after shutdown.
        """

        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        """Description:
            Execute the polling loop until the watcher is stopped.

        Requirements:
            - Poll once per configured interval while running.
        """

        while self._running:
            await self.poll_once()
            await asyncio.sleep(self.poll_interval)

    async def poll_once(self) -> list[dict[str, str]]:
        """Description:
            Poll the watched files once and return the validated change events.

        Requirements:
            - Ignore newly discovered files until their first baseline hash is stored.
            - Drop hashes for files that have been removed from the discovery set.

        :returns: List of successfully processed change event payloads.
        """

        events: list[dict[str, str]] = []
        current_files = {item.path: item for item in self.discover_files()}

        for path, watched in current_files.items():
            new_hash = self._compute_hash(path)
            old_hash = self._hashes.get(path)
            if old_hash is None:
                self._hashes[path] = new_hash
                continue
            if new_hash != old_hash:
                if await self._handle_change(watched):
                    self._hashes[path] = new_hash
                    events.append({"path": str(path), "kind": watched.kind})

        missing = set(self._hashes) - set(current_files)
        for path in missing:
            self._hashes.pop(path, None)

        return events

    async def _handle_change(self, watched: WatchedFile) -> bool:
        """Description:
            Validate and process one changed file.

        Requirements:
            - Publish a config-error event when validation fails.
            - Invoke the registered handler before publishing the success event.

        :param watched: Changed file metadata.
        :returns: ``True`` when the change was accepted, otherwise ``False``.
        """

        try:
            self._validate(watched)
        except ConfigLoadError as exc:
            await self._publish(
                "system:config_error",
                {
                    "path": str(watched.path),
                    "kind": watched.kind,
                    "error": str(exc),
                },
            )
            return False

        handler = self.handlers.get(watched.kind)
        if handler is not None:
            await handler(watched.path)

        await self._publish(
            "system:config_changed",
            {
                "path": str(watched.path),
                "kind": watched.kind,
            },
        )
        return True

    def _validate(self, watched: WatchedFile) -> None:
        """Description:
            Validate one watched file using the matching loader.

        Requirements:
            - Dispatch known file kinds to the existing config loader functions.
            - Raise a config load error for unsupported watched kinds.

        :param watched: Watched file metadata to validate.
        :raises ConfigLoadError: If the watched kind is unsupported.
        """

        path = watched.path
        if watched.kind == "framework.secrets":
            load_secrets()
        elif watched.kind == "project.system":
            load_system_config(self.project_root)
        elif watched.kind == "project.security":
            load_security_config(self.project_root)
        elif watched.kind == "tool.config":
            load_tool_config(path.name, self.project_root)
        elif watched.kind == "agent.config":
            load_agent_config(path.parent.name, self.project_root)
        elif watched.kind in {"agent.prompt", "skill.definition", "generic.yaml"}:
            return
        else:
            raise ConfigLoadError(f"Unsupported watched file kind: {watched.kind}")

    async def _publish(self, event_type: str, data: dict[str, str]) -> None:
        """Description:
            Publish one watcher event to the Redis system-events channel.

        Requirements:
            - Do nothing when no Redis client is configured.
            - Publish a JSON payload containing the event type, channel, and data.

        :param event_type: Event type name to publish.
        :param data: Event payload data.
        """

        if self.redis_client is None:
            return
        payload = json.dumps({"event": event_type, "channel": SYSTEM_EVENTS_CHANNEL, "data": data})
        await self.redis_client.publish(SYSTEM_EVENTS_CHANNEL, payload)
