"""Description:
    Verify FAITH log retention and rotation helpers.

Requirements:
    - Prove aged active logs are archived instead of deleted.
    - Prove aged session directories are archived and archive-size thresholds are reported.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faith_pa.logging.log_rotator import LogRotator


def _create_aged_file(path: Path, *, age_days: int) -> None:
    """Description:
        Create a file whose modification time is set in the past.

    Requirements:
        - Write a placeholder body before adjusting the file timestamp.

    :param path: File path to create.
    :param age_days: Age of the file in whole days.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test\n", encoding="utf-8")
    old_time = time.time() - (age_days * 86400)
    os.utime(path, (old_time, old_time))


def _create_aged_session(path: Path, *, age_days: int) -> None:
    """Description:
        Create a persisted session directory with an aged ``session.meta.json`` file.

    Requirements:
        - Store the canonical ``started`` timestamp used by retention logic.

    :param path: Session directory path to create.
    :param age_days: Age of the session in whole days.
    """

    path.mkdir(parents=True, exist_ok=True)
    started = (datetime.now(timezone.utc) - timedelta(days=age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (path / "session.meta.json").write_text(
        json.dumps({"session_id": path.name, "started": started}, indent=2),
        encoding="utf-8",
    )


def test_log_rotator_archives_old_logs_and_sessions(tmp_path: Path) -> None:
    """Description:
        Verify the log rotator archives aged active logs and aged session directories.

    Requirements:
        - This test is needed to prove Phase 9 retention moves logs into ``logs/archive`` instead of deleting them.
        - Verify audit, event, and token logs rotate alongside old session trees when thresholds are exceeded.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logs_dir = tmp_path / "logs"
    sessions_dir = tmp_path / ".faith" / "sessions"
    _create_aged_file(logs_dir / "audit.log", age_days=120)
    _create_aged_file(logs_dir / "events.log", age_days=120)
    _create_aged_file(logs_dir / "tokens.log", age_days=120)
    _create_aged_session(sessions_dir / "sess-0001-2026-01-01", age_days=500)

    rotator = LogRotator(
        logs_dir=logs_dir,
        session_root=sessions_dir,
        log_retention_days=90,
        session_retention_days=365,
        archive_size_threshold_bytes=1,
    )

    summary = rotator.rotate_all()

    assert summary["audit_rotated"] is True
    assert summary["events_rotated"] is True
    assert summary["tokens_rotated"] is True
    assert summary["sessions_archived"] == 1
    assert summary["archive_size_threshold_exceeded"] is True
    archived_names = sorted(path.name for path in (logs_dir / "archive").iterdir())
    assert any(name.startswith("audit_") for name in archived_names)
    assert any(name.startswith("events_") for name in archived_names)
    assert any(name.startswith("tokens_") for name in archived_names)
    assert any(name.startswith("sessions_") for name in archived_names)


def test_log_rotator_reads_thresholds_from_system_config(tmp_path: Path) -> None:
    """Description:
        Verify the log rotator reads retention thresholds from system config.

    Requirements:
        - This test is needed to prove the retention policy can be configured centrally from ``system.yaml``.
        - Verify log retention, session retention, and archive-size threshold values all honour config overrides.

    :param tmp_path: Temporary pytest directory fixture.
    """

    rotator = LogRotator.from_system_config(
        logs_dir=tmp_path / "logs",
        session_root=tmp_path / ".faith" / "sessions",
        system_config={
            "log_retention_days": 30,
            "session_retention_days": 730,
            "archive_size_threshold_gb": 2,
        },
    )

    assert rotator.log_retention_days == 30
    assert rotator.session_retention_days == 730
    assert rotator.archive_size_threshold_bytes == 2 * 1024 * 1024 * 1024
