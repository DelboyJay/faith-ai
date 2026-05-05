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
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from faith_pa.agent.cag import CAGManager, CAGValidationResult
from faith_pa.config.loader import load_all_agent_configs
from faith_pa.config.models import SystemConfig
from faith_pa.logging import AgentIndexWriter, TaskLogWriter, TaskMeta

PROJECT_AGENT_TRANSCRIPT_HEADER = "# Project Agent Transcript\n\n"
PROJECT_AGENT_TRANSCRIPT_ENTRY_PATTERN = re.compile(
    r"^## (?P<label>User|Assistant)\n~~~text\n(?P<content>.*?)\n~~~\n?",
    re.MULTILINE | re.DOTALL,
)


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

        channels = "\n".join(f"- {channel}" for channel in self.channel_assignments) or "- (none)"
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
            current_task=""
            if values.get("current_task") == "(none)"
            else values.get("current_task", ""),
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
    :param input_tokens: Aggregate input-token count recorded for the task.
    :param output_tokens: Aggregate output-token count recorded for the task.
    :param estimated_cost: Aggregate estimated model cost recorded for the task.
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
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0

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
        self._task_log_writers: dict[str, TaskLogWriter] = {}
        self._agent_index_writer = AgentIndexWriter(agents_dir=self.faith_dir / "agents")
        self._idle_task: asyncio.Task | None = None

    @staticmethod
    def _format_project_agent_message(role: str, content: str) -> str:
        """Description:
            Render one Project Agent transcript entry as markdown.

        Requirements:
            - Preserve the visible speaker label in a stable markdown format.
            - Keep multiline message content recoverable for restart-time rehydration.

        :param role: Transcript role name.
        :param content: Transcript content to persist.
        :returns: Markdown representation of one transcript entry.
        """

        label = "User" if role == "user" else "Assistant"
        normalised = content.rstrip("\n")
        return f"## {label}\n~~~text\n{normalised}\n~~~\n\n"

    @staticmethod
    def _parse_project_agent_transcript(markdown: str) -> list[dict[str, str]]:
        """Description:
            Parse one persisted Project Agent transcript markdown file.

        Requirements:
            - Ignore malformed transcript fragments outside the supported entry format.
            - Return messages in file order using `user` and `assistant` roles.

        :param markdown: Persisted transcript markdown content.
        :returns: Parsed transcript messages.
        """

        messages: list[dict[str, str]] = []
        for match in PROJECT_AGENT_TRANSCRIPT_ENTRY_PATTERN.finditer(markdown):
            label = match.group("label")
            content = match.group("content")
            messages.append(
                {
                    "role": "user" if label == "User" else "assistant",
                    "content": content,
                }
            )
        return messages

    def _latest_session_path(self) -> Path | None:
        """Description:
            Return the newest persisted session directory.

        Requirements:
            - Ignore non-directory entries under `.faith/sessions/`.
            - Return `None` when no sessions have been created yet.

        :returns: Newest session directory path, if any.
        """

        session_paths = [path for path in self.sessions_dir.iterdir() if path.is_dir()]
        if not session_paths:
            return None
        return sorted(session_paths, key=lambda path: path.name)[-1]

    def _project_agent_log_path(self, session_path: Path | None = None) -> Path:
        """Description:
            Return the Project Agent transcript log path for one session.

        Requirements:
            - Default to the current active session when one is available.
            - Raise when no session path can be resolved.

        :param session_path: Optional explicit session directory.
        :returns: Session-level `pa-user.log` path.
        :raises RuntimeError: If no session path can be resolved.
        """

        resolved = session_path or self.session_dir
        if resolved is None:
            raise RuntimeError("project agent transcript log is unavailable without a session")
        return resolved / "pa-user.log"

    def _session_meta_path(self) -> Path:
        """Description:
            Return the active session metadata path.

        Requirements:
            - Raise when no session directory is active yet.

        :returns: Active session metadata path.
        :raises RuntimeError: If no session is active.
        """

        if self.session_dir is None:
            raise RuntimeError("session has not been started")
        return self.session_dir / "session.meta.json"

    def _sync_session_totals(self) -> None:
        """Description:
            Sync aggregated task token and cost totals into the session metadata.

        Requirements:
            - Keep the FRS-required aggregate counters current after task updates.
            - Preserve existing session metadata fields while rewriting the file.
        """

        if self.session_dir is None:
            return
        meta_path = self._session_meta_path()
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        total_input_tokens = sum(getattr(task, "input_tokens", 0) for task in self.tasks.values())
        total_output_tokens = sum(getattr(task, "output_tokens", 0) for task in self.tasks.values())
        total_estimated_cost = sum(
            getattr(task, "estimated_cost", 0.0) for task in self.tasks.values()
        )
        active_agents = self.get_active_agent_ids()
        data["task_count"] = len(self.tasks)
        data["agents_active"] = active_agents
        data["total_input_tokens"] = total_input_tokens
        data["total_output_tokens"] = total_output_tokens
        data["total_estimated_cost"] = round(total_estimated_cost, 6)
        meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _ensure_task_log_writer(self, task: TaskRecord) -> TaskLogWriter:
        """Description:
            Return the persisted task log writer for one task.

        Requirements:
            - Create the writer lazily on first use.
            - Reuse the same writer for later log and metadata updates.

        :param task: Task record to back with a persisted task writer.
        :returns: Persisted task log writer.
        """

        writer = self._task_log_writers.get(task.task_id)
        if writer is not None:
            return writer
        if self.current_session is None:
            raise RuntimeError("task log writer is unavailable without an active session")
        writer = TaskLogWriter(
            task_dir=task.path,
            meta=TaskMeta(
                task_id=task.task_id,
                session_id=self.current_session.session_id,
                goal=task.goal,
                started=task.started_at,
                ended=task.ended_at,
                status=task.status,
                agents=sorted(task.agents),
                channels=list(task.channels.keys()),
                input_tokens=getattr(task, "input_tokens", 0),
                output_tokens=getattr(task, "output_tokens", 0),
                estimated_cost=round(getattr(task, "estimated_cost", 0.0), 6),
            ),
        )
        self._task_log_writers[task.task_id] = writer
        return writer

    def _update_agent_session_indices(self, task: TaskRecord) -> None:
        """Description:
            Update per-agent session indices for one task.

        Requirements:
            - Skip Project Agent and end-user pseudo-participants.
            - Point every participating specialist agent back to the persisted session.

        :param task: Task record whose participating agents should be indexed.
        """

        if self.current_session is None:
            return
        session_date = self.current_session.started_at[:10]
        channels = list(task.channels.keys())
        for agent_name in sorted(task.agents):
            if agent_name in {"project-agent", "user"}:
                continue
            self._agent_index_writer.update_index(
                agent_name=agent_name,
                session_id=self.current_session.session_id,
                session_date=session_date,
                task_id=task.task_id,
                task_goal=task.goal,
                channels=channels,
            )

    def _write_project_agent_sessions_index(self) -> Path:
        """Description:
            Rewrite the Project Agent session index from persisted session metadata.

        Requirements:
            - Keep the index under `.faith/agents/project-agent/sessions.index.md`.
            - Include one row per persisted session with status and transcript-log path.

        :returns: Written session-index path.
        """

        index_path = self.faith_dir / "agents" / "project-agent" / "sessions.index.md"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Project Agent Sessions Index",
            "",
            "| Session ID | Status | Started | Transcript |",
            "| --- | --- | --- | --- |",
        ]
        for session_path in sorted(
            [path for path in self.sessions_dir.iterdir() if path.is_dir()],
            key=lambda path: path.name,
        ):
            meta_path = session_path / "session.meta.json"
            if not meta_path.exists():
                continue
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            transcript_path = session_path / "pa-user.log"
            lines.append(
                "| "
                f"{data.get('session_id', session_path.name)} | "
                f"{data.get('status', 'unknown')} | "
                f"{data.get('started_at', '')} | "
                f"{transcript_path.as_posix()} |"
            )
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return index_path

    def append_project_agent_message(self, role: str, content: str) -> Path:
        """Description:
            Append one user or assistant transcript message to the session-level Project Agent log.

        Requirements:
            - Persist the log at `.faith/sessions/<session>/pa-user.log`.
            - Accept only `user` and `assistant` roles.
            - Create the log file lazily on first write.

        :param role: Transcript role name.
        :param content: Transcript content to persist.
        :returns: Written transcript-log path.
        :raises ValueError: If the role is unsupported.
        :raises RuntimeError: If no session is active.
        """

        if role not in {"user", "assistant"}:
            raise ValueError("project agent transcript role must be 'user' or 'assistant'")
        log_path = self._project_agent_log_path()
        if not log_path.exists():
            log_path.write_text(PROJECT_AGENT_TRANSCRIPT_HEADER, encoding="utf-8")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(self._format_project_agent_message(role, content))
        self._write_project_agent_sessions_index()
        return log_path

    def load_project_agent_transcript(
        self,
        session_path: Path | None = None,
    ) -> list[dict[str, str]]:
        """Description:
            Load one persisted Project Agent transcript from disk.

        Requirements:
            - Return an empty list when the transcript file does not exist yet.
            - Parse the markdown transcript back into structured role/content messages.

        :param session_path: Optional explicit session directory.
        :returns: Parsed transcript messages.
        """

        try:
            log_path = self._project_agent_log_path(session_path)
        except RuntimeError:
            return []
        if not log_path.exists():
            return []
        return self._parse_project_agent_transcript(log_path.read_text(encoding="utf-8"))

    def load_latest_project_agent_transcript(self) -> list[dict[str, str]]:
        """Description:
            Load the newest persisted Project Agent transcript from the sessions tree.

        Requirements:
            - Return an empty list when no prior session transcript exists.

        :returns: Parsed transcript messages from the newest session.
        """

        latest_session = self._latest_session_path()
        if latest_session is None:
            return []
        return self.load_project_agent_transcript(latest_session)

    def latest_project_agent_session_id(self) -> str | None:
        """Description:
            Return the newest persisted Project Agent session identifier.

        Requirements:
            - Prefer the active in-memory session when one exists.
            - Fall back to the newest persisted session metadata.

        :returns: Latest session identifier, if any.
        """

        if self.current_session is not None:
            return self.current_session.session_id
        latest_session = self._latest_session_path()
        if latest_session is None:
            return None
        meta_path = latest_session / "session.meta.json"
        if not meta_path.exists():
            return latest_session.name
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return str(data.get("session_id", latest_session.name))

    def resume_latest_session(self) -> SessionRecord | None:
        """Description:
            Restore the newest non-ended session into active in-memory state when appropriate.

        Requirements:
            - Resume only sessions whose metadata status is not `ended`.
            - Leave `current_session` unset when the newest session is already ended.

        :returns: Restored active session record, if any.
        """

        latest_session = self._latest_session_path()
        if latest_session is None:
            return None
        meta_path = latest_session / "session.meta.json"
        if not meta_path.exists():
            return None
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        session = SessionRecord(
            session_id=str(data.get("session_id", latest_session.name)),
            path=latest_session,
            trigger=str(data.get("trigger", "web-ui")),
            started_at=str(data.get("started_at", "")),
            active_task_id=data.get("active_task_id"),
        )
        if str(data.get("status", "active")) == "ended":
            return session
        self.current_session = session
        self.session_id = session.session_id
        self.session_dir = latest_session
        self._task_log_writers = {}
        return session

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

    def validate_all_agents_cag(self) -> dict[str, CAGValidationResult]:
        """Description:
            Validate the configured CAG documents for every agent in the active project.

        Requirements:
            - Use the validated agent configs from the project `.faith/agents` tree.
            - Apply each agent's own model override or the system default model for token counting.
            - Return an empty mapping when no system config is available.

        :returns: Per-agent CAG validation results keyed by agent identifier.
        """

        if self.system_config is None:
            return {}
        results: dict[str, CAGValidationResult] = {}
        for agent_id, config in load_all_agent_configs(self.project_root).items():
            manager = CAGManager(
                project_root=self.project_root,
                model_name=config.model or self.system_config.default_agent_model,
                document_paths=list(config.cag_documents),
                max_tokens=config.cag_max_tokens,
            )
            results[agent_id] = manager.load_all()
        return results

    def format_cag_validation_for_user(
        self,
        validation_results: dict[str, CAGValidationResult],
    ) -> str:
        """Description:
            Format per-agent CAG validation outcomes for PA/user reporting.

        Requirements:
            - Include only agents with warnings or errors in the human-facing report.
            - Return an empty string when there is nothing actionable to surface.

        :param validation_results: Per-agent CAG validation results.
        :returns: Human-readable CAG validation report.
        """

        lines: list[str] = []
        for agent_id, result in validation_results.items():
            if not result.errors and not result.warnings:
                continue
            lines.append(f"{agent_id}: {result.summary()}")
        return "\n\n".join(lines)

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
            "started": _iso(now),
            "ended": None,
            "privacy_profile": str(getattr(self.system_config, "privacy_profile", "internal")),
            "task_count": 0,
            "agents_active": [],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_estimated_cost": 0.0,
            "tasks": {},
        }
        cag_validation = self.validate_all_agents_cag()
        if cag_validation:
            metadata["cag_validation"] = {
                "report": self.format_cag_validation_for_user(cag_validation),
                "agents": {
                    agent_id: {
                        "success": result.success,
                        "total_tokens": result.total_tokens,
                        "max_tokens": result.max_tokens,
                        "document_count": result.document_count,
                        "loaded_count": result.loaded_count,
                        "errors": list(result.errors),
                        "warnings": list(result.warnings),
                    }
                    for agent_id, result in cag_validation.items()
                },
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
        self._task_log_writers = {}
        self._write_project_agent_sessions_index()
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
        task.input_tokens = 0
        task.output_tokens = 0
        task.estimated_cost = 0.0
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
            "session_id": self.current_session.session_id if self.current_session else None,
            "goal": task.goal,
            "status": task.status,
            "channels": task.channels,
            "agents": sorted(task.agents),
            "staged_agents": task.staged_agents,
            "current_phase": task.current_phase,
            "sandbox_id": task.sandbox_id,
            "started_at": task.started_at,
            "started": task.started_at,
            "ended_at": task.ended_at,
            "ended": task.ended_at,
            "input_tokens": getattr(task, "input_tokens", 0),
            "output_tokens": getattr(task, "output_tokens", 0),
            "estimated_cost": round(getattr(task, "estimated_cost", 0.0), 6),
        }
        (task.path / "task.meta.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        task_log_writer = self._task_log_writers.get(task.task_id)
        if task_log_writer is not None:
            task_log_writer.meta.status = task.status
            task_log_writer.meta.started = task.started_at
            task_log_writer.meta.ended = task.ended_at
            task_log_writer.meta.channels = list(task.channels.keys())
            task_log_writer.meta.agents = sorted(task.agents)
            task_log_writer.meta.input_tokens = getattr(task, "input_tokens", 0)
            task_log_writer.meta.output_tokens = getattr(task, "output_tokens", 0)
            task_log_writer.meta.estimated_cost = round(getattr(task, "estimated_cost", 0.0), 6)
            task_log_writer._write_meta()

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
        self._sync_session_totals()

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

    def append_channel_message(
        self,
        *,
        task_id: str,
        channel_name: str,
        sender: str,
        recipient: str,
        msg_type: str,
        summary: str,
        status: str | None = None,
        needs: str | None = None,
        files: list[str] | None = None,
        context_ref: str | None = None,
    ) -> Path:
        """Description:
            Append one compact-style task channel message to the persisted task log.

        Requirements:
            - Create the channel log lazily under the task directory.
            - Track specialist participants in task metadata and agent indices.

        :param task_id: Task identifier to append under.
        :param channel_name: Channel identifier to log.
        :param sender: Sending participant name.
        :param recipient: Receiving participant name.
        :param msg_type: Compact message type.
        :param summary: Human-readable summary text.
        :param status: Optional status field.
        :param needs: Optional needs field.
        :param files: Optional file list.
        :param context_ref: Optional context reference.
        :returns: Written channel log path.
        """

        task = self.tasks[task_id]
        task.channels.setdefault(
            channel_name, {"name": channel_name, "agents": [], "message_count": 0}
        )
        for participant in (sender, recipient):
            if participant not in {"project-agent", "user"}:
                task.agents.add(participant)
        channel_agents = set(task.channels[channel_name].get("agents", []))
        channel_agents.update(
            agent for agent in (sender, recipient) if agent not in {"project-agent", "user"}
        )
        task.channels[channel_name]["agents"] = sorted(channel_agents)
        task.channels[channel_name]["message_count"] = (
            int(task.channels[channel_name].get("message_count", 0)) + 1
        )
        task_writer = self._ensure_task_log_writer(task)
        for agent_name in sorted(task.agents):
            task_writer.add_agent(agent_name)
        channel_writer = task_writer.get_channel_writer(channel_name)
        timestamp = _now().strftime("%H:%M:%S")
        channel_writer.write_message(
            timestamp=timestamp,
            sender=sender,
            recipient=recipient,
            msg_type=msg_type,
            summary=summary,
            status=status,
            needs=needs,
            files=files,
            context_ref=context_ref,
        )
        self._write_task_meta(task)
        self._update_agent_session_indices(task)
        return task_writer.task_dir / f"{channel_name}.log"

    def record_token_usage(
        self,
        *,
        task_id: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float,
    ) -> None:
        """Description:
            Accumulate token and cost totals against one task and its parent session.

        Requirements:
            - Keep both task-level and session-level totals current.
            - Preserve the persisted metadata on every update.

        :param task_id: Task identifier receiving the token usage.
        :param input_tokens: Prompt token count to add.
        :param output_tokens: Completion token count to add.
        :param estimated_cost: Estimated cost to add in USD.
        """

        task = self.tasks[task_id]
        task.input_tokens = getattr(task, "input_tokens", 0) + input_tokens
        task.output_tokens = getattr(task, "output_tokens", 0) + output_tokens
        task.estimated_cost = getattr(task, "estimated_cost", 0.0) + estimated_cost
        task_writer = self._ensure_task_log_writer(task)
        task_writer.update_tokens(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
        )
        self._write_task_meta(task)
        self._sync_session_totals()

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
        task_writer = self._task_log_writers.get(task_id)
        if task_writer is not None:
            task_writer.complete()
        self._update_agent_session_indices(task)

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
        task_writer = self._task_log_writers.get(task_id)
        if task_writer is not None:
            task_writer.finish("cancelled")
        self._update_agent_session_indices(task)

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
        data["ended"] = data["ended_at"]
        active_agents = self.get_active_agent_ids()
        data["task_count"] = len(self.tasks)
        data["agents_active"] = active_agents
        data["active_task_id"] = None
        meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._sync_session_totals()
        self._write_project_agent_sessions_index()
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
        self._task_log_writers = {}


Session = SessionRecord
Task = TaskRecord
