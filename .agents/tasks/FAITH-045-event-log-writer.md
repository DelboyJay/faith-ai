# FAITH-045 — Event Log Writer

**Phase:** 9 — Logging & Observability
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-009
**FRS Reference:** Section 8.3

---

## Objective

Implement the `EventLogWriter` class that subscribes to the `system-events` Redis channel via `EventSubscriber` (FAITH-009) and writes every event to `logs/events.log` as JSON lines. The event log complements the audit log — where the audit log records *what agents did*, the event log records *the state changes that drove PA decisions*. Retention follows the same policy as the audit log (`log_retention_days` in `.faith/system.yaml`, default 90 days, archive not delete).

---

## Architecture

```
faith/logging/
├── __init__.py
└── event_log.py    ← EventLogWriter class (this task)

tests/
└── test_event_log.py  ← Tests (this task)
```

---

## Files to Create

### 1. `faith/logging/__init__.py`

```python
"""FAITH Logging — event log writer and log utilities."""

from faith.logging.event_log import EventLogWriter

__all__ = [
    "EventLogWriter",
]
```

### 2. `faith/logging/event_log.py`

```python
"""FAITH Event Log Writer — persistent record of all system events.

Subscribes to the system-events Redis channel (via EventSubscriber) and
writes every event to logs/events.log as JSON lines. The event log
complements the audit log: the audit log records what agents did, the
event log records the state changes that drove PA decisions.

Retention follows the same policy as the audit log (log_retention_days
in .faith/system.yaml, default 90 days, archive not delete).

FRS Reference: Section 8.3
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from faith.protocol.events import FaithEvent

logger = logging.getLogger("faith.logging.event_log")

# Default retention period in days (matches audit log)
DEFAULT_RETENTION_DAYS = 90


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventLogEntry(BaseModel):
    """A single event log entry.

    Fields match the FRS Section 8.3 example format. Each entry is a
    JSON line recording an event from the system-events channel.

    Attributes:
        ts: ISO 8601 UTC timestamp of when the event was received.
        event: Event type string (e.g. "agent:task_complete",
            "channel:stalled", "tool:call_started").
        source: The agent or system component that published the event.
        channel: The Redis channel associated with this event (if any).
        data: Arbitrary event payload (task details, error info, etc.).
    """

    ts: str = Field(default_factory=_now_iso)
    event: str
    source: str
    channel: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)

    def to_json_line(self) -> str:
        """Serialise to a single JSON line (no trailing newline).

        Excludes None fields for compact output.
        """
        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_json_line(cls, line: str) -> "EventLogEntry":
        """Deserialise from a JSON line string."""
        return cls.model_validate_json(line.strip())

    @classmethod
    def from_faith_event(cls, event: FaithEvent) -> "EventLogEntry":
        """Create an EventLogEntry from a FaithEvent.

        Maps the FaithEvent fields to the event log format defined
        in FRS Section 8.3.

        Args:
            event: The FaithEvent received from the system-events channel.

        Returns:
            An EventLogEntry ready to be written to the log.
        """
        return cls(
            ts=event.ts if hasattr(event, "ts") and event.ts else _now_iso(),
            event=event.event.value if hasattr(event.event, "value") else str(event.event),
            source=event.source,
            channel=event.channel,
            data=event.data if event.data else {},
        )


class EventLogWriter:
    """Writes system events to logs/events.log as JSON lines.

    The EventLogWriter is instantiated by the PA and registered as a
    handler with EventSubscriber (FAITH-009). It receives every event
    published to system-events and persists it to disk. The log file
    is opened in append mode with line buffering for immediate flush.

    Attributes:
        log_path: Absolute path to the events log file.
        archive_dir: Directory for rotated log archives.
        retention_days: Number of days to retain active logs before archiving.
    """

    def __init__(
        self,
        logs_dir: Path,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        """Initialise the event log writer.

        Args:
            logs_dir: Path to the logs/ directory (e.g. /app/logs or ./logs).
            retention_days: Days to retain before archiving. Loaded from
                .faith/system.yaml by the PA; defaults to 90.
        """
        self.logs_dir = Path(logs_dir)
        self.log_path = self.logs_dir / "events.log"
        self.archive_dir = self.logs_dir / "archive"
        self.retention_days = retention_days
        self._file = None

        # Ensure directories exist
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"EventLogWriter initialised: path={self.log_path}, "
            f"retention={self.retention_days} days"
        )

    def open(self) -> None:
        """Open the event log file for appending.

        Must be called before writing entries. The file is opened in
        append mode with UTF-8 encoding and line buffering for
        immediate flush on each write.
        """
        if self._file is not None:
            return
        self._file = open(self.log_path, "a", encoding="utf-8", buffering=1)
        logger.debug(f"Event log file opened: {self.log_path}")

    def close(self) -> None:
        """Close the event log file.

        Safe to call multiple times or when the file is not open.
        """
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception as e:
                logger.warning(f"Error closing event log: {e}")
            finally:
                self._file = None
            logger.debug("Event log file closed")

    def _ensure_open(self) -> None:
        """Open the file if it is not already open."""
        if self._file is None or self._file.closed:
            self._file = None
            self.open()

    def write(self, entry: EventLogEntry) -> None:
        """Write a single event log entry to the log.

        The entry is serialised as a JSON line and flushed immediately.

        Args:
            entry: The EventLogEntry to write.

        Raises:
            RuntimeError: If the log file cannot be written to.
        """
        self._ensure_open()
        try:
            line = entry.to_json_line()
            self._file.write(line + "\n")
            self._file.flush()
            logger.debug(f"Event log entry written: {entry.event}")
        except Exception as e:
            logger.error(f"Failed to write event log entry: {e}")
            raise RuntimeError(f"Event log write failed: {e}") from e

    async def handle_event(self, event: FaithEvent) -> None:
        """EventSubscriber handler — receives every system event.

        This method is registered with EventSubscriber as a catch-all
        handler. It converts the FaithEvent to an EventLogEntry and
        writes it to the log file.

        Args:
            event: The FaithEvent received from system-events channel.
        """
        entry = EventLogEntry.from_faith_event(event)
        self.write(entry)

    def write_event(
        self,
        event: str,
        source: str,
        channel: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> EventLogEntry:
        """Write an event directly (convenience method).

        For cases where the caller has raw event data rather than
        a FaithEvent object.

        Args:
            event: Event type string (e.g. "agent:task_complete").
            source: Source agent or component name.
            channel: Associated Redis channel (if any).
            data: Event payload dict (if any).

        Returns:
            The EventLogEntry that was written.
        """
        entry = EventLogEntry(
            event=event,
            source=source,
            channel=channel,
            data=data or {},
        )
        self.write(entry)
        return entry

    def rotate_if_needed(self) -> Optional[Path]:
        """Check if the event log needs rotation and archive if so.

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
                f"Event log age ({file_age_days}d) below retention "
                f"threshold ({self.retention_days}d) — no rotation needed"
            )
            return None

        # Close the current file before rotating
        self.close()

        # Archive with timestamp suffix
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_name = f"events_{timestamp}.log"
        archive_path = self.archive_dir / archive_name

        shutil.move(str(self.log_path), str(archive_path))
        logger.info(f"Event log archived to {archive_path}")

        # Reopen a fresh log file
        self.open()

        return archive_path

    def read_entries(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EventLogEntry]:
        """Read event log entries from the log file.

        Reads from the current event log. For archived logs, the caller
        must read from the archive directory directly.

        Args:
            limit: Maximum number of entries to return.
            offset: Number of entries to skip from the start.

        Returns:
            List of EventLogEntry objects.
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
                    entries.append(EventLogEntry.from_json_line(line))
                except Exception as e:
                    logger.warning(f"Skipping malformed event log line {i}: {e}")
        return entries

    def query(
        self,
        event: Optional[str] = None,
        source: Optional[str] = None,
        channel: Optional[str] = None,
        limit: int = 100,
    ) -> list[EventLogEntry]:
        """Query event log entries with optional filters.

        Simple in-memory filtering — suitable for the current file-based
        log. For large-scale querying the Web UI (FAITH-044) will
        implement more efficient approaches.

        Args:
            event: Filter by event type (e.g. "agent:task_complete").
            source: Filter by source agent/component.
            channel: Filter by Redis channel.
            limit: Maximum results to return.

        Returns:
            Matching EventLogEntry objects (newest first).
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
                    entry = EventLogEntry.from_json_line(line)
                except Exception:
                    continue

                if event and entry.event != event:
                    continue
                if source and entry.source != source:
                    continue
                if channel and entry.channel != channel:
                    continue

                all_entries.append(entry)

        # Return newest first, limited
        all_entries.reverse()
        return all_entries[:limit]

    @staticmethod
    def from_system_config(
        logs_dir: Path,
        system_config: dict[str, Any],
    ) -> "EventLogWriter":
        """Factory method to create an EventLogWriter from .faith/system.yaml config.

        Reads the `logging` section of system.yaml for retention settings.
        Uses the same retention_days as the audit log per FRS Section 8.3.

        Expected system.yaml structure:
        ```yaml
        logging:
          log_retention_days: 90
        ```

        Falls back to the `audit` section for backwards compatibility:
        ```yaml
        audit:
          retention_days: 90
        ```

        Args:
            logs_dir: Path to the logs/ directory.
            system_config: Parsed .faith/system.yaml as a dict.

        Returns:
            Configured EventLogWriter instance.
        """
        # Primary: logging.log_retention_days
        logging_config = system_config.get("logging", {})
        retention_days = logging_config.get("log_retention_days", None)

        # Fallback: audit.retention_days (same retention policy per FRS 8.3)
        if retention_days is None:
            audit_config = system_config.get("audit", {})
            retention_days = audit_config.get("retention_days", DEFAULT_RETENTION_DAYS)

        return EventLogWriter(
            logs_dir=logs_dir,
            retention_days=retention_days,
        )

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
            f"EventLogWriter(log_path={self.log_path}, "
            f"retention_days={self.retention_days})"
        )
```

### 3. `tests/test_event_log.py`

```python
"""Tests for the FAITH event log writer.

Covers EventLogEntry serialisation, EventLogWriter write/read/query,
handle_event handler, log rotation, context manager support,
and factory method from system config.
"""

import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.logging.event_log import (
    EventLogEntry,
    EventLogWriter,
    DEFAULT_RETENTION_DAYS,
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
def event_writer(logs_dir):
    """Create an EventLogWriter opened for writing."""
    ew = EventLogWriter(logs_dir=logs_dir)
    ew.open()
    yield ew
    ew.close()


@pytest.fixture
def sample_entry():
    """A sample EventLogEntry for testing."""
    return EventLogEntry(
        ts="2026-03-23T14:32:01Z",
        event="agent:task_complete",
        source="software-developer",
        channel="ch-auth-feature",
        data={"task": "JWT token refresh endpoint", "msg_id": 47},
    )


@pytest.fixture
def mock_faith_event():
    """A mock FaithEvent for testing handle_event."""
    event = MagicMock()
    event.ts = "2026-03-23T14:35:10Z"
    event.event.value = "channel:stalled"
    event.source = "system"
    event.channel = "ch-auth-feature"
    event.data = {"idle_seconds": 312}
    return event


# ──────────────────────────────────────────────────
# EventLogEntry serialisation tests
# ──────────────────────────────────────────────────


def test_entry_to_json_line(sample_entry):
    """EventLogEntry serialises to a valid JSON string."""
    line = sample_entry.to_json_line()
    parsed = json.loads(line)
    assert parsed["event"] == "agent:task_complete"
    assert parsed["source"] == "software-developer"
    assert parsed["channel"] == "ch-auth-feature"
    assert parsed["data"]["task"] == "JWT token refresh endpoint"
    assert parsed["data"]["msg_id"] == 47


def test_entry_from_json_line(sample_entry):
    """EventLogEntry deserialises from a JSON line string."""
    line = sample_entry.to_json_line()
    restored = EventLogEntry.from_json_line(line)
    assert restored.event == sample_entry.event
    assert restored.source == sample_entry.source
    assert restored.channel == sample_entry.channel
    assert restored.data == sample_entry.data


def test_entry_excludes_none_fields():
    """JSON output excludes fields that are None."""
    entry = EventLogEntry(
        event="agent:heartbeat",
        source="software-developer",
    )
    line = entry.to_json_line()
    parsed = json.loads(line)
    assert "channel" not in parsed


def test_entry_default_timestamp():
    """EventLogEntry gets a UTC timestamp by default."""
    entry = EventLogEntry(
        event="agent:heartbeat",
        source="software-developer",
    )
    assert entry.ts.endswith("Z")
    dt = datetime.strptime(entry.ts, "%Y-%m-%dT%H:%M:%SZ")
    assert dt.year >= 2026


def test_entry_default_data():
    """EventLogEntry gets an empty dict for data by default."""
    entry = EventLogEntry(
        event="agent:heartbeat",
        source="software-developer",
    )
    assert entry.data == {}


def test_from_faith_event(mock_faith_event):
    """from_faith_event correctly maps FaithEvent fields."""
    entry = EventLogEntry.from_faith_event(mock_faith_event)
    assert entry.ts == "2026-03-23T14:35:10Z"
    assert entry.event == "channel:stalled"
    assert entry.source == "system"
    assert entry.channel == "ch-auth-feature"
    assert entry.data == {"idle_seconds": 312}


def test_from_faith_event_no_channel():
    """from_faith_event handles events without a channel."""
    event = MagicMock()
    event.ts = "2026-03-23T14:36:00Z"
    event.event.value = "system:config_changed"
    event.source = "pa"
    event.channel = None
    event.data = {"file": "system.yaml"}
    entry = EventLogEntry.from_faith_event(event)
    assert entry.channel is None
    assert entry.event == "system:config_changed"


# ──────────────────────────────────────────────────
# EventLogWriter write tests
# ──────────────────────────────────────────────────


def test_write_creates_file(event_writer, sample_entry, logs_dir):
    """Writing an entry creates the events.log file."""
    event_writer.write(sample_entry)
    assert (logs_dir / "events.log").exists()


def test_write_appends_json_line(event_writer, sample_entry, logs_dir):
    """Each write appends exactly one JSON line."""
    event_writer.write(sample_entry)
    event_writer.write(sample_entry)

    lines = (logs_dir / "events.log").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    for line in lines:
        parsed = json.loads(line)
        assert parsed["event"] == "agent:task_complete"


def test_write_flush_immediate(event_writer, logs_dir):
    """Entries are flushed immediately (readable without close)."""
    entry = EventLogEntry(
        event="agent:heartbeat",
        source="software-developer",
    )
    event_writer.write(entry)

    # Read without closing the writer
    content = (logs_dir / "events.log").read_text(encoding="utf-8")
    assert "agent:heartbeat" in content


def test_write_event_convenience(event_writer, logs_dir):
    """write_event convenience method creates and writes an entry."""
    entry = event_writer.write_event(
        event="tool:call_started",
        source="software-developer",
        channel="ch-feature",
        data={"tool": "filesystem", "action": "write", "target": "src/main.py"},
    )
    assert entry.event == "tool:call_started"
    assert entry.source == "software-developer"

    content = (logs_dir / "events.log").read_text(encoding="utf-8")
    parsed = json.loads(content.strip())
    assert parsed["event"] == "tool:call_started"
    assert parsed["data"]["tool"] == "filesystem"


# ──────────────────────────────────────────────────
# handle_event (async handler) tests
# ──────────────────────────────────────────────────


def test_handle_event_writes_to_log(event_writer, mock_faith_event, logs_dir):
    """handle_event writes the FaithEvent to the log file."""
    asyncio.get_event_loop().run_until_complete(
        event_writer.handle_event(mock_faith_event)
    )

    content = (logs_dir / "events.log").read_text(encoding="utf-8")
    parsed = json.loads(content.strip())
    assert parsed["event"] == "channel:stalled"
    assert parsed["source"] == "system"
    assert parsed["data"]["idle_seconds"] == 312


# ──────────────────────────────────────────────────
# Read and query tests
# ──────────────────────────────────────────────────


def test_read_entries(event_writer, logs_dir):
    """read_entries returns written entries."""
    event_writer.write_event(
        event="agent:task_complete", source="dev-1", channel="ch-a"
    )
    event_writer.write_event(
        event="channel:stalled", source="system", channel="ch-b"
    )

    entries = event_writer.read_entries()
    assert len(entries) == 2
    assert entries[0].event == "agent:task_complete"
    assert entries[1].event == "channel:stalled"


def test_read_entries_with_limit(event_writer):
    """read_entries respects the limit parameter."""
    for i in range(10):
        event_writer.write_event(
            event=f"agent:heartbeat", source=f"dev-{i}"
        )
    entries = event_writer.read_entries(limit=3)
    assert len(entries) == 3


def test_read_entries_with_offset(event_writer):
    """read_entries respects the offset parameter."""
    for i in range(5):
        event_writer.write_event(
            event="agent:heartbeat", source=f"dev-{i}"
        )
    entries = event_writer.read_entries(offset=3, limit=10)
    assert len(entries) == 2
    assert entries[0].source == "dev-3"


def test_read_entries_empty_log(logs_dir):
    """read_entries returns empty list for non-existent log."""
    ew = EventLogWriter(logs_dir=logs_dir)
    entries = ew.read_entries()
    assert entries == []


def test_query_by_event(event_writer):
    """query filters by event type."""
    event_writer.write_event(
        event="agent:task_complete", source="dev-1", channel="ch-a"
    )
    event_writer.write_event(
        event="channel:stalled", source="system", channel="ch-a"
    )
    event_writer.write_event(
        event="agent:task_complete", source="dev-2", channel="ch-b"
    )

    results = event_writer.query(event="agent:task_complete")
    assert len(results) == 2
    assert all(e.event == "agent:task_complete" for e in results)


def test_query_by_source(event_writer):
    """query filters by source."""
    event_writer.write_event(
        event="agent:heartbeat", source="dev-1"
    )
    event_writer.write_event(
        event="agent:heartbeat", source="dev-2"
    )

    results = event_writer.query(source="dev-1")
    assert len(results) == 1
    assert results[0].source == "dev-1"


def test_query_by_channel(event_writer):
    """query filters by channel."""
    event_writer.write_event(
        event="agent:task_complete", source="dev-1", channel="ch-a"
    )
    event_writer.write_event(
        event="agent:task_complete", source="dev-2", channel="ch-b"
    )

    results = event_writer.query(channel="ch-a")
    assert len(results) == 1
    assert results[0].channel == "ch-a"


def test_query_returns_newest_first(event_writer):
    """query results are ordered newest first."""
    event_writer.write_event(
        event="agent:heartbeat", source="dev-1",
        data={"order": "first"},
    )
    event_writer.write_event(
        event="agent:heartbeat", source="dev-1",
        data={"order": "second"},
    )

    results = event_writer.query(source="dev-1")
    assert results[0].data["order"] == "second"
    assert results[1].data["order"] == "first"


def test_query_with_limit(event_writer):
    """query respects the limit parameter."""
    for i in range(10):
        event_writer.write_event(
            event="agent:heartbeat", source="dev", data={"i": i}
        )
    results = event_writer.query(limit=3)
    assert len(results) == 3


# ──────────────────────────────────────────────────
# Log rotation tests
# ──────────────────────────────────────────────────


def test_rotate_no_file(logs_dir):
    """rotate_if_needed returns None when log doesn't exist."""
    ew = EventLogWriter(logs_dir=logs_dir)
    assert ew.rotate_if_needed() is None


def test_rotate_young_file(event_writer, logs_dir):
    """rotate_if_needed skips files younger than retention_days."""
    event_writer.write_event(
        event="agent:heartbeat", source="dev"
    )
    result = event_writer.rotate_if_needed()
    assert result is None
    # Original file should still exist
    assert (logs_dir / "events.log").exists()


def test_rotate_old_file(logs_dir):
    """rotate_if_needed archives files older than retention_days."""
    import os

    ew = EventLogWriter(logs_dir=logs_dir, retention_days=0)
    ew.open()
    ew.write_event(event="agent:heartbeat", source="dev")

    # Set file mtime to 100 days ago
    old_time = time.time() - (100 * 86400)
    os.utime(ew.log_path, (old_time, old_time))

    archive_path = ew.rotate_if_needed()
    assert archive_path is not None
    assert archive_path.exists()
    assert archive_path.parent == logs_dir / "archive"
    assert archive_path.name.startswith("events_")
    assert archive_path.name.endswith(".log")

    # A new empty events.log should be ready for writing
    ew.write_event(event="agent:heartbeat", source="dev-2")
    assert (logs_dir / "events.log").exists()
    ew.close()


# ──────────────────────────────────────────────────
# Context manager tests
# ──────────────────────────────────────────────────


def test_context_manager(logs_dir):
    """EventLogWriter works as a context manager."""
    with EventLogWriter(logs_dir=logs_dir) as ew:
        ew.write_event(event="agent:heartbeat", source="dev")
    # File should be closed after exiting context
    assert ew._file is None
    # But data should persist
    content = (logs_dir / "events.log").read_text(encoding="utf-8")
    assert "agent:heartbeat" in content


# ──────────────────────────────────────────────────
# Factory method tests
# ──────────────────────────────────────────────────


def test_from_system_config_defaults(logs_dir):
    """Factory method uses defaults when logging config is absent."""
    ew = EventLogWriter.from_system_config(logs_dir, {})
    assert ew.retention_days == DEFAULT_RETENTION_DAYS


def test_from_system_config_logging_section(logs_dir):
    """Factory method reads log_retention_days from logging section."""
    config = {"logging": {"log_retention_days": 180}}
    ew = EventLogWriter.from_system_config(logs_dir, config)
    assert ew.retention_days == 180


def test_from_system_config_audit_fallback(logs_dir):
    """Factory method falls back to audit.retention_days."""
    config = {"audit": {"retention_days": 120}}
    ew = EventLogWriter.from_system_config(logs_dir, config)
    assert ew.retention_days == 120


def test_from_system_config_logging_takes_precedence(logs_dir):
    """logging.log_retention_days takes precedence over audit.retention_days."""
    config = {
        "logging": {"log_retention_days": 60},
        "audit": {"retention_days": 120},
    }
    ew = EventLogWriter.from_system_config(logs_dir, config)
    assert ew.retention_days == 60


# ──────────────────────────────────────────────────
# Directory creation tests
# ──────────────────────────────────────────────────


def test_creates_directories(tmp_path):
    """EventLogWriter creates logs/ and logs/archive/ if they don't exist."""
    logs_dir = tmp_path / "nonexistent" / "logs"
    ew = EventLogWriter(logs_dir=logs_dir)
    assert logs_dir.exists()
    assert (logs_dir / "archive").exists()


# ──────────────────────────────────────────────────
# Edge case tests
# ──────────────────────────────────────────────────


def test_close_idempotent(event_writer):
    """Closing an already-closed writer is safe."""
    event_writer.close()
    event_writer.close()  # Should not raise


def test_write_auto_opens(logs_dir):
    """Writing to a closed writer auto-opens the file."""
    ew = EventLogWriter(logs_dir=logs_dir)
    # Don't call open() — write should handle it
    ew.write_event(event="agent:heartbeat", source="dev")
    assert (logs_dir / "events.log").exists()
    ew.close()


def test_malformed_line_skipped_on_read(event_writer, logs_dir):
    """Malformed JSON lines are skipped during read_entries."""
    event_writer.write_event(event="agent:heartbeat", source="dev")
    event_writer.close()

    # Inject a malformed line
    with open(logs_dir / "events.log", "a", encoding="utf-8") as f:
        f.write("this is not json\n")

    event_writer.open()
    event_writer.write_event(event="channel:stalled", source="system")

    entries = event_writer.read_entries()
    assert len(entries) == 2  # Malformed line skipped
    assert entries[0].event == "agent:heartbeat"
    assert entries[1].event == "channel:stalled"
```

---

## Integration Points

The EventLogWriter integrates with FAITH components through the EventSubscriber (FAITH-009):

```python
# PA startup — register EventLogWriter as a catch-all handler with EventSubscriber.
# In the PA's initialisation (FAITH-014 / FAITH-016):

import yaml
from pathlib import Path

from faith.logging.event_log import EventLogWriter
from faith.protocol.events import EventType
from faith.protocol.subscriber import EventSubscriber

# Create EventLogWriter from .faith/system.yaml
system_yaml = Path(".faith/system.yaml")
system_config = yaml.safe_load(system_yaml.read_text()) if system_yaml.exists() else {}

event_log = EventLogWriter.from_system_config(
    logs_dir=Path("logs"),
    system_config=system_config,
)
event_log.open()

# Check if rotation is needed on startup
event_log.rotate_if_needed()

# Register as a catch-all handler — receives every event type.
# EventSubscriber dispatches by event type, so register for all types:
for event_type in EventType:
    subscriber.register(event_type, event_log.handle_event)
```

```python
# The EventLogWriter is a passive writer — it simply persists events
# to disk. Other components read the log via the Web UI (FAITH-044):

from faith.logging.event_log import EventLogWriter

event_log = EventLogWriter(logs_dir=Path("logs"))
recent = event_log.read_entries(limit=50)
stalls = event_log.query(event="channel:stalled", limit=20)
```

---

## Acceptance Criteria

1. `EventLogEntry` model includes all fields from FRS 8.3: `ts`, `event`, `source`, `channel`, `data`.
2. `EventLogEntry.to_json_line()` serialises to a single JSON object with no newlines; `None` fields are excluded.
3. `EventLogEntry.from_json_line()` round-trips correctly with `to_json_line()`.
4. `EventLogEntry.from_faith_event()` correctly maps a `FaithEvent` to an `EventLogEntry`.
5. `EventLogWriter` writes to `logs/events.log` in append-only mode with immediate flush (line buffering).
6. `EventLogWriter` creates `logs/` and `logs/archive/` directories if they do not exist.
7. `handle_event()` is an async method compatible with `EventSubscriber` handler registration; it converts a `FaithEvent` and writes it to the log.
8. `write_event()` convenience method creates and writes an entry from raw event data.
9. `read_entries()` reads back written entries with support for `limit` and `offset` parameters; malformed lines are skipped.
10. `query()` filters entries by event type, source, and channel; results are returned newest-first.
11. `rotate_if_needed()` archives the log to `logs/archive/events_YYYYMMDD_HHMMSS.log` when the file is older than `retention_days`; it does not delete archived files.
12. `from_system_config()` reads `logging.log_retention_days` from the parsed `.faith/system.yaml` dict, falling back to `audit.retention_days`, defaulting to 90 days.
13. `EventLogWriter` works as a context manager (`with EventLogWriter(...) as ew:`).
14. All 30 tests in `tests/test_event_log.py` pass, covering serialisation, write, read, query, handle_event, rotation, context manager, factory method, directory creation, and edge cases.

---

## Notes for Implementer

- **PA is the sole writer**: Only the PA container instantiates `EventLogWriter` and calls write methods. All agent containers mount `logs/` as read-only via Docker volume configuration (defined in `docker-compose.yml`). The `EventLogWriter` class itself does not enforce this — it is an architectural constraint.
- **Immediate flush**: The file is opened with `buffering=1` (line buffering) so each `write()` call is immediately flushed to disk. This minimises data loss if the PA crashes mid-session.
- **Same retention as audit log**: FRS Section 8.3 explicitly states "Retention follows the same policy as the audit log". The factory method reads `logging.log_retention_days` from `.faith/system.yaml`, falling back to `audit.retention_days` for consistency. Expected YAML structure:
  ```yaml
  logging:
    log_retention_days: 90
  ```
- **Catch-all handler pattern**: The `handle_event()` method is registered with `EventSubscriber` for every `EventType`. This ensures all events flowing through `system-events` are persisted. The handler is async to match the `EventSubscriber` handler signature, but the actual file write is synchronous (sufficient for the single-writer PA pattern).
- **No deletion, only archival**: The FRS states "older entries are archived, not deleted". The `rotate_if_needed()` method uses `shutil.move()`, and there is no delete method on `EventLogWriter`. The FAITH-048 log rotation task handles the periodic rotation schedule; this task provides the rotation mechanism.
- **Configuration via `.faith/system.yaml`**: The PA reads `system.yaml` at startup and passes the parsed dict to `EventLogWriter.from_system_config()`. No references to old `agents.yaml` or `tools.yaml` — all config comes from `.faith/system.yaml`.
- **Event-driven, not polling**: Unlike the audit log (which is called explicitly by PA logic), the event log writer is driven by `EventSubscriber` dispatch. Every event published to `system-events` triggers `handle_event()` automatically.
- **Web UI surfacing**: The `read_entries()` and `query()` methods provide basic read access for the Web UI log viewer (FAITH-044). For production scale, the Web UI may implement more efficient approaches (streaming, indexing), but these methods are sufficient for the initial implementation.
- **Thread safety**: The `EventLogWriter` is designed for single-writer use (the PA's async event loop). It is not thread-safe. If future requirements demand concurrent writers, a lock or queue should be added.
