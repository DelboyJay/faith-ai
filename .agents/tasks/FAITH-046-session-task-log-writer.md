# FAITH-046 — Session & Task Log Writer

**Phase:** 9 — Logging & Observability
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-015
**FRS Reference:** Section 8.4

---

## Objective

Implement the session and task log writer for FAITH. Session logs capture the full human-readable record of all conversations and tasks in a two-level directory structure under `.faith/sessions/`. The writer creates `session.meta.json` and `task.meta.json` with all fields defined in FRS Section 8.4. Channel logs are written in markdown format with compact protocol rendering. The PA-user conversation is logged to `pa-user.log` at session level. An agent cross-reference index (`agents/*/sessions.index.md`) provides per-agent session history without duplicating log content. The invariant is: one log per channel per task — no content duplication.

Current implementation note: session/task metadata, `pa-user.log`, task channel logs, direct `pa-<agent>.log` task assignments, agent session indices, task/session token aggregation, and persisted task-channel writes from the PA runtime are present in the runtime codebase and now satisfy the FRS scope for session and task logging.

---

## Architecture

```
faith/logging/
├── __init__.py
└── session_log.py    ← SessionLogWriter, TaskLogWriter, AgentIndexWriter (this task)

tests/
└── test_session_log.py  ← Tests (this task)
```

---

## Files to Create

### 1. `faith/logging/__init__.py`

```python
"""FAITH Logging — session logs, task logs, and observability writers."""

from faith.logging.session_log import (
    AgentIndexWriter,
    ChannelLogWriter,
    SessionLogWriter,
    SessionMeta,
    TaskLogWriter,
    TaskMeta,
)

__all__ = [
    "AgentIndexWriter",
    "ChannelLogWriter",
    "SessionLogWriter",
    "SessionMeta",
    "TaskLogWriter",
    "TaskMeta",
]
```

### 2. `faith/logging/session_log.py`

```python
"""FAITH Session & Task Log Writer — human-readable session history.

Session logs capture the full record of all conversations and tasks in a
two-level directory structure:

    .faith/sessions/
    └── sess-NNNN-YYYY-MM-DD/
        ├── session.meta.json
        ├── pa-user.log
        └── tasks/
            └── task-NNN-HHMMSS.mmm/
                ├── task.meta.json
                ├── ch-<channel-name>.log
                └── pa-<agent-name>.log

The PA is the sole writer. Channel logs use markdown format with compact
protocol rendering. Agent cross-reference indices provide per-agent session
history without content duplication.

FRS Reference: Section 8.4
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("faith.logging.session")

# Default session retention in days (from FRS Section 8.6)
DEFAULT_SESSION_RETENTION_DAYS = 365


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_time_compact() -> str:
    """Return the current UTC time as HH:MM:SS for log entries."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


# ──────────────────────────────────────────────────
# Metadata Models
# ──────────────────────────────────────────────────


class SessionMeta(BaseModel):
    """Session metadata written to session.meta.json.

    All fields match FRS Section 8.4 specification.

    Attributes:
        session_id: Unique session identifier (e.g. "sess-0042").
        started: ISO 8601 UTC timestamp when the session began.
        ended: ISO 8601 UTC timestamp when the session ended, or None if active.
        status: Session status — "active", "complete", or "interrupted".
        privacy_profile: Privacy profile applied to this session (e.g. "internal").
        task_count: Number of tasks created during this session.
        agents_active: List of agent short names that participated.
        total_input_tokens: Cumulative input tokens across all agents.
        total_output_tokens: Cumulative output tokens across all agents.
        total_estimated_cost: Cumulative estimated cost in USD.
    """

    session_id: str
    started: str = Field(default_factory=_now_iso)
    ended: Optional[str] = None
    status: str = "active"
    privacy_profile: str = "internal"
    task_count: int = 0
    agents_active: list[str] = Field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_estimated_cost: float = 0.0

    def to_json(self, indent: int = 2) -> str:
        """Serialise to formatted JSON string."""
        return self.model_dump_json(indent=indent, exclude_none=True)

    @classmethod
    def from_json(cls, data: str) -> "SessionMeta":
        """Deserialise from a JSON string."""
        return cls.model_validate_json(data.strip())

    @classmethod
    def from_file(cls, path: Path) -> "SessionMeta":
        """Load SessionMeta from a session.meta.json file."""
        return cls.from_json(path.read_text(encoding="utf-8"))


class TaskMeta(BaseModel):
    """Task metadata written to task.meta.json.

    Each task represents a discrete goal within a session. The task ID
    includes millisecond-precision timing for uniqueness.

    Attributes:
        task_id: Unique task identifier (e.g. "task-001-143201.456").
        session_id: Parent session identifier.
        goal: Human-readable description of the task goal.
        started: ISO 8601 UTC timestamp when the task began.
        ended: ISO 8601 UTC timestamp when the task ended, or None if active.
        status: Task status — "active", "complete", "blocked", "cancelled".
        agents: List of agent short names assigned to this task.
        channels: List of channel identifiers created for this task.
        input_tokens: Cumulative input tokens for this task.
        output_tokens: Cumulative output tokens for this task.
        estimated_cost: Cumulative estimated cost for this task in USD.
    """

    task_id: str
    session_id: str
    goal: str = ""
    started: str = Field(default_factory=_now_iso)
    ended: Optional[str] = None
    status: str = "active"
    agents: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0

    def to_json(self, indent: int = 2) -> str:
        """Serialise to formatted JSON string."""
        return self.model_dump_json(indent=indent, exclude_none=True)

    @classmethod
    def from_json(cls, data: str) -> "TaskMeta":
        """Deserialise from a JSON string."""
        return cls.model_validate_json(data.strip())

    @classmethod
    def from_file(cls, path: Path) -> "TaskMeta":
        """Load TaskMeta from a task.meta.json file."""
        return cls.from_json(path.read_text(encoding="utf-8"))


# ──────────────────────────────────────────────────
# Channel Log Writer
# ──────────────────────────────────────────────────


class ChannelLogWriter:
    """Writes channel conversation logs in markdown format.

    Each channel within a task has exactly one log file. Messages are
    rendered in compact protocol format per FRS Section 8.4.

    The markdown format:
        # Channel: ch-auth-feature
        # Task: task-001 — Implement JWT auth module
        # Started: 2026-03-23T14:32:00Z

        ---
        **[14:32:01] software-developer → qa-engineer**
        type: review_request | status: complete
        summary: "auth module done, 3 endpoints, JWT httponly cookies"
        needs: "test coverage for token expiry edge case"

    Attributes:
        log_path: Path to the channel log file.
        channel_name: Channel identifier.
        task_id: Parent task identifier.
        task_goal: Human-readable task goal for the header.
    """

    def __init__(
        self,
        log_path: Path,
        channel_name: str,
        task_id: str,
        task_goal: str = "",
    ):
        self.log_path = Path(log_path)
        self.channel_name = channel_name
        self.task_id = task_id
        self.task_goal = task_goal
        self._file = None
        self._initialised = False

    def _ensure_header(self) -> None:
        """Write the markdown header if this is a new log file."""
        if self._initialised:
            return

        if self.log_path.exists() and self.log_path.stat().st_size > 0:
            self._initialised = True
            return

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        goal_suffix = f" — {self.task_goal}" if self.task_goal else ""
        header = (
            f"# Channel: {self.channel_name}\n"
            f"# Task: {self.task_id}{goal_suffix}\n"
            f"# Started: {_now_iso()}\n"
        )
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write(header)

        self._initialised = True
        logger.debug(f"Channel log header written: {self.log_path}")

    def write_message(
        self,
        timestamp: str,
        sender: str,
        recipient: str,
        msg_type: str,
        summary: str,
        status: Optional[str] = None,
        needs: Optional[str] = None,
        files: Optional[list[str]] = None,
        context_ref: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> None:
        """Append a compact protocol message to the channel log.

        Args:
            timestamp: Time string for the message (e.g. "14:32:01").
            sender: Sending agent short name.
            recipient: Receiving agent short name or "all".
            msg_type: Message type (e.g. "review_request", "feedback").
            summary: Concise description of the message content.
            status: Optional status (e.g. "complete", "in_progress").
            needs: Optional description of what is needed from recipient.
            files: Optional list of relevant file paths.
            context_ref: Optional reference to previous messages.
            priority: Optional priority level.
        """
        self._ensure_header()

        lines = [
            "",
            "---",
            f"**[{timestamp}] {sender} → {recipient}**",
        ]

        # Build the compact protocol fields line
        fields = [f"type: {msg_type}"]
        if status:
            fields.append(f"status: {status}")
        if priority and priority != "normal":
            fields.append(f"priority: {priority}")
        lines.append(" | ".join(fields))

        if summary:
            lines.append(f'summary: "{summary}"')
        if needs:
            lines.append(f'needs: "{needs}"')
        if files:
            lines.append(f"files: [{', '.join(files)}]")
        if context_ref:
            lines.append(f"context_ref: {context_ref}")

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        logger.debug(
            f"Channel log entry: {self.channel_name} "
            f"{sender} → {recipient} ({msg_type})"
        )


# ──────────────────────────────────────────────────
# PA-User Log Writer
# ──────────────────────────────────────────────────


class PAUserLogWriter:
    """Writes the PA↔user conversation log at session level.

    This captures all direct interactions between the user and the PA
    in markdown format, stored as `pa-user.log` in the session directory.

    Attributes:
        log_path: Path to the pa-user.log file.
        session_id: Parent session identifier.
    """

    def __init__(self, log_path: Path, session_id: str):
        self.log_path = Path(log_path)
        self.session_id = session_id
        self._initialised = False

    def _ensure_header(self) -> None:
        """Write the log header if this is a new file."""
        if self._initialised:
            return

        if self.log_path.exists() and self.log_path.stat().st_size > 0:
            self._initialised = True
            return

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        header = (
            f"# PA ↔ User Conversation\n"
            f"# Session: {self.session_id}\n"
            f"# Started: {_now_iso()}\n"
        )
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write(header)

        self._initialised = True

    def write_user_message(self, timestamp: str, content: str) -> None:
        """Log a message from the user.

        Args:
            timestamp: Time string (e.g. "14:32:01").
            content: The user's message text.
        """
        self._ensure_header()
        entry = f"\n---\n**[{timestamp}] User:**\n{content}\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    def write_pa_message(self, timestamp: str, content: str) -> None:
        """Log a message from the PA.

        Args:
            timestamp: Time string (e.g. "14:32:05").
            content: The PA's response text.
        """
        self._ensure_header()
        entry = f"\n---\n**[{timestamp}] PA:**\n{content}\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry)


# ──────────────────────────────────────────────────
# Task Log Writer
# ──────────────────────────────────────────────────


class TaskLogWriter:
    """Manages logs for a single task within a session.

    Creates the task directory, writes task.meta.json, and provides
    channel log writers for each channel in the task. Enforces the
    invariant: one log per channel per task.

    Attributes:
        task_dir: Path to the task directory.
        meta: TaskMeta for this task.
    """

    def __init__(self, task_dir: Path, meta: TaskMeta):
        self.task_dir = Path(task_dir)
        self.meta = meta
        self._channel_writers: dict[str, ChannelLogWriter] = {}

        # Create task directory and write initial metadata
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self._write_meta()

        logger.info(f"TaskLogWriter initialised: {self.task_dir}")

    def _write_meta(self) -> None:
        """Write or update task.meta.json."""
        meta_path = self.task_dir / "task.meta.json"
        meta_path.write_text(self.meta.to_json(), encoding="utf-8")

    def get_channel_writer(
        self,
        channel_name: str,
        task_goal: str = "",
    ) -> ChannelLogWriter:
        """Get or create a ChannelLogWriter for the given channel.

        Enforces the one-log-per-channel-per-task invariant: if a writer
        for this channel already exists, it is returned rather than
        creating a duplicate.

        Args:
            channel_name: Channel identifier (e.g. "ch-auth-feature").
            task_goal: Human-readable task goal for the log header.

        Returns:
            ChannelLogWriter for the requested channel.
        """
        if channel_name in self._channel_writers:
            return self._channel_writers[channel_name]

        log_path = self.task_dir / f"{channel_name}.log"
        writer = ChannelLogWriter(
            log_path=log_path,
            channel_name=channel_name,
            task_id=self.meta.task_id,
            task_goal=task_goal or self.meta.goal,
        )
        self._channel_writers[channel_name] = writer

        # Track channel in metadata
        if channel_name not in self.meta.channels:
            self.meta.channels.append(channel_name)
            self._write_meta()

        logger.debug(f"Channel writer created: {channel_name} in {self.task_dir}")
        return writer

    def get_pa_agent_writer(self, agent_name: str) -> ChannelLogWriter:
        """Get or create a writer for PA↔agent direct assignment logs.

        These are written as `pa-<agent-name>.log` in the task directory,
        using the same ChannelLogWriter format.

        Args:
            agent_name: Agent short name (e.g. "software-developer").

        Returns:
            ChannelLogWriter for PA↔agent communication.
        """
        key = f"pa-{agent_name}"
        if key in self._channel_writers:
            return self._channel_writers[key]

        log_path = self.task_dir / f"pa-{agent_name}.log"
        writer = ChannelLogWriter(
            log_path=log_path,
            channel_name=f"pa-{agent_name}",
            task_id=self.meta.task_id,
            task_goal=self.meta.goal,
        )
        self._channel_writers[key] = writer
        return writer

    def add_agent(self, agent_name: str) -> None:
        """Record an agent as participating in this task.

        Args:
            agent_name: Agent short name.
        """
        if agent_name not in self.meta.agents:
            self.meta.agents.append(agent_name)
            self._write_meta()

    def update_tokens(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: float = 0.0,
    ) -> None:
        """Accumulate token usage for this task.

        Args:
            input_tokens: Additional input tokens to add.
            output_tokens: Additional output tokens to add.
            estimated_cost: Additional estimated cost to add.
        """
        self.meta.input_tokens += input_tokens
        self.meta.output_tokens += output_tokens
        self.meta.estimated_cost += estimated_cost
        self._write_meta()

    def complete(self) -> None:
        """Mark the task as complete and write final metadata."""
        self.meta.status = "complete"
        self.meta.ended = _now_iso()
        self._write_meta()
        logger.info(f"Task completed: {self.meta.task_id}")

    def cancel(self) -> None:
        """Mark the task as cancelled and write final metadata."""
        self.meta.status = "cancelled"
        self.meta.ended = _now_iso()
        self._write_meta()
        logger.info(f"Task cancelled: {self.meta.task_id}")


# ──────────────────────────────────────────────────
# Session Log Writer
# ──────────────────────────────────────────────────


class SessionLogWriter:
    """Top-level session log manager.

    Creates the session directory structure, manages session.meta.json,
    the PA-user conversation log, and task log writers. This is the
    primary entry point for the PA to write session logs.

    Directory structure:
        .faith/sessions/sess-NNNN-YYYY-MM-DD/
        ├── session.meta.json
        ├── pa-user.log
        └── tasks/
            └── task-NNN-HHMMSS.mmm/
                ├── task.meta.json
                ├── ch-<channel-name>.log
                └── pa-<agent-name>.log

    Attributes:
        sessions_dir: Path to .faith/sessions/.
        session_dir: Path to this session's directory.
        meta: SessionMeta for this session.
        pa_user_log: PAUserLogWriter for the PA↔user conversation.
    """

    def __init__(
        self,
        sessions_dir: Path,
        session_id: str,
        privacy_profile: str = "internal",
    ):
        """Initialise a new session log.

        Creates the session directory and writes initial session.meta.json.

        Args:
            sessions_dir: Path to the .faith/sessions/ directory.
            session_id: Session identifier (e.g. "sess-0042").
            privacy_profile: Privacy profile for this session.
        """
        self.sessions_dir = Path(sessions_dir)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dir_name = f"{session_id}-{date_str}"
        self.session_dir = self.sessions_dir / dir_name
        self._task_writers: dict[str, TaskLogWriter] = {}
        self._task_counter: int = 0

        # Create directory structure
        self.session_dir.mkdir(parents=True, exist_ok=True)
        (self.session_dir / "tasks").mkdir(exist_ok=True)

        # Initialise session metadata
        self.meta = SessionMeta(
            session_id=session_id,
            privacy_profile=privacy_profile,
        )
        self._write_meta()

        # Initialise PA-user log
        self.pa_user_log = PAUserLogWriter(
            log_path=self.session_dir / "pa-user.log",
            session_id=session_id,
        )

        logger.info(
            f"SessionLogWriter initialised: {self.session_dir} "
            f"(privacy={privacy_profile})"
        )

    def _write_meta(self) -> None:
        """Write or update session.meta.json."""
        meta_path = self.session_dir / "session.meta.json"
        meta_path.write_text(self.meta.to_json(), encoding="utf-8")

    def create_task(
        self,
        goal: str,
        task_id: Optional[str] = None,
    ) -> TaskLogWriter:
        """Create a new task within this session.

        Generates a task ID with millisecond precision if not provided,
        creates the task directory, and returns a TaskLogWriter.

        Args:
            goal: Human-readable description of the task goal.
            task_id: Optional explicit task ID. If not provided, one is
                generated as task-NNN-HHMMSS.mmm.

        Returns:
            TaskLogWriter for the new task.
        """
        self._task_counter += 1

        if task_id is None:
            now = datetime.now(timezone.utc)
            time_part = now.strftime("%H%M%S")
            ms_part = f"{now.microsecond // 1000:03d}"
            task_id = f"task-{self._task_counter:03d}-{time_part}.{ms_part}"

        task_dir = self.session_dir / "tasks" / task_id

        meta = TaskMeta(
            task_id=task_id,
            session_id=self.meta.session_id,
            goal=goal,
        )

        writer = TaskLogWriter(task_dir=task_dir, meta=meta)
        self._task_writers[task_id] = writer

        # Update session metadata
        self.meta.task_count += 1
        self._write_meta()

        logger.info(f"Task created: {task_id} — {goal}")
        return writer

    def get_task_writer(self, task_id: str) -> Optional[TaskLogWriter]:
        """Retrieve an existing task writer by task ID.

        Args:
            task_id: The task identifier.

        Returns:
            TaskLogWriter if found, else None.
        """
        return self._task_writers.get(task_id)

    def add_active_agent(self, agent_name: str) -> None:
        """Record an agent as active in this session.

        Args:
            agent_name: Agent short name.
        """
        if agent_name not in self.meta.agents_active:
            self.meta.agents_active.append(agent_name)
            self._write_meta()

    def update_tokens(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: float = 0.0,
    ) -> None:
        """Accumulate token usage for this session.

        Args:
            input_tokens: Additional input tokens to add.
            output_tokens: Additional output tokens to add.
            estimated_cost: Additional estimated cost to add.
        """
        self.meta.total_input_tokens += input_tokens
        self.meta.total_output_tokens += output_tokens
        self.meta.total_estimated_cost += estimated_cost
        self._write_meta()

    def complete(self) -> None:
        """Mark the session as complete and write final metadata."""
        self.meta.status = "complete"
        self.meta.ended = _now_iso()
        self._write_meta()
        logger.info(f"Session completed: {self.meta.session_id}")

    def interrupt(self) -> None:
        """Mark the session as interrupted (e.g. crash recovery).

        Used by the PA on restart to mark sessions that were active
        at crash time (FRS Section 7.4).
        """
        self.meta.status = "interrupted"
        self.meta.ended = _now_iso()
        self._write_meta()
        logger.info(f"Session interrupted: {self.meta.session_id}")

    @staticmethod
    def find_active_sessions(sessions_dir: Path) -> list[SessionMeta]:
        """Find all sessions with status != 'complete'.

        Used by the PA on restart to detect sessions that were active
        at crash time (FRS Section 7.4).

        Args:
            sessions_dir: Path to .faith/sessions/.

        Returns:
            List of SessionMeta for non-complete sessions.
        """
        active = []
        if not sessions_dir.exists():
            return active

        for session_path in sorted(sessions_dir.iterdir()):
            meta_file = session_path / "session.meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = SessionMeta.from_file(meta_file)
                if meta.status != "complete":
                    active.append(meta)
            except Exception as e:
                logger.warning(
                    f"Skipping malformed session.meta.json: {meta_file}: {e}"
                )
        return active

    @staticmethod
    def from_system_config(
        faith_dir: Path,
        system_config: dict[str, Any],
        session_id: str,
    ) -> "SessionLogWriter":
        """Factory method to create a SessionLogWriter from .faith/system.yaml.

        Reads the `session_logs` section of system.yaml for configuration.

        Expected system.yaml structure:
        ```yaml
        session_logs:
          privacy_profile: internal
        ```

        Args:
            faith_dir: Path to the .faith/ directory.
            system_config: Parsed .faith/system.yaml as a dict.
            session_id: Session identifier.

        Returns:
            Configured SessionLogWriter instance.
        """
        sessions_dir = faith_dir / "sessions"
        log_config = system_config.get("session_logs", {})
        privacy_profile = log_config.get("privacy_profile", "internal")

        return SessionLogWriter(
            sessions_dir=sessions_dir,
            session_id=session_id,
            privacy_profile=privacy_profile,
        )


# ──────────────────────────────────────────────────
# Agent Cross-Reference Index Writer
# ──────────────────────────────────────────────────


class AgentIndexWriter:
    """Writes per-agent session cross-reference indices.

    Each agent gets an index file at .faith/agents/<name>/sessions.index.md
    that lists sessions and tasks the agent participated in with relative
    links to the session log directories. This provides fast per-agent
    lookup without duplicating any log content.

    Index format:
        # Session Index: software-developer
        # Updated: 2026-03-23T18:45:00Z

        ## sess-0042 (2026-03-23)
        - task-001-143201.456 — Implement JWT auth module
          Channels: ch-auth-feature
          [Session logs](../../sessions/sess-0042-2026-03-23/)

    Attributes:
        agents_dir: Path to .faith/agents/.
    """

    def __init__(self, agents_dir: Path):
        self.agents_dir = Path(agents_dir)

    def update_index(
        self,
        agent_name: str,
        session_id: str,
        session_date: str,
        task_id: str,
        task_goal: str,
        channels: list[str],
    ) -> None:
        """Add or update an entry in the agent's session index.

        Args:
            agent_name: Agent short name (e.g. "software-developer").
            session_id: Session identifier (e.g. "sess-0042").
            session_date: Session date string (e.g. "2026-03-23").
            task_id: Task identifier.
            task_goal: Human-readable task goal.
            channels: List of channel names the agent participated in.
        """
        agent_dir = self.agents_dir / agent_name
        if not agent_dir.exists():
            logger.warning(
                f"Agent directory does not exist: {agent_dir} — "
                f"skipping index update for {agent_name}"
            )
            return

        index_path = agent_dir / "sessions.index.md"
        session_dir_name = f"{session_id}-{session_date}"
        rel_link = f"../../sessions/{session_dir_name}/"

        # Build the new entry
        channels_str = ", ".join(channels) if channels else "none"
        entry_lines = [
            f"- {task_id} — {task_goal}",
            f"  Channels: {channels_str}",
            f"  [Session logs]({rel_link})",
        ]
        entry_block = "\n".join(entry_lines)

        session_header = f"## {session_id} ({session_date})"

        if index_path.exists():
            content = index_path.read_text(encoding="utf-8")

            # Update the "Updated" timestamp in the header
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("# Updated:"):
                    lines[i] = f"# Updated: {_now_iso()}"
                    break
            content = "\n".join(lines)

            # Check if we already have an entry for this task in this session
            if task_id in content:
                logger.debug(
                    f"Task {task_id} already indexed for {agent_name} — skipping"
                )
                return

            # Append under the existing session header, or create a new one
            if session_header in content:
                # Insert the entry after the session header
                idx = content.index(session_header) + len(session_header)
                content = content[:idx] + "\n" + entry_block + content[idx:]
            else:
                # Append a new session section
                content = content.rstrip() + f"\n\n{session_header}\n{entry_block}\n"

            index_path.write_text(content, encoding="utf-8")
        else:
            # Create a new index file
            content = (
                f"# Session Index: {agent_name}\n"
                f"# Updated: {_now_iso()}\n"
                f"\n"
                f"{session_header}\n"
                f"{entry_block}\n"
            )
            index_path.write_text(content, encoding="utf-8")

        logger.debug(
            f"Agent index updated: {agent_name} — "
            f"{session_id}/{task_id}"
        )
```

### 3. `tests/test_session_log.py`

```python
"""Tests for the FAITH session & task log writer.

Covers SessionMeta/TaskMeta serialisation, SessionLogWriter lifecycle,
TaskLogWriter channel management, ChannelLogWriter markdown output,
PAUserLogWriter, AgentIndexWriter, the one-log-per-channel invariant,
active session discovery, and factory method from system config.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from faith.logging.session_log import (
    AgentIndexWriter,
    ChannelLogWriter,
    PAUserLogWriter,
    SessionLogWriter,
    SessionMeta,
    TaskLogWriter,
    TaskMeta,
    _now_iso,
)


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def faith_dir(tmp_path):
    """Create a temporary .faith directory structure."""
    d = tmp_path / ".faith"
    d.mkdir()
    (d / "sessions").mkdir()
    (d / "agents").mkdir()
    return d


@pytest.fixture
def sessions_dir(faith_dir):
    """Return the .faith/sessions/ directory."""
    return faith_dir / "sessions"


@pytest.fixture
def agents_dir(faith_dir):
    """Return the .faith/agents/ directory."""
    return faith_dir / "agents"


@pytest.fixture
def session_writer(sessions_dir):
    """Create a SessionLogWriter for testing."""
    return SessionLogWriter(
        sessions_dir=sessions_dir,
        session_id="sess-0042",
        privacy_profile="internal",
    )


@pytest.fixture
def sample_session_meta():
    """A sample SessionMeta for testing."""
    return SessionMeta(
        session_id="sess-0042",
        started="2026-03-23T14:30:00Z",
        ended="2026-03-23T18:45:00Z",
        status="complete",
        privacy_profile="internal",
        task_count=3,
        agents_active=["software-developer", "qa-engineer", "security-expert"],
        total_input_tokens=48200,
        total_output_tokens=12400,
        total_estimated_cost=0.87,
    )


@pytest.fixture
def sample_task_meta():
    """A sample TaskMeta for testing."""
    return TaskMeta(
        task_id="task-001-143201.456",
        session_id="sess-0042",
        goal="Implement JWT auth module",
        started="2026-03-23T14:32:00Z",
        status="active",
        agents=["software-developer", "qa-engineer"],
        channels=["ch-auth-feature"],
    )


# ──────────────────────────────────────────────────
# SessionMeta serialisation tests
# ──────────────────────────────────────────────────


def test_session_meta_to_json(sample_session_meta):
    """SessionMeta serialises to valid JSON with all FRS fields."""
    data = json.loads(sample_session_meta.to_json())
    assert data["session_id"] == "sess-0042"
    assert data["started"] == "2026-03-23T14:30:00Z"
    assert data["ended"] == "2026-03-23T18:45:00Z"
    assert data["privacy_profile"] == "internal"
    assert data["task_count"] == 3
    assert "software-developer" in data["agents_active"]
    assert data["total_input_tokens"] == 48200
    assert data["total_output_tokens"] == 12400
    assert data["total_estimated_cost"] == 0.87


def test_session_meta_round_trip(sample_session_meta):
    """SessionMeta round-trips through JSON correctly."""
    json_str = sample_session_meta.to_json()
    restored = SessionMeta.from_json(json_str)
    assert restored.session_id == sample_session_meta.session_id
    assert restored.task_count == sample_session_meta.task_count
    assert restored.agents_active == sample_session_meta.agents_active
    assert restored.total_estimated_cost == sample_session_meta.total_estimated_cost


def test_session_meta_excludes_none_ended():
    """Active session excludes None ended field."""
    meta = SessionMeta(session_id="sess-0001")
    data = json.loads(meta.to_json())
    assert "ended" not in data


def test_session_meta_default_values():
    """SessionMeta provides correct defaults."""
    meta = SessionMeta(session_id="sess-0001")
    assert meta.status == "active"
    assert meta.privacy_profile == "internal"
    assert meta.task_count == 0
    assert meta.agents_active == []
    assert meta.total_input_tokens == 0
    assert meta.total_output_tokens == 0
    assert meta.total_estimated_cost == 0.0
    assert meta.started.endswith("Z")


# ──────────────────────────────────────────────────
# TaskMeta serialisation tests
# ──────────────────────────────────────────────────


def test_task_meta_to_json(sample_task_meta):
    """TaskMeta serialises to valid JSON with all expected fields."""
    data = json.loads(sample_task_meta.to_json())
    assert data["task_id"] == "task-001-143201.456"
    assert data["session_id"] == "sess-0042"
    assert data["goal"] == "Implement JWT auth module"
    assert "software-developer" in data["agents"]
    assert "ch-auth-feature" in data["channels"]


def test_task_meta_round_trip(sample_task_meta):
    """TaskMeta round-trips through JSON correctly."""
    json_str = sample_task_meta.to_json()
    restored = TaskMeta.from_json(json_str)
    assert restored.task_id == sample_task_meta.task_id
    assert restored.goal == sample_task_meta.goal
    assert restored.agents == sample_task_meta.agents


def test_task_meta_default_values():
    """TaskMeta provides correct defaults."""
    meta = TaskMeta(task_id="task-001-120000.000", session_id="sess-0001")
    assert meta.status == "active"
    assert meta.goal == ""
    assert meta.agents == []
    assert meta.channels == []
    assert meta.input_tokens == 0
    assert meta.output_tokens == 0
    assert meta.estimated_cost == 0.0


# ──────────────────────────────────────────────────
# SessionLogWriter tests
# ──────────────────────────────────────────────────


def test_session_creates_directory(session_writer):
    """SessionLogWriter creates the session directory."""
    assert session_writer.session_dir.exists()
    assert (session_writer.session_dir / "tasks").exists()


def test_session_writes_meta(session_writer):
    """SessionLogWriter writes session.meta.json on init."""
    meta_path = session_writer.session_dir / "session.meta.json"
    assert meta_path.exists()
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-0042"
    assert data["status"] == "active"
    assert data["privacy_profile"] == "internal"


def test_session_dir_includes_date(session_writer):
    """Session directory name includes the date."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert session_writer.session_dir.name == f"sess-0042-{date_str}"


def test_session_create_task(session_writer):
    """create_task returns a TaskLogWriter and updates session meta."""
    task = session_writer.create_task(
        goal="Implement JWT auth module",
        task_id="task-001-143201.456",
    )
    assert isinstance(task, TaskLogWriter)
    assert task.meta.goal == "Implement JWT auth module"
    assert session_writer.meta.task_count == 1

    # task.meta.json should exist
    meta_path = task.task_dir / "task.meta.json"
    assert meta_path.exists()


def test_session_create_task_auto_id(session_writer):
    """create_task generates a task ID if not provided."""
    task = session_writer.create_task(goal="Auto ID task")
    assert task.meta.task_id.startswith("task-001-")
    assert "." in task.meta.task_id  # millisecond separator


def test_session_create_multiple_tasks(session_writer):
    """Multiple tasks get sequential counter prefixes."""
    t1 = session_writer.create_task(goal="First task")
    t2 = session_writer.create_task(goal="Second task")
    assert "task-001" in t1.meta.task_id
    assert "task-002" in t2.meta.task_id
    assert session_writer.meta.task_count == 2


def test_session_get_task_writer(session_writer):
    """get_task_writer retrieves an existing task writer."""
    task = session_writer.create_task(
        goal="Test task",
        task_id="task-001-120000.000",
    )
    retrieved = session_writer.get_task_writer("task-001-120000.000")
    assert retrieved is task


def test_session_get_task_writer_not_found(session_writer):
    """get_task_writer returns None for unknown task IDs."""
    assert session_writer.get_task_writer("task-999-000000.000") is None


def test_session_add_active_agent(session_writer):
    """add_active_agent tracks agents in session metadata."""
    session_writer.add_active_agent("software-developer")
    session_writer.add_active_agent("qa-engineer")
    session_writer.add_active_agent("software-developer")  # duplicate — ignored
    assert session_writer.meta.agents_active == [
        "software-developer",
        "qa-engineer",
    ]


def test_session_update_tokens(session_writer):
    """update_tokens accumulates token counts."""
    session_writer.update_tokens(input_tokens=1000, output_tokens=300, estimated_cost=0.15)
    session_writer.update_tokens(input_tokens=500, output_tokens=200, estimated_cost=0.10)
    assert session_writer.meta.total_input_tokens == 1500
    assert session_writer.meta.total_output_tokens == 500
    assert abs(session_writer.meta.total_estimated_cost - 0.25) < 0.001


def test_session_complete(session_writer):
    """complete() sets status and ended timestamp."""
    session_writer.complete()
    assert session_writer.meta.status == "complete"
    assert session_writer.meta.ended is not None
    assert session_writer.meta.ended.endswith("Z")

    # Verify persisted to file
    meta_path = session_writer.session_dir / "session.meta.json"
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    assert data["status"] == "complete"
    assert "ended" in data


def test_session_interrupt(session_writer):
    """interrupt() sets status to interrupted."""
    session_writer.interrupt()
    assert session_writer.meta.status == "interrupted"
    assert session_writer.meta.ended is not None


# ──────────────────────────────────────────────────
# TaskLogWriter tests
# ──────────────────────────────────────────────────


def test_task_creates_directory(session_writer):
    """TaskLogWriter creates the task directory."""
    task = session_writer.create_task(
        goal="Test task",
        task_id="task-001-120000.000",
    )
    assert task.task_dir.exists()


def test_task_writes_meta(session_writer):
    """TaskLogWriter writes task.meta.json with correct fields."""
    task = session_writer.create_task(
        goal="Implement JWT auth module",
        task_id="task-001-143201.456",
    )
    meta_path = task.task_dir / "task.meta.json"
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    assert data["task_id"] == "task-001-143201.456"
    assert data["session_id"] == "sess-0042"
    assert data["goal"] == "Implement JWT auth module"
    assert data["status"] == "active"


def test_task_add_agent(session_writer):
    """add_agent records participating agents."""
    task = session_writer.create_task(goal="Test", task_id="task-001-120000.000")
    task.add_agent("software-developer")
    task.add_agent("qa-engineer")
    task.add_agent("software-developer")  # duplicate — ignored
    assert task.meta.agents == ["software-developer", "qa-engineer"]


def test_task_update_tokens(session_writer):
    """update_tokens accumulates task-level token counts."""
    task = session_writer.create_task(goal="Test", task_id="task-001-120000.000")
    task.update_tokens(input_tokens=500, output_tokens=100, estimated_cost=0.05)
    task.update_tokens(input_tokens=300, output_tokens=80, estimated_cost=0.03)
    assert task.meta.input_tokens == 800
    assert task.meta.output_tokens == 180


def test_task_complete(session_writer):
    """complete() sets status and ended timestamp."""
    task = session_writer.create_task(goal="Test", task_id="task-001-120000.000")
    task.complete()
    assert task.meta.status == "complete"
    assert task.meta.ended is not None

    data = json.loads(
        (task.task_dir / "task.meta.json").read_text(encoding="utf-8")
    )
    assert data["status"] == "complete"


def test_task_cancel(session_writer):
    """cancel() sets status to cancelled."""
    task = session_writer.create_task(goal="Test", task_id="task-001-120000.000")
    task.cancel()
    assert task.meta.status == "cancelled"


def test_task_get_channel_writer(session_writer):
    """get_channel_writer creates and returns a ChannelLogWriter."""
    task = session_writer.create_task(goal="Test", task_id="task-001-120000.000")
    writer = task.get_channel_writer("ch-auth-feature")
    assert isinstance(writer, ChannelLogWriter)
    assert writer.channel_name == "ch-auth-feature"


def test_task_one_log_per_channel(session_writer):
    """get_channel_writer returns the same writer for the same channel."""
    task = session_writer.create_task(goal="Test", task_id="task-001-120000.000")
    w1 = task.get_channel_writer("ch-auth-feature")
    w2 = task.get_channel_writer("ch-auth-feature")
    assert w1 is w2  # Same object — no duplication


def test_task_channel_tracked_in_meta(session_writer):
    """Channels are recorded in task.meta.json."""
    task = session_writer.create_task(goal="Test", task_id="task-001-120000.000")
    task.get_channel_writer("ch-auth-feature")
    task.get_channel_writer("ch-db-setup")
    assert "ch-auth-feature" in task.meta.channels
    assert "ch-db-setup" in task.meta.channels


def test_task_pa_agent_writer(session_writer):
    """get_pa_agent_writer creates PA↔agent direct log writers."""
    task = session_writer.create_task(goal="Test", task_id="task-001-120000.000")
    writer = task.get_pa_agent_writer("software-developer")
    assert writer.log_path.name == "pa-software-developer.log"


# ──────────────────────────────────────────────────
# ChannelLogWriter tests
# ──────────────────────────────────────────────────


def test_channel_log_header(tmp_path):
    """ChannelLogWriter writes a markdown header on first message."""
    log_path = tmp_path / "ch-test.log"
    writer = ChannelLogWriter(
        log_path=log_path,
        channel_name="ch-test",
        task_id="task-001-143201.456",
        task_goal="Implement JWT auth module",
    )
    writer.write_message(
        timestamp="14:32:01",
        sender="dev",
        recipient="qa",
        msg_type="review_request",
        summary="auth module done",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "# Channel: ch-test" in content
    assert "# Task: task-001-143201.456 — Implement JWT auth module" in content
    assert "# Started:" in content


def test_channel_log_message_format(tmp_path):
    """Channel log messages use compact protocol markdown format."""
    log_path = tmp_path / "ch-test.log"
    writer = ChannelLogWriter(
        log_path=log_path,
        channel_name="ch-test",
        task_id="task-001",
    )
    writer.write_message(
        timestamp="14:32:01",
        sender="software-developer",
        recipient="qa-engineer",
        msg_type="review_request",
        summary="auth module done, 3 endpoints, JWT httponly cookies",
        status="complete",
        needs="test coverage for token expiry edge case",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "**[14:32:01] software-developer → qa-engineer**" in content
    assert "type: review_request | status: complete" in content
    assert 'summary: "auth module done, 3 endpoints, JWT httponly cookies"' in content
    assert 'needs: "test coverage for token expiry edge case"' in content


def test_channel_log_message_with_files(tmp_path):
    """Channel log includes file references when provided."""
    log_path = tmp_path / "ch-test.log"
    writer = ChannelLogWriter(
        log_path=log_path,
        channel_name="ch-test",
        task_id="task-001",
    )
    writer.write_message(
        timestamp="14:32:01",
        sender="dev",
        recipient="qa",
        msg_type="review_request",
        summary="done",
        files=["auth.py", "auth_test.py"],
    )
    content = log_path.read_text(encoding="utf-8")
    assert "files: [auth.py, auth_test.py]" in content


def test_channel_log_message_with_context_ref(tmp_path):
    """Channel log includes context_ref when provided."""
    log_path = tmp_path / "ch-test.log"
    writer = ChannelLogWriter(
        log_path=log_path,
        channel_name="ch-test",
        task_id="task-001",
    )
    writer.write_message(
        timestamp="14:32:01",
        sender="dev",
        recipient="qa",
        msg_type="status_update",
        summary="see previous",
        context_ref="ch-test/msg-42-46",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "context_ref: ch-test/msg-42-46" in content


def test_channel_log_multiple_messages(tmp_path):
    """Multiple messages append correctly with separators."""
    log_path = tmp_path / "ch-test.log"
    writer = ChannelLogWriter(
        log_path=log_path,
        channel_name="ch-test",
        task_id="task-001",
    )
    writer.write_message(
        timestamp="14:32:01",
        sender="dev",
        recipient="qa",
        msg_type="review_request",
        summary="first message",
    )
    writer.write_message(
        timestamp="14:45:22",
        sender="qa",
        recipient="dev",
        msg_type="feedback",
        summary="second message",
        status="in_progress",
    )
    content = log_path.read_text(encoding="utf-8")
    assert content.count("---") >= 2
    assert "first message" in content
    assert "second message" in content


def test_channel_log_priority_field(tmp_path):
    """Non-normal priority is included in the fields line."""
    log_path = tmp_path / "ch-test.log"
    writer = ChannelLogWriter(
        log_path=log_path,
        channel_name="ch-test",
        task_id="task-001",
    )
    writer.write_message(
        timestamp="14:32:01",
        sender="dev",
        recipient="pa",
        msg_type="escalate",
        summary="blocked",
        priority="critical",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "priority: critical" in content


def test_channel_log_normal_priority_omitted(tmp_path):
    """Normal priority is omitted from the fields line."""
    log_path = tmp_path / "ch-test.log"
    writer = ChannelLogWriter(
        log_path=log_path,
        channel_name="ch-test",
        task_id="task-001",
    )
    writer.write_message(
        timestamp="14:32:01",
        sender="dev",
        recipient="qa",
        msg_type="status_update",
        summary="ok",
        priority="normal",
    )
    content = log_path.read_text(encoding="utf-8")
    assert "priority" not in content


# ──────────────────────────────────────────────────
# PAUserLogWriter tests
# ──────────────────────────────────────────────────


def test_pa_user_log_header(tmp_path):
    """PAUserLogWriter writes a header on first message."""
    log_path = tmp_path / "pa-user.log"
    writer = PAUserLogWriter(log_path=log_path, session_id="sess-0042")
    writer.write_user_message("14:30:00", "Hello PA")
    content = log_path.read_text(encoding="utf-8")
    assert "# PA ↔ User Conversation" in content
    assert "# Session: sess-0042" in content


def test_pa_user_log_messages(tmp_path):
    """User and PA messages are formatted correctly."""
    log_path = tmp_path / "pa-user.log"
    writer = PAUserLogWriter(log_path=log_path, session_id="sess-0042")
    writer.write_user_message("14:30:00", "Implement JWT auth")
    writer.write_pa_message("14:30:05", "I'll set up the task now.")
    content = log_path.read_text(encoding="utf-8")
    assert "**[14:30:00] User:**" in content
    assert "Implement JWT auth" in content
    assert "**[14:30:05] PA:**" in content
    assert "I'll set up the task now." in content


def test_pa_user_log_at_session_level(session_writer):
    """pa-user.log is created at session directory level."""
    session_writer.pa_user_log.write_user_message("14:30:00", "Hello")
    log_path = session_writer.session_dir / "pa-user.log"
    assert log_path.exists()


# ──────────────────────────────────────────────────
# AgentIndexWriter tests
# ──────────────────────────────────────────────────


def test_agent_index_creates_file(agents_dir):
    """AgentIndexWriter creates sessions.index.md for an agent."""
    (agents_dir / "software-developer").mkdir()
    writer = AgentIndexWriter(agents_dir=agents_dir)
    writer.update_index(
        agent_name="software-developer",
        session_id="sess-0042",
        session_date="2026-03-23",
        task_id="task-001-143201.456",
        task_goal="Implement JWT auth module",
        channels=["ch-auth-feature"],
    )
    index_path = agents_dir / "software-developer" / "sessions.index.md"
    assert index_path.exists()
    content = index_path.read_text(encoding="utf-8")
    assert "# Session Index: software-developer" in content
    assert "## sess-0042 (2026-03-23)" in content
    assert "task-001-143201.456 — Implement JWT auth module" in content
    assert "ch-auth-feature" in content
    assert "../../sessions/sess-0042-2026-03-23/" in content


def test_agent_index_appends_tasks(agents_dir):
    """Multiple tasks in the same session append correctly."""
    (agents_dir / "software-developer").mkdir()
    writer = AgentIndexWriter(agents_dir=agents_dir)
    writer.update_index(
        agent_name="software-developer",
        session_id="sess-0042",
        session_date="2026-03-23",
        task_id="task-001-143201.456",
        task_goal="First task",
        channels=["ch-first"],
    )
    writer.update_index(
        agent_name="software-developer",
        session_id="sess-0042",
        session_date="2026-03-23",
        task_id="task-002-150000.000",
        task_goal="Second task",
        channels=["ch-second"],
    )
    content = (
        agents_dir / "software-developer" / "sessions.index.md"
    ).read_text(encoding="utf-8")
    assert "task-001-143201.456" in content
    assert "task-002-150000.000" in content
    # Only one session header
    assert content.count("## sess-0042") == 1


def test_agent_index_multiple_sessions(agents_dir):
    """Tasks across different sessions create separate sections."""
    (agents_dir / "qa-engineer").mkdir()
    writer = AgentIndexWriter(agents_dir=agents_dir)
    writer.update_index(
        agent_name="qa-engineer",
        session_id="sess-0042",
        session_date="2026-03-23",
        task_id="task-001-143201.456",
        task_goal="First session task",
        channels=["ch-a"],
    )
    writer.update_index(
        agent_name="qa-engineer",
        session_id="sess-0043",
        session_date="2026-03-24",
        task_id="task-001-100000.000",
        task_goal="Second session task",
        channels=["ch-b"],
    )
    content = (
        agents_dir / "qa-engineer" / "sessions.index.md"
    ).read_text(encoding="utf-8")
    assert "## sess-0042 (2026-03-23)" in content
    assert "## sess-0043 (2026-03-24)" in content


def test_agent_index_no_duplicate_tasks(agents_dir):
    """Duplicate task entries are not written."""
    (agents_dir / "software-developer").mkdir()
    writer = AgentIndexWriter(agents_dir=agents_dir)
    writer.update_index(
        agent_name="software-developer",
        session_id="sess-0042",
        session_date="2026-03-23",
        task_id="task-001-143201.456",
        task_goal="Same task",
        channels=["ch-a"],
    )
    writer.update_index(
        agent_name="software-developer",
        session_id="sess-0042",
        session_date="2026-03-23",
        task_id="task-001-143201.456",
        task_goal="Same task",
        channels=["ch-a"],
    )
    content = (
        agents_dir / "software-developer" / "sessions.index.md"
    ).read_text(encoding="utf-8")
    assert content.count("task-001-143201.456") == 1


def test_agent_index_missing_agent_dir(agents_dir):
    """Missing agent directory is handled gracefully (warning, no crash)."""
    writer = AgentIndexWriter(agents_dir=agents_dir)
    # Should not raise
    writer.update_index(
        agent_name="nonexistent-agent",
        session_id="sess-0042",
        session_date="2026-03-23",
        task_id="task-001-143201.456",
        task_goal="Test",
        channels=[],
    )
    assert not (agents_dir / "nonexistent-agent" / "sessions.index.md").exists()


# ──────────────────────────────────────────────────
# Active session discovery tests
# ──────────────────────────────────────────────────


def test_find_active_sessions(sessions_dir):
    """find_active_sessions returns non-complete sessions."""
    # Create an active session
    s1 = SessionLogWriter(sessions_dir, "sess-0042")

    # Create a completed session
    s2 = SessionLogWriter(sessions_dir, "sess-0043")
    s2.complete()

    # Create an interrupted session
    s3 = SessionLogWriter(sessions_dir, "sess-0044")
    s3.interrupt()

    active = SessionLogWriter.find_active_sessions(sessions_dir)
    active_ids = [m.session_id for m in active]
    assert "sess-0042" in active_ids  # active
    assert "sess-0043" not in active_ids  # complete
    assert "sess-0044" in active_ids  # interrupted


def test_find_active_sessions_empty(tmp_path):
    """find_active_sessions returns empty list for non-existent dir."""
    active = SessionLogWriter.find_active_sessions(tmp_path / "nonexistent")
    assert active == []


# ──────────────────────────────────────────────────
# Factory method tests
# ──────────────────────────────────────────────────


def test_from_system_config_defaults(faith_dir):
    """Factory method uses defaults when session_logs config is absent."""
    writer = SessionLogWriter.from_system_config(faith_dir, {}, "sess-0042")
    assert writer.meta.privacy_profile == "internal"
    assert writer.session_dir.exists()


def test_from_system_config_custom(faith_dir):
    """Factory method reads privacy_profile from system config."""
    config = {"session_logs": {"privacy_profile": "confidential"}}
    writer = SessionLogWriter.from_system_config(faith_dir, config, "sess-0042")
    assert writer.meta.privacy_profile == "confidential"


# ──────────────────────────────────────────────────
# End-to-end integration test
# ──────────────────────────────────────────────────


def test_full_session_lifecycle(faith_dir):
    """End-to-end test: create session, task, channel, write messages, complete."""
    sessions_dir = faith_dir / "sessions"
    agents_dir = faith_dir / "agents"
    (agents_dir / "software-developer").mkdir()
    (agents_dir / "qa-engineer").mkdir()

    # Create session
    session = SessionLogWriter(sessions_dir, "sess-0042")
    session.add_active_agent("software-developer")
    session.add_active_agent("qa-engineer")

    # Log PA-user conversation
    session.pa_user_log.write_user_message("14:30:00", "Implement JWT auth")
    session.pa_user_log.write_pa_message(
        "14:30:05", "Creating task with software-developer and qa-engineer."
    )

    # Create task
    task = session.create_task(
        goal="Implement JWT auth module",
        task_id="task-001-143201.456",
    )
    task.add_agent("software-developer")
    task.add_agent("qa-engineer")

    # Write channel messages
    ch = task.get_channel_writer("ch-auth-feature")
    ch.write_message(
        timestamp="14:32:01",
        sender="software-developer",
        recipient="qa-engineer",
        msg_type="review_request",
        summary="auth module done, 3 endpoints, JWT httponly cookies",
        status="complete",
        needs="test coverage for token expiry edge case",
        files=["auth.py", "auth_test.py"],
    )
    ch.write_message(
        timestamp="14:45:22",
        sender="qa-engineer",
        recipient="software-developer",
        msg_type="feedback",
        summary="token expiry tests written, one edge case found in refresh logic",
        status="in_progress",
    )

    # Update tokens
    task.update_tokens(input_tokens=1240, output_tokens=380, estimated_cost=0.0)
    session.update_tokens(input_tokens=1240, output_tokens=380, estimated_cost=0.0)

    # Complete task and session
    task.complete()
    session.complete()

    # Update agent indices
    index_writer = AgentIndexWriter(agents_dir)
    for agent in ["software-developer", "qa-engineer"]:
        index_writer.update_index(
            agent_name=agent,
            session_id="sess-0042",
            session_date="2026-03-23",
            task_id="task-001-143201.456",
            task_goal="Implement JWT auth module",
            channels=["ch-auth-feature"],
        )

    # Verify everything was written
    assert (session.session_dir / "session.meta.json").exists()
    assert (session.session_dir / "pa-user.log").exists()
    assert (task.task_dir / "task.meta.json").exists()
    assert (task.task_dir / "ch-auth-feature.log").exists()

    session_data = json.loads(
        (session.session_dir / "session.meta.json").read_text(encoding="utf-8")
    )
    assert session_data["status"] == "complete"
    assert session_data["task_count"] == 1
    assert session_data["total_input_tokens"] == 1240

    task_data = json.loads(
        (task.task_dir / "task.meta.json").read_text(encoding="utf-8")
    )
    assert task_data["status"] == "complete"
    assert "ch-auth-feature" in task_data["channels"]
    assert "software-developer" in task_data["agents"]

    ch_content = (task.task_dir / "ch-auth-feature.log").read_text(encoding="utf-8")
    assert "software-developer → qa-engineer" in ch_content
    assert "qa-engineer → software-developer" in ch_content

    for agent in ["software-developer", "qa-engineer"]:
        idx = (agents_dir / agent / "sessions.index.md").read_text(encoding="utf-8")
        assert "sess-0042" in idx
        assert "task-001-143201.456" in idx


# ──────────────────────────────────────────────────
# SessionMeta / TaskMeta from_file tests
# ──────────────────────────────────────────────────


def test_session_meta_from_file(tmp_path):
    """SessionMeta.from_file loads from a JSON file."""
    meta = SessionMeta(session_id="sess-0099", status="active")
    path = tmp_path / "session.meta.json"
    path.write_text(meta.to_json(), encoding="utf-8")
    loaded = SessionMeta.from_file(path)
    assert loaded.session_id == "sess-0099"


def test_task_meta_from_file(tmp_path):
    """TaskMeta.from_file loads from a JSON file."""
    meta = TaskMeta(
        task_id="task-001-120000.000",
        session_id="sess-0099",
        goal="Test loading",
    )
    path = tmp_path / "task.meta.json"
    path.write_text(meta.to_json(), encoding="utf-8")
    loaded = TaskMeta.from_file(path)
    assert loaded.task_id == "task-001-120000.000"
    assert loaded.goal == "Test loading"
```

---

## Integration Points

The SessionLogWriter integrates with the PA's session/task management (FAITH-015) and the event system (FAITH-008):

```python
# PA startup — create SessionLogWriter from .faith/system.yaml:

import yaml
from pathlib import Path
from faith.logging.session_log import SessionLogWriter, AgentIndexWriter

faith_dir = Path(".faith")
system_yaml = faith_dir / "system.yaml"
system_config = yaml.safe_load(system_yaml.read_text()) if system_yaml.exists() else {}

# Generate session ID (PA tracks the counter)
session_id = "sess-0042"

session_log = SessionLogWriter.from_system_config(
    faith_dir=faith_dir,
    system_config=system_config,
    session_id=session_id,
)
```

```python
# PA task creation — wire into FAITH-015 session/task management:

from faith.logging.session_log import SessionLogWriter, AgentIndexWriter

# When PA creates a new task:
task_writer = session_log.create_task(
    goal="Implement JWT auth module",
    task_id="task-001-143201.456",
)
task_writer.add_agent("software-developer")
task_writer.add_agent("qa-engineer")

# When PA creates a channel for the task:
ch_writer = task_writer.get_channel_writer("ch-auth-feature")
```

```python
# PA message handling — log compact protocol messages to channels:

from faith.protocol.compact import CompactMessage  # FAITH-007

def log_channel_message(msg: CompactMessage, task_writer):
    """Called by the PA when relaying a compact protocol message."""
    ch_writer = task_writer.get_channel_writer(msg.channel)
    ch_writer.write_message(
        timestamp=msg.ts_short,  # e.g. "14:32:01"
        sender=msg.sender,
        recipient=msg.to,
        msg_type=msg.type,
        summary=msg.summary,
        status=msg.status,
        needs=msg.needs,
        files=msg.files,
        context_ref=msg.context_ref,
        priority=msg.priority,
    )
```

```python
# PA session end — update agent indices and complete session:

from faith.logging.session_log import AgentIndexWriter

agent_index = AgentIndexWriter(agents_dir=Path(".faith/agents"))

# For each agent that participated in each task:
for task_id, task_writer in session_log._task_writers.items():
    for agent in task_writer.meta.agents:
        agent_index.update_index(
            agent_name=agent,
            session_id=session_log.meta.session_id,
            session_date="2026-03-23",
            task_id=task_id,
            task_goal=task_writer.meta.goal,
            channels=task_writer.meta.channels,
        )

session_log.complete()
```

```python
# PA crash recovery — find active sessions on restart (FRS Section 7.4):

from faith.logging.session_log import SessionLogWriter

active = SessionLogWriter.find_active_sessions(Path(".faith/sessions"))
for meta in active:
    print(f"Recovering session: {meta.session_id} (status={meta.status})")
```

---

## Acceptance Criteria

1. `SessionMeta` model includes all fields from FRS Section 8.4: `session_id`, `started`, `ended`, `privacy_profile`, `task_count`, `agents_active`, `total_input_tokens`, `total_output_tokens`, `total_estimated_cost`, plus `status` for crash recovery.
2. `TaskMeta` model includes all fields from FRS Section 8.4: `task_id`, `session_id`, `goal`, `started`, `ended`, `status`, `agents`, `channels`, `input_tokens`, `output_tokens`, `estimated_cost`.
3. `SessionMeta.to_json()` serialises to formatted JSON; `None` fields (e.g. `ended` when active) are excluded. Round-trip via `from_json()` is lossless.
4. `TaskMeta.to_json()` serialises to formatted JSON with the same exclusion and round-trip behaviour.
5. `SessionLogWriter` creates the session directory as `.faith/sessions/sess-NNNN-YYYY-MM-DD/` with a `tasks/` subdirectory.
6. `SessionLogWriter` writes `session.meta.json` on creation and updates it on every state change (task creation, token update, agent addition, completion).
7. `SessionLogWriter.create_task()` generates task IDs with millisecond-precision timing (`task-NNN-HHMMSS.mmm`), creates task directories, writes `task.meta.json`, and increments `task_count`.
8. `TaskLogWriter.get_channel_writer()` returns a `ChannelLogWriter` and enforces the one-log-per-channel-per-task invariant — repeated calls for the same channel return the same writer.
9. Channel log files use the FRS markdown format: header with channel name, task ID, goal, and start time; messages separated by `---` with `**[HH:MM:SS] sender → recipient**`, compact protocol fields, summary, needs, files, and context_ref.
10. `PAUserLogWriter` writes `pa-user.log` at the session directory level with user and PA messages in markdown format.
11. `AgentIndexWriter` writes `sessions.index.md` in each agent's `.faith/agents/<name>/` directory with relative links to session log directories. No content is duplicated — only references.
12. `AgentIndexWriter` does not create duplicate entries for the same task, appends new tasks under existing session headers, and creates new session sections for new sessions.
13. `SessionLogWriter.find_active_sessions()` returns `SessionMeta` for all sessions with `status != "complete"`, enabling crash recovery (FRS Section 7.4).
14. `SessionLogWriter.from_system_config()` reads `session_logs.privacy_profile` from `.faith/system.yaml`, defaulting to `"internal"`.
15. All tests in `tests/test_session_log.py` pass, covering metadata serialisation, session lifecycle, task management, channel log formatting, PA-user logging, agent index writing, active session discovery, factory methods, and end-to-end integration.

---

## Notes for Implementer

- **PA is the sole writer**: Only the PA writes session logs. Agent containers do not have write access to `.faith/sessions/`. The `SessionLogWriter` class does not enforce this — it is an architectural constraint applied via Docker volume mounts.
- **New architecture — `.faith/sessions/`**: Session logs live under `.faith/sessions/`, not `logs/sessions/`. This aligns with the new architecture where `.faith/` is the project-level configuration and state directory. The FRS Section 2.4 directory tree shows sessions under `.faith/sessions/`. Do not reference `logs/sessions/` from the old architecture.
- **No references to `agents.yaml` or `tools.yaml`**: Configuration is read from `.faith/system.yaml` (project settings), `.faith/agents/*/config.yaml` (agent definitions), and `.faith/tools/*.yaml` (tool configs). There are no monolithic `agents.yaml` or `tools.yaml` files.
- **One log per channel per task**: This is a hard invariant. The `TaskLogWriter.get_channel_writer()` method caches writers by channel name and returns the existing writer if one already exists. This prevents duplicate log files and ensures all messages for a channel are in a single chronological file.
- **Metadata files are rewritten, not appended**: `session.meta.json` and `task.meta.json` are overwritten on every update (using `write_text()`). This is acceptable because they are small JSON files and the PA is the sole writer. If future requirements demand atomicity, a write-to-temp-then-rename pattern can be added.
- **Agent index is append-only per session**: The `AgentIndexWriter` appends entries to `sessions.index.md`. It checks for duplicate task IDs to prevent re-indexing. The index uses relative links (`../../sessions/sess-XXXX-YYYY-MM-DD/`) so it works regardless of the absolute `.faith/` path.
- **Token tracking is split**: Token counts are tracked at both session and task level. The PA is responsible for calling `update_tokens()` on both the `SessionLogWriter` and the `TaskLogWriter` when token usage is reported. This task provides the accumulation logic; the actual token counting comes from FAITH-010 (base agent) and FAITH-013 (LLM API client).
- **Crash recovery**: `SessionLogWriter.find_active_sessions()` scans all session directories for `session.meta.json` files where `status != "complete"`. The PA calls this on restart (FRS Section 7.4) to identify sessions that need recovery. The `interrupt()` method marks a session as interrupted rather than complete.
- **Privacy profile**: The `privacy_profile` field controls what data is included in logs (defined elsewhere in the FRS). This task writes the field to metadata; enforcement of privacy filtering is handled by the PA's message routing logic.
- **Channel log files are named `ch-<name>.log`**: The channel name from the compact protocol (e.g. `ch-auth-feature`) becomes the log filename directly. PA-agent direct assignment logs use `pa-<agent-name>.log`.
- **Gitignored**: The entire `.faith/sessions/` directory is gitignored (FRS Section 2.4). Session logs are local runtime artefacts, not committed to the project repository.
