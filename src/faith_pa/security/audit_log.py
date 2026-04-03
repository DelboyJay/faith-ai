"""Description:
    Write append-only audit entries for FAITH runtime operations and approval decisions.

Requirements:
    - Persist audit entries as newline-delimited JSON.
    - Support canonical approval-tier vocabulary from the FRS.
    - Support log rotation based on file age.
    - Provide simple read and query helpers for the Web UI and debugging workflows.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_RETENTION_DAYS = 90
CANONICAL_APPROVAL_TIERS = {
    "always_allow",
    "approve_session",
    "allow_once",
    "always_ask",
    "always_deny",
    "unattended",
    "unknown",
}
_audit_counter = 0


def _next_audit_id() -> str:
    """Description:
        Return the next sequential audit identifier.

    Requirements:
        - Cycle the counter back to ``1`` after the five-digit range is exhausted.

    :returns: Next audit identifier.
    """

    global _audit_counter
    _audit_counter += 1
    if _audit_counter > 99999:
        _audit_counter = 1
    return f"aud-{_audit_counter:05d}"


def _now_iso() -> str:
    """Description:
        Return the current UTC time in audit-log string format.

    Requirements:
        - Use a stable UTC timestamp format suitable for JSON logs.

    :returns: Current UTC timestamp string.
    """

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_approval_tier(approval_tier: str | None) -> str | None:
    """Description:
        Normalise approval-tier values to the canonical FRS vocabulary.

    Requirements:
        - Convert legacy permanent-deny wording to ``always_deny``.
        - Collapse unsupported one-off deny wording to ``unknown``.
        - Leave canonical values untouched.

    :param approval_tier: Raw approval tier value.
    :returns: Canonical approval tier value, or ``None`` when absent.
    """

    if approval_tier is None:
        return None
    if approval_tier == "deny_permanently":
        return "always_deny"
    if approval_tier == "deny_once":
        return "unknown"
    if approval_tier in CANONICAL_APPROVAL_TIERS:
        return approval_tier
    return "unknown"


class AuditEntry(BaseModel):
    """Description:
        Represent one persisted FAITH audit-log entry.

    Requirements:
        - Preserve timestamp, action, approval, and routing metadata.
        - Generate default timestamps and audit identifiers automatically.
    """

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
        """Description:
            Serialise the audit entry as one JSON-lines record.

        Requirements:
            - Exclude ``None`` values from the persisted payload.

        :returns: JSON-lines representation of the audit entry.
        """

        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_json_line(cls, line: str) -> AuditEntry:
        """Description:
            Parse one JSON-lines record into an ``AuditEntry``.

        Requirements:
            - Strip surrounding whitespace before validation.

        :param line: JSON-lines record to parse.
        :returns: Parsed audit entry.
        """

        return cls.model_validate_json(line.strip())


class AuditLogger:
    """Description:
        Append, rotate, and query the FAITH audit log.

    Requirements:
        - Create the log and archive directories on initialisation.
        - Keep the log file open lazily.
        - Support log rotation and filtered reads for UI and debugging workflows.

    :param logs_dir: Directory containing the active audit log.
    :param retention_days: Maximum age in days before the active log is rotated.
    """

    def __init__(self, logs_dir: Path, retention_days: int = DEFAULT_RETENTION_DAYS):
        """Description:
            Initialise the audit logger.

        Requirements:
            - Create the active log directory and archive directory when missing.
            - Start with the file handle closed.

        :param logs_dir: Directory containing the active audit log.
        :param retention_days: Maximum age in days before the active log is rotated.
        """

        self.logs_dir = Path(logs_dir)
        self.log_path = self.logs_dir / "audit.log"
        self.archive_dir = self.logs_dir / "archive"
        self.retention_days = retention_days
        self._file = None
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def open(self) -> None:
        """Description:
            Open the active audit log file for append if it is not already open.

        Requirements:
            - Use line buffering so entries are flushed promptly.
        """

        if self._file is None:
            self._file = self.log_path.open("a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        """Description:
            Close the active audit log file when it is open.

        Requirements:
            - Flush pending data before closing.
            - Reset the stored file handle after close.
        """

        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None

    def _ensure_open(self) -> None:
        """Description:
            Ensure the active audit log file handle is open.

        Requirements:
            - Re-open the log file when the handle is missing or closed.
        """

        if self._file is None or self._file.closed:
            self.open()

    def write(self, entry: AuditEntry) -> None:
        """Description:
            Append one audit entry to the active log file.

        Requirements:
            - Ensure the log file is open before writing.
            - Flush the file after appending the entry.

        :param entry: Audit entry to append.
        """

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
        """Description:
            Create and write one audit entry for a tool operation.

        Requirements:
            - Preserve tool, action, target, approval, and routing metadata.
            - Normalise approval tiers to the canonical FRS vocabulary before writing.

        :param agent: Agent responsible for the operation.
        :param tool: Tool name involved in the operation.
        :param action: Action name being audited.
        :param target: Operation target string.
        :param approval_tier: Optional approval tier associated with the action.
        :param rule_matched: Optional approval rule that matched the action.
        :param decision: Final allow or deny outcome.
        :param channel: Optional channel associated with the action.
        :param msg_id: Optional message identifier associated with the action.
        :returns: Persisted audit entry.
        """

        entry = AuditEntry(
            agent=agent,
            tool=tool,
            action=action,
            target=target,
            approval_tier=_normalise_approval_tier(approval_tier),
            rule_matched=rule_matched,
            decision=decision,
            channel=channel,
            msg_id=msg_id,
        )
        self.write(entry)
        return entry

    def log_approval_decision(self, **kwargs) -> AuditEntry:
        """Description:
            Create and write one audit entry for an approval decision.

        Requirements:
            - Delegate to the general tool-operation logging path.

        :returns: Persisted audit entry.
        """

        return self.log_tool_operation(**kwargs)

    def log_container_lifecycle(
        self, *, agent: str, action: str, target: str, channel: str | None = None
    ) -> AuditEntry:
        """Description:
            Create and write one audit entry for a container lifecycle event.

        Requirements:
            - Record the tool name as ``container``.

        :param agent: Agent or runtime component responsible for the event.
        :param action: Lifecycle action name.
        :param target: Container target string.
        :param channel: Optional channel associated with the event.
        :returns: Persisted audit entry.
        """

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
        """Description:
            Create and write one audit entry for a filesystem restoration action.

        Requirements:
            - Record the tool as ``filesystem`` and the action as ``restore``.
            - Record the approval tier as ``allow_once``.

        :param agent: Agent responsible for the restoration.
        :param target: Restored path.
        :param channel: Optional channel associated with the action.
        :param msg_id: Optional message identifier associated with the action.
        :returns: Persisted audit entry.
        """

        return self.log_tool_operation(
            agent=agent,
            tool="filesystem",
            action="restore",
            target=target,
            approval_tier="allow_once",
            channel=channel,
            msg_id=msg_id,
        )

    async def record(self, *, action: str, sandbox_id: str, **_: object) -> AuditEntry:
        """Description:
            Write one audit entry through the async compatibility interface.

        Requirements:
            - Support PA components that expect an awaitable ``record`` method.
            - Record sandbox lifecycle entries under the ``sandbox`` tool name.

        :param action: Sandbox lifecycle action being audited.
        :param sandbox_id: Sandbox identifier used as the audit target.
        :returns: Persisted audit entry.
        """

        return self.log_tool_operation(
            agent="pa",
            tool="sandbox",
            action=action,
            target=sandbox_id,
        )

    def rotate_if_needed(self) -> Path | None:
        """Description:
            Rotate the active audit log when it exceeds the configured age threshold.

        Requirements:
            - Return ``None`` when there is no active log or rotation is not yet required.
            - Move the old log into the archive directory and reopen a fresh active log on rotation.

        :returns: Archive path for the rotated log, or ``None`` when no rotation occurred.
        """

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
        """Description:
            Read a slice of audit entries from the active log file.

        Requirements:
            - Skip malformed and blank lines without raising.
            - Honour the supplied offset and limit.

        :param limit: Maximum number of entries to return.
        :param offset: Number of entries to skip from the beginning of the log.
        :returns: Parsed audit entries.
        """

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
        """Description:
            Filter audit entries by common fields and return the newest matches first.

        Requirements:
            - Read the full active log before applying in-memory filters.
            - Return at most the configured number of entries.

        :param agent: Optional agent filter.
        :param tool: Optional tool filter.
        :param action: Optional action filter.
        :param decision: Optional decision filter.
        :param limit: Maximum number of matching entries to return.
        :returns: Filtered audit entries in reverse chronological order.
        """

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
        """Description:
            Build an audit logger from the system configuration payload.

        Requirements:
            - Honour the configured retention period when one is provided.
            - Fall back to the default retention period otherwise.

        :param logs_dir: Directory containing the active audit log.
        :param system_config: System configuration payload.
        :returns: Configured audit logger instance.
        """

        retention_days = system_config.get("audit", {}).get(
            "retention_days", DEFAULT_RETENTION_DAYS
        )
        return cls(logs_dir=logs_dir, retention_days=retention_days)

    def __enter__(self):
        """Description:
            Open the audit log when entering a context-manager block.

        Requirements:
            - Return the logger instance itself.

        :returns: Audit logger instance.
        """

        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Description:
            Close the audit log when exiting a context-manager block.

        Requirements:
            - Never suppress exceptions from the wrapped block.

        :param exc_type: Exception type raised inside the context, if any.
        :param exc_val: Exception instance raised inside the context, if any.
        :param exc_tb: Traceback raised inside the context, if any.
        :returns: ``False`` to avoid suppressing exceptions.
        """

        self.close()
        return False
