"""Description:
    Verify approval requests, resolutions, learned-rule persistence, and audit logging.

Requirements:
    - Prove approval requests publish request and decision events.
    - Prove session approvals update the approval engine memory.
    - Prove permanent denials persist generated rules to ``security.yaml``.
    - Prove resolved decisions are written to the audit log.
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
import yaml

from faith_pa.security.approval_engine import ApprovalEngine, ApprovalTier
from faith_pa.security.approval_flow import ApprovalFlow, UserApprovalDecision
from faith_pa.security.audit_log import AuditLogger


class DummyPublisher:
    """Description:
        Provide a minimal event publisher for approval-flow tests.

    Requirements:
        - Record published events in call order for later assertions.
    """

    def __init__(self):
        """Description:
            Initialise the dummy publisher state.

        Requirements:
            - Start with an empty event list.
        """

        self.events = []

    async def publish(self, event_type, payload):
        """Description:
            Record one published event.

        Requirements:
            - Preserve the event type and payload tuple for assertions.

        :param event_type: Published event type.
        :param payload: Published event payload.
        """

        self.events.append((event_type, payload))


def write_file(path: Path, contents: str) -> None:
    """Description:
        Write one test configuration file with normalised indentation.

    Requirements:
        - Create parent directories when needed.
        - Ensure the written file ends with a trailing newline.

    :param path: Target file path.
    :param contents: File content to write after dedenting.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).strip() + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_request_and_resolve_allow_once(tmp_path):
    """Description:
        Verify the approval flow publishes request and decision events for an allow-once decision.

        Requirements:
            - This test is needed to prove pending requests can be created and resolved end to end.
            - Verify both the request and decision events are published in order.

        :param tmp_path: Temporary pytest directory fixture.
    """

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
    """Description:
        Verify approve-session decisions are recorded in the approval engine session memory.

        Requirements:
            - This test is needed to prove repeat prompts can be suppressed within a session.
            - Verify a later engine evaluation returns the remembered session approval.

        :param tmp_path: Temporary pytest directory fixture.
    """

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
    """Description:
        Verify permanent denials persist a generated rule and affect later evaluations.

        Requirements:
            - This test is needed to prove learned deny rules survive beyond the initial request.
            - Verify the generated rule is written to ``security.yaml`` and then matched by the engine.

        :param tmp_path: Temporary pytest directory fixture.
    """

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
    """Description:
        Verify resolved approval decisions are written to the audit log.

        Requirements:
            - This test is needed to prove approval outcomes are retained for later audit and UI inspection.
            - Verify denied decisions are written with the expected approval tier.

        :param tmp_path: Temporary pytest directory fixture.
    """

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
