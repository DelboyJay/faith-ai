"""Config hot-reload watcher for the FAITH runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from faith.config.loader import (
    ConfigLoadError,
    config_dir,
    load_agent_config,
    load_secrets,
    load_security_config,
    load_system_config,
    load_tool_config,
    project_config_dir,
)
from faith.utils import SYSTEM_EVENTS_CHANNEL

Handler = Callable[[Path], Awaitable[None]]


@dataclass(slots=True)
class WatchedFile:
    path: Path
    kind: str


class ConfigWatcher:
    """Polling watcher that validates changed config files before applying them."""

    def __init__(
        self,
        *,
        project_root: Path,
        poll_interval: float = 5.0,
        redis_client=None,
        handlers: dict[str, Handler] | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.poll_interval = poll_interval
        self.redis_client = redis_client
        self.handlers = handlers or {}
        self._hashes: dict[Path, str] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    def discover_files(self) -> list[WatchedFile]:
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
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return ""

    def refresh_snapshot(self) -> None:
        self._hashes = {item.path: self._compute_hash(item.path) for item in self.discover_files()}

    async def start(self) -> None:
        if self._running:
            return
        self.refresh_snapshot()
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while self._running:
            await self.poll_once()
            await asyncio.sleep(self.poll_interval)

    async def poll_once(self) -> list[dict[str, str]]:
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
        if self.redis_client is None:
            return
        payload = json.dumps({"event": event_type, "channel": SYSTEM_EVENTS_CHANNEL, "data": data})
        await self.redis_client.publish(SYSTEM_EVENTS_CHANNEL, payload)
