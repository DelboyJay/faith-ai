"""Description:
    Verify append-only audit logging, canonical approval tiers, and query behaviour.

Requirements:
    - Prove audit entries round-trip through JSON-lines serialisation.
    - Prove the logger writes, reads, filters, and rotates logs correctly.
    - Prove malformed lines are ignored safely.
    - Prove approval tiers are normalised to the canonical FRS vocabulary.
"""

from __future__ import annotations

import time

from faith_pa.security.audit_log import AuditEntry, AuditLogger


def test_audit_entry_round_trip():
    """Description:
    Verify audit entries round-trip through JSON-lines serialisation.

    Requirements:
        - This test is needed to prove persisted audit entries can be read back without losing key fields.
        - Verify the restored entry preserves the agent and tool values.
    """

    entry = AuditEntry(agent="dev", tool="filesystem", action="read", target="/repo/a.txt")
    restored = AuditEntry.from_json_line(entry.to_json_line())
    assert restored.agent == "dev"
    assert restored.tool == "filesystem"


def test_logger_writes_and_reads(tmp_path):
    """Description:
    Verify the audit logger writes entries and reads them back.

    Requirements:
        - This test is needed to prove the basic append-and-read path works.
        - Verify the stored approval tier is preserved.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = AuditLogger(tmp_path / "logs")
    logger.log_tool_operation(
        agent="dev", tool="python", action="execute", target="print(1)", approval_tier="allow_once"
    )
    entries = logger.read_entries()
    assert len(entries) == 1
    assert entries[0].approval_tier == "allow_once"


def test_logger_skips_malformed_lines(tmp_path):
    """Description:
    Verify malformed audit-log lines are skipped rather than raising errors.

    Requirements:
        - This test is needed to prove the audit reader is resilient to partial or corrupted log lines.
        - Verify valid lines after malformed ones are still returned.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = AuditLogger(tmp_path / "logs")
    logger.log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.log_path.write_text(
        "{bad json}\n\n"
        + AuditEntry(agent="dev", tool="python", action="execute", target="print(1)").to_json_line()
        + "\n",
        encoding="utf-8",
    )

    entries = logger.read_entries()
    assert len(entries) == 1
    assert entries[0].agent == "dev"


def test_logger_query_filters(tmp_path):
    """Description:
    Verify audit queries filter entries by the supplied criteria.

    Requirements:
        - This test is needed to prove the UI and debugging workflows can retrieve filtered audit slices.
        - Verify filtering by agent returns only matching entries.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = AuditLogger(tmp_path / "logs")
    logger.log_tool_operation(agent="dev", tool="python", action="execute", target="print(1)")
    logger.log_tool_operation(agent="qa", tool="filesystem", action="read", target="/repo/test.txt")
    entries = logger.query(agent="qa")
    assert len(entries) == 1
    assert entries[0].agent == "qa"


def test_rotation_archives_old_log(tmp_path):
    """Description:
    Verify log rotation archives the active log when the retention threshold is met.

    Requirements:
        - This test is needed to prove the audit log can be rotated without losing data.
        - Verify an archive file is created when rotation runs.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = AuditLogger(tmp_path / "logs", retention_days=0)
    logger.log_tool_operation(agent="dev", tool="python", action="execute", target="print(1)")
    time.sleep(1)
    archived = logger.rotate_if_needed()
    assert archived is not None
    assert archived.exists()


def test_file_restoration_uses_allow_once(tmp_path):
    """Description:
    Verify file restoration audit entries use the expected filesystem action metadata.

    Requirements:
        - This test is needed to prove restoration actions are logged consistently.
        - Verify the recorded approval tier is ``allow_once``.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = AuditLogger(tmp_path / "logs")
    entry = logger.log_file_restoration(agent="dev", target="/repo/file.txt")
    assert entry.approval_tier == "allow_once"


def test_approval_logger_normalises_legacy_permanent_deny_value(tmp_path):
    """Description:
    Verify audit logging normalises legacy permanent-deny wording.

    Requirements:
        - This test is needed to prove audit entries use the canonical FRS approval vocabulary.
        - Verify ``deny_permanently`` is stored as ``always_deny``.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = AuditLogger(tmp_path / "logs")
    entry = logger.log_approval_decision(
        agent="dev",
        tool="python",
        action="execute",
        target="rm -rf /",
        approval_tier="deny_permanently",
        decision="denied",
    )

    assert entry.approval_tier == "always_deny"
    assert logger.read_entries()[0].approval_tier == "always_deny"


def test_approval_logger_uses_unknown_for_noncanonical_deny_once(tmp_path):
    """Description:
    Verify one-off denials are recorded with the canonical fallback approval tier.

    Requirements:
        - This test is needed to prove audit entries do not leak noncanonical one-off deny values.
        - Verify ``deny_once`` is normalised to ``unknown``.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = AuditLogger(tmp_path / "logs")
    entry = logger.log_approval_decision(
        agent="dev",
        tool="python",
        action="execute",
        target="print(1)",
        approval_tier="deny_once",
        decision="denied",
    )

    assert entry.approval_tier == "unknown"


def test_async_record_writes_sandbox_audit_entry(tmp_path):
    """Description:
    Verify the async compatibility logger path records sandbox lifecycle events.

    Requirements:
        - This test is needed to prove PA components can audit events through the async ``record`` interface.
        - Verify the recorded sandbox entry uses the ``sandbox`` tool channel.

    :param tmp_path: Temporary pytest directory fixture.
    """

    import asyncio

    logger = AuditLogger(tmp_path / "logs")
    asyncio.run(
        logger.record(
            action="allocated",
            sandbox_id="sbx-001",
            session_id="sess-1",
            task_id="task-1",
            allocation_mode="shared",
        )
    )

    entries = logger.read_entries()
    assert len(entries) == 1
    assert entries[0].tool == "sandbox"
    assert entries[0].action == "allocated"
    assert entries[0].target == "sbx-001"
