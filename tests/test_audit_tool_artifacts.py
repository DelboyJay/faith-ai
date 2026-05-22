"""Description:
    Verify replay-friendly MCP audit artifacts are persisted beside the compact audit log.

Requirements:
    - Prove the main audit log stays compact while per-call tool details land in dedicated artifact files.
"""

from __future__ import annotations

import json
from pathlib import Path

from faith_pa.security.audit_log import AuditLogger


def test_audit_logger_writes_tool_artifact_and_links_it_from_main_entry(tmp_path: Path) -> None:
    """Description:
        Verify one tool operation writes a detailed artifact file and records its location in the compact audit entry.

    Requirements:
        - This test is needed to prove Phase 19 keeps replay-friendly MCP details without bloating `audit.log`.
        - Verify the audit entry records the tool-call id and artifact path, and the artifact stores bounded request and response payloads.

    :param tmp_path: Temporary log root used for audit persistence.
    """

    logger = AuditLogger(tmp_path / "logs")
    entry = logger.log_tool_operation(
        agent="project-agent",
        tool="excerpt",
        action="retrieve",
        target='{"path":"README.md"}',
        channel="pa-user",
        session_id="sess-123",
        request_payload={"path": "README.md", "match_ids": ["m-001"]},
        response_payload={"success": True, "matches": [{"text": "Hello"}]},
    )

    artifact_path = (
        tmp_path / "logs" / "audit" / "tools" / "sess-123" / f"{entry.tool_call_id}.json"
    )
    artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    audit_entry_payload = json.loads(
        (tmp_path / "logs" / "audit.log").read_text(encoding="utf-8").splitlines()[0]
    )

    assert artifact_path.exists()
    assert audit_entry_payload["tool_call_id"] == entry.tool_call_id
    assert audit_entry_payload["artifact_path"] == artifact_path.as_posix()
    assert artifact_payload["request"]["path"] == "README.md"
    assert artifact_payload["response"]["success"] is True
