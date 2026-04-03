# FAITH-021 — Audit Log System

**Phase:** 5 — Security
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-008
**FRS Reference:** Section 5.5

---

## Objective

Implement the append-only audit log system for FAITH. The audit log is a core safety feature that records every tool operation, approval decision, container lifecycle event, and file history restoration. It is always on and cannot be disabled. The PA is the sole writer; all other containers mount the log directory read-only. Entries are written as JSON lines to `logs/audit.log`. Log rotation is configurable via `.faith/system.yaml` (default 90 days, archive not delete).

---

## Architecture

```
faith/security/
├── __init__.py
└── audit_log.py    ← AuditLogger class (this task)

tests/
└── test_audit_log.py  ← Tests (this task)
```

---

## Files to Create

### 1. `faith/security/__init__.py`

```python
"""FAITH Security — audit logging, approval system, and permission enforcement."""

from faith.security.audit_log import AuditLogger

__all__ = [
    "AuditLogger",
]
```

### 2. `faith/security/audit_log.py`

```python
"""FAITH Audit Log — append-only record of all agent actions.

The audit log is a core safety feature, not an optional observability tool.
It is always on and cannot be disabled. Every tool operation, approval
decision, container lifecycle event, and file history restoration is recorded
as a JSON line in logs/audit.log.

The PA is the sole writer. All agent containers mount logs/ read-only.

FRS Reference: Section 5.5
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("faith.security.audit")

# Default retention period in days
DEFAULT_RETENTION_DAYS = 90

# Counter for generating sequential audit IDs within a session.
# Resets on PA restart — combined with timestamp this is unique enough.
_audit_counter: int = 0


def _next_audit_id() -> str:
    """Generate the next sequential audit ID.

    Format: aud-XXXXX (zero-padded, wraps at 99999).
    Combined with the ISO timestamp, this provides a unique identifier
    across restarts.
    """
    global _audit_counter
    _audit_counter += 1
    if _audit_counter > 99999:
        _audit_counter = 1
    return f"aud-{_audit_counter:05d}"


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditEntry(BaseModel):
    """A single audit log entry.

    All fields match the FRS Section 5.5.2 specification. Entries are
    serialised as one JSON object per line (JSON lines format).

    Attributes:
        ts: ISO 8601 UTC timestamp.
        agent: The agent that initiated the action.
        tool: The tool used (e.g. "filesystem", "database", "python", "browser").
        action: The specific action performed (e.g. "read", "write", "query", "execute").
        target: The target of the action (e.g. file path, URL, query string).
        approval_tier: How the action was approved ("always_allow", "allow_once",
            "approve_session", "always_ask", "always_deny", or None for non-tool events).
        rule_matched: The regex or path rule that matched (if remembered/denied), or None.
        decision: The approval outcome ("approved", "denied").
        channel: The Redis channel associated with this action (if any).
        msg_id: The compact protocol message ID that triggered this action (if any).
        audit_id: Unique identifier for this audit entry.
    """

    ts: str = Field(default_factory=_now_iso)
    agent: str
    tool: str
    action: str
    target: str
    approval_tier: Optional[str] = None
    rule_matched: Optional[str] = None
    decision: str = "approved"
    channel: Optional[str] = None
    msg_id: Optional[int] = None
    audit_id: str = Field(default_factory=_next_audit_id)

    def to_json_line(self) -> str:
        """Serialise to a single JSON line (no trailing newline).

        Excludes None fields for compact output.
        """
        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_json_line(cls, line: str) -> "AuditEntry":
        """Deserialise from a JSON line string."""
        return cls.model_validate_json(line.strip())


class AuditLogger:
    """Append-only audit logger for the FAITH framework.

    The AuditLogger writes structured JSON lines to logs/audit.log.
    Only the PA instantiates this class — it is the sole writer.
    The log file is opened in append mode; entries are flushed
    immediately to minimise data loss on crash.

    Attributes:
        log_path: Absolute path to the audit log file.
        archive_dir: Directory for rotated log archives.
        retention_days: Number of days to retain active logs before archiving.
    """

    def __init__(
        self,
        logs_dir: Path,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        """Initialise the audit logger.

        Args:
            logs_dir: Path to the logs/ directory (e.g. /app/logs or ./logs).
            retention_days: Days to retain before archiving. Loaded from
                .faith/system.yaml by the PA; defaults to 90.
        """
        self.logs_dir = Path(logs_dir)
        self.log_path = self.logs_dir / "audit.log"
        self.archive_dir = self.logs_dir / "archive"
        self.retention_days = retention_days
        self._file = None

        # Ensure directories exist
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"AuditLogger initialised: path={self.log_path}, "
            f"retention={self.retention_days} days"
        )

    def open(self) -> None:
        """Open the audit log file for appending.

        Must be called before writing entries. The file is opened in
        append mode with UTF-8 encoding and line buffering for
        immediate flush on each write.
        """
        if self._file is not None:
            return
        self._file = open(self.log_path, "a", encoding="utf-8", buffering=1)
        logger.debug(f"Audit log file opened: {self.log_path}")

    def close(self) -> None:
        """Close the audit log file.

        Safe to call multiple times or when the file is not open.
        """
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception as e:
                logger.warning(f"Error closing audit log: {e}")
            finally:
                self._file = None
            logger.debug("Audit log file closed")

    def _ensure_open(self) -> None:
        """Open the file if it is not already open."""
        if self._file is None or self._file.closed:
            self._file = None
            self.open()

    def write(self, entry: AuditEntry) -> None:
        """Write a single audit entry to the log.

        The entry is serialised as a JSON line and flushed immediately.

        Args:
            entry: The AuditEntry to write.

        Raises:
            RuntimeError: If the log file cannot be written to.
        """
        self._ensure_open()
        try:
            line = entry.to_json_line()
            self._file.write(line + "\n")
            self._file.flush()
            logger.debug(f"Audit entry written: {entry.audit_id}")
        except Exception as e:
            logger.error(f"Failed to write audit entry: {e}")
            raise RuntimeError(f"Audit log write failed: {e}") from e

    def log_tool_operation(
        self,
        agent: str,
        tool: str,
        action: str,
        target: str,
        approval_tier: Optional[str] = None,
        rule_matched: Optional[str] = None,
        decision: str = "approved",
        channel: Optional[str] = None,
        msg_id: Optional[int] = None,
    ) -> AuditEntry:
        """Log a tool operation (filesystem, database, Python, browser).

        Convenience method that creates an AuditEntry and writes it.

        Args:
            agent: The agent that performed the operation.
            tool: Tool name (e.g. "filesystem", "database", "python", "browser").
            action: Specific action (e.g. "read", "write", "query", "execute").
            target: Target of the action (file path, URL, query, etc.).
            approval_tier: How the action was approved.
            rule_matched: Regex or path rule that matched (if a remembered
                or denied rule applied).
            decision: Approval outcome ("approved" or "denied").
            channel: Associated Redis channel.
            msg_id: Triggering message ID.

        Returns:
            The AuditEntry that was written.
        """
        entry = AuditEntry(
            agent=agent,
            tool=tool,
            action=action,
            target=target,
            approval_tier=approval_tier,
            rule_matched=rule_matched,
            decision=decision,
            channel=channel,
            msg_id=msg_id,
        )
        self.write(entry)
        return entry

    def log_approval_decision(
        self,
        agent: str,
        tool: str,
        action: str,
        target: str,
        approval_tier: str,
        rule_matched: Optional[str],
        decision: str,
        channel: Optional[str] = None,
        msg_id: Optional[int] = None,
    ) -> AuditEntry:
        """Log an approval decision (approved, denied, remembered).

        Args:
            agent: The agent whose action was evaluated.
            tool: The tool involved.
            action: The action that was evaluated.
            target: The target of the action.
            approval_tier: The tier that applied ("always_allow",
                "allow_once", "approve_session", "always_ask", "always_deny").
            rule_matched: The regex or path rule that matched (or None).
            decision: "approved" or "denied".
            channel: Associated Redis channel.
            msg_id: Triggering message ID.

        Returns:
            The AuditEntry that was written.
        """
        entry = AuditEntry(
            agent=agent,
            tool=tool,
            action=action,
            target=target,
            approval_tier=approval_tier,
            rule_matched=rule_matched,
            decision=decision,
            channel=channel,
            msg_id=msg_id,
        )
        self.write(entry)
        return entry

    def log_container_lifecycle(
        self,
        agent: str,
        action: str,
        target: str,
        channel: Optional[str] = None,
    ) -> AuditEntry:
        """Log a container lifecycle event (start, stop, restart).

        Args:
            agent: The agent or "pa" that triggered the event.
            action: "start", "stop", or "restart".
            target: Container identifier (e.g. "agent-software-developer").
            channel: Associated Redis channel (if any).

        Returns:
            The AuditEntry that was written.
        """
        entry = AuditEntry(
            agent=agent,
            tool="container",
            action=action,
            target=target,
            decision="approved",
            channel=channel,
        )
        self.write(entry)
        return entry

    def log_file_restoration(
        self,
        agent: str,
        target: str,
        channel: Optional[str] = None,
        msg_id: Optional[int] = None,
    ) -> AuditEntry:
        """Log a file history restoration event.

        See FRS Section 4.3.5 — the filesystem tool maintains file
        history, and restorations are always audit-logged.

        Args:
            agent: The agent that requested the restoration.
            target: The file path that was restored.
            channel: Associated Redis channel.
            msg_id: Triggering message ID.

        Returns:
            The AuditEntry that was written.
        """
        entry = AuditEntry(
            agent=agent,
            tool="filesystem",
            action="restore",
            target=target,
            approval_tier="allow_once",
            decision="approved",
            channel=channel,
            msg_id=msg_id,
        )
        self.write(entry)
        return entry

    def rotate_if_needed(self) -> Optional[Path]:
        """Check if the audit log needs rotation and archive if so.

        Rotation is based on file age, not size. If the log file is
        older than retention_days, it is moved to logs/archive/ with
        a timestamp suffix. A new empty log file is started.

        This method is called by the PA on startup and periodically
        (e.g. daily via FAITH-048 log rotation task).

        Returns:
            Path to the archived file if rotation occurred, else None.
        """
        if not self.log_path.exists():
            return None

        # Check file modification time
        stat = self.log_path.stat()
        file_age_days = (
            datetime.now(timezone.utc)
            - datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        ).days

        if file_age_days < self.retention_days:
            logger.debug(
                f"Audit log age ({file_age_days}d) below retention "
                f"threshold ({self.retention_days}d) — no rotation needed"
            )
            return None

        # Close the current file before rotating
        self.close()

        # Archive with timestamp suffix
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_name = f"audit_{timestamp}.log"
        archive_path = self.archive_dir / archive_name

        shutil.move(str(self.log_path), str(archive_path))
        logger.info(f"Audit log archived to {archive_path}")

        # Reopen a fresh log file
        self.open()

        return archive_path

    def read_entries(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Read audit entries from the log file.

        Reads from the current audit log. For archived logs, the caller
        must read from the archive directory directly.

        Args:
            limit: Maximum number of entries to return.
            offset: Number of entries to skip from the start.

        Returns:
            List of AuditEntry objects.
        """
        if not self.log_path.exists():
            return []

        entries = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                if i < offset:
                    continue
                if len(entries) >= limit:
                    break
                try:
                    entries.append(AuditEntry.from_json_line(line))
                except Exception as e:
                    logger.warning(f"Skipping malformed audit line {i}: {e}")
        return entries

    def query(
        self,
        agent: Optional[str] = None,
        tool: Optional[str] = None,
        action: Optional[str] = None,
        decision: Optional[str] = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query audit entries with optional filters.

        Simple in-memory filtering — suitable for the current file-based
        log. For large-scale querying the Web UI (FAITH-044) will
        implement more efficient approaches.

        Args:
            agent: Filter by agent name.
            tool: Filter by tool name.
            action: Filter by action.
            decision: Filter by decision ("approved" or "denied").
            limit: Maximum results to return.

        Returns:
            Matching AuditEntry objects (newest first).
        """
        if not self.log_path.exists():
            return []

        all_entries = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = AuditEntry.from_json_line(line)
                except Exception:
                    continue

                if agent and entry.agent != agent:
                    continue
                if tool and entry.tool != tool:
                    continue
                if action and entry.action != action:
                    continue
                if decision and entry.decision != decision:
                    continue

                all_entries.append(entry)

        # Return newest first, limited
        all_entries.reverse()
        return all_entries[:limit]

    @staticmethod
    def from_system_config(
        logs_dir: Path,
        system_config: dict[str, Any],
    ) -> "AuditLogger":
        """Factory method to create an AuditLogger from .faith/system.yaml config.

        Reads the `audit` section of system.yaml for retention settings.

        Expected system.yaml structure:
        ```yaml
        audit:
          retention_days: 90
        ```

        Args:
            logs_dir: Path to the logs/ directory.
            system_config: Parsed .faith/system.yaml as a dict.

        Returns:
            Configured AuditLogger instance.
        """
        audit_config = system_config.get("audit", {})
        retention_days = audit_config.get("retention_days", DEFAULT_RETENTION_DAYS)

        audit_logger = AuditLogger(
            logs_dir=logs_dir,
            retention_days=retention_days,
        )
        return audit_logger

    def __enter__(self):
        """Context manager support — opens the log file."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support — closes the log file."""
        self.close()
        return False

    def __repr__(self) -> str:
        return (
            f"AuditLogger(log_path={self.log_path}, "
            f"retention_days={self.retention_days})"
        )
```

### 3. `tests/test_audit_log.py`

```python
"""Tests for the FAITH audit log system.

Covers AuditEntry serialisation, AuditLogger write/read/query,
convenience logging methods, log rotation, context manager support,
and factory method from system config.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from faith.security.audit_log import (
    AuditEntry,
    AuditLogger,
    DEFAULT_RETENTION_DAYS,
    _next_audit_id,
    _now_iso,
)


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def logs_dir(tmp_path):
    """Create a temporary logs directory."""
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def audit_logger(logs_dir):
    """Create an AuditLogger opened for writing."""
    al = AuditLogger(logs_dir=logs_dir)
    al.open()
    yield al
    al.close()


@pytest.fixture
def sample_entry():
    """A sample AuditEntry for testing."""
    return AuditEntry(
        ts="2026-03-23T14:32:01Z",
        agent="software-developer",
        tool="filesystem",
        action="write",
        target="workspace/src/auth.py",
        approval_tier="always_allow",
        rule_matched="^(write|read) workspace/src/.*$",
        decision="approved",
        channel="ch-auth-feature",
        msg_id=47,
        audit_id="aud-00001",
    )


# ──────────────────────────────────────────────────
# AuditEntry serialisation tests
# ──────────────────────────────────────────────────


def test_entry_to_json_line(sample_entry):
    """AuditEntry serialises to a valid JSON string."""
    line = sample_entry.to_json_line()
    parsed = json.loads(line)
    assert parsed["agent"] == "software-developer"
    assert parsed["tool"] == "filesystem"
    assert parsed["action"] == "write"
    assert parsed["target"] == "workspace/src/auth.py"
    assert parsed["approval_tier"] == "always_allow"
    assert parsed["decision"] == "approved"
    assert parsed["audit_id"] == "aud-00001"


def test_entry_from_json_line(sample_entry):
    """AuditEntry deserialises from a JSON line string."""
    line = sample_entry.to_json_line()
    restored = AuditEntry.from_json_line(line)
    assert restored.agent == sample_entry.agent
    assert restored.tool == sample_entry.tool
    assert restored.action == sample_entry.action
    assert restored.target == sample_entry.target
    assert restored.audit_id == sample_entry.audit_id


def test_entry_excludes_none_fields():
    """JSON output excludes fields that are None."""
    entry = AuditEntry(
        agent="pa",
        tool="container",
        action="start",
        target="agent-software-developer",
        decision="approved",
    )
    line = entry.to_json_line()
    parsed = json.loads(line)
    assert "approval_tier" not in parsed
    assert "rule_matched" not in parsed
    assert "channel" not in parsed
    assert "msg_id" not in parsed


def test_entry_default_timestamp():
    """AuditEntry gets a UTC timestamp by default."""
    entry = AuditEntry(
        agent="pa",
        tool="container",
        action="start",
        target="test-container",
        decision="approved",
    )
    assert entry.ts.endswith("Z")
    # Should be parseable as ISO 8601
    dt = datetime.strptime(entry.ts, "%Y-%m-%dT%H:%M:%SZ")
    assert dt.year >= 2026


def test_entry_default_audit_id():
    """AuditEntry gets a sequential audit ID by default."""
    entry = AuditEntry(
        agent="pa",
        tool="container",
        action="start",
        target="test-container",
        decision="approved",
    )
    assert entry.audit_id.startswith("aud-")
    assert len(entry.audit_id) == 9  # "aud-" + 5 digits


def test_audit_id_increments():
    """Sequential audit IDs increment correctly."""
    e1 = AuditEntry(
        agent="pa", tool="test", action="a", target="t", decision="approved"
    )
    e2 = AuditEntry(
        agent="pa", tool="test", action="b", target="t", decision="approved"
    )
    id1 = int(e1.audit_id.split("-")[1])
    id2 = int(e2.audit_id.split("-")[1])
    assert id2 == id1 + 1


# ──────────────────────────────────────────────────
# AuditLogger write tests
# ──────────────────────────────────────────────────


def test_write_creates_file(audit_logger, sample_entry, logs_dir):
    """Writing an entry creates the audit.log file."""
    audit_logger.write(sample_entry)
    assert (logs_dir / "audit.log").exists()


def test_write_appends_json_line(audit_logger, sample_entry, logs_dir):
    """Each write appends exactly one JSON line."""
    audit_logger.write(sample_entry)
    audit_logger.write(sample_entry)

    lines = (logs_dir / "audit.log").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    for line in lines:
        parsed = json.loads(line)
        assert parsed["agent"] == "software-developer"


def test_write_flush_immediate(audit_logger, logs_dir):
    """Entries are flushed immediately (readable without close)."""
    entry = AuditEntry(
        agent="pa",
        tool="test",
        action="write",
        target="test.txt",
        decision="approved",
    )
    audit_logger.write(entry)

    # Read without closing the logger
    content = (logs_dir / "audit.log").read_text(encoding="utf-8")
    assert "test.txt" in content


# ──────────────────────────────────────────────────
# Convenience method tests
# ──────────────────────────────────────────────────


def test_log_tool_operation(audit_logger, logs_dir):
    """log_tool_operation writes a complete tool operation entry."""
    entry = audit_logger.log_tool_operation(
        agent="software-developer",
        tool="filesystem",
        action="write",
        target="src/main.py",
        approval_tier="always_allow",
        rule_matched="^write src/.*$",
        decision="approved",
        channel="ch-feature",
        msg_id=10,
    )
    assert entry.agent == "software-developer"
    assert entry.tool == "filesystem"

    content = (logs_dir / "audit.log").read_text(encoding="utf-8")
    parsed = json.loads(content.strip())
    assert parsed["tool"] == "filesystem"
    assert parsed["action"] == "write"
    assert parsed["approval_tier"] == "always_allow"


def test_log_approval_decision(audit_logger, logs_dir):
    """log_approval_decision writes an approval decision entry."""
    entry = audit_logger.log_approval_decision(
        agent="software-developer",
        tool="database",
        action="query",
        target="SELECT * FROM users",
        approval_tier="allow_once",
        rule_matched=None,
        decision="approved",
        channel="ch-db",
        msg_id=20,
    )
    assert entry.decision == "approved"
    assert entry.approval_tier == "allow_once"


def test_log_approval_denied(audit_logger, logs_dir):
    """Denied approval decisions are logged correctly."""
    entry = audit_logger.log_approval_decision(
        agent="software-developer",
        tool="python",
        action="execute",
        target="rm -rf /",
        approval_tier="always_deny",
        rule_matched="^execute rm.*$",
        decision="denied",
    )
    assert entry.decision == "denied"

    content = (logs_dir / "audit.log").read_text(encoding="utf-8")
    parsed = json.loads(content.strip())
    assert parsed["decision"] == "denied"
    assert parsed["approval_tier"] == "always_deny"


def test_log_container_lifecycle(audit_logger, logs_dir):
    """log_container_lifecycle writes container start/stop/restart entries."""
    entry = audit_logger.log_container_lifecycle(
        agent="pa",
        action="start",
        target="agent-software-developer",
    )
    assert entry.tool == "container"
    assert entry.action == "start"

    content = (logs_dir / "audit.log").read_text(encoding="utf-8")
    parsed = json.loads(content.strip())
    assert parsed["tool"] == "container"
    assert parsed["target"] == "agent-software-developer"


def test_log_file_restoration(audit_logger, logs_dir):
    """log_file_restoration writes a file restore entry."""
    entry = audit_logger.log_file_restoration(
        agent="software-developer",
        target="src/auth.py",
        channel="ch-auth",
        msg_id=55,
    )
    assert entry.tool == "filesystem"
    assert entry.action == "restore"
    assert entry.approval_tier == "allow_once"


# ──────────────────────────────────────────────────
# Read and query tests
# ──────────────────────────────────────────────────


def test_read_entries(audit_logger, logs_dir):
    """read_entries returns written entries."""
    audit_logger.log_tool_operation(
        agent="dev-1", tool="filesystem", action="read", target="f1.py"
    )
    audit_logger.log_tool_operation(
        agent="dev-2", tool="database", action="query", target="SELECT 1"
    )

    entries = audit_logger.read_entries()
    assert len(entries) == 2
    assert entries[0].agent == "dev-1"
    assert entries[1].agent == "dev-2"


def test_read_entries_with_limit(audit_logger):
    """read_entries respects the limit parameter."""
    for i in range(10):
        audit_logger.log_tool_operation(
            agent=f"dev-{i}", tool="test", action="a", target="t"
        )
    entries = audit_logger.read_entries(limit=3)
    assert len(entries) == 3


def test_read_entries_with_offset(audit_logger):
    """read_entries respects the offset parameter."""
    for i in range(5):
        audit_logger.log_tool_operation(
            agent=f"dev-{i}", tool="test", action="a", target="t"
        )
    entries = audit_logger.read_entries(offset=3, limit=10)
    assert len(entries) == 2
    assert entries[0].agent == "dev-3"


def test_read_entries_empty_log(logs_dir):
    """read_entries returns empty list for non-existent log."""
    al = AuditLogger(logs_dir=logs_dir)
    entries = al.read_entries()
    assert entries == []


def test_query_by_agent(audit_logger):
    """query filters by agent name."""
    audit_logger.log_tool_operation(
        agent="dev-1", tool="filesystem", action="read", target="a.py"
    )
    audit_logger.log_tool_operation(
        agent="dev-2", tool="filesystem", action="read", target="b.py"
    )
    audit_logger.log_tool_operation(
        agent="dev-1", tool="database", action="query", target="SELECT 1"
    )

    results = audit_logger.query(agent="dev-1")
    assert len(results) == 2
    assert all(e.agent == "dev-1" for e in results)


def test_query_by_tool(audit_logger):
    """query filters by tool name."""
    audit_logger.log_tool_operation(
        agent="dev-1", tool="filesystem", action="read", target="a.py"
    )
    audit_logger.log_tool_operation(
        agent="dev-1", tool="database", action="query", target="SELECT 1"
    )

    results = audit_logger.query(tool="database")
    assert len(results) == 1
    assert results[0].tool == "database"


def test_query_by_decision(audit_logger):
    """query filters by approval decision."""
    audit_logger.log_approval_decision(
        agent="dev-1", tool="python", action="execute",
        target="print(1)", approval_tier="always_allow",
        rule_matched=None, decision="approved",
    )
    audit_logger.log_approval_decision(
        agent="dev-1", tool="python", action="execute",
        target="os.system('rm -rf /')", approval_tier="always_deny",
        rule_matched="^execute os\\.system.*$", decision="denied",
    )

    approved = audit_logger.query(decision="approved")
    denied = audit_logger.query(decision="denied")
    assert len(approved) == 1
    assert len(denied) == 1
    assert denied[0].target == "os.system('rm -rf /')"


def test_query_returns_newest_first(audit_logger):
    """query results are ordered newest first."""
    audit_logger.log_tool_operation(
        agent="dev-1", tool="test", action="first", target="1"
    )
    audit_logger.log_tool_operation(
        agent="dev-1", tool="test", action="second", target="2"
    )

    results = audit_logger.query(agent="dev-1")
    assert results[0].action == "second"
    assert results[1].action == "first"


def test_query_with_limit(audit_logger):
    """query respects the limit parameter."""
    for i in range(10):
        audit_logger.log_tool_operation(
            agent="dev", tool="test", action="a", target=f"t{i}"
        )
    results = audit_logger.query(limit=3)
    assert len(results) == 3


# ──────────────────────────────────────────────────
# Log rotation tests
# ──────────────────────────────────────────────────


def test_rotate_no_file(logs_dir):
    """rotate_if_needed returns None when log doesn't exist."""
    al = AuditLogger(logs_dir=logs_dir)
    assert al.rotate_if_needed() is None


def test_rotate_young_file(audit_logger, logs_dir):
    """rotate_if_needed skips files younger than retention_days."""
    audit_logger.log_tool_operation(
        agent="dev", tool="test", action="a", target="t"
    )
    result = audit_logger.rotate_if_needed()
    assert result is None
    # Original file should still exist
    assert (logs_dir / "audit.log").exists()


def test_rotate_old_file(logs_dir):
    """rotate_if_needed archives files older than retention_days."""
    al = AuditLogger(logs_dir=logs_dir, retention_days=0)
    al.open()
    al.log_tool_operation(agent="dev", tool="test", action="a", target="t")

    # Set file mtime to 100 days ago
    import os
    old_time = time.time() - (100 * 86400)
    os.utime(al.log_path, (old_time, old_time))

    archive_path = al.rotate_if_needed()
    assert archive_path is not None
    assert archive_path.exists()
    assert archive_path.parent == logs_dir / "archive"
    assert archive_path.name.startswith("audit_")
    assert archive_path.name.endswith(".log")

    # A new empty audit.log should be ready for writing
    al.log_tool_operation(agent="dev", tool="test", action="b", target="t2")
    assert (logs_dir / "audit.log").exists()
    al.close()


# ──────────────────────────────────────────────────
# Context manager tests
# ──────────────────────────────────────────────────


def test_context_manager(logs_dir):
    """AuditLogger works as a context manager."""
    with AuditLogger(logs_dir=logs_dir) as al:
        al.log_tool_operation(
            agent="dev", tool="test", action="a", target="t"
        )
    # File should be closed after exiting context
    assert al._file is None
    # But data should persist
    content = (logs_dir / "audit.log").read_text(encoding="utf-8")
    assert "dev" in content


# ──────────────────────────────────────────────────
# Factory method tests
# ──────────────────────────────────────────────────


def test_from_system_config_defaults(logs_dir):
    """Factory method uses defaults when audit config is absent."""
    al = AuditLogger.from_system_config(logs_dir, {})
    assert al.retention_days == DEFAULT_RETENTION_DAYS


def test_from_system_config_custom(logs_dir):
    """Factory method reads retention_days from system config."""
    config = {"audit": {"retention_days": 180}}
    al = AuditLogger.from_system_config(logs_dir, config)
    assert al.retention_days == 180


# ──────────────────────────────────────────────────
# Directory creation tests
# ──────────────────────────────────────────────────


def test_creates_directories(tmp_path):
    """AuditLogger creates logs/ and logs/archive/ if they don't exist."""
    logs_dir = tmp_path / "nonexistent" / "logs"
    al = AuditLogger(logs_dir=logs_dir)
    assert logs_dir.exists()
    assert (logs_dir / "archive").exists()


# ──────────────────────────────────────────────────
# Edge case tests
# ──────────────────────────────────────────────────


def test_close_idempotent(audit_logger):
    """Closing an already-closed logger is safe."""
    audit_logger.close()
    audit_logger.close()  # Should not raise


def test_write_auto_opens(logs_dir):
    """Writing to a closed logger auto-opens the file."""
    al = AuditLogger(logs_dir=logs_dir)
    # Don't call open() — write should handle it
    al.log_tool_operation(agent="dev", tool="test", action="a", target="t")
    assert (logs_dir / "audit.log").exists()
    al.close()


def test_malformed_line_skipped_on_read(audit_logger, logs_dir):
    """Malformed JSON lines are skipped during read_entries."""
    audit_logger.log_tool_operation(
        agent="dev", tool="test", action="a", target="t"
    )
    audit_logger.close()

    # Inject a malformed line
    with open(logs_dir / "audit.log", "a", encoding="utf-8") as f:
        f.write("this is not json\n")

    audit_logger.open()
    audit_logger.log_tool_operation(
        agent="dev2", tool="test", action="b", target="t2"
    )

    entries = audit_logger.read_entries()
    assert len(entries) == 2  # Malformed line skipped
    assert entries[0].agent == "dev"
    assert entries[1].agent == "dev2"
```

---

## Integration Points

The AuditLogger integrates with FAITH components through the event system (FAITH-008):

```python
# PA subscribes to tool and approval events, then audit-logs them.
# In the PA's event dispatcher (FAITH-016):

from faith.protocol.events import EventType, FaithEvent
from faith.security.audit_log import AuditLogger

async def handle_tool_complete(event: FaithEvent, audit: AuditLogger):
    """Called by EventSubscriber when a tool:call_complete event arrives."""
    audit.log_tool_operation(
        agent=event.source,
        tool=event.data.get("tool", "unknown"),
        action=event.data.get("action", "unknown"),
        target=event.data.get("target", ""),
        approval_tier=event.data.get("approval_tier"),
        rule_matched=event.data.get("rule_matched"),
        decision=event.data.get("decision", "approved"),
        channel=event.channel,
        msg_id=event.data.get("msg_id"),
    )

async def handle_approval_decision(event: FaithEvent, audit: AuditLogger):
    """Called when an approval:decision event arrives."""
    audit.log_approval_decision(
        agent=event.data.get("agent", event.source),
        tool=event.data.get("tool", "unknown"),
        action=event.data.get("action", "unknown"),
        target=event.data.get("target", ""),
        approval_tier=event.data.get("approval_tier", "unknown"),
        rule_matched=event.data.get("rule_matched"),
        decision=event.data.get("decision", "approved"),
        channel=event.channel,
        msg_id=event.data.get("msg_id"),
    )

async def handle_container_event(event: FaithEvent, audit: AuditLogger):
    """Called when container:started or container:stopped events arrive."""
    action = event.event.value.split(":")[-1]  # "started" / "stopped"
    audit.log_container_lifecycle(
        agent="pa",
        action=action,
        target=event.data.get("container", event.source),
        channel=event.channel,
    )
```

```python
# PA startup — create AuditLogger from .faith/system.yaml:

import yaml
from pathlib import Path
from faith.security.audit_log import AuditLogger

system_yaml = Path(".faith/system.yaml")
system_config = yaml.safe_load(system_yaml.read_text()) if system_yaml.exists() else {}

audit = AuditLogger.from_system_config(
    logs_dir=Path("logs"),
    system_config=system_config,
)
audit.open()

# Check if rotation is needed on startup
audit.rotate_if_needed()
```

---

## Acceptance Criteria

1. `AuditEntry` model includes all fields from FRS 5.5.2: `ts`, `agent`, `tool`, `action`, `target`, `approval_tier`, `rule_matched`, `decision`, `channel`, `msg_id`, `audit_id`.
2. `AuditEntry.to_json_line()` serialises to a single JSON object with no newlines; `None` fields are excluded.
3. `AuditEntry.from_json_line()` round-trips correctly with `to_json_line()`.
4. `AuditLogger` writes to `logs/audit.log` in append-only mode with immediate flush (line buffering).
5. `AuditLogger` creates `logs/` and `logs/archive/` directories if they do not exist.
6. `log_tool_operation()` correctly logs filesystem read/write, database query, Python execution, and browser action entries.
7. `log_approval_decision()` logs approved, denied, remembered-rule, and unattended decisions with the correct tier and rule.
8. `log_container_lifecycle()` logs container start, stop, and restart events with `tool="container"`.
9. `log_file_restoration()` logs file history restoration events with `tool="filesystem"`, `action="restore"`.
10. `read_entries()` reads back written entries with support for `limit` and `offset` parameters; malformed lines are skipped.
11. `query()` filters entries by agent, tool, action, and decision; results are returned newest-first.
12. `rotate_if_needed()` archives the log to `logs/archive/audit_YYYYMMDD_HHMMSS.log` when the file is older than `retention_days`; it does not delete archived files.
13. `from_system_config()` reads `audit.retention_days` from the parsed `.faith/system.yaml` dict, defaulting to 90 days.
14. `AuditLogger` works as a context manager (`with AuditLogger(...) as al:`).
15. All 30 tests in `tests/test_audit_log.py` pass, covering serialisation, write, read, query, rotation, context manager, factory method, directory creation, and edge cases.

---

## Notes for Implementer

- **PA is the sole writer**: Only the PA container instantiates `AuditLogger` and calls write methods. All agent containers mount `logs/` as read-only via Docker volume configuration (defined in `docker-compose.yml`). The `AuditLogger` class itself does not enforce this — it is an architectural constraint.
- **Immediate flush**: The file is opened with `buffering=1` (line buffering) so each `write()` call is immediately flushed to disk. This minimises data loss if the PA crashes mid-session.
- **Audit ID uniqueness**: The `_audit_counter` is a module-level integer that resets on PA restart. Combined with the ISO timestamp, this is sufficient for uniqueness. If the PA restarts, IDs restart from `aud-00001`, but the timestamp disambiguates. No database or lock file is needed.
- **No deletion, only archival**: The FRS explicitly states "older entries are archived, not deleted" (Section 5.5.3). The `rotate_if_needed()` method uses `shutil.move()`, and there is no delete method on `AuditLogger`. The FAITH-048 log rotation task handles the periodic rotation schedule; this task provides the rotation mechanism.
- **Configuration via `.faith/system.yaml`**: The PA reads `system.yaml` at startup and passes the parsed dict to `AuditLogger.from_system_config()`. The expected YAML structure is:
  ```yaml
  audit:
    retention_days: 90
  ```
  If the `audit` section is missing, the default of 90 days applies.
- **Event-driven architecture**: The `AuditLogger` is a passive writer — it does not subscribe to Redis events itself. The PA's event dispatcher (FAITH-016) receives events via `EventSubscriber` (FAITH-009) and calls the appropriate `log_*` method on the `AuditLogger`. This keeps the audit log decoupled from the event system.
- **Web UI surfacing**: The `read_entries()` and `query()` methods provide basic read access for the Web UI log viewer (FAITH-044). For production scale, the Web UI may implement more efficient approaches (streaming, indexing), but these methods are sufficient for the initial implementation.
- **Thread safety**: The `AuditLogger` is designed for single-writer use (the PA's async event loop). It is not thread-safe. If future requirements demand concurrent writers, a lock or queue should be added.

