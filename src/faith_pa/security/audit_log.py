"""Append-only audit logging for FAITH."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_RETENTION_DAYS = 90
_audit_counter = 0


def _next_audit_id() -> str:
    global _audit_counter
    _audit_counter += 1
    if _audit_counter > 99999:
        _audit_counter = 1
    return f"aud-{_audit_counter:05d}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditEntry(BaseModel):
    ts: str = Field(default_factory=_now_iso)
    agent: str
    tool: str
    action: str
    target: str
    approval_tier: str | None = None
    rule_matched: str | None = None
    decision: str = "approved"
    channel: str | None = None
    msg_id: int | None = None
    audit_id: str = Field(default_factory=_next_audit_id)

    def to_json_line(self) -> str:
        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_json_line(cls, line: str) -> AuditEntry:
        return cls.model_validate_json(line.strip())


class AuditLogger:
    def __init__(self, logs_dir: Path, retention_days: int = DEFAULT_RETENTION_DAYS):
        self.logs_dir = Path(logs_dir)
        self.log_path = self.logs_dir / "audit.log"
        self.archive_dir = self.logs_dir / "archive"
        self.retention_days = retention_days
        self._file = None
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def open(self) -> None:
        if self._file is None:
            self._file = self.log_path.open("a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None

    def _ensure_open(self) -> None:
        if self._file is None or self._file.closed:
            self.open()

    def write(self, entry: AuditEntry) -> None:
        self._ensure_open()
        self._file.write(entry.to_json_line() + "\n")
        self._file.flush()

    def log_tool_operation(
        self,
        *,
        agent: str,
        tool: str,
        action: str,
        target: str,
        approval_tier: str | None = None,
        rule_matched: str | None = None,
        decision: str = "approved",
        channel: str | None = None,
        msg_id: int | None = None,
    ) -> AuditEntry:
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

    def log_approval_decision(self, **kwargs) -> AuditEntry:
        return self.log_tool_operation(**kwargs)

    def log_container_lifecycle(
        self, *, agent: str, action: str, target: str, channel: str | None = None
    ) -> AuditEntry:
        return self.log_tool_operation(
            agent=agent, tool="container", action=action, target=target, channel=channel
        )

    def log_file_restoration(
        self,
        *,
        agent: str,
        target: str,
        channel: str | None = None,
        msg_id: int | None = None,
    ) -> AuditEntry:
        return self.log_tool_operation(
            agent=agent,
            tool="filesystem",
            action="restore",
            target=target,
            approval_tier="allow_once",
            channel=channel,
            msg_id=msg_id,
        )

    def rotate_if_needed(self) -> Path | None:
        if not self.log_path.exists():
            return None

        age_days = (
            datetime.now(timezone.utc)
            - datetime.fromtimestamp(self.log_path.stat().st_mtime, tz=timezone.utc)
        ).days
        if age_days < self.retention_days:
            return None

        self.close()
        archive_name = f"audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
        archive_path = self.archive_dir / archive_name
        shutil.move(str(self.log_path), str(archive_path))
        self.open()
        return archive_path

    def read_entries(self, *, limit: int = 100, offset: int = 0) -> list[AuditEntry]:
        if not self.log_path.exists():
            return []
        entries: list[AuditEntry] = []
        with self.log_path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index < offset:
                    continue
                if len(entries) >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(AuditEntry.from_json_line(line))
                except Exception:
                    continue
        return entries

    def query(
        self,
        *,
        agent: str | None = None,
        tool: str | None = None,
        action: str | None = None,
        decision: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        entries = self.read_entries(limit=100000, offset=0)
        filtered = [
            entry
            for entry in entries
            if (agent is None or entry.agent == agent)
            and (tool is None or entry.tool == tool)
            and (action is None or entry.action == action)
            and (decision is None or entry.decision == decision)
        ]
        filtered.reverse()
        return filtered[:limit]

    @classmethod
    def from_system_config(cls, logs_dir: Path, system_config: dict) -> AuditLogger:
        retention_days = system_config.get("audit", {}).get(
            "retention_days", DEFAULT_RETENTION_DAYS
        )
        return cls(logs_dir=logs_dir, retention_days=retention_days)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
