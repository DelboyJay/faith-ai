"""Description:
    Apply retention and archival rules to FAITH logs and session history.

Requirements:
    - Archive aged audit, event, and token logs instead of deleting them.
    - Archive aged persisted session directories into the active logs archive.
    - Report whether archive size has exceeded the configured threshold.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOG_RETENTION_DAYS = 90
DEFAULT_SESSION_RETENTION_DAYS = 365
DEFAULT_ARCHIVE_SIZE_THRESHOLD_BYTES = 1024 * 1024 * 1024


class LogRotator:
    """Description:
        Archive aged FAITH runtime logs and session histories.

    Requirements:
        - Treat `audit.log`, `events.log`, and `tokens.log` as active logs.
        - Archive sessions using the `started` timestamp from `session.meta.json`.
        - Never delete data automatically.

    :param logs_dir: Directory containing active log files.
    :param session_root: Root directory containing `.faith/sessions`.
    :param log_retention_days: Active-log archival age threshold in days.
    :param session_retention_days: Session archival age threshold in days.
    :param archive_size_threshold_bytes: Archive-size warning threshold in bytes.
    """

    def __init__(
        self,
        *,
        logs_dir: Path,
        session_root: Path,
        log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS,
        session_retention_days: int = DEFAULT_SESSION_RETENTION_DAYS,
        archive_size_threshold_bytes: int = DEFAULT_ARCHIVE_SIZE_THRESHOLD_BYTES,
    ) -> None:
        """Description:
            Initialise the log rotator.

        Requirements:
            - Create the log archive directory eagerly.

        :param logs_dir: Directory containing active log files.
        :param session_root: Root directory containing `.faith/sessions`.
        :param log_retention_days: Active-log archival age threshold in days.
        :param session_retention_days: Session archival age threshold in days.
        :param archive_size_threshold_bytes: Archive-size warning threshold in bytes.
        """

        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir = self.logs_dir / "archive"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.session_root = Path(session_root)
        self.log_retention_days = log_retention_days
        self.session_retention_days = session_retention_days
        self.archive_size_threshold_bytes = archive_size_threshold_bytes

    def rotate_all(self) -> dict[str, Any]:
        """Description:
            Rotate all eligible logs and session trees in one pass.

        Requirements:
            - Return a structured summary describing the archival work completed.

        :returns: Rotation summary payload.
        """

        audit_rotated = self.rotate_named_log("audit.log") is not None
        events_rotated = self.rotate_named_log("events.log") is not None
        tokens_rotated = self.rotate_named_log("tokens.log") is not None
        archived_sessions = self.rotate_sessions()
        archive_size = self.get_archive_size()
        return {
            "audit_rotated": audit_rotated,
            "events_rotated": events_rotated,
            "tokens_rotated": tokens_rotated,
            "sessions_archived": len(archived_sessions),
            "archive_size_bytes": archive_size,
            "archive_size_threshold_exceeded": archive_size > self.archive_size_threshold_bytes,
        }

    def rotate_named_log(self, file_name: str) -> Path | None:
        """Description:
            Archive one named active log when it exceeds the age threshold.

        Requirements:
            - Return `None` when the file is missing or still within retention.

        :param file_name: Active log filename to rotate.
        :returns: Archived path, if the file was moved.
        """

        log_path = self.logs_dir / file_name
        if not log_path.exists():
            return None
        age_days = self._file_age_days(log_path)
        if age_days < self.log_retention_days:
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_name = f"{log_path.stem}_{timestamp}{log_path.suffix}"
        archive_path = self.archive_dir / archive_name
        shutil.move(str(log_path), str(archive_path))
        return archive_path

    def rotate_sessions(self) -> list[Path]:
        """Description:
            Archive aged session directories from the persisted session root.

        Requirements:
            - Use the `started` timestamp from `session.meta.json`.
            - Group one rotation pass under a timestamped `sessions_*` archive directory.

        :returns: Archived session directory paths.
        """

        if self.session_root.name == "sessions":
            sessions_dir = self.session_root
        else:
            sessions_dir = self.session_root / ".faith" / "sessions"
        if not sessions_dir.exists():
            return []
        to_archive: list[Path] = []
        for session_dir in sorted(path for path in sessions_dir.iterdir() if path.is_dir()):
            meta_path = session_dir / "session.meta.json"
            if not meta_path.exists():
                continue
            try:
                import json

                data = json.loads(meta_path.read_text(encoding="utf-8"))
                started = str(data["started"]).replace("Z", "+00:00")
                started_at = datetime.fromisoformat(started)
            except Exception:
                continue
            age_days = (datetime.now(timezone.utc) - started_at).days
            if age_days >= self.session_retention_days:
                to_archive.append(session_dir)
        if not to_archive:
            return []
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_parent = self.archive_dir / f"sessions_{timestamp}"
        archive_parent.mkdir(parents=True, exist_ok=True)
        archived_paths: list[Path] = []
        for session_dir in to_archive:
            archive_path = archive_parent / session_dir.name
            shutil.move(str(session_dir), str(archive_path))
            archived_paths.append(archive_path)
        return archived_paths

    def get_archive_size(self) -> int:
        """Description:
            Return the total byte size of the archive directory.

        Requirements:
            - Include nested archived files.

        :returns: Archive size in bytes.
        """

        if not self.archive_dir.exists():
            return 0
        return sum(path.stat().st_size for path in self.archive_dir.rglob("*") if path.is_file())

    @classmethod
    def from_system_config(
        cls,
        *,
        logs_dir: Path,
        session_root: Path,
        system_config: dict[str, Any],
    ) -> LogRotator:
        """Description:
            Build a log rotator from the parsed system configuration payload.

        Requirements:
            - Honour log retention, session retention, and archive-size threshold overrides.

        :param logs_dir: Directory containing active log files.
        :param session_root: Root directory containing `.faith/sessions`.
        :param system_config: Parsed system configuration payload.
        :returns: Configured log rotator instance.
        """

        threshold_gb = float(system_config.get("archive_size_threshold_gb", 1))
        return cls(
            logs_dir=logs_dir,
            session_root=session_root,
            log_retention_days=int(
                system_config.get("log_retention_days", DEFAULT_LOG_RETENTION_DAYS)
            ),
            session_retention_days=int(
                system_config.get("session_retention_days", DEFAULT_SESSION_RETENTION_DAYS)
            ),
            archive_size_threshold_bytes=int(threshold_gb * 1024 * 1024 * 1024),
        )

    @staticmethod
    def _file_age_days(path: Path) -> int:
        """Description:
            Return the age of one file in whole days.

        Requirements:
            - Use file modification time as the active-log age source.

        :param path: File path to inspect.
        :returns: Whole-day age of the file.
        """

        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - modified_at).days
