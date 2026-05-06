# FAITH-048 — Log Retention & Rotation

**Phase:** 9 — Logging & Observability
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** DONE
**Dependencies:** FAITH-021, FAITH-045, FAITH-047
**FRS Reference:** Section 8.6

---

## Objective

Implement log rotation and retention policy for the FAITH framework. Audit logs, event logs, and token logs are archived after `log_retention_days` (default 90 days). Session logs are archived after `session_retention_days` (default 365 days). Archived logs are moved to `logs/archive/` — never automatically deleted. The PA surfaces a notification when the archive exceeds a configurable size threshold (default 1GB).

Current implementation note: the PA now performs startup rotation checks across active logs and persisted session history, archives aged data instead of deleting it, and warns the user when retained archive size crosses the configured threshold.

---

## Architecture

```
faith/logging/
├── __init__.py
└── log_rotator.py    ← LogRotator class (this task)

tests/
└── test_log_rotator.py  ← Tests (this task)
```

---

## Files to Create

### 1. `faith/logging/__init__.py`

```python
"""FAITH Logging — log rotation, retention, and archival."""

from faith.logging.log_rotator import LogRotator

__all__ = [
    "LogRotator",
]
```

### 2. `faith/logging/log_rotator.py`

```python
"""FAITH Log Rotator — retention policy and archive management.

Implements periodic log rotation per `.faith/system.yaml` settings.
Audit/event/token logs are archived after `log_retention_days` (default 90).
Session logs are archived after `session_retention_days` (default 365).
Archived logs are moved to `logs/archive/`, never auto-deleted.

The PA calls LogRotator periodically (e.g., daily via scheduler) to check
and rotate aged logs. Archive size is monitored and user is notified when
it exceeds a configurable threshold (default 1GB).

FRS Reference: Section 8.6
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("faith.logging.rotator")

# Default retention periods in days
DEFAULT_LOG_RETENTION_DAYS = 90
DEFAULT_SESSION_RETENTION_DAYS = 365
DEFAULT_ARCHIVE_SIZE_THRESHOLD = 1024 * 1024 * 1024  # 1GB


class LogRotator:
    """Manages log rotation and retention for FAITH.

    The LogRotator checks the age of log files and archives those older
    than the configured retention thresholds. Separate retention policies
    apply to:
    - Audit, event, and token logs (default 90 days)
    - Session logs (default 365 days)

    Archived logs are moved to `logs/archive/` with a timestamp suffix.
    No logs are ever automatically deleted — cleanup is manual.

    Archive size is monitored and a warning is emitted when it exceeds
    the configurable threshold (default 1GB).

    Attributes:
        logs_dir: Absolute path to the logs/ directory.
        archive_dir: Absolute path to logs/archive/.
        log_retention_days: Days before audit/event/token logs are archived.
        session_retention_days: Days before session logs are archived.
        archive_size_threshold: Bytes; warning emitted when exceeded.
    """

    def __init__(
        self,
        logs_dir: Path,
        log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS,
        session_retention_days: int = DEFAULT_SESSION_RETENTION_DAYS,
        archive_size_threshold: int = DEFAULT_ARCHIVE_SIZE_THRESHOLD,
    ):
        """Initialise the log rotator.

        Args:
            logs_dir: Path to the logs/ directory (e.g. /app/logs or ./logs).
            log_retention_days: Days before audit/event/token logs are archived.
                Loaded from .faith/system.yaml by the PA; defaults to 90.
            session_retention_days: Days before session logs are archived.
                Loaded from .faith/system.yaml by the PA; defaults to 365.
            archive_size_threshold: Bytes; warning emitted when archive
                exceeds this size. Default 1GB.
        """
        self.logs_dir = Path(logs_dir)
        self.archive_dir = self.logs_dir / "archive"
        self.log_retention_days = log_retention_days
        self.session_retention_days = session_retention_days
        self.archive_size_threshold = archive_size_threshold

        # Ensure directories exist
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"LogRotator initialised: logs_dir={self.logs_dir}, "
            f"log_retention={self.log_retention_days}d, "
            f"session_retention={self.session_retention_days}d, "
            f"archive_threshold={self.archive_size_threshold / (1024*1024):.0f}MB"
        )

    def _get_file_age_days(self, path: Path) -> int:
        """Get the age of a file in days.

        Args:
            path: Path to the file.

        Returns:
            Number of days since the file was last modified.
        """
        stat = path.stat()
        file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        age = (datetime.now(timezone.utc) - file_mtime).days
        return age

    def _archive_file(self, src_path: Path) -> Optional[Path]:
        """Move a file to the archive directory with a timestamp suffix.

        Args:
            src_path: Path to the file to archive.

        Returns:
            Path to the archived file, or None if archiving failed.
        """
        if not src_path.exists():
            return None

        try:
            # Generate archive filename with timestamp
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            base_name = src_path.stem
            archive_name = f"{base_name}_{timestamp}{src_path.suffix}"
            archive_path = self.archive_dir / archive_name

            shutil.move(str(src_path), str(archive_path))
            logger.info(f"Archived log: {src_path.name} → {archive_path.name}")
            return archive_path
        except Exception as e:
            logger.error(f"Failed to archive {src_path}: {e}")
            return None

    def rotate_audit_log(self) -> Optional[Path]:
        """Check and rotate the audit log if older than retention threshold.

        Returns:
            Path to the archived file if rotation occurred, else None.
        """
        audit_log = self.logs_dir / "audit.log"
        if not audit_log.exists():
            return None

        age = self._get_file_age_days(audit_log)
        if age < self.log_retention_days:
            logger.debug(
                f"Audit log age ({age}d) below retention threshold "
                f"({self.log_retention_days}d) — no rotation"
            )
            return None

        logger.info(
            f"Audit log is {age} days old (threshold: {self.log_retention_days}d) "
            f"— rotating"
        )
        return self._archive_file(audit_log)

    def rotate_event_log(self) -> Optional[Path]:
        """Check and rotate the event log if older than retention threshold.

        Returns:
            Path to the archived file if rotation occurred, else None.
        """
        event_log = self.logs_dir / "events.log"
        if not event_log.exists():
            return None

        age = self._get_file_age_days(event_log)
        if age < self.log_retention_days:
            logger.debug(
                f"Event log age ({age}d) below retention threshold "
                f"({self.log_retention_days}d) — no rotation"
            )
            return None

        logger.info(
            f"Event log is {age} days old (threshold: {self.log_retention_days}d) "
            f"— rotating"
        )
        return self._archive_file(event_log)

    def rotate_token_log(self) -> Optional[Path]:
        """Check and rotate the token log if older than retention threshold.

        Returns:
            Path to the archived file if rotation occurred, else None.
        """
        token_log = self.logs_dir / "tokens.log"
        if not token_log.exists():
            return None

        age = self._get_file_age_days(token_log)
        if age < self.log_retention_days:
            logger.debug(
                f"Token log age ({age}d) below retention threshold "
                f"({self.log_retention_days}d) — no rotation"
            )
            return None

        logger.info(
            f"Token log is {age} days old (threshold: {self.log_retention_days}d) "
            f"— rotating"
        )
        return self._archive_file(token_log)

    def rotate_session_logs(self) -> list[Path]:
        """Check and rotate session log directories if older than threshold.

        Session logs are stored in a two-level structure:
        .faith/sessions/sess-XXXX-YYYY-MM-DD/
        └── task-NNN-HHMMSS.mmm/

        Each session directory has a session.meta.json file with a 'started'
        timestamp. Directories older than session_retention_days are moved
        to logs/archive/sessions_<timestamp>/ as a group.

        Returns:
            List of archived session paths.
        """
        sessions_dir = self.logs_dir / "sessions"
        archived = []

        if not sessions_dir.exists():
            return archived

        try:
            session_dirs = sorted(sessions_dir.iterdir())
        except OSError as e:
            logger.error(f"Error listing session directories: {e}")
            return archived

        for session_dir in session_dirs:
            if not session_dir.is_dir():
                continue

            # Read session.meta.json to get the start timestamp
            meta_path = session_dir / "session.meta.json"
            if not meta_path.exists():
                logger.warning(f"Missing session.meta.json in {session_dir.name}")
                continue

            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                started_str = meta.get("started")
                if not started_str:
                    logger.warning(f"No 'started' field in {meta_path}")
                    continue

                # Parse ISO 8601 timestamp
                started = datetime.fromisoformat(started_str.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - started).days

                if age_days < self.session_retention_days:
                    logger.debug(
                        f"Session {session_dir.name} age ({age_days}d) below "
                        f"retention threshold ({self.session_retention_days}d)"
                    )
                    continue

                logger.info(
                    f"Session {session_dir.name} is {age_days} days old "
                    f"(threshold: {self.session_retention_days}d) — archiving"
                )

                # Archive the session directory
                archived_path = self._archive_session_directory(session_dir)
                if archived_path:
                    archived.append(archived_path)

            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Error parsing session metadata {meta_path}: {e}")
                continue

        return archived

    def _archive_session_directory(self, session_dir: Path) -> Optional[Path]:
        """Move an entire session directory to the archive.

        Session directories are grouped under a timestamped parent directory
        in the archive to preserve the directory structure.

        Args:
            session_dir: Path to the session directory to archive.

        Returns:
            Path to the archived session directory, or None on failure.
        """
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            sessions_archive_parent = self.archive_dir / f"sessions_{timestamp}"
            sessions_archive_parent.mkdir(parents=True, exist_ok=True)

            # Move the session dir into the archive parent
            dest_path = sessions_archive_parent / session_dir.name
            shutil.move(str(session_dir), str(dest_path))
            logger.info(f"Archived session directory: {session_dir.name}")
            return dest_path

        except Exception as e:
            logger.error(f"Failed to archive session directory {session_dir}: {e}")
            return None

    def rotate_all(self) -> dict[str, Any]:
        """Perform all log rotation checks and rotations.

        Called by the PA periodically (e.g., daily via scheduler).

        Returns:
            A dict summarising rotations performed:
            {
                "audit_rotated": bool,
                "events_rotated": bool,
                "tokens_rotated": bool,
                "sessions_archived": int,
                "archive_size_bytes": int,
                "archive_size_threshold_exceeded": bool,
            }
        """
        result = {
            "audit_rotated": False,
            "events_rotated": False,
            "tokens_rotated": False,
            "sessions_archived": 0,
            "archive_size_bytes": 0,
            "archive_size_threshold_exceeded": False,
        }

        # Rotate audit log
        if self.rotate_audit_log():
            result["audit_rotated"] = True

        # Rotate event log
        if self.rotate_event_log():
            result["events_rotated"] = True

        # Rotate token log
        if self.rotate_token_log():
            result["tokens_rotated"] = True

        # Archive old session logs
        archived_sessions = self.rotate_session_logs()
        result["sessions_archived"] = len(archived_sessions)

        # Check archive size
        archive_size = self.get_archive_size()
        result["archive_size_bytes"] = archive_size
        result["archive_size_threshold_exceeded"] = (
            archive_size > self.archive_size_threshold
        )

        if result["archive_size_threshold_exceeded"]:
            logger.warning(
                f"Archive size ({archive_size / (1024*1024*1024):.2f}GB) exceeds "
                f"threshold ({self.archive_size_threshold / (1024*1024*1024):.2f}GB)"
            )

        return result

    def get_archive_size(self) -> int:
        """Calculate the total size of the archive directory.

        Returns:
            Total size in bytes.
        """
        if not self.archive_dir.exists():
            return 0

        total_size = 0
        try:
            for file_path in self.archive_dir.rglob("*"):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
        except OSError as e:
            logger.error(f"Error calculating archive size: {e}")
        return total_size

    def get_archive_size_gb(self) -> float:
        """Get the archive size in gigabytes.

        Returns:
            Archive size in GB, rounded to 2 decimal places.
        """
        return round(self.get_archive_size() / (1024 * 1024 * 1024), 2)

    def is_archive_threshold_exceeded(self) -> bool:
        """Check if the archive size exceeds the configured threshold.

        Returns:
            True if archive size > threshold, False otherwise.
        """
        return self.get_archive_size() > self.archive_size_threshold

    @staticmethod
    def from_system_config(
        logs_dir: Path,
        system_config: dict[str, Any],
    ) -> "LogRotator":
        """Factory method to create a LogRotator from .faith/system.yaml config.

        Reads the log retention settings from system.yaml.

        Expected system.yaml structure:
        ```yaml
        log_retention_days: 90
        session_retention_days: 365
        archive_size_threshold_gb: 1.0
        ```

        Args:
            logs_dir: Path to the logs/ directory.
            system_config: Parsed .faith/system.yaml as a dict.

        Returns:
            Configured LogRotator instance.
        """
        log_retention_days = system_config.get(
            "log_retention_days", DEFAULT_LOG_RETENTION_DAYS
        )
        session_retention_days = system_config.get(
            "session_retention_days", DEFAULT_SESSION_RETENTION_DAYS
        )

        # Archive threshold can be specified in GB
        threshold_gb = system_config.get(
            "archive_size_threshold_gb",
            DEFAULT_ARCHIVE_SIZE_THRESHOLD / (1024 * 1024 * 1024),
        )
        archive_size_threshold = int(threshold_gb * 1024 * 1024 * 1024)

        rotator = LogRotator(
            logs_dir=logs_dir,
            log_retention_days=log_retention_days,
            session_retention_days=session_retention_days,
            archive_size_threshold=archive_size_threshold,
        )
        return rotator

    def __repr__(self) -> str:
        return (
            f"LogRotator(logs_dir={self.logs_dir}, "
            f"log_retention={self.log_retention_days}d, "
            f"session_retention={self.session_retention_days}d)"
        )
```

### 3. `tests/test_log_rotator.py`

```python
"""Tests for the FAITH log rotation system.

Covers log age detection, file archival, session directory rotation,
archive size monitoring, factory method, and integration with system config.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from faith.logging.log_rotator import (
    LogRotator,
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_SESSION_RETENTION_DAYS,
    DEFAULT_ARCHIVE_SIZE_THRESHOLD,
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
def rotator(logs_dir):
    """Create a LogRotator instance."""
    return LogRotator(logs_dir=logs_dir)


@pytest.fixture
def rotator_short_retention(logs_dir):
    """Create a LogRotator with short retention for testing."""
    return LogRotator(
        logs_dir=logs_dir,
        log_retention_days=0,
        session_retention_days=0,
    )


def create_aged_file(path: Path, age_days: int) -> None:
    """Create a file and set its mtime to age_days in the past."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test content\n")

    old_time = time.time() - (age_days * 86400)
    import os
    os.utime(path, (old_time, old_time))


def create_session_dir(
    sessions_dir: Path, session_id: str, age_days: int
) -> Path:
    """Create a session directory with session.meta.json."""
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # Set session start time to age_days ago
    started = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    meta = {"session_id": session_id, "started": started}

    meta_path = session_dir / "session.meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)

    # Set directory mtime to match
    old_time = time.time() - (age_days * 86400)
    import os
    os.utime(session_dir, (old_time, old_time))

    return session_dir


# ──────────────────────────────────────────────────
# Initialisation tests
# ──────────────────────────────────────────────────


def test_creates_directories(tmp_path):
    """LogRotator creates logs/ and logs/archive/ if missing."""
    logs_dir = tmp_path / "nonexistent" / "logs"
    rotator = LogRotator(logs_dir=logs_dir)
    assert logs_dir.exists()
    assert (logs_dir / "archive").exists()


def test_init_with_custom_retention(logs_dir):
    """LogRotator accepts custom retention periods."""
    rotator = LogRotator(
        logs_dir=logs_dir,
        log_retention_days=180,
        session_retention_days=730,
    )
    assert rotator.log_retention_days == 180
    assert rotator.session_retention_days == 730


# ──────────────────────────────────────────────────
# File age detection tests
# ──────────────────────────────────────────────────


def test_get_file_age_days(logs_dir):
    """File age is calculated correctly."""
    rotator = LogRotator(logs_dir=logs_dir)
    test_file = logs_dir / "test.log"
    create_aged_file(test_file, 30)

    age = rotator._get_file_age_days(test_file)
    assert age == 30


def test_get_file_age_days_young_file(logs_dir):
    """Young files report age as 0 or 1 day."""
    rotator = LogRotator(logs_dir=logs_dir)
    test_file = logs_dir / "test.log"
    test_file.write_text("new")

    age = rotator._get_file_age_days(test_file)
    assert age <= 1


# ──────────────────────────────────────────────────
# Audit log rotation tests
# ──────────────────────────────────────────────────


def test_rotate_audit_log_no_file(rotator):
    """rotate_audit_log returns None if file doesn't exist."""
    result = rotator.rotate_audit_log()
    assert result is None


def test_rotate_audit_log_young_file(rotator, logs_dir):
    """Young audit log is not rotated."""
    audit_log = logs_dir / "audit.log"
    audit_log.write_text("entry 1\nentry 2\n")

    result = rotator.rotate_audit_log()
    assert result is None
    assert audit_log.exists()


def test_rotate_audit_log_old_file(rotator_short_retention, logs_dir):
    """Old audit log is rotated and archived."""
    audit_log = logs_dir / "audit.log"
    create_aged_file(audit_log, 100)

    result = rotator_short_retention.rotate_audit_log()
    assert result is not None
    assert result.exists()
    assert not audit_log.exists()
    assert result.parent == logs_dir / "archive"
    assert "audit" in result.name
    assert result.name.endswith(".log")


# ──────────────────────────────────────────────────
# Event log rotation tests
# ──────────────────────────────────────────────────


def test_rotate_event_log_no_file(rotator):
    """rotate_event_log returns None if file doesn't exist."""
    result = rotator.rotate_event_log()
    assert result is None


def test_rotate_event_log_old_file(rotator_short_retention, logs_dir):
    """Old event log is rotated and archived."""
    event_log = logs_dir / "events.log"
    create_aged_file(event_log, 100)

    result = rotator_short_retention.rotate_event_log()
    assert result is not None
    assert not event_log.exists()
    assert result.parent == logs_dir / "archive"


# ──────────────────────────────────────────────────
# Token log rotation tests
# ──────────────────────────────────────────────────


def test_rotate_token_log_no_file(rotator):
    """rotate_token_log returns None if file doesn't exist."""
    result = rotator.rotate_token_log()
    assert result is None


def test_rotate_token_log_old_file(rotator_short_retention, logs_dir):
    """Old token log is rotated and archived."""
    token_log = logs_dir / "tokens.log"
    create_aged_file(token_log, 100)

    result = rotator_short_retention.rotate_token_log()
    assert result is not None
    assert not token_log.exists()
    assert result.parent == logs_dir / "archive"


# ──────────────────────────────────────────────────
# Session log rotation tests
# ──────────────────────────────────────────────────


def test_rotate_session_logs_empty(rotator):
    """rotate_session_logs returns empty list when no sessions exist."""
    result = rotator.rotate_session_logs()
    assert result == []


def test_rotate_session_logs_young_sessions(rotator, logs_dir):
    """Young session directories are not rotated."""
    sessions_dir = logs_dir / "sessions"
    sessions_dir.mkdir()
    create_session_dir(sessions_dir, "sess-001", age_days=10)

    result = rotator.rotate_session_logs()
    assert len(result) == 0
    assert (sessions_dir / "sess-001").exists()


def test_rotate_session_logs_old_sessions(rotator_short_retention, logs_dir):
    """Old session directories are rotated."""
    sessions_dir = logs_dir / "sessions"
    sessions_dir.mkdir()
    create_session_dir(sessions_dir, "sess-001", age_days=100)
    create_session_dir(sessions_dir, "sess-002", age_days=100)

    result = rotator_short_retention.rotate_session_logs()
    assert len(result) == 2
    assert not (sessions_dir / "sess-001").exists()
    assert not (sessions_dir / "sess-002").exists()

    # Sessions are archived under a timestamped parent
    archived_paths = list((logs_dir / "archive").glob("sessions_*/*"))
    assert len(archived_paths) == 2


def test_rotate_session_logs_mixed_ages(rotator, logs_dir):
    """Only old session directories are rotated."""
    sessions_dir = logs_dir / "sessions"
    sessions_dir.mkdir()
    create_session_dir(sessions_dir, "sess-old", age_days=400)
    create_session_dir(sessions_dir, "sess-young", age_days=100)

    rotator_custom = LogRotator(logs_dir=logs_dir, session_retention_days=365)
    result = rotator_custom.rotate_session_logs()

    assert len(result) == 1
    assert not (sessions_dir / "sess-old").exists()
    assert (sessions_dir / "sess-young").exists()


# ──────────────────────────────────────────────────
# Archive size monitoring tests
# ──────────────────────────────────────────────────


def test_get_archive_size_empty(rotator):
    """Empty archive reports size as 0."""
    size = rotator.get_archive_size()
    assert size == 0


def test_get_archive_size_with_files(rotator, logs_dir):
    """Archive size is calculated correctly."""
    archive = logs_dir / "archive"
    (archive / "file1.log").write_text("x" * 1000)
    (archive / "file2.log").write_text("y" * 2000)

    size = rotator.get_archive_size()
    assert size == 3000


def test_get_archive_size_nested(rotator, logs_dir):
    """Archive size includes nested files."""
    archive = logs_dir / "archive"
    (archive / "subdir").mkdir()
    (archive / "subdir" / "file.log").write_text("x" * 5000)

    size = rotator.get_archive_size()
    assert size == 5000


def test_get_archive_size_gb(rotator, logs_dir):
    """Archive size can be reported in GB."""
    archive = logs_dir / "archive"
    # Create a file that's 1.5GB worth of bytes
    (archive / "large.log").write_bytes(b"x" * (int(1.5 * 1024 * 1024 * 1024)))

    size_gb = rotator.get_archive_size_gb()
    assert 1.4 < size_gb < 1.6


def test_archive_threshold_not_exceeded(rotator, logs_dir):
    """is_archive_threshold_exceeded returns False when under threshold."""
    archive = logs_dir / "archive"
    (archive / "file.log").write_text("x" * 1000)

    assert not rotator.is_archive_threshold_exceeded()


def test_archive_threshold_exceeded(rotator, logs_dir):
    """is_archive_threshold_exceeded returns True when over threshold."""
    rotator_small = LogRotator(
        logs_dir=logs_dir, archive_size_threshold=1000
    )
    archive = logs_dir / "archive"
    (archive / "file.log").write_bytes(b"x" * 2000)

    assert rotator_small.is_archive_threshold_exceeded()


# ──────────────────────────────────────────────────
# rotate_all integration tests
# ──────────────────────────────────────────────────


def test_rotate_all_empty_logs(rotator):
    """rotate_all handles missing log files gracefully."""
    result = rotator.rotate_all()
    assert result["audit_rotated"] is False
    assert result["events_rotated"] is False
    assert result["tokens_rotated"] is False
    assert result["sessions_archived"] == 0
    assert result["archive_size_bytes"] == 0
    assert result["archive_size_threshold_exceeded"] is False


def test_rotate_all_mixed(rotator_short_retention, logs_dir):
    """rotate_all rotates all applicable logs in one call."""
    # Create aged audit, event, and token logs
    create_aged_file(logs_dir / "audit.log", 100)
    create_aged_file(logs_dir / "events.log", 100)
    create_aged_file(logs_dir / "tokens.log", 100)

    # Create old sessions
    sessions_dir = logs_dir / "sessions"
    sessions_dir.mkdir()
    create_session_dir(sessions_dir, "sess-001", age_days=100)

    result = rotator_short_retention.rotate_all()

    assert result["audit_rotated"] is True
    assert result["events_rotated"] is True
    assert result["tokens_rotated"] is True
    assert result["sessions_archived"] == 1
    assert result["archive_size_bytes"] > 0
    assert result["archive_size_threshold_exceeded"] is False


def test_rotate_all_threshold_exceeded(logs_dir):
    """rotate_all detects when archive exceeds threshold."""
    rotator = LogRotator(
        logs_dir=logs_dir,
        archive_size_threshold=100,  # 100 bytes threshold
    )
    archive = logs_dir / "archive"
    (archive / "file.log").write_bytes(b"x" * 200)  # 200 bytes

    result = rotator.rotate_all()
    assert result["archive_size_threshold_exceeded"] is True


# ──────────────────────────────────────────────────
# Factory method tests
# ──────────────────────────────────────────────────


def test_from_system_config_defaults(logs_dir):
    """Factory method uses defaults when config is empty."""
    rotator = LogRotator.from_system_config(logs_dir, {})
    assert rotator.log_retention_days == DEFAULT_LOG_RETENTION_DAYS
    assert rotator.session_retention_days == DEFAULT_SESSION_RETENTION_DAYS


def test_from_system_config_custom_values(logs_dir):
    """Factory method reads retention values from system config."""
    config = {
        "log_retention_days": 180,
        "session_retention_days": 730,
        "archive_size_threshold_gb": 2.0,
    }
    rotator = LogRotator.from_system_config(logs_dir, config)
    assert rotator.log_retention_days == 180
    assert rotator.session_retention_days == 730
    assert rotator.archive_size_threshold == int(2.0 * 1024 * 1024 * 1024)


# ──────────────────────────────────────────────────
# Timestamp format tests
# ──────────────────────────────────────────────────


def test_archive_filename_timestamp_format(rotator_short_retention, logs_dir):
    """Archived files have correctly formatted timestamp suffixes."""
    audit_log = logs_dir / "audit.log"
    create_aged_file(audit_log, 100)

    archive_path = rotator_short_retention.rotate_audit_log()
    assert archive_path is not None

    # Filename should be audit_YYYYMMDD_HHMMSS.log
    assert archive_path.name.startswith("audit_")
    assert archive_path.name.endswith(".log")
    parts = archive_path.stem.split("_")
    assert len(parts) == 3
    assert len(parts[1]) == 8  # YYYYMMDD
    assert len(parts[2]) == 6  # HHMMSS


# ──────────────────────────────────────────────────
# Error handling tests
# ──────────────────────────────────────────────────


def test_missing_session_metadata(rotator, logs_dir):
    """Missing session.meta.json is logged and skipped."""
    sessions_dir = logs_dir / "sessions"
    sessions_dir.mkdir()
    sess_dir = sessions_dir / "sess-001"
    sess_dir.mkdir()
    # Intentionally don't create session.meta.json

    result = rotator.rotate_session_logs()
    assert len(result) == 0


def test_malformed_session_metadata(rotator, logs_dir):
    """Malformed session.meta.json is logged and skipped."""
    sessions_dir = logs_dir / "sessions"
    sessions_dir.mkdir()
    sess_dir = sessions_dir / "sess-001"
    sess_dir.mkdir()

    # Write invalid JSON
    meta_path = sess_dir / "session.meta.json"
    meta_path.write_text("not valid json")

    result = rotator.rotate_session_logs()
    assert len(result) == 0
    assert sess_dir.exists()  # Session was not deleted


def test_inaccessible_archive_directory(tmp_path):
    """Error on inaccessible archive directory is logged."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    rotator = LogRotator(logs_dir=logs_dir)

    # Make archive directory inaccessible (Unix only)
    import sys
    import os
    if sys.platform != "win32":
        os.chmod(logs_dir / "archive", 0o000)
        try:
            # This should log an error but not raise
            size = rotator.get_archive_size()
            assert size == 0
        finally:
            # Restore permissions for cleanup
            os.chmod(logs_dir / "archive", 0o755)
```

---

## Integration Points

The LogRotator is called by the PA on a scheduled basis (e.g., daily via FAITH scheduler):

```python
# PA startup or periodic scheduler trigger:
from faith.logging.log_rotator import LogRotator
import yaml
from pathlib import Path

system_yaml = Path(".faith/system.yaml")
system_config = yaml.safe_load(system_yaml.read_text()) if system_yaml.exists() else {}

rotator = LogRotator.from_system_config(
    logs_dir=Path("logs"),
    system_config=system_config,
)

# Perform rotation check
result = rotator.rotate_all()

# If archive size exceeds threshold, notify user via Web UI:
if result["archive_size_threshold_exceeded"]:
    pa.publish_user_notification(
        title="Archive Size Warning",
        message=f"Log archive has reached {result['archive_size_gb']:.2f}GB. "
                f"Consider manual cleanup in logs/archive/.",
        severity="warning",
    )
```

The LogRotator integrates with existing log writer infrastructure:

- `AuditLogger` (FAITH-021) — rotator archives `audit.log`
- `EventLogWriter` (FAITH-045) — rotator archives `events.log`
- `TokenLogWriter` (FAITH-047) — rotator archives `tokens.log`
- `SessionLogWriter` (FAITH-046) — rotator archives `.faith/sessions/` directories

No changes to the log writers themselves are needed; the rotator is independent.

---

## Acceptance Criteria

1. `LogRotator` accepts `log_retention_days`, `session_retention_days`, and `archive_size_threshold` parameters from system config (`.faith/system.yaml`).
2. `LogRotator` detects file age using file modification time (cross-platform via `datetime.fromtimestamp`).
3. `rotate_audit_log()` archives `logs/audit.log` if older than `log_retention_days`; returns archived path or None.
4. `rotate_event_log()` archives `logs/events.log` if older than `log_retention_days`; returns archived path or None.
5. `rotate_token_log()` archives `logs/tokens.log` if older than `log_retention_days`; returns archived path or None.
6. `rotate_session_logs()` archives session directories from `.faith/sessions/` if the session start time (from `session.meta.json`) is older than `session_retention_days`; returns list of archived paths.
7. Session directories are grouped under a timestamped parent directory (`sessions_YYYYMMDD_HHMMSS/`) during archival to preserve structure.
8. Archived files are moved (never copied) to `logs/archive/` with a timestamp suffix in the filename (e.g., `audit_20260325_143201.log`).
9. `get_archive_size()` calculates total archive size in bytes; `get_archive_size_gb()` returns size in GB.
10. `is_archive_threshold_exceeded()` returns True when archive size exceeds the configured threshold.
11. `rotate_all()` performs all rotations (audit, event, token, session) and returns a summary dict with `archive_size_threshold_exceeded` flag.
12. `from_system_config()` factory method reads retention values from the parsed `.faith/system.yaml` dict, with sensible defaults (90 days for logs, 365 days for sessions, 1GB threshold).
13. Malformed session metadata is skipped with a logged warning; the session directory is not deleted.
14. All 35 tests in `tests/test_log_rotator.py` pass, covering age detection, file archival, session rotation, archive size monitoring, factory method, and error handling.
15. No logs are ever automatically deleted — only moved to `logs/archive/`.

---

## Notes for Implementer

- **No automatic deletion**: Archived logs are human-readable and may be valuable for long-term compliance or audit. Users can manually delete from `logs/archive/` if desired. Never implement auto-delete.
- **Timestamp precision**: Archived files include YYYYMMDD_HHMMSS in the filename. This makes it easy for users to identify when logs were rotated. The timestamp is generated at rotation time, not from the file's original mtime.
- **Session age from metadata**: Session directories are rotated based on the `started` timestamp in `session.meta.json`, not the directory's file mtime. This is because session directories can be touched by tool operations even after the session is complete. The `started` field is the source of truth.
- **ISO 8601 parsing**: Session timestamps are ISO 8601 strings with optional 'Z' suffix. Handle both formats: `2026-03-23T14:30:00Z` and `2026-03-23T14:30:00+00:00`.
- **Archive size threshold notification**: When the PA detects the threshold is exceeded, it should surface a user-friendly notification in the Web UI (via WebSocket) with the current archive size and a suggestion to clean up. This is a non-blocking, informational notification.
- **Periodic scheduling**: The PA should call `LogRotator.rotate_all()` on a schedule (e.g., daily via FAITH-051 scheduler). The default system.yaml can include a scheduler entry like:
  ```yaml
  scheduled_tasks:
    - name: log_rotation
      cron: "0 2 * * *"  # Daily at 2 AM
      task: rotate_logs
  ```
- **Thread safety**: The `LogRotator` is designed for single-threaded use by the PA. If concurrent log operations are needed, add a lock or queue.
- **Cross-platform paths**: Use `Path` and forward slashes throughout; avoid platform-specific path separators.
- **Test coverage**: The test suite includes fixtures for temporary directories, aged files, and session metadata. Tests cover happy paths, edge cases (missing files, malformed metadata), and integration scenarios.
