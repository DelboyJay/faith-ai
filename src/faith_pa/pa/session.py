"""Description:
    Manage PA sessions, tasks, and lightweight agent state under the project ``.faith`` directory.

Requirements:
    - Create and persist session metadata for each runtime session.
    - Create and persist task metadata for active work.
    - Track active agents, staged agents, phases, and optional sandbox assignments.
    - End idle sessions automatically when an idle timeout is configured.
"""

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
    """Description:
        Return the current UTC timestamp.

    Requirements:
        - Use timezone-aware UTC datetimes.

    :returns: Current UTC datetime.
    """

    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Description:
        Convert one datetime into the session metadata timestamp format.

    Requirements:
        - Return an ISO-8601 UTC string ending with ``Z``.

    :param dt: Datetime to format.
    :returns: Formatted UTC timestamp string.
    """

    return dt.isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class AgentState:
    """Description:
        Represent a lightweight persisted agent state snapshot.

    Requirements:
        - Preserve the agent identifier, current task, progress, channel assignments, file watches, and summary text.

    :param agent_id: Agent identifier.
    :param current_task: Current task description.
    :param progress: Human-readable progress summary.
    :param channel_assignments: Channel identifiers assigned to the agent.
    :param file_watches: File-watch patterns assigned to the agent.
    :param summary: Human-readable summary of the current state.
    :param status: Optional agent runtime status.
    """

    agent_id: str
    current_task: str = ""
    progress: str = ""
    channel_assignments: list[str] = field(default_factory=list)
    file_watches: list[str] = field(default_factory=list)
    summary: str = ""
    status: str = "idle"

    def to_markdown(self) -> str:
        """Description:
            Render the agent state as a simple markdown-backed metadata file.

        Requirements:
            - Preserve the task, progress, channel, file-watch, summary, and status fields in a readable stable format.

        :returns: Markdown representation of the agent state.
        """

        channels = (
            "\n".join(f"- {channel}" for channel in self.channel_assignments) or "- (none)"
        )
        file_watches = "\n".join(f"- {path}" for path in self.file_watches) or "- (none)"
        current_task = self.current_task or "(none)"
        progress = self.progress or "(none)"
        summary = self.summary or "(none)"
        return (
            f"# Agent State: {self.agent_id}\n\n"
            f"status: {self.status}\n"
            f"current_task: {current_task}\n"
            f"progress: {progress}\n\n"
            f"## Channels\n{channels}\n\n"
            f"## File Watches\n{file_watches}\n\n"
            f"## Summary\n{summary}\n"
        )

    @classmethod
    def from_markdown(cls, markdown: str) -> AgentState:
        """Description:
            Parse an agent state from its markdown-backed metadata representation.

        Requirements:
            - Ignore malformed lines outside the recognised metadata sections.
            - Parse channel and file-watch bullet lists from their dedicated sections.

        :param markdown: Markdown text to parse.
        :returns: Parsed agent state.
        """

        values: dict[str, str] = {}
        channel_assignments: list[str] = []
        file_watches: list[str] = []
        summary_lines: list[str] = []
        current_section = ""
        for raw_line in markdown.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("# Agent State:"):
                values["agent_id"] = line.split(":", 1)[1].strip()
                continue
            if line.startswith("## "):
                current_section = line[3:].strip().lower()
                continue
            if current_section == "channels" and line.startswith("- "):
                value = line[2:].strip()
                if value != "(none)":
                    channel_assignments.append(value)
                continue
            if current_section == "file watches" and line.startswith("- "):
                value = line[2:].strip()
                if value != "(none)":
                    file_watches.append(value)
                continue
            if current_section == "summary":
                if line != "(none)":
                    summary_lines.append(raw_line.strip())
                continue
            if ":" not in line or line.startswith("#"):
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
        return cls(
            agent_id=values.get("agent_id", ""),
            current_task="" if values.get("current_task") == "(none)" else values.get("current_task", ""),
            progress="" if values.get("progress") == "(none)" else values.get("progress", ""),
            channel_assignments=channel_assignments,
            file_watches=file_watches,
            summary="\n".join(summary_lines).strip(),
            status=values.get("status", "idle"),
        )


@dataclass(slots=True)
class SessionRecord:
    """Description:
        Represent one persisted PA session.

    Requirements:
        - Preserve the session identifier, metadata path, trigger, start time, and active task pointer.

    :param session_id: Session identifier.
    :param path: Session metadata directory.
    :param trigger: Trigger that created the session.
    :param started_at: Session start timestamp.
    :param active_task_id: Optional active task identifier.
    """

    session_id: str
    path: Path
    trigger: str
    started_at: str
    active_task_id: str | None = None


@dataclass(slots=True)
class TaskRecord:
    """Description:
        Represent one persisted task within a PA session.

    Requirements:
        - Preserve channels, active agents, staged agents, phase state, and optional sandbox assignment.
        - Track start and end timestamps for task lifecycle reporting.

    :param task_id: Task identifier.
    :param goal: Human-readable task goal.
    :param path: Task metadata directory.
    :param status: Current task status.
    :param channels: Task channel metadata.
    :param agents: Active agent identifiers.
    :param staged_agents: Agents staged per phase.
    :param current_phase: Currently active phase.
    :param sandbox_id: Optional sandbox identifier associated with the task.
    :param started_at: Task start timestamp.
    :param ended_at: Task end timestamp.
    """

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
        """Description:
            Stage agents for activation during one task phase.

        Requirements:
            - Preserve the supplied phase ordering as a plain list.

        :param phase: Phase identifier.
        :param agents: Agent identifiers to stage.
        """

        self.staged_agents[phase] = list(agents)

    def activate_phase(self, phase: str) -> list[str]:
        """Description:
            Activate one staged phase and add its agents to the task.

        Requirements:
            - Merge joining agents into the task-level active-agent set.
            - Reflect the joining agents into all tracked channel metadata.

        :param phase: Phase identifier to activate.
        :returns: Agents joining during the phase activation.
        """

        self.current_phase = phase
        joining = list(self.staged_agents.get(phase, []))
        self.agents.update(joining)
        for channel in self.channels.values():
            existing = set(channel.get("agents", []))
            channel["agents"] = sorted(existing.union(joining))
        return joining

    def finish(self, status: str) -> None:
        """Description:
            Mark the task as finished with the supplied status.

        Requirements:
            - Record the end timestamp when the task transitions out of the active state.

        :param status: Final task status value.
        """

        self.status = status
        self.ended_at = _iso(_now())


class SessionManager:
    """Description:
        Manage sessions, tasks, and agent metadata under the project's ``.faith/sessions`` tree.

    Requirements:
        - Create a new session directory and metadata file for each session.
        - Create per-task metadata under the active session.
        - Support idle-timeout shutdown when configured.
        - Publish phase-activation events when a Redis client is available.

    :param project_root: Project root or ``.faith`` directory.
    :param system_config: System configuration payload.
    :param redis_client: Optional Redis client for runtime notifications.
    :param idle_timeout_seconds: Optional idle-session timeout in seconds.
    :param workspace_path: Optional workspace-path alias for project root.
    :param channel_agent_limit: Optional channel agent limit override.
    """

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
        """Description:
            Initialise the session manager.

        Requirements:
            - Accept either a project root or a direct ``.faith`` directory.
            - Create the sessions directory eagerly.
            - Start with no active session and an empty task map.

        :param project_root: Project root or ``.faith`` directory.
        :param system_config: System configuration payload.
        :param redis_client: Optional Redis client for runtime notifications.
        :param idle_timeout_seconds: Optional idle-session timeout in seconds.
        :param workspace_path: Optional workspace-path alias for project root.
        :param channel_agent_limit: Optional channel agent limit override.
        """

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
        """Description:
            Return the current active session record.

        Requirements:
            - Expose the same object stored in ``current_session``.

        :returns: Active session record, if any.
        """

        return self.current_session

    def session_path(self) -> Path:
        """Description:
            Return the path for the active session directory.

        Requirements:
            - Raise when no session has been started yet.

        :returns: Active session directory path.
        :raises RuntimeError: If no session is active.
        """

        if not self.session_dir:
            raise RuntimeError("session has not been started")
        return self.session_dir

    async def start_session(
        self, trigger: str = "web-ui", source: str | None = None
    ) -> SessionRecord:
        """Description:
            Start a new PA session and persist its metadata.

        Requirements:
            - Create the session directory and ``session.meta.json`` file.
            - Cancel any existing idle monitor before starting a new one.
            - Start an idle monitor when an idle timeout is configured.

        :param trigger: High-level trigger for the session start.
        :param source: Optional explicit source override.
        :returns: New session record.
        """

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
        """Description:
            Start a session synchronously for host-side callers.

        Requirements:
            - Allow callers to override the project root before session creation.

        :param project_root: Optional project root override.
        :returns: New session record.
        """

        if project_root is not None:
            self.project_root = Path(project_root).resolve()
            self.workspace_path = self.project_root
            self.faith_dir = self.project_root / ".faith"
            self.sessions_dir = self.faith_dir / "sessions"
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
        return asyncio.run(self.start_session())

    async def _idle_monitor(self) -> None:
        """Description:
            End the active session after the configured idle timeout elapses.

        Requirements:
            - Exit quietly when the idle monitor is cancelled.
        """

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
        """Description:
            Create a new task record under the active session.

        Requirements:
            - Require an active session before task creation.
            - Create a per-task metadata directory and file.
            - Update the session metadata with the new task summary.

        :param goal: Human-readable task goal.
        :param channels: Optional channel names associated with the task.
        :param staged_agents: Optional staged-agent mapping keyed by phase.
        :param sandbox_id: Optional sandbox identifier associated with the task.
        :returns: New task record.
        :raises RuntimeError: If no session is active.
        """

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
        self.current_session.active_task_id = task_id
        self._write_task_meta(task)
        self._update_session_tasks(task)
        return task

    def start_task(self, goal: str, *, channel: str, sandbox_id: str | None = None) -> TaskRecord:
        """Description:
            Create a task with a single initial channel.

        Requirements:
            - Delegate to ``create_task`` with the supplied channel.

        :param goal: Human-readable task goal.
        :param channel: Initial channel name.
        :param sandbox_id: Optional sandbox identifier associated with the task.
        :returns: New task record.
        """

        return self.create_task(goal, channels=[channel], sandbox_id=sandbox_id)

    def _write_task_meta(self, task: TaskRecord) -> None:
        """Description:
            Persist the metadata for one task record.

        Requirements:
            - Write the task metadata as indented JSON under the task directory.

        :param task: Task record to persist.
        """

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
        """Description:
            Update the session metadata summary with one task record.

        Requirements:
            - Write the task goal, status, first channel, and sandbox identifier into ``session.meta.json``.

        :param task: Task record to reflect into the session summary.
        """

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
        """Description:
            Activate one task phase and optionally notify newly activated agents.

        Requirements:
            - Accept either a task record or task identifier.
            - Persist the updated task metadata after phase activation.
            - Publish phase-activation notifications when a Redis client is configured.

        :param task: Task record or task identifier.
        :param phase: Phase identifier to activate.
        :returns: Agents joining during the phase activation.
        """

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
        """Description:
            Activate one phase synchronously without Redis notification support.

        Requirements:
            - Persist the updated task metadata after phase activation.

        :param task_id: Task identifier to update.
        :param phase: Phase identifier to activate.
        :returns: Agents joining during the phase activation.
        """

        task = self.tasks[task_id]
        joining = task.activate_phase(str(phase))
        self._write_task_meta(task)
        return joining

    def complete_task(self, task_id: str) -> None:
        """Description:
            Mark one task as complete.

        Requirements:
            - Persist both task metadata and session summary updates.

        :param task_id: Task identifier to complete.
        """

        task = self.tasks[task_id]
        task.finish("complete")
        self._write_task_meta(task)
        self._update_session_tasks(task)

    def cancel_task(self, task_id: str) -> None:
        """Description:
            Mark one task as cancelled.

        Requirements:
            - Persist both task metadata and session summary updates.

        :param task_id: Task identifier to cancel.
        """

        task = self.tasks[task_id]
        task.finish("cancelled")
        self._write_task_meta(task)
        self._update_session_tasks(task)

    def get_active_tasks(self) -> dict[str, str]:
        """Description:
            Return the currently active task goals keyed by task identifier.

        Requirements:
            - Include only tasks still in the ``active`` status.

        :returns: Mapping of active task identifiers to goals.
        """

        return {
            task_id: task.goal for task_id, task in self.tasks.items() if task.status == "active"
        }

    def get_active_task(self) -> TaskRecord | None:
        """Description:
            Return the most recent active task record.

        Requirements:
            - Prefer the explicit session active-task pointer when it is set.
            - Fall back to scanning the active task map when needed.

        :returns: Active task record, if any.
        """

        if self.current_session and self.current_session.active_task_id:
            task = self.tasks.get(self.current_session.active_task_id)
            if task and task.status == "active":
                return task
        for task in reversed(list(self.tasks.values())):
            if task.status == "active":
                return task
        return None

    def get_active_agent_ids(self) -> list[str]:
        """Description:
            Return the sorted active agent identifiers across active tasks.

        Requirements:
            - Include agents only from tasks still marked active.

        :returns: Sorted active agent identifiers.
        """

        active: set[str] = set()
        for task in self.tasks.values():
            if task.status == "active":
                active.update(task.agents)
        return sorted(active)

    def load_agent_configs(self) -> dict[str, Any]:
        """Description:
            Load the project agent configuration files into a simple mapping.

        Requirements:
            - Return an empty mapping when the agents directory does not exist.

        :returns: Mapping of agent identifiers to parsed config payloads.
        """

        agents_dir = self.faith_dir / "agents"
        configs: dict[str, Any] = {}
        if not agents_dir.exists():
            return configs
        for path in agents_dir.glob("*/config.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            configs[path.parent.name] = data
        return configs

    def get_agent_config(self, agent_id: str) -> Any | None:
        """Description:
            Return the parsed configuration for one agent.

        Requirements:
            - Delegate to the bulk agent-config loader.

        :param agent_id: Agent identifier to inspect.
        :returns: Parsed agent config payload, if present.
        """

        return self.load_agent_configs().get(agent_id)

    def write_agent_state(self, agent_id: str, content: str) -> Path:
        """Description:
            Persist a lightweight agent state file under the project ``.faith`` tree.

        Requirements:
            - Create the agent state directory when needed.

        :param agent_id: Agent identifier whose state is being written.
        :param content: Agent state file content.
        :returns: Written state file path.
        """

        path = self.faith_dir / "agents" / agent_id / "state.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    async def end_session(self) -> None:
        """Description:
            End the active session and persist its final metadata.

        Requirements:
            - Mark the session status as ended and record an end timestamp.
            - Clear the active session pointers after persistence.

        """

        if not self.current_session:
            return
        for task in self.tasks.values():
            if task.status == "active":
                task.finish("complete")
                self._write_task_meta(task)
                self._update_session_tasks(task)
        meta_path = self.current_session.path / "session.meta.json"
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        data["status"] = "ended"
        data["ended_at"] = _iso(_now())
        active_agents = self.get_active_agent_ids()
        data["task_count"] = len(self.tasks)
        data["agents_active"] = active_agents
        data["active_task_id"] = None
        meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.current_session.active_task_id = None
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
        self.current_session = None
        self.session_id = None
        self.session_dir = None


Session = SessionRecord
Task = TaskRecord
