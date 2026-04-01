import time

from faith.security.audit_log import AuditEntry, AuditLogger


def test_audit_entry_round_trip():
    entry = AuditEntry(agent="dev", tool="filesystem", action="read", target="/repo/a.txt")
    restored = AuditEntry.from_json_line(entry.to_json_line())
    assert restored.agent == "dev"
    assert restored.tool == "filesystem"


def test_logger_writes_and_reads(tmp_path):
    logger = AuditLogger(tmp_path / "logs")
    logger.log_tool_operation(
        agent="dev", tool="python", action="execute", target="print(1)", approval_tier="allow_once"
    )
    entries = logger.read_entries()
    assert len(entries) == 1
    assert entries[0].approval_tier == "allow_once"


def test_logger_skips_malformed_lines(tmp_path):
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
    logger = AuditLogger(tmp_path / "logs")
    logger.log_tool_operation(agent="dev", tool="python", action="execute", target="print(1)")
    logger.log_tool_operation(agent="qa", tool="filesystem", action="read", target="/repo/test.txt")
    entries = logger.query(agent="qa")
    assert len(entries) == 1
    assert entries[0].agent == "qa"


def test_rotation_archives_old_log(tmp_path):
    logger = AuditLogger(tmp_path / "logs", retention_days=0)
    logger.log_tool_operation(agent="dev", tool="python", action="execute", target="print(1)")
    time.sleep(1)
    archived = logger.rotate_if_needed()
    assert archived is not None
    assert archived.exists()


def test_file_restoration_uses_allow_once(tmp_path):
    logger = AuditLogger(tmp_path / "logs")
    entry = logger.log_file_restoration(agent="dev", target="/repo/file.txt")
    assert entry.approval_tier == "allow_once"
