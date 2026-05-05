"""Description:
    Provide standalone session, task, and channel log writers for FAITH.

Requirements:
    - Persist session and task metadata with the fields required by FRS section 8.4.
    - Write markdown channel logs and PA-user transcript logs without duplicating channel content.
    - Maintain per-agent session cross-reference indices that point back to session directories.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _now_iso() -> str:
    """Description:
        Return the current UTC time as an ISO-8601 string.

    Requirements:
        - Always emit a trailing `Z` suffix for UTC timestamps.

    :returns: Current UTC timestamp string.
    """

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_dir_name(session_id: str, started: str) -> str:
    """Description:
        Build the persisted session-directory name for one session identifier.

    Requirements:
        - Preserve existing identifiers that already carry a timestamp suffix.
        - Append the UTC calendar date for simpler identifiers that do not already encode it.

    :param session_id: Session identifier being persisted.
    :param started: ISO-8601 UTC start timestamp.
    :returns: Session directory name.
    """

    if len(session_id.split("-")) >= 3:
        return session_id
    return f"{session_id}-{started[:10]}"


class SessionMeta(BaseModel):
    """Description:
        Represent one persisted session metadata payload.

    Requirements:
        - Preserve all fields required by FRS section 8.4.
        - Exclude `None` values when serialising to JSON.
    """

    session_id: str
    started: str = Field(default_factory=_now_iso)
    ended: str | None = None
    privacy_profile: str = "internal"
    task_count: int = 0
    agents_active: list[str] = Field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_estimated_cost: float = 0.0
    status: str = "active"

    def to_json(self) -> str:
        """Description:
            Serialise the session metadata as formatted JSON.

        Requirements:
            - Exclude `None` fields from the output.

        :returns: JSON representation of the session metadata.
        """

        return self.model_dump_json(indent=2, exclude_none=True)

    @classmethod
    def from_json(cls, payload: str) -> SessionMeta:
        """Description:
            Restore session metadata from JSON text.

        Requirements:
            - Strip surrounding whitespace before validation.

        :param payload: JSON text to parse.
        :returns: Parsed session metadata.
        """

        return cls.model_validate_json(payload.strip())


class TaskMeta(BaseModel):
    """Description:
        Represent one persisted task metadata payload.

    Requirements:
        - Preserve all task metadata fields required by FRS section 8.4.
        - Exclude `None` values when serialising to JSON.
    """

    task_id: str
    session_id: str
    goal: str
    started: str = Field(default_factory=_now_iso)
    ended: str | None = None
    status: str = "active"
    agents: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0

    def to_json(self) -> str:
        """Description:
            Serialise the task metadata as formatted JSON.

        Requirements:
            - Exclude `None` fields from the output.

        :returns: JSON representation of the task metadata.
        """

        return self.model_dump_json(indent=2, exclude_none=True)

    @classmethod
    def from_json(cls, payload: str) -> TaskMeta:
        """Description:
            Restore task metadata from JSON text.

        Requirements:
            - Strip surrounding whitespace before validation.

        :param payload: JSON text to parse.
        :returns: Parsed task metadata.
        """

        return cls.model_validate_json(payload.strip())


class ChannelLogWriter:
    """Description:
        Write one markdown log for a single task channel.

    Requirements:
        - Emit a stable markdown header the first time the channel is written.
        - Append messages in the compact protocol style described by the FRS.
    """

    def __init__(self, *, log_path: Path, channel_name: str, task_id: str, task_goal: str) -> None:
        """Description:
            Initialise the channel log writer.

        Requirements:
            - Preserve the target path, channel name, task identifier, and task goal for header creation.

        :param log_path: Channel log file path.
        :param channel_name: Channel identifier.
        :param task_id: Parent task identifier.
        :param task_goal: Human-readable task goal.
        """

        self.log_path = Path(log_path)
        self.channel_name = channel_name
        self.task_id = task_id
        self.task_goal = task_goal

    def _ensure_header(self) -> None:
        """Description:
            Create the channel log file header when the file is still empty.

        Requirements:
            - Leave existing non-empty logs unchanged.
            - Create parent directories when needed.
        """

        if self.log_path.exists() and self.log_path.stat().st_size > 0:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# Channel: {self.channel_name}\n"
            f"# Task: {self.task_id} — {self.task_goal}\n"
            f"# Started: {_now_iso()}\n"
        )
        self.log_path.write_text(header, encoding="utf-8")

    def write_message(
        self,
        *,
        timestamp: str,
        sender: str,
        recipient: str,
        msg_type: str,
        summary: str,
        status: str | None = None,
        needs: str | None = None,
        files: list[str] | None = None,
        context_ref: str | None = None,
    ) -> None:
        """Description:
            Append one compact-protocol entry to the channel log.

        Requirements:
            - Emit the sender/recipient header line.
            - Preserve optional status, needs, file, and context-reference fields when supplied.

        :param timestamp: Human-readable message timestamp.
        :param sender: Sending participant.
        :param recipient: Receiving participant.
        :param msg_type: Compact protocol message type.
        :param summary: Human-readable summary.
        :param status: Optional status field.
        :param needs: Optional `needs` field.
        :param files: Optional file list.
        :param context_ref: Optional context reference.
        """

        self._ensure_header()
        field_bits = [f"type: {msg_type}"]
        if status:
            field_bits.append(f"status: {status}")
        lines = [
            "",
            "---",
            f"**[{timestamp}] {sender} → {recipient}**",
            " | ".join(field_bits),
            f'summary: "{summary}"',
        ]
        if needs:
            lines.append(f'needs: "{needs}"')
        if files:
            lines.append(f"files: [{', '.join(files)}]")
        if context_ref:
            lines.append(f"context_ref: {context_ref}")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


class PAUserLogWriter:
    """Description:
        Write the PA↔user transcript log for one session.

    Requirements:
        - Keep the markdown format compatible with the existing Project Agent transcript parser.
        - Create the header lazily on first write.
    """

    HEADER = "# Project Agent Transcript\n\n"

    def __init__(self, *, log_path: Path) -> None:
        """Description:
            Initialise the PA-user log writer.

        Requirements:
            - Preserve the target log path for later writes.

        :param log_path: Session-level `pa-user.log` path.
        """

        self.log_path = Path(log_path)

    def _ensure_header(self) -> None:
        """Description:
            Create the transcript log header when the file is still empty.

        Requirements:
            - Leave existing non-empty logs unchanged.
            - Create parent directories when needed.
        """

        if self.log_path.exists() and self.log_path.stat().st_size > 0:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(self.HEADER, encoding="utf-8")

    def write_message(self, *, role: str, content: str) -> None:
        """Description:
            Append one user or assistant transcript message.

        Requirements:
            - Accept only `user` and `assistant` roles.
            - Preserve multiline content in a fenced block compatible with transcript rehydration.

        :param role: Transcript role name.
        :param content: Transcript content to persist.
        :raises ValueError: If the role is unsupported.
        """

        if role not in {"user", "assistant"}:
            raise ValueError("PAUserLogWriter only supports 'user' and 'assistant' roles.")
        self._ensure_header()
        label = "User" if role == "user" else "Assistant"
        normalised = content.rstrip("\n")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"## {label}\n~~~text\n{normalised}\n~~~\n\n")


class TaskLogWriter:
    """Description:
        Manage the persisted logs for one task inside a session.

    Requirements:
        - Maintain the task metadata file.
        - Ensure one channel log per channel per task.
    """

    def __init__(self, *, task_dir: Path, meta: TaskMeta) -> None:
        """Description:
            Initialise the task log writer.

        Requirements:
            - Create the task directory eagerly.
            - Persist the initial task metadata immediately.

        :param task_dir: Task directory path.
        :param meta: Task metadata payload.
        """

        self.task_dir = Path(task_dir)
        self.meta = meta
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self._channel_writers: dict[str, ChannelLogWriter] = {}
        self._write_meta()

    def _write_meta(self) -> None:
        """Description:
            Persist the current task metadata to disk.

        Requirements:
            - Overwrite the existing metadata file on every update.
        """

        (self.task_dir / "task.meta.json").write_text(self.meta.to_json(), encoding="utf-8")

    def get_channel_writer(self, channel_name: str) -> ChannelLogWriter:
        """Description:
            Return the unique channel log writer for one task channel.

        Requirements:
            - Reuse the existing writer when the same channel is requested again.
            - Track the channel name in task metadata the first time it is seen.

        :param channel_name: Channel identifier.
        :returns: Unique channel writer for the requested channel.
        """

        if channel_name not in self._channel_writers:
            self._channel_writers[channel_name] = ChannelLogWriter(
                log_path=self.task_dir / f"{channel_name}.log",
                channel_name=channel_name,
                task_id=self.meta.task_id,
                task_goal=self.meta.goal,
            )
            if channel_name not in self.meta.channels:
                self.meta.channels.append(channel_name)
                self._write_meta()
        return self._channel_writers[channel_name]

    def get_pa_agent_writer(self, agent_name: str) -> ChannelLogWriter:
        """Description:
            Return the dedicated PA↔agent log writer for one agent.

        Requirements:
            - Persist the file as `pa-<agent>.log`.

        :param agent_name: Agent identifier.
        :returns: Channel-style writer for PA↔agent messages.
        """

        channel_name = f"pa-{agent_name}"
        return self.get_channel_writer(channel_name)

    def add_agent(self, agent_name: str) -> None:
        """Description:
            Register one participating agent in the task metadata.

        Requirements:
            - Avoid duplicate agent entries.

        :param agent_name: Agent identifier.
        """

        if agent_name not in self.meta.agents:
            self.meta.agents.append(agent_name)
            self._write_meta()

    def update_tokens(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: float = 0.0,
    ) -> None:
        """Description:
            Accumulate token and cost totals for the task.

        Requirements:
            - Add the supplied deltas onto the existing totals.

        :param input_tokens: Additional input tokens to record.
        :param output_tokens: Additional output tokens to record.
        :param estimated_cost: Additional estimated cost to record.
        """

        self.meta.input_tokens += input_tokens
        self.meta.output_tokens += output_tokens
        self.meta.estimated_cost += estimated_cost
        self._write_meta()

    def finish(self, status: str) -> None:
        """Description:
            Mark the task as finished.

        Requirements:
            - Persist the terminal status and end timestamp.

        :param status: Terminal status value.
        """

        self.meta.status = status
        self.meta.ended = _now_iso()
        self._write_meta()

    def complete(self) -> None:
        """Description:
            Mark the task as complete.

        Requirements:
            - Delegate to the generic finish helper with `complete`.
        """

        self.finish("complete")


class SessionLogWriter:
    """Description:
        Manage one persisted session log tree.

    Requirements:
        - Create the session directory and metadata file eagerly.
        - Expose task creation, transcript logging, token accumulation, and completion helpers.
    """

    def __init__(
        self,
        *,
        sessions_dir: Path,
        session_id: str,
        privacy_profile: str = "internal",
        started: str | None = None,
    ) -> None:
        """Description:
            Initialise the session log writer.

        Requirements:
            - Resolve the persisted session-directory name from the session identifier and start timestamp.
            - Create the session directory and its `tasks/` child eagerly.

        :param sessions_dir: Root sessions directory.
        :param session_id: Session identifier.
        :param privacy_profile: Privacy profile value to persist.
        :param started: Optional explicit start timestamp.
        """

        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        started_at = started or _now_iso()
        self.meta = SessionMeta(
            session_id=session_id,
            started=started_at,
            privacy_profile=privacy_profile,
        )
        self.session_dir = self.sessions_dir / _session_dir_name(session_id, started_at)
        (self.session_dir / "tasks").mkdir(parents=True, exist_ok=True)
        self.pa_user_log = PAUserLogWriter(log_path=self.session_dir / "pa-user.log")
        self._task_writers: dict[str, TaskLogWriter] = {}
        self._write_meta()

    def _write_meta(self) -> None:
        """Description:
            Persist the current session metadata to disk.

        Requirements:
            - Overwrite the existing metadata file on every update.
        """

        (self.session_dir / "session.meta.json").write_text(self.meta.to_json(), encoding="utf-8")

    def create_task(self, *, goal: str, task_id: str) -> TaskLogWriter:
        """Description:
            Create one task writer under the current session.

        Requirements:
            - Reuse the existing writer when the same task identifier is requested twice.
            - Increment the session task count when a new task is created.

        :param goal: Human-readable task goal.
        :param task_id: Task identifier.
        :returns: Task writer for the created task.
        """

        if task_id in self._task_writers:
            return self._task_writers[task_id]
        writer = TaskLogWriter(
            task_dir=self.session_dir / "tasks" / task_id,
            meta=TaskMeta(task_id=task_id, session_id=self.meta.session_id, goal=goal),
        )
        self._task_writers[task_id] = writer
        self.meta.task_count = len(self._task_writers)
        self._write_meta()
        return writer

    def add_active_agent(self, agent_name: str) -> None:
        """Description:
            Register one active agent in the session metadata.

        Requirements:
            - Keep the list unique and sorted for stable output.

        :param agent_name: Agent identifier.
        """

        if agent_name not in self.meta.agents_active:
            self.meta.agents_active.append(agent_name)
            self.meta.agents_active = sorted(self.meta.agents_active)
            self._write_meta()

    def update_tokens(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: float = 0.0,
    ) -> None:
        """Description:
            Accumulate token and cost totals for the session.

        Requirements:
            - Add the supplied deltas onto the existing totals.

        :param input_tokens: Additional input tokens to record.
        :param output_tokens: Additional output tokens to record.
        :param estimated_cost: Additional estimated cost to record.
        """

        self.meta.total_input_tokens += input_tokens
        self.meta.total_output_tokens += output_tokens
        self.meta.total_estimated_cost += estimated_cost
        self._write_meta()

    def complete(self) -> None:
        """Description:
            Mark the session as complete.

        Requirements:
            - Persist the terminal status and end timestamp.
        """

        self.meta.status = "complete"
        self.meta.ended = _now_iso()
        self._write_meta()

    def interrupt(self) -> None:
        """Description:
            Mark the session as interrupted.

        Requirements:
            - Persist the terminal status and end timestamp.
        """

        self.meta.status = "interrupted"
        self.meta.ended = _now_iso()
        self._write_meta()

    @classmethod
    def find_active_sessions(cls, sessions_dir: Path) -> list[SessionMeta]:
        """Description:
            Return persisted sessions that are still active or interrupted.

        Requirements:
            - Ignore missing or malformed metadata files safely.
            - Exclude sessions marked `complete`.

        :param sessions_dir: Root sessions directory to scan.
        :returns: Non-complete persisted session metadata.
        """

        root = Path(sessions_dir)
        if not root.exists():
            return []
        active: list[SessionMeta] = []
        for session_dir in root.iterdir():
            meta_path = session_dir / "session.meta.json"
            if not session_dir.is_dir() or not meta_path.exists():
                continue
            try:
                meta = SessionMeta.from_json(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if meta.status != "complete":
                active.append(meta)
        return active

    @classmethod
    def from_system_config(
        cls,
        *,
        faith_dir: Path,
        system_config: dict[str, Any],
        session_id: str,
    ) -> SessionLogWriter:
        """Description:
            Build a session log writer from `.faith/system.yaml`-style config.

        Requirements:
            - Read the `session_logs.privacy_profile` override when present.
            - Fall back to `internal` when the config omits it.

        :param faith_dir: Project `.faith` directory.
        :param system_config: Parsed system configuration payload.
        :param session_id: Session identifier.
        :returns: Configured session log writer.
        """

        privacy_profile = system_config.get("session_logs", {}).get("privacy_profile", "internal")
        return cls(
            sessions_dir=Path(faith_dir) / "sessions",
            session_id=session_id,
            privacy_profile=privacy_profile,
        )


class AgentIndexWriter:
    """Description:
        Maintain per-agent session cross-reference indices.

    Requirements:
        - Link agents to session/task participation without duplicating channel log content.
        - Avoid duplicate task entries when the same task is indexed repeatedly.
    """

    def __init__(self, *, agents_dir: Path) -> None:
        """Description:
            Initialise the agent index writer.

        Requirements:
            - Preserve the root agents directory for later index updates.

        :param agents_dir: Root `.faith/agents` directory.
        """

        self.agents_dir = Path(agents_dir)

    def update_index(
        self,
        *,
        agent_name: str,
        session_id: str,
        session_date: str,
        task_id: str,
        task_goal: str,
        channels: list[str],
    ) -> None:
        """Description:
            Add one task reference to an agent's session index.

        Requirements:
            - Create the agent directory when it does not already exist.
            - Avoid writing duplicate task entries for the same session/task pair.

        :param agent_name: Agent identifier.
        :param session_id: Session identifier.
        :param session_date: Human-readable session date.
        :param task_id: Task identifier.
        :param task_goal: Human-readable task goal.
        :param channels: Channel names associated with the task.
        """

        agent_dir = self.agents_dir / agent_name
        agent_dir.mkdir(parents=True, exist_ok=True)
        index_path = agent_dir / "sessions.index.md"
        session_dir_name = (
            session_id if len(session_id.split("-")) >= 3 else f"{session_id}-{session_date}"
        )
        link = f"../../sessions/{session_dir_name}/"
        existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        task_marker = f"{task_id} — {task_goal}"
        if task_marker in existing:
            return

        lines: list[str]
        if existing:
            lines = existing.rstrip().splitlines()
        else:
            lines = [f"# Session Index: {agent_name}", ""]

        session_header = f"## {session_id} ({session_date})"
        if session_header not in lines:
            lines.extend([session_header, f"- Session logs: {link}"])
        channel_text = ", ".join(channels) if channels else "(none)"
        lines.append(f"- {task_id} — {task_goal} | channels: {channel_text}")
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
