"""Description:
    Evaluate ask-first approval rules for FAITH tool and filesystem actions.

Requirements:
    - Load persistent and learned rule sets from ``security.yaml``.
    - Apply ask, deny, and allow rules in the FRS precedence order before session memory.
    - Support filesystem session memory using exact, folder, and path-pattern scopes.
    - Fall back to ask-first behaviour when no rule matches.
"""

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
    """Description:
        Enumerate the approval outcomes used by the FAITH approval engine.

    Requirements:
        - Use the canonical v1 approval vocabulary from the FRS.
    """

    ALWAYS_ALLOW = "always_allow"
    APPROVE_SESSION = "approve_session"
    ALWAYS_ASK = "always_ask"
    ALWAYS_DENY = "always_deny"
    ASK_FIRST = "ask_first"


@dataclass(slots=True)
class ApprovalDecision:
    """Description:
        Represent the approval decision returned for one requested action.

    Requirements:
        - Preserve the effective tier, matched rule, and whether approval is still required.

    :param tier: Effective approval tier for the action.
    :param rule_matched: Regex rule that matched the action, when one exists.
    :param rule_source: Configuration section that supplied the matching rule.
    :param requires_approval: Whether user approval is still required.
    :param remembered: Whether the decision came from session memory.
    """

    tier: ApprovalTier
    rule_matched: str | None = None
    rule_source: str | None = None
    requires_approval: bool = True
    remembered: bool = False


@dataclass(slots=True)
class _CompiledRuleSet:
    """Description:
        Hold the compiled regex rules for one agent's approval policy.

    Requirements:
        - Keep each persistent and learned rule section separate for precedence handling.
    """

    always_ask: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    always_deny: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    always_allow: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    always_ask_learned: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    always_allow_learned: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    always_deny_learned: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)


@dataclass(slots=True)
class _SessionMemoryRule:
    """Description:
        Hold one remembered session-scoped approval or denial rule.

    Requirements:
        - Preserve the compiled session pattern and decision tier.
        - Keep the original rule text for audit and debugging visibility.
    """

    tier: ApprovalTier
    pattern_text: str
    pattern: re.Pattern[str]


class ApprovalEngine:
    """Description:
        Evaluate runtime actions against FAITH approval rules and session memory.

    Requirements:
        - Load per-agent approval rules from ``security.yaml``.
        - Preserve per-agent trust levels from the agent configs.
        - Apply explicit rule precedence before falling back to ask-first behaviour.

    :param faith_dir: Project ``.faith`` directory containing security and agent config.
    """

    def __init__(self, faith_dir: Path):
        """Description:
            Initialise the approval engine state.

        Requirements:
            - Keep the security file path anchored under the supplied ``.faith`` directory.
            - Start with empty rule, trust-level, and session-memory state.

        :param faith_dir: Project ``.faith`` directory containing security and agent config.
        """

        self.faith_dir = Path(faith_dir)
        self._security_yaml_path = self.faith_dir / "security.yaml"
        self._agent_rules: dict[str, _CompiledRuleSet] = {}
        self._trust_levels: dict[str, TrustLevel] = {}
        self._session_memory: dict[str, list[_SessionMemoryRule]] = {}

    def load_rules(self) -> None:
        """Description:
            Load persistent approval rules and trust levels from disk.

        Requirements:
            - Refresh both the security rule set and agent trust levels.
        """

        self._load_security_yaml()
        self._load_trust_levels()

    def reload_rules(self) -> None:
        """Description:
            Reload the on-disk approval policy.

        Requirements:
            - Delegate to the main rule-loading routine.
        """

        self.load_rules()

    def evaluate(self, agent_id: str, action: str) -> ApprovalDecision:
        """Description:
            Evaluate one action against the approval policy for an agent.

        Requirements:
            - Apply ask rules before deny rules, deny rules before allow rules, and session memory afterward.
            - Allow persistent rules to override remembered session decisions.

        :param agent_id: Agent identifier requesting the action.
        :param action: Canonical action string to evaluate.
        :returns: Effective approval decision for the action.
        """

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

        match = self._match_any(action, ruleset.always_deny)
        if match:
            return ApprovalDecision(
                tier=ApprovalTier.ALWAYS_DENY,
                rule_matched=match,
                rule_source="always_deny",
                requires_approval=False,
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

        remembered = self._match_session_memory(agent_id, action)
        if remembered is not None:
            return remembered

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
        scope: str | None = None,
        rule_matched: str | None = None,
    ) -> None:
        """Description:
            Remember a one-session approval for a specific action.

        Requirements:
            - Delegate to the generic session-decision recorder using the approval tier.

        :param agent_id: Agent identifier receiving the session approval.
        :param action: Canonical action string that was approved.
        :param scope: Optional filesystem scope such as ``exact``, ``folder``, or ``glob``.
        :param rule_matched: Optional explicit rule string associated with the approval.
        """

        self.record_session_decision(
            agent_id,
            action,
            scope=scope,
            decision=ApprovalTier.APPROVE_SESSION,
            rule_matched=rule_matched,
        )

    def record_session_decision(
        self,
        agent_id: str,
        action: str,
        *,
        scope: str | None = None,
        decision: ApprovalTier = ApprovalTier.APPROVE_SESSION,
        rule_matched: str | None = None,
    ) -> None:
        """Description:
            Remember one session-scoped approval or denial rule.

        Requirements:
            - Support exact, folder, and glob session scopes for filesystem actions.
            - Preserve the remembered decision tier so both approvals and denials can be replayed.

        :param agent_id: Agent identifier receiving the session rule.
        :param action: Canonical action string to remember.
        :param scope: Optional filesystem scope such as ``exact``, ``folder``, or ``glob``.
        :param decision: Remembered decision tier to replay.
        :param rule_matched: Optional explicit rule text associated with the remembered decision.
        """

        pattern_text = rule_matched or self._build_session_pattern(action, scope=scope)
        self._session_memory.setdefault(agent_id, []).append(
            _SessionMemoryRule(
                tier=decision,
                pattern_text=pattern_text,
                pattern=re.compile(pattern_text),
            )
        )

    def clear_session_memory(self) -> None:
        """Description:
            Clear all in-memory session approvals.

        Requirements:
            - Remove every remembered session approval entry.
        """

        self._session_memory.clear()

    def clear_agent_session_memory(self, agent_id: str) -> None:
        """Description:
            Clear session memory entries for one specific agent.

        Requirements:
            - Leave other agents' remembered session rules untouched.

        :param agent_id: Agent identifier whose session memory should be cleared.
        """

        self._session_memory.pop(agent_id, None)

    def get_trust_level(self, agent_id: str) -> TrustLevel:
        """Description:
            Return the configured trust level for one agent.

        Requirements:
            - Fall back to ``standard`` trust when no explicit agent config exists.

        :param agent_id: Agent identifier to inspect.
        :returns: Effective trust level for the agent.
        """

        return self._trust_levels.get(agent_id, TrustLevel.STANDARD)

    def _load_security_yaml(self) -> None:
        """Description:
            Load persistent approval rules from the project security file.

        Requirements:
            - Reset the compiled rule map when the file does not exist.
            - Compile per-agent persistent and learned rule sections.
        """

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
            ruleset.always_deny = self._compile_patterns(
                raw_rules.get("always_deny", []), agent_id, "always_deny"
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
        """Description:
            Load per-agent trust levels from the agent configuration files.

        Requirements:
            - Ignore missing agent directories.
            - Log and skip malformed agent configuration files.
        """

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
        """Description:
            Compile a list of regex patterns from the security configuration.

        Requirements:
            - Ignore non-list sections and non-string entries.
            - Skip invalid regular expressions while logging the issue.

        :param patterns: Raw pattern collection from YAML.
        :param agent_id: Agent identifier owning the rules.
        :param section: Security section name being compiled.
        :returns: Compiled regex rule list.
        """

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
        """Description:
            Return the first rule pattern that matches an action string.

        Requirements:
            - Preserve the original pattern text in the returned value.

        :param action: Canonical action string to test.
        :param patterns: Compiled rule list to evaluate.
        :returns: Matching rule pattern text, if any.
        """

        for pattern, compiled in patterns:
            if compiled.search(action):
                return pattern
        return None

    def _match_session_memory(self, agent_id: str, action: str) -> ApprovalDecision | None:
        """Description:
            Return the first remembered session decision that matches an action.

        Requirements:
            - Evaluate remembered rules only for the requesting agent.
            - Preserve the remembered rule text in the returned decision.

        :param agent_id: Agent identifier to inspect.
        :param action: Canonical action string to test.
        :returns: Matching remembered decision, if one exists.
        """

        for remembered in self._session_memory.get(agent_id, []):
            if remembered.pattern.search(action):
                return ApprovalDecision(
                    tier=remembered.tier,
                    rule_matched=remembered.pattern_text,
                    rule_source="session_memory",
                    requires_approval=False,
                    remembered=True,
                )
        return None

    @staticmethod
    def _build_session_pattern(action: str, *, scope: str | None = None) -> str:
        """Description:
            Build the regex pattern used for one remembered session decision.

        Requirements:
            - Use exact matching by default.
            - Support folder and glob scopes for filesystem action keys.

        :param action: Canonical action string to remember.
        :param scope: Optional scope hint such as ``exact``, ``folder``, or ``glob``.
        :returns: Regex pattern string for the remembered session decision.
        """

        scope_kind = (scope or "exact").lower()
        if not action.startswith("filesystem:"):
            return rf"^{re.escape(action)}$"

        try:
            tool, verb, target = action.split(":", 2)
        except ValueError:
            return rf"^{re.escape(action)}$"

        normalized_target = target.replace("\\", "/")
        prefix = re.escape(f"{tool}:{verb}:")
        if scope_kind == "folder":
            folder = (
                normalized_target.rsplit("/", 1)[0]
                if "/" in normalized_target
                else normalized_target
            )
            folder = folder.rstrip("/")
            return rf"^{prefix}{re.escape(folder)}(?:/.*)?$"
        if scope_kind == "glob":
            return rf"^{prefix}{ApprovalEngine._glob_to_regex(normalized_target)}$"
        return rf"^{re.escape(f'{tool}:{verb}:{normalized_target}')}$"

    @staticmethod
    def _glob_to_regex(pattern: str) -> str:
        """Description:
            Convert a simple glob pattern into a regex fragment.

        Requirements:
            - Support ``*`` and ``?`` wildcards while escaping all other characters.

        :param pattern: Glob-style pattern to convert.
        :returns: Regex fragment representing the glob pattern.
        """

        parts: list[str] = []
        for char in pattern:
            if char == "*":
                parts.append(".*")
            elif char == "?":
                parts.append(".")
            else:
                parts.append(re.escape(char))
        return "".join(parts)
