import textwrap
from pathlib import Path

from faith_pa.security.approval_engine import ApprovalEngine, ApprovalTier


def write_file(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).strip() + "\n", encoding="utf-8")


def test_engine_respects_precedence(tmp_path):
    faith_dir = tmp_path / ".faith"
    write_file(
        faith_dir / "security.yaml",
        """
        approval_rules:
          dev:
            always_ask:
              - "^git push.*$"
            always_allow:
              - "^git .*"
        always_allow_learned: {}
        always_ask_learned: {}
        always_deny_learned: {}
        """,
    )

    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    decision = engine.evaluate("dev", "git push origin main")
    assert decision.tier == ApprovalTier.ALWAYS_ASK
    assert decision.rule_source == "always_ask"


def test_session_approval_is_remembered(tmp_path):
    faith_dir = tmp_path / ".faith"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    engine.record_session_approval("dev", "filesystem:read:/tmp/demo.txt")
    decision = engine.evaluate("dev", "filesystem:read:/tmp/demo.txt")
    assert decision.tier == ApprovalTier.APPROVE_SESSION
    assert decision.remembered is True


def test_deny_learned_blocks_without_prompt(tmp_path):
    faith_dir = tmp_path / ".faith"
    write_file(
        faith_dir / "security.yaml",
        """
        approval_rules: {}
        always_deny_learned:
          dev:
            - "^python:execute:rm -rf /$"
        """,
    )
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    decision = engine.evaluate("dev", "python:execute:rm -rf /")
    assert decision.tier == ApprovalTier.ALWAYS_DENY
    assert decision.requires_approval is False


def test_unknown_agent_defaults_to_ask_first(tmp_path):
    faith_dir = tmp_path / ".faith"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    decision = engine.evaluate("unknown", "browser:navigate:https://example.com")
    assert decision.tier == ApprovalTier.ASK_FIRST
    assert decision.requires_approval is True

