"""Description:
    Verify the ask-first approval engine rule precedence and session-memory behaviour.

Requirements:
    - Prove explicit ask rules override broader allow rules.
    - Prove persistent deny rules and learned deny rules block actions without prompting.
    - Prove filesystem session memory supports exact, folder, and path-pattern scopes.
    - Prove unknown agents fall back to ask-first.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from faith_pa.security.approval_engine import ApprovalEngine, ApprovalTier


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


def test_engine_respects_precedence(tmp_path):
    """Description:
        Verify explicit ask rules take precedence over broader allow rules.

        Requirements:
            - This test is needed to prove the engine follows the intended rule precedence.
            - Verify a matching ``always_ask`` rule wins over a broader ``always_allow`` rule.

        :param tmp_path: Temporary pytest directory fixture.
    """

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
    """Description:
        Verify session approvals are remembered for later evaluations.

        Requirements:
            - This test is needed to prove ``approve_session`` decisions suppress repeat prompts within the session.
            - Verify the remembered decision is returned with the session tier.

        :param tmp_path: Temporary pytest directory fixture.
    """

    faith_dir = tmp_path / ".faith"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    engine.record_session_approval("dev", "filesystem:read:/tmp/demo.txt")
    decision = engine.evaluate("dev", "filesystem:read:/tmp/demo.txt")
    assert decision.tier == ApprovalTier.APPROVE_SESSION
    assert decision.remembered is True


def test_persistent_always_deny_rule_blocks_without_prompt(tmp_path):
    """Description:
        Verify persistent ``always_deny`` rules in ``approval_rules`` block matching actions.

        Requirements:
            - This test is needed to prove the engine honours the canonical deny tier from user-authored rules.
            - Verify the returned decision uses the canonical ``always_deny`` tier value.

        :param tmp_path: Temporary pytest directory fixture.
    """

    faith_dir = tmp_path / ".faith"
    write_file(
        faith_dir / "security.yaml",
        """
        approval_rules:
          dev:
            always_deny:
              - "^filesystem:write:/etc/.*$"
        """,
    )
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    decision = engine.evaluate("dev", "filesystem:write:/etc/hosts")
    assert decision.tier == ApprovalTier.ALWAYS_DENY
    assert decision.tier.value == "always_deny"
    assert decision.rule_source == "always_deny"
    assert decision.requires_approval is False


def test_deny_learned_blocks_without_prompt(tmp_path):
    """Description:
        Verify learned deny rules block matching actions without prompting.

        Requirements:
            - This test is needed to prove permanently denied actions are rejected automatically.
            - Verify the decision tier is the deny tier and does not require approval.

        :param tmp_path: Temporary pytest directory fixture.
    """

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


def test_filesystem_folder_session_approval_matches_descendants(tmp_path):
    """Description:
        Verify filesystem session approvals can be remembered at folder scope.

        Requirements:
            - This test is needed to prove filesystem approvals are path-based instead of exact-string only.
            - Verify a folder-scoped approval matches later actions under the same directory tree.

        :param tmp_path: Temporary pytest directory fixture.
    """

    faith_dir = tmp_path / ".faith"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    engine.record_session_decision(
        "dev",
        "filesystem:write:/repo/src/app.py",
        scope="folder",
        decision=ApprovalTier.APPROVE_SESSION,
    )

    decision = engine.evaluate("dev", "filesystem:write:/repo/src/utils/helpers.py")
    assert decision.tier == ApprovalTier.APPROVE_SESSION
    assert decision.remembered is True
    assert decision.rule_source == "session_memory"


def test_filesystem_glob_session_deny_blocks_matching_paths(tmp_path):
    """Description:
        Verify filesystem session memory can remember a denying path pattern.

        Requirements:
            - This test is needed to prove remembered filesystem decisions include both scope and decision type.
            - Verify a glob-scoped deny blocks matching paths without surfacing another approval.

        :param tmp_path: Temporary pytest directory fixture.
    """

    faith_dir = tmp_path / ".faith"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    engine.record_session_decision(
        "dev",
        "filesystem:write:/repo/secrets/*.txt",
        scope="glob",
        decision=ApprovalTier.ALWAYS_DENY,
    )

    decision = engine.evaluate("dev", "filesystem:write:/repo/secrets/token.txt")
    assert decision.tier == ApprovalTier.ALWAYS_DENY
    assert decision.remembered is True
    assert decision.requires_approval is False


def test_unknown_agent_defaults_to_ask_first(tmp_path):
    """Description:
        Verify unknown agents fall back to ask-first behaviour.

        Requirements:
            - This test is needed to prove the engine fails closed for agents with no configured rules.
            - Verify the default decision requires approval.

        :param tmp_path: Temporary pytest directory fixture.
    """

    faith_dir = tmp_path / ".faith"
    write_file(faith_dir / "security.yaml", "approval_rules: {}\n")
    engine = ApprovalEngine(faith_dir)
    engine.load_rules()
    decision = engine.evaluate("unknown", "browser:navigate:https://example.com")
    assert decision.tier == ApprovalTier.ASK_FIRST
    assert decision.requires_approval is True
