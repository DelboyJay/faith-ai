"""Ask-first approval engine for FAITH tool actions."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from faith_pa.config.models import AgentConfig, TrustLevel

logger = logging.getLogger("faith.security.approval_engine")


class ApprovalTier(str, Enum):
    ALWAYS_ALLOW = "always_allow"
    APPROVE_SESSION = "approve_session"
    ALWAYS_ASK = "always_ask"
    ALWAYS_DENY = "deny_permanently"
    ASK_FIRST = "ask_first"


@dataclass(slots=True)
class ApprovalDecision:
    tier: ApprovalTier
    rule_matched: str | None = None
    rule_source: str | None = None
    requires_approval: bool = True
    remembered: bool = False


@dataclass(slots=True)
class _CompiledRuleSet:
    always_ask: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    always_allow: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    always_ask_learned: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    always_allow_learned: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    always_deny_learned: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)


class ApprovalEngine:
    """Evaluate actions against ask-first security rules."""

    def __init__(self, faith_dir: Path):
        self.faith_dir = Path(faith_dir)
        self._security_yaml_path = self.faith_dir / "security.yaml"
        self._agent_rules: dict[str, _CompiledRuleSet] = {}
        self._trust_levels: dict[str, TrustLevel] = {}
        self._session_memory: dict[tuple[str, str], ApprovalDecision] = {}

    def load_rules(self) -> None:
        self._load_security_yaml()
        self._load_trust_levels()

    def reload_rules(self) -> None:
        self.load_rules()

    def evaluate(self, agent_id: str, action: str) -> ApprovalDecision:
        ruleset = self._agent_rules.get(agent_id, _CompiledRuleSet())

        match = self._match_any(action, ruleset.always_ask)
        if match:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_ASK,
                rule_matched=match,
                rule_source="always_ask",
                requires_approval=True,
            )

        match = self._match_any(action, ruleset.always_ask_learned)
        if match:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_ASK,
                rule_matched=match,
                rule_source="always_ask_learned",
                requires_approval=True,
            )

        match = self._match_any(action, ruleset.always_deny_learned)
        if match:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_DENY,
                rule_matched=match,
                rule_source="always_deny_learned",
                requires_approval=False,
            )

        match = self._match_any(action, ruleset.always_allow)
        if match:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_ALLOW,
                rule_matched=match,
                rule_source="always_allow",
                requires_approval=False,
            )

        match = self._match_any(action, ruleset.always_allow_learned)
        if match:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_ALLOW,
                rule_matched=match,
                rule_source="always_allow_learned",
                requires_approval=False,
            )

        remembered = self._session_memory.get((agent_id, action))
        if remembered is not None:
            return ApprovalDecision(
                tier=ApprovalTier.APPROVE_SESSION,
                rule_matched=remembered.rule_matched,
                rule_source="session_memory",
                requires_approval=False,
                remembered=True,
            )

        return ApprovalDecision(
            tier=ApprovalTier.ASK_FIRST,
            rule_source="ask_first_fallback",
            requires_approval=True,
        )

    def record_session_approval(
        self,
        agent_id: str,
        action: str,
        *,
        rule_matched: str | None = None,
    ) -> None:
        self._session_memory[(agent_id, action)] = ApprovalDecision(
            tier=ApprovalTier.APPROVE_SESSION,
            rule_matched=rule_matched,
            rule_source="approve_session",
            requires_approval=False,
            remembered=True,
        )

    def clear_session_memory(self) -> None:
        self._session_memory.clear()

    def get_trust_level(self, agent_id: str) -> TrustLevel:
        return self._trust_levels.get(agent_id, TrustLevel.STANDARD)

    def _load_security_yaml(self) -> None:
        if not self._security_yaml_path.exists():
            self._agent_rules = {}
            return

        with self._security_yaml_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}

        self._agent_rules = {}
        approval_rules = data.get("approval_rules", {})
        always_allow_learned = data.get("always_allow_learned", {})
        always_ask_learned = data.get("always_ask_learned", {})
        always_deny_learned = data.get("always_deny_learned", {})

        all_agent_ids = set(approval_rules.keys())
        all_agent_ids.update(always_allow_learned.keys())
        all_agent_ids.update(always_ask_learned.keys())
        all_agent_ids.update(always_deny_learned.keys())

        for agent_id in all_agent_ids:
            ruleset = _CompiledRuleSet()
            raw_rules = (
                approval_rules.get(agent_id, {})
                if isinstance(approval_rules.get(agent_id, {}), dict)
                else {}
            )
            ruleset.always_ask = self._compile_patterns(
                raw_rules.get("always_ask", []), agent_id, "always_ask"
            )
            ruleset.always_allow = self._compile_patterns(
                raw_rules.get("always_allow", []), agent_id, "always_allow"
            )
            ruleset.always_ask_learned = self._compile_patterns(
                always_ask_learned.get(agent_id, []), agent_id, "always_ask_learned"
            )
            ruleset.always_allow_learned = self._compile_patterns(
                always_allow_learned.get(agent_id, []), agent_id, "always_allow_learned"
            )
            ruleset.always_deny_learned = self._compile_patterns(
                always_deny_learned.get(agent_id, []), agent_id, "always_deny_learned"
            )
            self._agent_rules[agent_id] = ruleset

    def _load_trust_levels(self) -> None:
        self._trust_levels = {}
        agents_dir = self.faith_dir / "agents"
        if not agents_dir.exists():
            return

        for config_path in agents_dir.glob("*/config.yaml"):
            try:
                with config_path.open("r", encoding="utf-8") as handle:
                    raw = yaml.safe_load(handle) or {}
                config = AgentConfig.model_validate(raw)
                self._trust_levels[config_path.parent.name] = config.trust
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Failed to load trust level from %s: %s", config_path, exc)

    @staticmethod
    def _compile_patterns(
        patterns: object, agent_id: str, section: str
    ) -> list[tuple[str, re.Pattern[str]]]:
        if not isinstance(patterns, list):
            return []
        compiled: list[tuple[str, re.Pattern[str]]] = []
        for pattern in patterns:
            if not isinstance(pattern, str):
                continue
            try:
                compiled.append((pattern, re.compile(pattern)))
            except re.error as exc:
                logger.warning(
                    "Skipping invalid regex for %s/%s: %s (%s)", agent_id, section, pattern, exc
                )
        return compiled

    @staticmethod
    def _match_any(action: str, patterns: list[tuple[str, re.Pattern[str]]]) -> str | None:
        for pattern, compiled in patterns:
            if compiled.search(action):
                return pattern
        return None

