"""Session and task management for the PA."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from faith_pa.config.models import SystemConfig


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class AgentState:
    agent_id: str
    status: str
    summary: str = ""

    def to_markdown(self) -> str:
        return f"# Agent State\nagent_id: {self.agent_id}\nstatus: {self.status}\nsummary: {self.summary}\n"

    @classmethod
    def from_markdown(cls, markdown: str) -> AgentState:
        values: dict[str, str] = {}
        for line in markdown.splitlines():
            if ":" not in line or line.startswith("#"):
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
        return cls(
            agent_id=values.get("agent_id", ""),
            status=values.get("status", ""),
            summary=values.get("summary", ""),
        )


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    path: Path
    trigger: str
    started_at: str


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    goal: str
    path: Path
    status: str = "active"
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    agents: set[str] = field(default_factory=set)
    staged_agents: dict[str, list[str]] = field(default_factory=dict)
    current_phase: str | None = None
    sandbox_id: str | None = None
    started_at: str = field(default_factory=lambda: _iso(_now()))
    ended_at: str | None = None

    def stage_agents(self, phase: str, agents: list[str]) -> None:
        self.staged_agents[phase] = list(agents)

    def activate_phase(self, phase: str) -> list[str]:
        self.current_phase = phase
        joining = list(self.staged_agents.get(phase, []))
        self.agents.update(joining)
        for channel in self.channels.values():
            existing = set(channel.get("agents", []))
            channel["agents"] = sorted(existing.union(joining))
        return joining

    def finish(self, status: str) -> None:
        self.status = status
        self.ended_at = _iso(_now())


class SessionManager:
    """Manage user sessions and their tasks under .faith/sessions."""

    def __init__(
        self,
        project_root: Path | None = None,
        system_config: SystemConfig | None = None,
        redis_client: Any | None = None,
        idle_timeout_seconds: float | None = None,
        *,
        workspace_path: Path | None = None,
        channel_agent_limit: int | None = None,
    ) -> None:
        initial = Path(project_root or workspace_path or Path.cwd()).resolve()
        if initial.name == ".faith":
            self.faith_dir = initial
            self.project_root = initial.parent
        else:
            self.project_root = initial
            self.faith_dir = self.project_root / ".faith"
        self.workspace_path = self.project_root
        self.system_config = system_config
        self.redis_client = redis_client
        self.idle_timeout_seconds = idle_timeout_seconds
        self.sessions_dir = self.faith_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.channel_agent_limit = (
            channel_agent_limit or getattr(system_config, "channel_agent_limit", 5) or 5
        )
        self.project_name = self.project_root.name
        self.current_session: SessionRecord | None = None
        self.session_id: str | None = None
        self.session_dir: Path | None = None
        self._task_counter = 0
        self._session_counter = len([path for path in self.sessions_dir.iterdir() if path.is_dir()])
        self.tasks: dict[str, TaskRecord] = {}
        self._idle_task: asyncio.Task | None = None

    @property
    def active_session(self) -> SessionRecord | None:
        return self.current_session

    def session_path(self) -> Path:
        if not self.session_dir:
            raise RuntimeError("session has not been started")
        return self.session_dir

    async def start_session(
        self, trigger: str = "web-ui", source: str | None = None
    ) -> SessionRecord:
        self._session_counter += 1
        now = _now()
        session_id = f"sess-{self._session_counter:04d}-{now.strftime('%Y%m%d%H%M%S')}"
        session_path = self.sessions_dir / session_id
        (session_path / "tasks").mkdir(parents=True, exist_ok=True)
        metadata = {
            "session_id": session_id,
            "workspace": str(self.project_root),
            "project_name": self.project_name,
            "status": "active",
            "trigger": trigger if source is None else source,
            "started_at": _iso(now),
            "tasks": {},
        }
        (session_path / "session.meta.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        session = SessionRecord(
            session_id=session_id,
            path=session_path,
            trigger=metadata["trigger"],
            started_at=metadata["started_at"],
        )
        self.current_session = session
        self.session_id = session_id
        self.session_dir = session_path
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        if self.idle_timeout_seconds:
            self._idle_task = asyncio.create_task(self._idle_monitor(), name="faith-session-idle")
        return session

    def create_session(self, project_root: Path | None = None) -> SessionRecord:
        if project_root is not None:
            self.project_root = Path(project_root).resolve()
            self.workspace_path = self.project_root
            self.faith_dir = self.project_root / ".faith"
            self.sessions_dir = self.faith_dir / "sessions"
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
        return asyncio.run(self.start_session())

    async def _idle_monitor(self) -> None:
        try:
            await asyncio.sleep(self.idle_timeout_seconds or 0)
        except asyncio.CancelledError:
            return
        await self.end_session()

    def create_task(
        self,
        goal: str,
        *,
        channels: list[str] | None = None,
        staged_agents: dict[str, list[str]] | None = None,
        sandbox_id: str | None = None,
    ) -> TaskRecord:
        if not self.current_session or not self.session_dir:
            raise RuntimeError("session has not been started")
        self._task_counter += 1
        now = _now()
        task_id = (
            f"task-{self._task_counter}-{now.strftime('%H%M%S')}.{now.microsecond // 1000:03d}"
        )
        task_path = self.session_dir / "tasks" / task_id
        task_path.mkdir(parents=True, exist_ok=True)
        task = TaskRecord(task_id=task_id, goal=goal, path=task_path, sandbox_id=sandbox_id)
        for channel in channels or []:
            task.channels[channel] = {"name": channel, "agents": [], "message_count": 0}
        for phase, agents in (staged_agents or {}).items():
            task.stage_agents(str(phase), agents)
        self.tasks[task_id] = task
        self._write_task_meta(task)
        self._update_session_tasks(task)
        return task

    def start_task(self, goal: str, *, channel: str, sandbox_id: str | None = None) -> TaskRecord:
        return self.create_task(goal, channels=[channel], sandbox_id=sandbox_id)

    def _write_task_meta(self, task: TaskRecord) -> None:
        payload = {
            "task_id": task.task_id,
            "goal": task.goal,
            "status": task.status,
            "channels": task.channels,
            "agents": sorted(task.agents),
            "staged_agents": task.staged_agents,
            "current_phase": task.current_phase,
            "sandbox_id": task.sandbox_id,
            "started_at": task.started_at,
            "ended_at": task.ended_at,
        }
        (task.path / "task.meta.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _update_session_tasks(self, task: TaskRecord) -> None:
        if not self.session_dir:
            return
        meta_path = self.session_dir / "session.meta.json"
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        channel = next(iter(task.channels), None)
        data.setdefault("tasks", {})[task.task_id] = {
            "goal": task.goal,
            "status": task.status,
            "channel": channel,
            "sandbox_id": task.sandbox_id,
        }
        meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    async def activate_task_phase(self, task: TaskRecord | str, phase: str) -> list[str]:
        task_obj = self.tasks[task] if isinstance(task, str) else task
        joining = task_obj.activate_phase(phase)
        self._write_task_meta(task_obj)
        if self.redis_client is not None:
            for agent in joining:
                await self.redis_client.publish(
                    f"pa-{agent}", json.dumps({"type": "phase_activation", "phase": phase})
                )
        return joining

    def activate_phase(self, task_id: str, phase: int | str) -> list[str]:
        task = self.tasks[task_id]
        joining = task.activate_phase(str(phase))
        self._write_task_meta(task)
        return joining

    def complete_task(self, task_id: str) -> None:
        task = self.tasks[task_id]
        task.finish("complete")
        self._write_task_meta(task)
        self._update_session_tasks(task)

    def cancel_task(self, task_id: str) -> None:
        task = self.tasks[task_id]
        task.finish("cancelled")
        self._write_task_meta(task)
        self._update_session_tasks(task)

    def get_active_tasks(self) -> dict[str, str]:
        return {
            task_id: task.goal for task_id, task in self.tasks.items() if task.status == "active"
        }

    def get_active_agent_ids(self) -> list[str]:
        active: set[str] = set()
        for task in self.tasks.values():
            if task.status == "active":
                active.update(task.agents)
        return sorted(active)

    def load_agent_configs(self) -> dict[str, Any]:
        agents_dir = self.faith_dir / "agents"
        configs: dict[str, Any] = {}
        if not agents_dir.exists():
            return configs
        for path in agents_dir.glob("*/config.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            configs[path.parent.name] = data
        return configs

    def get_agent_config(self, agent_id: str) -> Any | None:
        return self.load_agent_configs().get(agent_id)

    def write_agent_state(self, agent_id: str, content: str) -> Path:
        path = self.faith_dir / "agents" / agent_id / "state.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    async def end_session(self) -> None:
        if not self.current_session:
            return
        meta_path = self.current_session.path / "session.meta.json"
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        data["status"] = "ended"
        data["ended_at"] = _iso(_now())
        meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.current_session = None
        self.session_id = None
        self.session_dir = None


Session = SessionRecord
Task = TaskRecord

