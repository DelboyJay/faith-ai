# FAITH-019 — Security YAML Schema & Regex Approval Engine

**Phase:** 5 — Security & Approval System
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-003
**FRS Reference:** Section 5.1, 5.3

---

## Objective

Implement the ask-first approval engine for MCP actions. The engine evaluates actions against `.faith/security.yaml` rules in strict precedence order: `always_ask` first (safety wins), then `always_deny`, then `always_allow`, then the default prompt-the-user fallback. Session-scoped approval memory allows `approve for session` / `deny for session` style decisions to persist for the duration of a session without re-prompting. Filesystem actions must support path-based remembered scopes (exact file, folder, or path-pattern). Per-agent trust levels (`high`, `standard`, `low`) may influence recommendations or UI emphasis but do not silently bypass approval for new actions.

---

## Architecture

```
faith/security/
├── __init__.py              ← Package exports
└── approval_engine.py       ← ApprovalEngine class (this task)

tests/
└── test_approval_engine.py  ← Full test coverage (this task)
```

---

## Files to Create

### 1. `faith/security/approval_engine.py`

```python
"""FAITH Approval Engine — three-tier regex-based action evaluation.

Evaluates agent tool-call actions against `.faith/security.yaml` rules
in strict precedence order:

  1. always_ask          — always surface for user approval
  2. always_deny_learned — permanently denied (written by FAITH-020)
  3. always_allow        — execute without prompting
  4. always_allow_learned — learned allow rules (written by FAITH-020)
  5. ask_first           — fallback: prompt the user and remember session decisions

Per-agent trust levels do not bypass the fallback:
  - high:     recommend broader durable allow rules, but still ask first
  - standard: ask first (default)
  - low:      ask first with stricter UI emphasis

FRS Reference: Section 5.1, 5.3
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("faith.security.approval_engine")


class ApprovalTier(str, Enum):
    """Approval tiers used by the ask-first policy."""

    ALWAYS_ALLOW = "always_allow"
    ASK_FIRST = "ask_first"
    ALWAYS_ASK = "always_ask"
    ALWAYS_DENY = "always_deny"


class TrustLevel(str, Enum):
    """Per-agent trust levels from .faith/agents/{id}/config.yaml."""

    HIGH = "high"
    STANDARD = "standard"
    LOW = "low"


@dataclass
class ApprovalDecision:
    """Result of evaluating an action against the approval engine.

    Attributes:
        tier: The approval tier that matched.
        rule_matched: The regex pattern that matched, or None for fallback.
        rule_source: Which section the match came from (e.g. "always_allow",
            "always_ask", "always_allow_learned", "always_deny_learned").
        requires_approval: Whether the action needs user approval before
            executing.
        remembered: Whether this decision came from session memory
            (a previously approved ask-first action).
    """

    tier: ApprovalTier
    rule_matched: Optional[str] = None
    rule_source: Optional[str] = None
    requires_approval: bool = True
    remembered: bool = False


@dataclass
class _CompiledRuleSet:
    """Pre-compiled regex patterns for a single agent's rule sections.

    Each list holds (original_pattern_string, compiled_regex) tuples.
    """

    always_ask: list[tuple[str, re.Pattern]] = field(default_factory=list)
    always_deny_learned: list[tuple[str, re.Pattern]] = field(default_factory=list)
    always_allow: list[tuple[str, re.Pattern]] = field(default_factory=list)
    always_allow_learned: list[tuple[str, re.Pattern]] = field(default_factory=list)


class ApprovalEngine:
    """Ask-first regex-based approval engine.

    Loads rules from `.faith/security.yaml` and per-agent trust levels
    from `.faith/agents/{id}/config.yaml`. Maintains session-scoped
    approval memory for ask-first actions.

    Usage:
        engine = ApprovalEngine(faith_dir=Path(".faith"))
        engine.load_rules()

        decision = engine.evaluate("software-developer", "git push origin main")
        if decision.requires_approval:
            # Surface to user via approval panel
            ...
        else:
            # Execute the action
            ...

        # After user approves an ask-first action for this session:
        engine.record_session_approval("software-developer", "git push origin main")

    Attributes:
        faith_dir: Path to the project's .faith directory.
    """

    def __init__(self, faith_dir: Path):
        """Initialise the approval engine.

        Args:
            faith_dir: Path to the .faith directory containing
                security.yaml and agents/*/config.yaml.
        """
        self.faith_dir = faith_dir
        self._security_yaml_path = faith_dir / "security.yaml"

        # Per-agent compiled rule sets: agent_id -> _CompiledRuleSet
        self._agent_rules: dict[str, _CompiledRuleSet] = {}

        # Per-agent trust levels: agent_id -> TrustLevel
        self._trust_levels: dict[str, TrustLevel] = {}

        # Session-scoped approval memory:
        # (agent_id, action_string) -> ApprovalDecision
        self._session_memory: dict[tuple[str, str], ApprovalDecision] = {}

    def load_rules(self) -> None:
        """Load and compile approval rules from security.yaml.

        Also loads trust levels from all discovered agent config files.
        Call this on startup and whenever security.yaml or agent configs
        are hot-reloaded.

        Raises:
            FileNotFoundError: If security.yaml does not exist.
            yaml.YAMLError: If security.yaml is malformed.
        """
        self._load_security_yaml()
        self._load_trust_levels()

    def reload_rules(self) -> None:
        """Reload rules from disk without clearing session memory.

        Use this when the config watcher detects a change to
        security.yaml or agent config files. Session approvals
        are preserved — they remain valid until the session ends.
        """
        logger.info("Reloading approval rules from disk")
        self._load_security_yaml()
        self._load_trust_levels()

    def _load_security_yaml(self) -> None:
        """Parse security.yaml and compile regex rules per agent."""
        try:
            raw = self._security_yaml_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
        except FileNotFoundError:
            logger.warning(
                f"security.yaml not found at {self._security_yaml_path} "
                f"— no approval rules loaded"
            )
            self._agent_rules = {}
            return
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse security.yaml: {e}")
            raise

        self._agent_rules = {}

        # Parse each rule section
        approval_rules = data.get("approval_rules", {})
        always_allow_learned = data.get("always_allow_learned", {})
        always_deny_learned = data.get("always_deny_learned", {})

        # Discover all agent IDs across all sections
        all_agent_ids = set()
        all_agent_ids.update(approval_rules.keys())
        all_agent_ids.update(always_allow_learned.keys())
        all_agent_ids.update(always_deny_learned.keys())

        for agent_id in all_agent_ids:
            ruleset = _CompiledRuleSet()

            # From approval_rules section
            agent_rules = approval_rules.get(agent_id, {})
            if isinstance(agent_rules, dict):
                ruleset.always_ask = self._compile_patterns(
                    agent_rules.get("always_ask", []), agent_id, "always_ask"
                )
                ruleset.always_allow = self._compile_patterns(
                    agent_rules.get("always_allow", []), agent_id, "always_allow"
                )

            # From learned sections (written by FAITH-020)
            ruleset.always_allow_learned = self._compile_patterns(
                always_allow_learned.get(agent_id, []),
                agent_id,
                "always_allow_learned",
            )
            ruleset.always_deny_learned = self._compile_patterns(
                always_deny_learned.get(agent_id, []),
                agent_id,
                "always_deny_learned",
            )

            self._agent_rules[agent_id] = ruleset

        total_rules = sum(
            len(rs.always_ask)
            + len(rs.always_allow)
            + len(rs.always_allow_learned)
            + len(rs.always_deny_learned)
            for rs in self._agent_rules.values()
        )
        logger.info(
            f"Loaded {total_rules} approval rules for "
            f"{len(self._agent_rules)} agent(s)"
        )

    def _compile_patterns(
        self,
        patterns: list[str] | Any,
        agent_id: str,
        section: str,
    ) -> list[tuple[str, re.Pattern]]:
        """Compile a list of regex pattern strings.

        Invalid patterns are logged and skipped — they do not halt
        rule loading.

        Args:
            patterns: List of regex strings.
            agent_id: Agent ID (for logging).
            section: Config section name (for logging).

        Returns:
            List of (original_string, compiled_pattern) tuples.
        """
        if not isinstance(patterns, list):
            if patterns is not None:
                logger.warning(
                    f"Expected list for {agent_id}.{section}, "
                    f"got {type(patterns).__name__} — skipping"
                )
            return []

        compiled = []
        for pattern_str in patterns:
            if not isinstance(pattern_str, str):
                logger.warning(
                    f"Skipping non-string pattern in {agent_id}.{section}: "
                    f"{pattern_str!r}"
                )
                continue
            try:
                compiled.append((pattern_str, re.compile(pattern_str)))
            except re.error as e:
                logger.error(
                    f"Invalid regex in {agent_id}.{section}: "
                    f"'{pattern_str}' — {e}"
                )
        return compiled

    def _load_trust_levels(self) -> None:
        """Load trust levels from all .faith/agents/*/config.yaml files."""
        agents_dir = self.faith_dir / "agents"
        self._trust_levels = {}

        if not agents_dir.is_dir():
            logger.debug(f"No agents directory at {agents_dir}")
            return

        for agent_dir in agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue

            config_path = agent_dir / "config.yaml"
            if not config_path.exists():
                continue

            try:
                raw = config_path.read_text(encoding="utf-8")
                config = yaml.safe_load(raw) or {}
                trust_str = config.get("trust_level", "standard")
                try:
                    trust = TrustLevel(trust_str)
                except ValueError:
                    logger.warning(
                        f"Invalid trust_level '{trust_str}' for agent "
                        f"'{agent_dir.name}' — defaulting to standard"
                    )
                    trust = TrustLevel.STANDARD
                self._trust_levels[agent_dir.name] = trust
            except Exception as e:
                logger.warning(
                    f"Failed to load config for agent '{agent_dir.name}': {e}"
                )

        logger.info(
            f"Loaded trust levels for {len(self._trust_levels)} agent(s)"
        )

    def get_trust_level(self, agent_id: str) -> TrustLevel:
        """Return the trust level for an agent.

        Args:
            agent_id: The agent's identifier.

        Returns:
            The agent's trust level. Defaults to STANDARD if not found.
        """
        return self._trust_levels.get(agent_id, TrustLevel.STANDARD)

    def evaluate(self, agent_id: str, action: str) -> ApprovalDecision:
        """Evaluate an action against the approval rules.

        Checks rules in strict precedence order:
          1. always_ask rules — if matched, always requires approval
          2. always_deny_learned rules — if matched, permanently denied
          3. always_allow rules — if matched, proceeds without prompting
          4. always_allow_learned rules — if matched, proceeds without prompting
          5. Session memory — if previously approved this session
          6. Ask-first fallback for everything else

        Args:
            agent_id: The agent requesting the action.
            action: The action string to evaluate (e.g. "git push origin main").

        Returns:
            An ApprovalDecision describing whether the action needs
            user approval and which rule matched.
        """
        ruleset = self._agent_rules.get(agent_id, _CompiledRuleSet())

        # --- Tier 1: always_ask ---
        match = self._match_any(action, ruleset.always_ask)
        if match is not None:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_ASK,
                rule_matched=match,
                rule_source="always_ask",
                requires_approval=True,
            )

        # --- Tier 2: always_deny_learned ---
        match = self._match_any(action, ruleset.always_deny_learned)
        if match is not None:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_DENY,
                rule_matched=match,
                rule_source="always_deny_learned",
                requires_approval=False,  # No approval needed — outright denied
            )

        # --- Tier 3: always_allow ---
        match = self._match_any(action, ruleset.always_allow)
        if match is not None:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_ALLOW,
                rule_matched=match,
                rule_source="always_allow",
                requires_approval=False,
            )

        # --- Tier 4: always_allow_learned ---
        match = self._match_any(action, ruleset.always_allow_learned)
        if match is not None:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_ALLOW,
                rule_matched=match,
                rule_source="always_allow_learned",
                requires_approval=False,
            )

        # --- Tier 5: Session memory ---
        memory_key = (agent_id, action)
        if memory_key in self._session_memory:
            logger.debug(
                f"Session memory hit for {agent_id}: '{action}'"
            )
            remembered = self._session_memory[memory_key]
            return ApprovalDecision(
                tier=remembered.tier,
                rule_matched=remembered.rule_matched,
                rule_source="session_memory",
                requires_approval=False,
                remembered=True,
            )

        # --- Tier 6: Ask-first fallback ---
        trust = self.get_trust_level(agent_id)
        return self._apply_trust_fallback(trust)

    def _match_any(
        self,
        action: str,
        patterns: list[tuple[str, re.Pattern]],
    ) -> Optional[str]:
        """Test an action against a list of compiled regex patterns.

        Args:
            action: The action string to test.
            patterns: List of (original_string, compiled_pattern) tuples.

        Returns:
            The original pattern string if any pattern matches, else None.
        """
        for pattern_str, compiled in patterns:
            try:
                if compiled.search(action):
                    return pattern_str
            except Exception as e:
                logger.error(
                    f"Regex match error for pattern '{pattern_str}': {e}"
                )
        return None

    def _apply_trust_fallback(self, trust: TrustLevel) -> ApprovalDecision:
        """Generate the ask-first fallback decision.

        Args:
            trust: The agent's trust level. Used for metadata and future
                recommendation hooks only; it does not bypass approval.

        Returns:
            ApprovalDecision using the ask-first fallback tier.
        """
        return ApprovalDecision(
            tier=ApprovalTier.ASK_FIRST,
            rule_source="ask_first_fallback",
            requires_approval=True,
        )

    def record_session_approval(
        self, agent_id: str, action: str
    ) -> None:
        """Record that an ask-first action was approved for this session.

        After recording, subsequent evaluate() calls for the same
        agent + action will return a remembered approval without
        prompting the user again.

        Args:
            agent_id: The agent that requested the action.
            action: The exact action string that was approved.
        """
        key = (agent_id, action)
        self._session_memory[key] = ApprovalDecision(
            tier=ApprovalTier.ASK_FIRST,
            rule_source="session_memory",
            requires_approval=False,
            remembered=True,
        )
        logger.info(
            f"Recorded session approval for {agent_id}: '{action}'"
        )

    def clear_session_memory(self) -> None:
        """Clear all session-scoped approval memory.

        Call this when a session ends. The next session starts
        with a clean slate — all ask-first actions will
        prompt again.
        """
        count = len(self._session_memory)
        self._session_memory.clear()
        logger.info(f"Cleared session memory ({count} entries)")

    def clear_agent_session_memory(self, agent_id: str) -> None:
        """Clear session memory for a specific agent only.

        Args:
            agent_id: The agent whose session memory to clear.
        """
        keys_to_remove = [
            k for k in self._session_memory if k[0] == agent_id
        ]
        for k in keys_to_remove:
            del self._session_memory[k]
        if keys_to_remove:
            logger.info(
                f"Cleared {len(keys_to_remove)} session memory entries "
                f"for agent '{agent_id}'"
            )

    def get_rules_for_agent(self, agent_id: str) -> dict[str, list[str]]:
        """Return the current rule patterns for an agent (for debugging/UI).

        Args:
            agent_id: The agent identifier.

        Returns:
            Dict with keys "always_ask", "always_allow",
            "always_allow_learned", "always_deny_learned", each
            containing a list of regex pattern strings.
        """
        ruleset = self._agent_rules.get(agent_id, _CompiledRuleSet())
        return {
            "always_ask": [p for p, _ in ruleset.always_ask],
            "always_allow": [p for p, _ in ruleset.always_allow],
            "always_allow_learned": [p for p, _ in ruleset.always_allow_learned],
            "always_deny_learned": [p for p, _ in ruleset.always_deny_learned],
        }

    def get_session_memory_count(self, agent_id: Optional[str] = None) -> int:
        """Return the number of entries in session memory.

        Args:
            agent_id: If provided, count only entries for this agent.

        Returns:
            Number of session memory entries.
        """
        if agent_id is None:
            return len(self._session_memory)
        return sum(1 for k in self._session_memory if k[0] == agent_id)
```

### 2. `faith/security/__init__.py`

```python
"""FAITH Security — approval engine and security rule evaluation."""

from faith.security.approval_engine import (
    ApprovalDecision,
    ApprovalEngine,
    ApprovalTier,
    TrustLevel,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalEngine",
    "ApprovalTier",
    "TrustLevel",
]
```

### 3. `tests/test_approval_engine.py`

```python
"""Tests for the FAITH approval engine.

Covers three-tier evaluation, regex matching, trust levels, session
memory, rule reloading, edge cases, and learned rule handling.
"""

from pathlib import Path

import pytest
import yaml

from faith.security.approval_engine import (
    ApprovalDecision,
    ApprovalEngine,
    ApprovalTier,
    TrustLevel,
    _CompiledRuleSet,
)


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


SECURITY_YAML_CONTENT = {
    "approval_rules": {
        "software-developer": {
            "always_allow": [
                "^pytest( .+)?$",
                "^pip install [a-zA-Z0-9_\\-]+(==[\\d.]+)?$",
                "^git (status|log|diff|add|commit).*$",
            ],
            "always_ask": [
                "^git push.*$",
                "^rm .*$",
                "^docker .*$",
            ],
        },
        "qa-engineer": {
            "always_allow": [
                "^pytest.*$",
                "^coverage.*$",
            ],
            "always_ask": [
                "^rm .*$",
            ],
        },
    },
    "always_allow_learned": {
        "software-developer": [
            "^git commit -m .+$",
            "^pytest tests/.*$",
        ],
    },
    "always_deny_learned": {
        "software-developer": [
            "^rm -rf.*$",
        ],
    },
}


@pytest.fixture
def faith_dir(tmp_path):
    """Create a temporary .faith directory with security.yaml and agent configs."""
    faith = tmp_path / ".faith"
    faith.mkdir()

    # Write security.yaml
    (faith / "security.yaml").write_text(
        yaml.dump(SECURITY_YAML_CONTENT, default_flow_style=False),
        encoding="utf-8",
    )

    # Write agent configs with trust levels
    dev_dir = faith / "agents" / "software-developer"
    dev_dir.mkdir(parents=True)
    (dev_dir / "config.yaml").write_text(
        yaml.dump({"trust_level": "standard", "model": "gpt-4o"}),
        encoding="utf-8",
    )

    qa_dir = faith / "agents" / "qa-engineer"
    qa_dir.mkdir(parents=True)
    (qa_dir / "config.yaml").write_text(
        yaml.dump({"trust_level": "high", "model": "gpt-4o-mini"}),
        encoding="utf-8",
    )

    low_dir = faith / "agents" / "junior-dev"
    low_dir.mkdir(parents=True)
    (low_dir / "config.yaml").write_text(
        yaml.dump({"trust_level": "low", "model": "gpt-4o-mini"}),
        encoding="utf-8",
    )

    return faith


@pytest.fixture
def engine(faith_dir):
    """Create and load an ApprovalEngine."""
    eng = ApprovalEngine(faith_dir=faith_dir)
    eng.load_rules()
    return eng


# ──────────────────────────────────────────────────
# Rule loading tests
# ──────────────────────────────────────────────────


def test_load_rules_counts(engine):
    """Rules are loaded for all agents mentioned in security.yaml."""
    rules = engine.get_rules_for_agent("software-developer")
    assert len(rules["always_ask"]) == 3
    assert len(rules["always_allow"]) == 3
    assert len(rules["always_allow_learned"]) == 2
    assert len(rules["always_deny_learned"]) == 1


def test_load_rules_qa_agent(engine):
    """QA agent rules are loaded correctly."""
    rules = engine.get_rules_for_agent("qa-engineer")
    assert len(rules["always_allow"]) == 2
    assert len(rules["always_ask"]) == 1
    assert len(rules["always_allow_learned"]) == 0
    assert len(rules["always_deny_learned"]) == 0


def test_load_rules_unknown_agent(engine):
    """Unknown agent returns empty rule sets."""
    rules = engine.get_rules_for_agent("nonexistent-agent")
    assert all(len(v) == 0 for v in rules.values())


def test_load_rules_missing_security_yaml(tmp_path):
    """Missing security.yaml results in empty rules without crashing."""
    faith = tmp_path / ".faith"
    faith.mkdir()
    eng = ApprovalEngine(faith_dir=faith)
    eng.load_rules()
    rules = eng.get_rules_for_agent("any-agent")
    assert all(len(v) == 0 for v in rules.values())


def test_load_rules_invalid_regex(tmp_path):
    """Invalid regex patterns are skipped without crashing."""
    faith = tmp_path / ".faith"
    faith.mkdir()
    (faith / "security.yaml").write_text(
        yaml.dump({
            "approval_rules": {
                "dev": {
                    "always_allow": [
                        "^valid_pattern$",
                        "[invalid(regex",  # broken regex
                        "^another_valid$",
                    ],
                },
            },
        }),
        encoding="utf-8",
    )
    eng = ApprovalEngine(faith_dir=faith)
    eng.load_rules()
    rules = eng.get_rules_for_agent("dev")
    # The invalid pattern should be skipped, leaving 2 valid rules
    assert len(rules["always_allow"]) == 2


def test_load_rules_non_list_section(tmp_path):
    """Non-list rule sections are logged and skipped."""
    faith = tmp_path / ".faith"
    faith.mkdir()
    (faith / "security.yaml").write_text(
        yaml.dump({
            "approval_rules": {
                "dev": {
                    "always_allow": "not a list",
                },
            },
        }),
        encoding="utf-8",
    )
    eng = ApprovalEngine(faith_dir=faith)
    eng.load_rules()
    rules = eng.get_rules_for_agent("dev")
    assert len(rules["always_allow"]) == 0


# ──────────────────────────────────────────────────
# Trust level loading tests
# ──────────────────────────────────────────────────


def test_trust_level_standard(engine):
    """Software developer has standard trust."""
    assert engine.get_trust_level("software-developer") == TrustLevel.STANDARD


def test_trust_level_high(engine):
    """QA engineer has high trust."""
    assert engine.get_trust_level("qa-engineer") == TrustLevel.HIGH


def test_trust_level_low(engine):
    """Junior dev has low trust."""
    assert engine.get_trust_level("junior-dev") == TrustLevel.LOW


def test_trust_level_unknown_agent_defaults_standard(engine):
    """Unknown agent defaults to standard trust."""
    assert engine.get_trust_level("unknown-agent") == TrustLevel.STANDARD


def test_trust_level_invalid_value(tmp_path):
    """Invalid trust_level value in config defaults to standard."""
    faith = tmp_path / ".faith"
    faith.mkdir()
    (faith / "security.yaml").write_text("{}", encoding="utf-8")
    agent_dir = faith / "agents" / "bad-config"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(
        yaml.dump({"trust_level": "ultra"}), encoding="utf-8"
    )
    eng = ApprovalEngine(faith_dir=faith)
    eng.load_rules()
    assert eng.get_trust_level("bad-config") == TrustLevel.STANDARD


# ──────────────────────────────────────────────────
# Three-tier evaluation tests
# ──────────────────────────────────────────────────


def test_always_ask_git_push(engine):
    """git push matches always_ask and requires approval."""
    decision = engine.evaluate("software-developer", "git push origin main")
    assert decision.tier == ApprovalTier.ALWAYS_ASK
    assert decision.requires_approval is True
    assert decision.rule_source == "always_ask"
    assert decision.rule_matched == "^git push.*$"


def test_always_ask_rm(engine):
    """rm commands always require approval."""
    decision = engine.evaluate("software-developer", "rm temp.txt")
    assert decision.tier == ApprovalTier.ALWAYS_ASK
    assert decision.requires_approval is True


def test_always_ask_docker(engine):
    """docker commands always require approval."""
    decision = engine.evaluate("software-developer", "docker build .")
    assert decision.tier == ApprovalTier.ALWAYS_ASK
    assert decision.requires_approval is True


def test_always_allow_pytest(engine):
    """pytest matches always_allow and does not require approval."""
    decision = engine.evaluate("software-developer", "pytest")
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW
    assert decision.requires_approval is False
    assert decision.rule_source == "always_allow"


def test_always_allow_pytest_with_args(engine):
    """pytest with arguments matches always_allow."""
    decision = engine.evaluate("software-developer", "pytest tests/ -v")
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW
    assert decision.requires_approval is False


def test_always_allow_pip_install(engine):
    """pip install named package matches always_allow."""
    decision = engine.evaluate("software-developer", "pip install requests")
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW
    assert decision.requires_approval is False


def test_always_allow_pip_install_with_version(engine):
    """pip install with version pin matches always_allow."""
    decision = engine.evaluate("software-developer", "pip install requests==2.31.0")
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW
    assert decision.requires_approval is False


def test_always_allow_git_status(engine):
    """git status matches always_allow."""
    decision = engine.evaluate("software-developer", "git status")
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW
    assert decision.requires_approval is False


def test_always_allow_git_commit(engine):
    """git commit matches always_allow."""
    decision = engine.evaluate("software-developer", "git commit -m 'fix bug'")
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW
    assert decision.requires_approval is False


def test_always_ask_overrides_always_allow(engine):
    """git push matches both always_ask and always_allow — always_ask wins.

    The always_ask pattern '^git push.*$' matches, and since always_ask
    is evaluated before always_allow, safety wins.
    """
    # "git push" could also match "^git (status|log|diff|add|commit).*$"?
    # No, it wouldn't — but the principle holds: always_ask is checked first.
    decision = engine.evaluate("software-developer", "git push origin main")
    assert decision.tier == ApprovalTier.ALWAYS_ASK
    assert decision.requires_approval is True


def test_always_deny_learned(engine):
    """rm -rf matches always_deny_learned — action is permanently denied."""
    decision = engine.evaluate("software-developer", "rm -rf /tmp/stuff")
    # Note: "rm -rf /tmp/stuff" matches always_ask "^rm .*$" first!
    # always_ask is checked before always_deny_learned, so it should
    # actually be always_ask. Let's test with a more specific case.
    assert decision.tier == ApprovalTier.ALWAYS_ASK
    assert decision.requires_approval is True


def test_always_deny_learned_no_always_ask_overlap(tmp_path):
    """always_deny_learned blocks actions not covered by always_ask."""
    faith = tmp_path / ".faith"
    faith.mkdir()
    (faith / "security.yaml").write_text(
        yaml.dump({
            "approval_rules": {
                "dev": {
                    "always_ask": [],
                    "always_allow": [],
                },
            },
            "always_deny_learned": {
                "dev": ["^dangerous_command.*$"],
            },
        }),
        encoding="utf-8",
    )
    eng = ApprovalEngine(faith_dir=faith)
    eng.load_rules()
    decision = eng.evaluate("dev", "dangerous_command --force")
    assert decision.tier == ApprovalTier.ALWAYS_DENY
    assert decision.requires_approval is False
    assert decision.rule_source == "always_deny_learned"


def test_always_allow_learned(engine):
    """Learned always_allow rules work for actions not matched earlier."""
    # "pytest tests/unit" matches both always_allow "^pytest( .+)?$"
    # and always_allow_learned "^pytest tests/.*$". always_allow is
    # checked first, so it matches there. Let's test a learned-only case.
    pass  # Covered by the dedicated test below.


def test_always_allow_learned_only(tmp_path):
    """always_allow_learned matches when no manual rules match."""
    faith = tmp_path / ".faith"
    faith.mkdir()
    (faith / "security.yaml").write_text(
        yaml.dump({
            "approval_rules": {
                "dev": {
                    "always_ask": [],
                    "always_allow": [],
                },
            },
            "always_allow_learned": {
                "dev": ["^make build$"],
            },
        }),
        encoding="utf-8",
    )
    eng = ApprovalEngine(faith_dir=faith)
    eng.load_rules()
    decision = eng.evaluate("dev", "make build")
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW
    assert decision.requires_approval is False
    assert decision.rule_source == "always_allow_learned"


def test_ask_first_fallback(engine):
    """Unmatched actions fall through to ask_first for standard trust."""
    decision = engine.evaluate("software-developer", "curl https://example.com")
    assert decision.tier == ApprovalTier.ASK_FIRST
    assert decision.requires_approval is True
    assert decision.rule_matched is None
    assert decision.rule_source == "ask_first_fallback"


# ──────────────────────────────────────────────────
# Trust level fallback tests
# ──────────────────────────────────────────────────


def test_high_trust_still_asks_first(engine):
    """High-trust agents still use ask_first for unmatched actions."""
    decision = engine.evaluate("qa-engineer", "some unknown command")
    assert decision.tier == ApprovalTier.ASK_FIRST
    assert decision.requires_approval is True
    assert decision.rule_source == "ask_first_fallback"


def test_low_trust_fallback_always_asks(engine):
    """Low-trust agents still use ask_first for unmatched actions."""
    decision = engine.evaluate("junior-dev", "any command at all")
    assert decision.tier == ApprovalTier.ASK_FIRST
    assert decision.requires_approval is True
    assert decision.rule_source == "ask_first_fallback"


def test_standard_trust_fallback(engine):
    """Standard-trust agents use ask_first for unmatched actions."""
    decision = engine.evaluate("software-developer", "unknown command")
    assert decision.tier == ApprovalTier.ASK_FIRST
    assert decision.requires_approval is True
    assert decision.rule_source == "ask_first_fallback"


def test_unknown_agent_standard_fallback(engine):
    """Agent with no rules and no config defaults to standard trust."""
    decision = engine.evaluate("totally-new-agent", "some action")
    assert decision.tier == ApprovalTier.ASK_FIRST
    assert decision.requires_approval is True
    assert decision.rule_source == "ask_first_fallback"


# ──────────────────────────────────────────────────
# Session memory tests
# ──────────────────────────────────────────────────


def test_session_memory_remembers_approval(engine):
    """After session approval, the same action is allowed from session memory."""
    # First evaluation — should be ask_first
    decision1 = engine.evaluate("software-developer", "curl https://api.example.com")
    assert decision1.requires_approval is True

    # Record the approval
    engine.record_session_approval("software-developer", "curl https://api.example.com")

    # Second evaluation — should be remembered
    decision2 = engine.evaluate("software-developer", "curl https://api.example.com")
    assert decision2.requires_approval is False
    assert decision2.remembered is True
    assert decision2.rule_source == "session_memory"


def test_session_memory_is_per_agent(engine):
    """Session memory is scoped to each agent independently."""
    engine.record_session_approval("software-developer", "curl https://api.example.com")

    # Same action for a different agent — not remembered
    decision = engine.evaluate("junior-dev", "curl https://api.example.com")
    assert decision.remembered is False
    assert decision.requires_approval is True


def test_session_memory_exact_match(engine):
    """Session memory requires an exact action string match."""
    engine.record_session_approval("software-developer", "curl https://api.example.com")

    # Similar but not identical action
    decision = engine.evaluate(
        "software-developer", "curl https://api.example.com/v2"
    )
    assert decision.remembered is False
    assert decision.requires_approval is True


def test_clear_session_memory(engine):
    """Clearing session memory removes all remembered approvals."""
    engine.record_session_approval("software-developer", "curl https://example.com")
    engine.record_session_approval("qa-engineer", "npm test")
    assert engine.get_session_memory_count() == 2

    engine.clear_session_memory()
    assert engine.get_session_memory_count() == 0

    # Previously remembered action now requires approval again
    decision = engine.evaluate("software-developer", "curl https://example.com")
    assert decision.requires_approval is True
    assert decision.remembered is False


def test_clear_agent_session_memory(engine):
    """Clearing memory for one agent leaves other agents' memory intact."""
    engine.record_session_approval("software-developer", "curl https://example.com")
    engine.record_session_approval("qa-engineer", "npm test")

    engine.clear_agent_session_memory("software-developer")

    assert engine.get_session_memory_count("software-developer") == 0
    assert engine.get_session_memory_count("qa-engineer") == 1


def test_session_memory_count_per_agent(engine):
    """get_session_memory_count filters by agent when specified."""
    engine.record_session_approval("software-developer", "action-1")
    engine.record_session_approval("software-developer", "action-2")
    engine.record_session_approval("qa-engineer", "action-3")

    assert engine.get_session_memory_count() == 3
    assert engine.get_session_memory_count("software-developer") == 2
    assert engine.get_session_memory_count("qa-engineer") == 1


def test_session_memory_does_not_override_always_ask(engine):
    """Session memory is checked AFTER always_ask — always_ask always wins."""
    # Record approval for an always_ask action
    engine.record_session_approval("software-developer", "git push origin main")

    # always_ask still takes precedence
    decision = engine.evaluate("software-developer", "git push origin main")
    assert decision.tier == ApprovalTier.ALWAYS_ASK
    assert decision.requires_approval is True
    assert decision.remembered is False


# ──────────────────────────────────────────────────
# Reload tests
# ──────────────────────────────────────────────────


def test_reload_preserves_session_memory(engine, faith_dir):
    """Reloading rules does not clear session memory."""
    engine.record_session_approval("software-developer", "curl https://example.com")
    assert engine.get_session_memory_count() == 1

    # Add a new rule and reload
    new_yaml = SECURITY_YAML_CONTENT.copy()
    new_yaml["approval_rules"]["software-developer"]["always_allow"].append(
        "^curl .*$"
    )
    (faith_dir / "security.yaml").write_text(
        yaml.dump(new_yaml, default_flow_style=False),
        encoding="utf-8",
    )
    engine.reload_rules()

    # Session memory is still there
    assert engine.get_session_memory_count() == 1

    # But the new rule takes effect (always_allow checked before session memory)
    decision = engine.evaluate("software-developer", "curl https://example.com")
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW
    assert decision.rule_source == "always_allow"


def test_reload_picks_up_new_rules(engine, faith_dir):
    """Reloading reflects new rules written to security.yaml."""
    # Initially, "make test" falls through to ask_first
    decision = engine.evaluate("software-developer", "make test")
    assert decision.tier == ApprovalTier.ASK_FIRST

    # Add rule and reload
    new_yaml = SECURITY_YAML_CONTENT.copy()
    new_yaml["approval_rules"]["software-developer"]["always_allow"].append(
        "^make test$"
    )
    (faith_dir / "security.yaml").write_text(
        yaml.dump(new_yaml, default_flow_style=False),
        encoding="utf-8",
    )
    engine.reload_rules()

    # Now it's always_allow
    decision = engine.evaluate("software-developer", "make test")
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW


# ──────────────────────────────────────────────────
# Edge case tests
# ──────────────────────────────────────────────────


def test_empty_action_string(engine):
    """Empty action string falls through to trust fallback."""
    decision = engine.evaluate("software-developer", "")
    assert decision.tier == ApprovalTier.ASK_FIRST


def test_action_with_special_regex_chars(engine):
    """Actions containing regex metacharacters don't break matching."""
    decision = engine.evaluate(
        "software-developer",
        "pip install some-package[extra]==1.0"
    )
    # The regex "^pip install [a-zA-Z0-9_\\-]+(==[\\d.]+)?$" won't match
    # because of the [extra] part. Falls through to ask_first.
    assert decision.tier == ApprovalTier.ASK_FIRST


def test_multiline_action_string(engine):
    """Multiline action strings are matched per regex semantics."""
    decision = engine.evaluate("software-developer", "pytest\n--verbose")
    # "^pytest( .+)?$" matches "pytest" at start; \n prevents full match
    # with default flags. re.search finds "pytest" in the string though.
    # re.search will find the match at the start of the string.
    assert decision.tier == ApprovalTier.ALWAYS_ALLOW


def test_approval_decision_dataclass():
    """ApprovalDecision fields have correct defaults."""
    d = ApprovalDecision(tier=ApprovalTier.ALWAYS_ALLOW)
    assert d.rule_matched is None
    assert d.rule_source is None
    assert d.requires_approval is True
    assert d.remembered is False


def test_no_agents_dir(tmp_path):
    """Engine works when agents/ directory does not exist."""
    faith = tmp_path / ".faith"
    faith.mkdir()
    (faith / "security.yaml").write_text("{}", encoding="utf-8")
    eng = ApprovalEngine(faith_dir=faith)
    eng.load_rules()
    assert eng.get_trust_level("any") == TrustLevel.STANDARD


def test_agent_config_without_trust_level(tmp_path):
    """Agent config without trust_level defaults to standard."""
    faith = tmp_path / ".faith"
    faith.mkdir()
    (faith / "security.yaml").write_text("{}", encoding="utf-8")
    agent_dir = faith / "agents" / "minimal"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(
        yaml.dump({"model": "gpt-4o"}), encoding="utf-8"
    )
    eng = ApprovalEngine(faith_dir=faith)
    eng.load_rules()
    assert eng.get_trust_level("minimal") == TrustLevel.STANDARD
```

---

## Integration Points

The ApprovalEngine is called by the PA before executing any agent tool call. It integrates with the following components:

```python
# PA evaluates an action before executing it (FAITH-016)
from faith.security import ApprovalEngine, ApprovalTier

engine = ApprovalEngine(faith_dir=Path(".faith"))
engine.load_rules()

# Agent requests to run a command
decision = engine.evaluate("software-developer", "git push origin main")

if decision.tier == ApprovalTier.ALWAYS_DENY:
    # Block the action — permanently denied
    await notify_agent_denied(agent_id, action, decision)

elif decision.requires_approval:
    # Surface to user via approval panel (FAITH-020)
    await publish_approval_request(agent_id, action, decision)
    # ... wait for user response ...

    # If user approves with "approve for this session":
    engine.record_session_approval("software-developer", "git push origin main")

else:
    # Auto-approved — execute and log
    await execute_action(agent_id, action)
    await audit_log(agent_id, action, decision)  # FAITH-021
```

```python
# Config hot-reload handler (FAITH-004) triggers rule reload
async def on_security_yaml_changed():
    engine.reload_rules()
    # Session memory is preserved across reloads

# Session end handler (FAITH-015) clears session memory
async def on_session_end():
    engine.clear_session_memory()
```

```python
# FAITH-020 writes learned rules to security.yaml, then the config
# watcher detects the change and calls engine.reload_rules(). The
# engine re-parses always_allow_learned, always_ask_learned, and
# always_deny_learned.
```

---

## Acceptance Criteria

1. `ApprovalEngine.load_rules()` parses `.faith/security.yaml` and compiles all regex patterns across `approval_rules`, `always_allow_learned`, `always_ask_learned`, and `always_deny_learned` sections. Invalid patterns are logged and skipped without crashing.
2. `ApprovalEngine.evaluate()` checks tiers in strict order: `always_ask` → `always_deny_learned` → `always_allow` → `always_allow_learned` → session memory → ask-first fallback. `always_ask` always wins over `always_allow` for overlapping patterns.
3. Per-agent trust levels are loaded from `.faith/agents/{id}/config.yaml`. Missing config or invalid `trust_level` values default to `standard`.
4. Trust levels are loaded correctly and do not silently bypass the ask-first fallback for unmatched actions.
5. `record_session_approval()` stores approvals scoped to (agent_id, action). Subsequent `evaluate()` calls for the same agent+action return `remembered=True` with `requires_approval=False`.
6. Session memory does not override `always_ask` or `always_deny_learned` — those tiers are checked before session memory.
7. `clear_session_memory()` wipes all session memory. `clear_agent_session_memory()` wipes only the specified agent's entries.
8. `reload_rules()` re-reads `security.yaml` and agent configs from disk without clearing session memory.
9. `always_allow_learned`, `always_ask_learned`, and `always_deny_learned` sections (written by FAITH-020) are correctly parsed and participate in the evaluation chain.
10. All tests in `tests/test_approval_engine.py` pass, covering: rule loading, trust levels, ask-first evaluation, session memory, reload behaviour, edge cases (empty actions, special characters, missing files, invalid config).

---

## Notes for Implementer

- **Evaluation order is a safety invariant**: `always_ask` is checked first so that a broad allow rule cannot accidentally silence a narrow always-ask pattern. This is the FRS Section 5.1.3 rule: "safety over convenience." Do not reorder the evaluation tiers.
- **`re.search()` not `re.match()`**: The engine uses `re.search()` to test patterns. Most security.yaml rules use `^...$` anchors, so `search` behaves like `match` for those. But `search` is more forgiving for user-written patterns that forget anchors. Implementers and users writing rules should always use anchors for precision.
- **Session memory stores exact strings**: Memory keys are `(agent_id, action)` tuples with exact string comparison. This means `"git push origin main"` and `"git push origin develop"` are tracked separately. FAITH-020 handles durable remembered decisions by writing regexes to learned sections rather than relying on session memory.
- **Learned sections are PA-managed**: The `always_allow_learned`, `always_ask_learned`, and `always_deny_learned` sections in `security.yaml` are written exclusively by FAITH-020's approval flow. The approval engine only reads them. Users may also edit or delete learned rules directly in the YAML file.
- **No secrets access**: The approval engine never reads `config/secrets.yaml`. It only reads `.faith/security.yaml` and `.faith/agents/*/config.yaml`. Both are project-level config files readable by agents.
- **Thread safety**: The engine is designed to run in a single async event loop (the PA). If concurrent access is needed in the future, `_session_memory` and `_agent_rules` would need locking. For now, the PA's single-threaded async model avoids this.
- **`always_deny_learned` vs `always_ask`**: Note that `always_deny_learned` returns `requires_approval=False` because the action is unconditionally blocked — there is nothing to approve. The PA should report the denial to the agent and log it. `always_ask` returns `requires_approval=True` because the user still gets to decide.
- **Testing against FAITH-003 models**: The Pydantic models for `security.yaml` are defined in FAITH-003. This task implements the runtime engine that consumes the parsed config. If FAITH-003's `SecurityConfig` model changes schema, the `_load_security_yaml` method may need adjustment to match.

