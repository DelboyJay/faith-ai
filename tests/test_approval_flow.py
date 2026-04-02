import asyncio
import textwrap
from pathlib import Path

import pytest
import yaml

from faith_pa.security.approval_engine import ApprovalEngine, ApprovalTier
from faith_pa.security.approval_flow import ApprovalFlow, UserApprovalDecision
from faith_pa.security.audit_log import AuditLogger


class DummyPublisher:
    def __init__(self):
        self.events = []

    async def publish(self, event_type, payload):
        self.events.append((event_type, payload))


def write_file(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).strip() + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_request_and_resolve_allow_once(tmp_path):
    faith_dir = tmp_path / ".faith"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    publisher = DummyPublisher()
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    flow = ApprovalFlow(
        approval_engine=engine,
        security_yaml_path=faith_dir / "security.yaml",
        event_publisher=publisher,
    )

    task = asyncio.create_task(
        flow.request_approval(agent_id="dev", tool="python", action="execute", target="print(1)")
    )
    await asyncio.sleep(0)
    request_id = next(iter(flow.pending))
    await flow.resolve_request(request_id, UserApprovalDecision.ALLOW_ONCE)
    result = await task

    assert result == UserApprovalDecision.ALLOW_ONCE
    assert publisher.events[0][0] == "approval:requested"
    assert publisher.events[1][0] == "approval:decision"


@pytest.mark.asyncio
async def test_approve_session_records_engine_memory(tmp_path):
    faith_dir = tmp_path / ".faith"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    flow = ApprovalFlow(approval_engine=engine, security_yaml_path=faith_dir / "security.yaml")

    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev", tool="filesystem", action="read", target="/repo/file.txt"
        )
    )
    await asyncio.sleep(0)
    request_id = next(iter(flow.pending))
    request = await flow.resolve_request(request_id, UserApprovalDecision.APPROVE_SESSION)
    result = await task

    assert result == UserApprovalDecision.APPROVE_SESSION
    decision = engine.evaluate("dev", "filesystem:read:/repo/file.txt")
    assert decision.remembered is True
    assert request.resolved is True


@pytest.mark.asyncio
async def test_deny_permanently_writes_matching_rule(tmp_path):
    faith_dir = tmp_path / ".faith"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    flow = ApprovalFlow(approval_engine=engine, security_yaml_path=faith_dir / "security.yaml")

    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev", tool="filesystem", action="write", target="/repo/src/app.py"
        )
    )
    await asyncio.sleep(0)
    request_id = next(iter(flow.pending))
    request = await flow.resolve_request(request_id, UserApprovalDecision.DENY_PERMANENTLY)
    result = await task

    assert result == UserApprovalDecision.DENY_PERMANENTLY
    security = yaml.safe_load((faith_dir / "security.yaml").read_text(encoding="utf-8"))
    assert request.generated_rule in security["always_deny_learned"]["dev"]

    engine.reload_rules()
    decision = engine.evaluate("dev", "filesystem:write:/repo/src/app.py")
    assert decision.tier == ApprovalTier.ALWAYS_DENY
    assert decision.rule_source == "always_deny_learned"


@pytest.mark.asyncio
async def test_flow_logs_audit_decision(tmp_path):
    faith_dir = tmp_path / ".faith"
    logs_dir = tmp_path / "logs"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    audit = AuditLogger(logs_dir)
    flow = ApprovalFlow(
        approval_engine=engine, security_yaml_path=faith_dir / "security.yaml", audit_logger=audit
    )

    task = asyncio.create_task(
        flow.request_approval(agent_id="dev", tool="python", action="execute", target="print(1)")
    )
    await asyncio.sleep(0)
    request_id = next(iter(flow.pending))
    await flow.resolve_request(request_id, UserApprovalDecision.DENY_ONCE)
    await task

    entries = audit.read_entries()
    assert len(entries) == 1
    assert entries[0].decision == "denied"
    assert entries[0].approval_tier == "deny_once"

