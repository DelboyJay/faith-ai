# FAITH-050 — Privacy Profile Enforcement & Provider Knowledge Base

**Phase:** 9 — Installation & First-Run
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-049, FAITH-003
**FRS Reference:** Section 9.3.2, 9.4

---

## Objective

Embed the curated provider privacy reference dataset into a standalone, versioned JSON file at `~/.faith/data/provider-privacy.json` and implement runtime privacy profile enforcement in the PA. Enforcement covers: filtering model recommendations to those compliant with the active profile, warning users and requiring explicit override acknowledgement when a non-compliant model is configured, publishing `system:config_changed` events when the profile changes, and handling mid-session profile changes per FRS Section 7.3 — surfacing per-agent compliance cards in the Web UI where the user decides individually (switch model or acknowledge override). No automatic forced reassignment ever occurs.

---

## Architecture

```
~/.faith/data/
└── provider-privacy.json          ← Curated, versioned provider T&C dataset (new file)

faith/privacy/
├── __init__.py                    ← Package marker (new)
├── knowledge_base.py              ← Load/query provider-privacy.json; ProviderRecord dataclass (new)
├── enforcer.py                    ← PrivacyEnforcer: profile validation, override flow, hot-reload handler (new)
└── compliance_card.py             ← ComplianceCard dataclass + card builder for Web UI payload (new)

faith/wizard/
└── privacy_kb.py                  ← Extend existing stub from FAITH-049: wire to knowledge_base.py

tests/
├── test_privacy_knowledge_base.py ← Dataset integrity, lookup, tier filtering (new)
├── test_privacy_enforcer.py       ← Enforcement logic, override flow, event publishing (new)
└── test_compliance_card.py        ← Card building, mid-session change handling (new)
```

The `faith/wizard/privacy_kb.py` file created in FAITH-049 contains a hardcoded inline `PROVIDER_PRIVACY_KB` list. FAITH-050 migrates that data to `provider-privacy.json`, and updates `privacy_kb.py` to delegate lookups to `faith.privacy.knowledge_base` rather than its own hardcoded list. The wizard's `PrivacyKB` public API surface is unchanged so FAITH-049 tests continue to pass.

---

## Files to Create / Modify

### 1. `~/.faith/data/provider-privacy.json`

The authoritative, versioned provider privacy dataset. Updated with each FAITH release. Not a live web scrape. The dataset covers all providers and model families FAITH recommends. The `faith_privacy_tier` field maps directly to the three FAITH privacy profiles (`public` / `internal` / `confidential`): a tier value of `"confidential"` means the provider meets even the strictest profile; `"internal"` means it meets Internal and Public but not Confidential; `"public"` means it is only safe for the Public profile.

```json
{
  "_meta": {
    "version": "0.1.0",
    "updated": "2026-03",
    "description": "Curated FAITH provider privacy knowledge base. Updated with each FAITH release. Not a live web scrape.",
    "fields": [
      "provider",
      "model_pattern",
      "training_opt_out",
      "human_review",
      "data_retention_days",
      "dpa_available",
      "compliance",
      "faith_privacy_tier",
      "notes"
    ]
  },
  "providers": [
    {
      "provider": "ollama",
      "model_pattern": "*",
      "training_opt_out": true,
      "human_review": false,
      "data_retention_days": 0,
      "dpa_available": false,
      "compliance": [],
      "faith_privacy_tier": "confidential",
      "notes": "Fully local inference. No data leaves the host machine. Compatible with all FAITH privacy profiles."
    },
    {
      "provider": "openrouter/anthropic",
      "model_pattern": "claude-*",
      "training_opt_out": true,
      "human_review": false,
      "data_retention_days": 30,
      "dpa_available": true,
      "compliance": ["SOC2", "GDPR"],
      "faith_privacy_tier": "internal",
      "notes": "Anthropic API offers no-training guarantee by default. DPA available on request. Suitable for Internal and Public profiles."
    },
    {
      "provider": "openrouter/openai",
      "model_pattern": "gpt-*",
      "training_opt_out": true,
      "human_review": false,
      "data_retention_days": 30,
      "dpa_available": true,
      "compliance": ["SOC2", "GDPR", "HIPAA"],
      "faith_privacy_tier": "internal",
      "notes": "OpenAI API does not train on API data by default. Enterprise DPA available. Suitable for Internal and Public profiles."
    },
    {
      "provider": "openrouter/openai",
      "model_pattern": "o1-*",
      "training_opt_out": true,
      "human_review": false,
      "data_retention_days": 30,
      "dpa_available": true,
      "compliance": ["SOC2", "GDPR"],
      "faith_privacy_tier": "internal",
      "notes": "Same data policy as gpt-* family via OpenAI API."
    },
    {
      "provider": "openrouter/google",
      "model_pattern": "gemini-*",
      "training_opt_out": true,
      "human_review": false,
      "data_retention_days": 30,
      "dpa_available": true,
      "compliance": ["SOC2", "GDPR"],
      "faith_privacy_tier": "internal",
      "notes": "Google Cloud API does not train on API data when using the Vertex AI endpoint. Standard API may differ — DPA available via Google Cloud."
    },
    {
      "provider": "openrouter/meta-llama",
      "model_pattern": "llama-*",
      "training_opt_out": false,
      "human_review": true,
      "data_retention_days": 30,
      "dpa_available": false,
      "compliance": [],
      "faith_privacy_tier": "public",
      "notes": "Routed through third-party OpenRouter infrastructure. Training and human review policies vary by underlying host. Only suitable for Public (non-sensitive) data."
    },
    {
      "provider": "openrouter/mistralai",
      "model_pattern": "mistral-*",
      "training_opt_out": false,
      "human_review": true,
      "data_retention_days": 30,
      "dpa_available": false,
      "compliance": [],
      "faith_privacy_tier": "public",
      "notes": "Routed through OpenRouter. Training and review policies vary. Only suitable for Public data."
    },
    {
      "provider": "openrouter/mistralai",
      "model_pattern": "codestral-*",
      "training_opt_out": false,
      "human_review": true,
      "data_retention_days": 30,
      "dpa_available": false,
      "compliance": [],
      "faith_privacy_tier": "public",
      "notes": "Routed through OpenRouter. Only suitable for Public data."
    },
    {
      "provider": "openrouter/deepseek",
      "model_pattern": "deepseek-*",
      "training_opt_out": false,
      "human_review": true,
      "data_retention_days": 90,
      "dpa_available": false,
      "compliance": [],
      "faith_privacy_tier": "public",
      "notes": "Data may be processed on servers outside user jurisdiction. Only suitable for non-sensitive, Public data."
    }
  ]
}
```

---

### 2. `faith/privacy/__init__.py`

```python
"""FAITH privacy profile enforcement and provider knowledge base.

Implements runtime privacy enforcement:
  - Loads and queries the curated provider-privacy.json dataset.
  - Filters model recommendations to those compliant with the active profile.
  - Warns on non-compliant model assignment and requires explicit override.
  - Publishes system:config_changed events when the privacy profile changes.
  - Handles mid-session profile changes per FRS Section 7.3.

FRS Reference: Section 9.3.2, 9.4
"""
```

---

### 3. `faith/privacy/knowledge_base.py`

```python
"""Provider privacy knowledge base loader and query interface.

Loads the curated provider-privacy.json dataset from the FAITH data
directory and provides tier-based filtering and per-provider lookups.
The dataset is versioned and updated with each FAITH release — it is
never fetched from the web at runtime.

FRS Reference: Section 9.4
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("faith.privacy.knowledge_base")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Canonical path inside the FAITH framework home
DEFAULT_KB_PATH = Path.home() / ".faith" / "data" / "provider-privacy.json"

# Tier rank used for profile comparisons: higher rank = stricter profile.
# A provider is permitted if its tier rank >= the active profile's rank.
TIER_RANK: dict[str, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderRecord:
    """Privacy metadata for a single LLM provider or model family.

    Attributes:
        provider: Provider identifier (e.g. "openrouter/anthropic", "ollama").
        model_pattern: Glob-style pattern matching model names, or "*" for all.
        training_opt_out: Whether messages are excluded from training by default.
        human_review: Whether human operators may review conversations.
        data_retention_days: How long messages are stored (0 = not stored,
            -1 = indefinite).
        dpa_available: Whether a Data Processing Agreement is offered.
        compliance: Notable certifications (e.g. ["SOC2", "GDPR", "HIPAA"]).
        faith_privacy_tier: The most restrictive FAITH profile this provider
            satisfies. One of "public", "internal", "confidential".
        notes: Human-readable clarification notes included in UI cards.
    """
    provider: str
    model_pattern: str
    training_opt_out: bool
    human_review: bool
    data_retention_days: int
    dpa_available: bool
    compliance: list[str]
    faith_privacy_tier: str
    notes: str = ""


# ---------------------------------------------------------------------------
# ProviderKnowledgeBase
# ---------------------------------------------------------------------------

class ProviderKnowledgeBase:
    """Loads and queries the provider privacy dataset.

    Reads provider-privacy.json once at construction. All queries run
    in memory — no disk I/O after initialisation.

    Args:
        kb_path: Path to provider-privacy.json. Defaults to
            ~/.faith/data/provider-privacy.json.

    Raises:
        FileNotFoundError: If the JSON file does not exist.
        ValueError: If the JSON structure is invalid.
    """

    def __init__(self, kb_path: Optional[Path] = None) -> None:
        self._path = kb_path or DEFAULT_KB_PATH
        self._records: list[ProviderRecord] = []
        self._version: str = "unknown"
        self._load()

    # --- Public API ---------------------------------------------------------

    @property
    def version(self) -> str:
        """Dataset version string from the _meta block."""
        return self._version

    @property
    def records(self) -> list[ProviderRecord]:
        """All records in the knowledge base (read-only view)."""
        return list(self._records)

    def get_permitted_providers(self, privacy_profile: str) -> list[ProviderRecord]:
        """Return records compatible with the given privacy profile.

        A record is permitted when its `faith_privacy_tier` rank is greater
        than or equal to the rank of `privacy_profile`. For example, with
        profile "internal", only records with tier "internal" or "confidential"
        are returned — public-only providers are excluded.

        Args:
            privacy_profile: "public", "internal", or "confidential".

        Returns:
            Filtered list of ProviderRecord entries.

        Raises:
            ValueError: If privacy_profile is not a known profile name.
        """
        if privacy_profile not in TIER_RANK:
            raise ValueError(
                f"Unknown privacy profile '{privacy_profile}'. "
                f"Expected one of: {list(TIER_RANK)}"
            )
        required_rank = TIER_RANK[privacy_profile]
        return [
            r for r in self._records
            if TIER_RANK.get(r.faith_privacy_tier, 0) >= required_rank
        ]

    def lookup(self, provider: str, model: str) -> Optional[ProviderRecord]:
        """Find the best-matching record for a provider/model pair.

        Matches first on exact provider, then on model_pattern. A pattern
        of "*" matches any model name. Returns the first match found (the
        dataset is ordered from most-specific to least-specific per provider).

        Args:
            provider: Provider identifier (e.g. "openrouter/anthropic").
            model: Model name without the provider prefix (e.g. "claude-sonnet-4-6").

        Returns:
            Matching ProviderRecord, or None if no record is found.
        """
        import fnmatch
        for record in self._records:
            if record.provider != provider:
                continue
            if fnmatch.fnmatch(model, record.model_pattern):
                return record
        return None

    def is_compliant(self, provider: str, model: str, privacy_profile: str) -> bool:
        """Check whether a specific provider/model is compliant with the profile.

        Args:
            provider: Provider identifier.
            model: Model name (without provider prefix).
            privacy_profile: Active FAITH privacy profile.

        Returns:
            True if the provider/model meets the profile requirement.
            Returns False if no record is found (unknown providers are blocked
            by default — unknown is not safe).
        """
        record = self.lookup(provider, model)
        if record is None:
            logger.warning(
                "Provider '%s' model '%s' not found in privacy KB — "
                "treating as non-compliant.",
                provider, model,
            )
            return False
        required_rank = TIER_RANK.get(privacy_profile, 0)
        tier_rank = TIER_RANK.get(record.faith_privacy_tier, 0)
        return tier_rank >= required_rank

    # --- Internal -----------------------------------------------------------

    def _load(self) -> None:
        """Parse provider-privacy.json into ProviderRecord instances."""
        if not self._path.exists():
            raise FileNotFoundError(
                f"Provider privacy knowledge base not found at {self._path}. "
                "Ensure FAITH is correctly installed."
            )

        with self._path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)

        meta = raw.get("_meta", {})
        self._version = meta.get("version", "unknown")

        providers_raw = raw.get("providers", [])
        if not isinstance(providers_raw, list):
            raise ValueError(
                f"Invalid provider-privacy.json: 'providers' must be a list."
            )

        records = []
        for item in providers_raw:
            try:
                records.append(
                    ProviderRecord(
                        provider=item["provider"],
                        model_pattern=item["model_pattern"],
                        training_opt_out=bool(item["training_opt_out"]),
                        human_review=bool(item["human_review"]),
                        data_retention_days=int(item["data_retention_days"]),
                        dpa_available=bool(item["dpa_available"]),
                        compliance=list(item.get("compliance", [])),
                        faith_privacy_tier=item["faith_privacy_tier"],
                        notes=item.get("notes", ""),
                    )
                )
            except (KeyError, TypeError) as exc:
                raise ValueError(
                    f"Malformed provider record in privacy KB: {exc!r} — {item}"
                ) from exc

        self._records = records
        logger.info(
            "Provider privacy KB loaded: %d records, version %s",
            len(self._records), self._version,
        )
```

---

### 4. `faith/privacy/compliance_card.py`

```python
"""Compliance card builder for the FAITH Web UI.

When the PA detects a non-compliant model assignment — either at
configuration time or following a mid-session privacy profile change —
it surfaces a compliance card in the Web UI. The card presents the
agent name, current model, the privacy issue, and two user actions:
switch to a compliant model, or acknowledge and continue with override.

FRS Reference: Section 9.3.2, 9.4, 7.3.1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from faith.privacy.knowledge_base import ProviderRecord


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ComplianceCard:
    """Payload describing a single non-compliant agent model assignment.

    Sent to the Web UI as a JSON-serialisable dict. The UI renders one
    card per non-compliant agent; the user acts on each card independently.

    Attributes:
        agent_id: The agent's directory-level identifier (e.g. "software-developer").
        agent_label: Human-readable display name.
        current_model: Full model string currently assigned (e.g. "openrouter/meta-llama/llama-*").
        privacy_profile: The active privacy profile that is not satisfied.
        record: The matched ProviderRecord (or None if provider is unknown).
        suggested_models: Ordered list of compliant model strings the PA
            recommends as replacements. May be empty if no alternatives exist.
        override_acknowledged: Set to True when the user explicitly accepts
            the non-compliant assignment. Defaults to False.
    """
    agent_id: str
    agent_label: str
    current_model: str
    privacy_profile: str
    record: Optional[ProviderRecord]
    suggested_models: list[str] = field(default_factory=list)
    override_acknowledged: bool = False

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict for the Web UI event payload."""
        compliance = None
        if self.record:
            compliance = {
                "faith_privacy_tier": self.record.faith_privacy_tier,
                "training_opt_out": self.record.training_opt_out,
                "human_review": self.record.human_review,
                "data_retention_days": self.record.data_retention_days,
                "dpa_available": self.record.dpa_available,
                "compliance": self.record.compliance,
                "notes": self.record.notes,
            }
        return {
            "type": "compliance_card",
            "agent_id": self.agent_id,
            "agent_label": self.agent_label,
            "current_model": self.current_model,
            "privacy_profile": self.privacy_profile,
            "provider_record": compliance,
            "suggested_models": self.suggested_models,
            "override_acknowledged": self.override_acknowledged,
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_compliance_cards(
    agent_assignments: list[dict],
    privacy_profile: str,
    kb: "ProviderKnowledgeBase",  # noqa: F821 — avoid circular import at module level
    permitted_models: list[str],
) -> list[ComplianceCard]:
    """Build compliance cards for all non-compliant agent model assignments.

    Iterates over agent_assignments, checks each model against the KB,
    and returns a ComplianceCard for every agent that does not satisfy
    the active privacy profile.

    Args:
        agent_assignments: List of dicts with keys "agent_id", "agent_label",
            and "model" (full model string, e.g. "openrouter/meta-llama/llama-3:8b").
        privacy_profile: Active profile ("public", "internal", or "confidential").
        kb: Loaded ProviderKnowledgeBase instance.
        permitted_models: Ordered list of compliant full model strings to offer
            as suggested replacements in each card.

    Returns:
        List of ComplianceCard — one per non-compliant agent. Empty list if
        all assignments are compliant.
    """
    cards: list[ComplianceCard] = []

    for assignment in agent_assignments:
        model_string: str = assignment["model"]
        provider, _, model_name = _split_model_string(model_string)

        if kb.is_compliant(provider, model_name, privacy_profile):
            continue

        record = kb.lookup(provider, model_name)
        cards.append(
            ComplianceCard(
                agent_id=assignment["agent_id"],
                agent_label=assignment["agent_label"],
                current_model=model_string,
                privacy_profile=privacy_profile,
                record=record,
                suggested_models=list(permitted_models),
            )
        )

    return cards


def _split_model_string(model_string: str) -> tuple[str, str, str]:
    """Split a full model string into (backend, provider, model_name).

    Examples:
        "ollama/llama3:8b"                        → ("ollama", "", "llama3:8b")
        "openrouter/anthropic/claude-sonnet-4-6"  → ("openrouter", "anthropic", "claude-sonnet-4-6")

    Returns:
        Tuple of (backend_prefix, provider_org, model_name). For ollama,
        provider_org is an empty string.
    """
    if model_string.startswith("ollama/"):
        return "ollama", "", model_string[len("ollama/"):]
    elif model_string.startswith("openrouter/"):
        rest = model_string[len("openrouter/"):]
        parts = rest.split("/", 1)
        if len(parts) == 2:
            return "openrouter", f"openrouter/{parts[0]}", parts[1]
        return "openrouter", f"openrouter/{rest}", "*"
    else:
        return model_string, "", "*"
```

---

### 5. `faith/privacy/enforcer.py`

```python
"""Privacy profile enforcer for the FAITH PA.

Validates model assignments against the active privacy profile,
requires explicit override acknowledgement for non-compliant models,
and handles mid-session profile changes by surfacing compliance cards
in the Web UI without forcing any reassignment.

Called by the PA's config hot-reload handler for .faith/system.yaml
(FRS Section 7.3.1) and by the wizard and PA model selection flows.

FRS Reference: Section 9.3.2, 9.4, 7.3.1
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from faith.privacy.compliance_card import ComplianceCard, build_compliance_cards
from faith.privacy.knowledge_base import ProviderKnowledgeBase

logger = logging.getLogger("faith.privacy.enforcer")


# ---------------------------------------------------------------------------
# Enforcement result
# ---------------------------------------------------------------------------

class PrivacyViolation(Exception):
    """Raised when a non-compliant model is assigned without override.

    Attributes:
        model: The non-compliant model string.
        privacy_profile: The active profile that was violated.
        record_notes: Notes from the provider record (or empty string).
    """

    def __init__(self, model: str, privacy_profile: str, record_notes: str = "") -> None:
        msg = (
            f"Model '{model}' does not meet the active privacy profile "
            f"'{privacy_profile}'."
        )
        if record_notes:
            msg += f" Provider note: {record_notes}"
        super().__init__(msg)
        self.model = model
        self.privacy_profile = privacy_profile
        self.record_notes = record_notes


# ---------------------------------------------------------------------------
# PrivacyEnforcer
# ---------------------------------------------------------------------------

class PrivacyEnforcer:
    """Enforces privacy profile constraints on model assignments.

    Lifecycle:
      1. Instantiate with a loaded ProviderKnowledgeBase and an EventPublisher.
      2. Call enforce_model_assignment() when the user or wizard assigns a model.
      3. Call handle_profile_change() when system.yaml is hot-reloaded with a
         new privacy_profile value (FRS 7.3.1).

    Args:
        kb: Loaded ProviderKnowledgeBase instance.
        event_publisher: EventPublisher used to publish system:config_changed
            events and surface compliance cards to the Web UI.
    """

    def __init__(self, kb: ProviderKnowledgeBase, event_publisher: Any) -> None:
        self._kb = kb
        self._publisher = event_publisher

    # --- Public API ---------------------------------------------------------

    def check_model(self, model_string: str, privacy_profile: str) -> bool:
        """Return True if model_string is compliant with privacy_profile.

        Does not raise — use enforce_model_assignment() for hard enforcement.

        Args:
            model_string: Full model string (e.g. "openrouter/anthropic/claude-sonnet-4-6").
            privacy_profile: Active profile ("public", "internal", "confidential").

        Returns:
            True if compliant, False if not or if provider is unknown.
        """
        provider, model_name = _parse_provider_model(model_string)
        return self._kb.is_compliant(provider, model_name, privacy_profile)

    def enforce_model_assignment(
        self,
        model_string: str,
        privacy_profile: str,
        override_acknowledged: bool = False,
    ) -> None:
        """Validate a model assignment against the active privacy profile.

        If the model is non-compliant and override_acknowledged is False,
        raises PrivacyViolation. If override_acknowledged is True, logs a
        warning and allows the assignment to proceed.

        This method is called by the PA and wizard whenever a model is
        assigned to the PA itself or to any specialist agent.

        Args:
            model_string: Full model string to validate.
            privacy_profile: Active FAITH privacy profile.
            override_acknowledged: True if the user has explicitly accepted
                the compliance warning. Only set this after the user has
                confirmed an override in the Web UI or CLI.

        Raises:
            PrivacyViolation: If model is non-compliant and no override
                has been acknowledged.
        """
        provider, model_name = _parse_provider_model(model_string)
        compliant = self._kb.is_compliant(provider, model_name, privacy_profile)

        if compliant:
            logger.debug(
                "Model '%s' is compliant with profile '%s'.",
                model_string, privacy_profile,
            )
            return

        record = self._kb.lookup(provider, model_name)
        notes = record.notes if record else ""

        if override_acknowledged:
            logger.warning(
                "Non-compliant model '%s' assigned to profile '%s' — "
                "user has explicitly acknowledged the override.",
                model_string, privacy_profile,
            )
            return

        raise PrivacyViolation(
            model=model_string,
            privacy_profile=privacy_profile,
            record_notes=notes,
        )

    async def handle_profile_change(
        self,
        new_profile: str,
        agent_assignments: list[dict],
    ) -> list[ComplianceCard]:
        """Handle a mid-session privacy profile change (FRS Section 7.3.1).

        Called by the system.yaml hot-reload handler when the privacy_profile
        field changes. Checks all active agent model assignments against the
        new profile, builds a ComplianceCard for each non-compliant agent,
        publishes a system:config_changed event carrying the cards as payload,
        and returns the card list.

        The user decides per agent in the Web UI: switch model or acknowledge
        override. Active tasks are NOT interrupted. No automatic reassignment
        occurs. Model changes take effect on the agent's next LLM call.

        Args:
            new_profile: The new privacy profile that was just written to
                system.yaml ("public", "internal", or "confidential").
            agent_assignments: List of dicts with "agent_id", "agent_label",
                and "model" for every active specialist agent plus the PA.

        Returns:
            List of ComplianceCard for non-compliant agents. Empty list if
            all assignments satisfy the new profile.
        """
        logger.info(
            "Privacy profile changed to '%s' — checking %d agent assignment(s).",
            new_profile, len(agent_assignments),
        )

        permitted_records = self._kb.get_permitted_providers(new_profile)
        permitted_models = _records_to_model_strings(permitted_records)

        cards = build_compliance_cards(
            agent_assignments=agent_assignments,
            privacy_profile=new_profile,
            kb=self._kb,
            permitted_models=permitted_models,
        )

        # Always publish config_changed so the Web UI panel refreshes
        event_payload = {
            "change": "privacy_profile",
            "new_profile": new_profile,
            "compliance_cards": [c.to_dict() for c in cards],
            "non_compliant_count": len(cards),
        }

        await self._publisher.publish(
            channel="system-events",
            event="system:config_changed",
            data=event_payload,
        )

        if cards:
            logger.warning(
                "Privacy profile '%s' — %d agent(s) have non-compliant model "
                "assignments. Compliance cards surfaced in Web UI.",
                new_profile, len(cards),
            )
        else:
            logger.info(
                "Privacy profile '%s' — all agent assignments are compliant.",
                new_profile,
            )

        return cards

    def get_recommended_models(self, privacy_profile: str) -> list[str]:
        """Return ordered list of full model strings permitted for the profile.

        Used to populate model selection dropdowns in the wizard and Web UI.
        Ollama models are listed first (local, always available), followed
        by vetted cloud providers.

        Args:
            privacy_profile: Active FAITH privacy profile.

        Returns:
            List of full model strings, ordered local-first.
        """
        records = self._kb.get_permitted_providers(privacy_profile)

        ollama_models: list[str] = []
        cloud_models: list[str] = []

        for record in records:
            if record.provider == "ollama":
                # Ollama — surface as generic guidance; actual models depend
                # on what is pulled locally. Use a representative example.
                ollama_models.append("ollama/llama3:8b")
                ollama_models.append("ollama/llama3:70b")
            else:
                # Build a representative model string from provider + a known
                # model name derived from the pattern prefix.
                pattern = record.model_pattern.rstrip("*").rstrip("-")
                if pattern:
                    cloud_models.append(f"{record.provider}/{pattern}")

        # De-duplicate while preserving order
        seen: set[str] = set()
        result: list[str] = []
        for m in ollama_models + cloud_models:
            if m not in seen:
                seen.add(m)
                result.append(m)

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_provider_model(model_string: str) -> tuple[str, str]:
    """Split a full model string into (provider_key, model_name).

    Handles the three forms used in FAITH:
      "ollama/llama3:8b"                        → ("ollama", "llama3:8b")
      "openrouter/anthropic/claude-sonnet-4-6"  → ("openrouter/anthropic", "claude-sonnet-4-6")
      "openrouter/meta-llama/llama-3:8b"        → ("openrouter/meta-llama", "llama-3:8b")

    Args:
        model_string: Full model string as stored in config.yaml or system.yaml.

    Returns:
        Tuple of (provider_key, model_name) where provider_key matches
        the "provider" field in provider-privacy.json.
    """
    if model_string.startswith("ollama/"):
        return "ollama", model_string[len("ollama/"):]

    if model_string.startswith("openrouter/"):
        rest = model_string[len("openrouter/"):]
        parts = rest.split("/", 1)
        if len(parts) == 2:
            return f"openrouter/{parts[0]}", parts[1]
        # Single-component openrouter path (unusual but tolerated)
        return "openrouter", rest

    # Unrecognised prefix — return as-is; is_compliant will return False
    return model_string, "*"


def _records_to_model_strings(records: list) -> list[str]:
    """Convert ProviderRecords to representative full model strings.

    Used to populate the "suggested models" list in compliance cards.
    Returns one entry per record using the model_pattern prefix as the
    model name. Records with wildcard-only patterns use a placeholder name.
    """
    result = []
    for r in records:
        pattern = r.model_pattern.rstrip("*").rstrip("-")
        if r.provider == "ollama":
            result.append("ollama/llama3:8b")
        elif pattern:
            result.append(f"{r.provider}/{pattern}")
    return result
```

---

### 6. `faith/wizard/privacy_kb.py` — Update (FAITH-049 stub)

The FAITH-049 task created `faith/wizard/privacy_kb.py` with a hardcoded `PROVIDER_PRIVACY_KB` list. Update it to delegate to `faith.privacy.knowledge_base` so there is a single source of truth. The public API — `get_permitted_providers()` and `is_provider_permitted()` — is unchanged, keeping FAITH-049 tests green.

```python
"""Provider privacy knowledge base interface for the FAITH wizard.

Thin compatibility shim over faith.privacy.knowledge_base.
The wizard calls these functions during Step 2 (privacy profile selection)
and Step 3 (PA model selection) to filter available providers.

The actual dataset lives in ~/.faith/data/provider-privacy.json and is
loaded by faith.privacy.knowledge_base.ProviderKnowledgeBase.

FRS Reference: Section 9.3.2, 9.4
"""

from __future__ import annotations

from faith.privacy.knowledge_base import (
    DEFAULT_KB_PATH,
    ProviderKnowledgeBase,
    ProviderRecord,
)

# Module-level singleton loaded once at import time.
# Re-exported as ProviderPrivacyRecord for backward compatibility with FAITH-049.
ProviderPrivacyRecord = ProviderRecord

_KB: ProviderKnowledgeBase | None = None


def _get_kb() -> ProviderKnowledgeBase:
    """Return (or lazily initialise) the module-level KB singleton."""
    global _KB
    if _KB is None:
        _KB = ProviderKnowledgeBase()
    return _KB


def get_permitted_providers(privacy_profile: str) -> list[ProviderRecord]:
    """Return providers whose faith_privacy_tier is compatible with the profile.

    Delegates to ProviderKnowledgeBase.get_permitted_providers().
    Identical semantics to the FAITH-049 inline implementation.

    Args:
        privacy_profile: "public", "internal", or "confidential".

    Returns:
        List of permitted ProviderRecord entries.
    """
    return _get_kb().get_permitted_providers(privacy_profile)


def is_provider_permitted(provider: str, privacy_profile: str) -> bool:
    """Check whether a specific provider is permitted for the profile.

    Args:
        provider: Provider string (e.g. "openrouter/anthropic", "ollama").
        privacy_profile: Active privacy profile.

    Returns:
        True if the provider meets the privacy requirement.
    """
    permitted = get_permitted_providers(privacy_profile)
    return any(r.provider == provider for r in permitted)
```

---

### 7. `tests/test_privacy_knowledge_base.py`

```python
"""Tests for the FAITH provider privacy knowledge base.

Covers: JSON loading, dataset integrity, tier filtering, provider lookup,
fnmatch model pattern matching, is_compliant(), and error handling.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from faith.privacy.knowledge_base import (
    ProviderKnowledgeBase,
    ProviderRecord,
    TIER_RANK,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_KB_JSON = {
    "_meta": {"version": "0.1.0", "updated": "2026-03"},
    "providers": [
        {
            "provider": "ollama",
            "model_pattern": "*",
            "training_opt_out": True,
            "human_review": False,
            "data_retention_days": 0,
            "dpa_available": False,
            "compliance": [],
            "faith_privacy_tier": "confidential",
            "notes": "Local only.",
        },
        {
            "provider": "openrouter/anthropic",
            "model_pattern": "claude-*",
            "training_opt_out": True,
            "human_review": False,
            "data_retention_days": 30,
            "dpa_available": True,
            "compliance": ["SOC2", "GDPR"],
            "faith_privacy_tier": "internal",
        },
        {
            "provider": "openrouter/meta-llama",
            "model_pattern": "llama-*",
            "training_opt_out": False,
            "human_review": True,
            "data_retention_days": 30,
            "dpa_available": False,
            "compliance": [],
            "faith_privacy_tier": "public",
        },
    ],
}


@pytest.fixture
def kb_file(tmp_path: Path) -> Path:
    """Write a minimal KB JSON file and return its path."""
    p = tmp_path / "provider-privacy.json"
    p.write_text(json.dumps(MINIMAL_KB_JSON), encoding="utf-8")
    return p


@pytest.fixture
def kb(kb_file: Path) -> ProviderKnowledgeBase:
    return ProviderKnowledgeBase(kb_path=kb_file)


# ---------------------------------------------------------------------------
# Test: Loading
# ---------------------------------------------------------------------------

class TestLoading:

    def test_loads_all_records(self, kb: ProviderKnowledgeBase):
        assert len(kb.records) == 3

    def test_version_extracted(self, kb: ProviderKnowledgeBase):
        assert kb.version == "0.1.0"

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="not found"):
            ProviderKnowledgeBase(kb_path=tmp_path / "missing.json")

    def test_malformed_record_raises(self, tmp_path: Path):
        bad_json = {"_meta": {}, "providers": [{"provider": "x"}]}  # missing fields
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(bad_json))
        with pytest.raises(ValueError, match="Malformed provider record"):
            ProviderKnowledgeBase(kb_path=p)

    def test_providers_not_list_raises(self, tmp_path: Path):
        bad = {"_meta": {}, "providers": "not-a-list"}
        p = tmp_path / "bad2.json"
        p.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="must be a list"):
            ProviderKnowledgeBase(kb_path=p)


# ---------------------------------------------------------------------------
# Test: Tier filtering
# ---------------------------------------------------------------------------

class TestTierFiltering:

    def test_public_profile_permits_all(self, kb: ProviderKnowledgeBase):
        results = kb.get_permitted_providers("public")
        assert len(results) == 3

    def test_internal_profile_excludes_public_tier(self, kb: ProviderKnowledgeBase):
        results = kb.get_permitted_providers("internal")
        providers = [r.provider for r in results]
        assert "openrouter/meta-llama" not in providers
        assert "openrouter/anthropic" in providers
        assert "ollama" in providers

    def test_confidential_profile_ollama_only(self, kb: ProviderKnowledgeBase):
        results = kb.get_permitted_providers("confidential")
        assert len(results) == 1
        assert results[0].provider == "ollama"

    def test_unknown_profile_raises(self, kb: ProviderKnowledgeBase):
        with pytest.raises(ValueError, match="Unknown privacy profile"):
            kb.get_permitted_providers("top-secret")


# ---------------------------------------------------------------------------
# Test: Lookup with fnmatch
# ---------------------------------------------------------------------------

class TestLookup:

    def test_lookup_ollama_any_model(self, kb: ProviderKnowledgeBase):
        record = kb.lookup("ollama", "llama3:8b")
        assert record is not None
        assert record.provider == "ollama"

    def test_lookup_claude_pattern(self, kb: ProviderKnowledgeBase):
        record = kb.lookup("openrouter/anthropic", "claude-sonnet-4-6")
        assert record is not None
        assert record.faith_privacy_tier == "internal"

    def test_lookup_llama_pattern(self, kb: ProviderKnowledgeBase):
        record = kb.lookup("openrouter/meta-llama", "llama-3:8b")
        assert record is not None
        assert record.faith_privacy_tier == "public"

    def test_lookup_unknown_provider_returns_none(self, kb: ProviderKnowledgeBase):
        assert kb.lookup("openrouter/deepmind", "unknown-model") is None

    def test_lookup_non_matching_model_returns_none(self, kb: ProviderKnowledgeBase):
        # anthropic pattern is "claude-*"; "gpt-4" does not match
        assert kb.lookup("openrouter/anthropic", "gpt-4") is None


# ---------------------------------------------------------------------------
# Test: is_compliant
# ---------------------------------------------------------------------------

class TestIsCompliant:

    def test_ollama_always_compliant(self, kb: ProviderKnowledgeBase):
        assert kb.is_compliant("ollama", "llama3:8b", "confidential") is True

    def test_anthropic_not_compliant_for_confidential(self, kb: ProviderKnowledgeBase):
        assert kb.is_compliant("openrouter/anthropic", "claude-sonnet-4-6", "confidential") is False

    def test_anthropic_compliant_for_internal(self, kb: ProviderKnowledgeBase):
        assert kb.is_compliant("openrouter/anthropic", "claude-sonnet-4-6", "internal") is True

    def test_llama_not_compliant_for_internal(self, kb: ProviderKnowledgeBase):
        assert kb.is_compliant("openrouter/meta-llama", "llama-3:8b", "internal") is False

    def test_unknown_provider_not_compliant(self, kb: ProviderKnowledgeBase):
        # Unknown providers default to False — unknown is not safe
        assert kb.is_compliant("openrouter/unknown", "mystery-model", "public") is False
```

---

### 8. `tests/test_privacy_enforcer.py`

```python
"""Tests for the FAITH PrivacyEnforcer.

Covers: compliant model assignment, non-compliant raises PrivacyViolation,
override_acknowledged bypass, handle_profile_change card generation,
event publication, and recommended model ordering.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from faith.privacy.enforcer import PrivacyEnforcer, PrivacyViolation
from faith.privacy.knowledge_base import ProviderKnowledgeBase


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_KB_JSON = {
    "_meta": {"version": "0.1.0"},
    "providers": [
        {
            "provider": "ollama",
            "model_pattern": "*",
            "training_opt_out": True,
            "human_review": False,
            "data_retention_days": 0,
            "dpa_available": False,
            "compliance": [],
            "faith_privacy_tier": "confidential",
            "notes": "Local only.",
        },
        {
            "provider": "openrouter/anthropic",
            "model_pattern": "claude-*",
            "training_opt_out": True,
            "human_review": False,
            "data_retention_days": 30,
            "dpa_available": True,
            "compliance": ["SOC2"],
            "faith_privacy_tier": "internal",
        },
        {
            "provider": "openrouter/meta-llama",
            "model_pattern": "llama-*",
            "training_opt_out": False,
            "human_review": True,
            "data_retention_days": 30,
            "dpa_available": False,
            "compliance": [],
            "faith_privacy_tier": "public",
        },
    ],
}


@pytest.fixture
def kb(tmp_path: Path) -> ProviderKnowledgeBase:
    p = tmp_path / "provider-privacy.json"
    p.write_text(json.dumps(MINIMAL_KB_JSON))
    return ProviderKnowledgeBase(kb_path=p)


@pytest.fixture
def publisher() -> AsyncMock:
    pub = AsyncMock()
    pub.publish = AsyncMock()
    return pub


@pytest.fixture
def enforcer(kb: ProviderKnowledgeBase, publisher: AsyncMock) -> PrivacyEnforcer:
    return PrivacyEnforcer(kb=kb, event_publisher=publisher)


# ---------------------------------------------------------------------------
# Test: check_model (non-raising)
# ---------------------------------------------------------------------------

class TestCheckModel:

    def test_ollama_always_true(self, enforcer: PrivacyEnforcer):
        assert enforcer.check_model("ollama/llama3:8b", "confidential") is True

    def test_anthropic_internal_ok(self, enforcer: PrivacyEnforcer):
        assert enforcer.check_model(
            "openrouter/anthropic/claude-sonnet-4-6", "internal"
        ) is True

    def test_llama_internal_false(self, enforcer: PrivacyEnforcer):
        assert enforcer.check_model(
            "openrouter/meta-llama/llama-3:8b", "internal"
        ) is False


# ---------------------------------------------------------------------------
# Test: enforce_model_assignment (raising)
# ---------------------------------------------------------------------------

class TestEnforceModelAssignment:

    def test_compliant_model_no_exception(self, enforcer: PrivacyEnforcer):
        # Should not raise
        enforcer.enforce_model_assignment("ollama/llama3:8b", "confidential")

    def test_non_compliant_raises_privacy_violation(self, enforcer: PrivacyEnforcer):
        with pytest.raises(PrivacyViolation) as exc_info:
            enforcer.enforce_model_assignment(
                "openrouter/meta-llama/llama-3:8b", "internal"
            )
        assert "llama" in str(exc_info.value).lower() or "meta" in str(exc_info.value).lower()
        assert exc_info.value.privacy_profile == "internal"

    def test_override_acknowledged_bypasses_violation(self, enforcer: PrivacyEnforcer):
        # Should not raise even for non-compliant model
        enforcer.enforce_model_assignment(
            "openrouter/meta-llama/llama-3:8b",
            "internal",
            override_acknowledged=True,
        )

    def test_violation_contains_model_string(self, enforcer: PrivacyEnforcer):
        with pytest.raises(PrivacyViolation) as exc_info:
            enforcer.enforce_model_assignment(
                "openrouter/meta-llama/llama-3:8b", "confidential"
            )
        assert exc_info.value.model == "openrouter/meta-llama/llama-3:8b"


# ---------------------------------------------------------------------------
# Test: handle_profile_change
# ---------------------------------------------------------------------------

class TestHandleProfileChange:

    @pytest.mark.asyncio
    async def test_no_cards_when_all_compliant(
        self, enforcer: PrivacyEnforcer, publisher: AsyncMock
    ):
        assignments = [
            {"agent_id": "pa", "agent_label": "Project Agent", "model": "ollama/llama3:8b"},
        ]
        cards = await enforcer.handle_profile_change("confidential", assignments)
        assert cards == []
        publisher.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_cards_for_non_compliant_agents(
        self, enforcer: PrivacyEnforcer, publisher: AsyncMock
    ):
        assignments = [
            {"agent_id": "pa", "agent_label": "Project Agent",
             "model": "openrouter/anthropic/claude-sonnet-4-6"},
            {"agent_id": "dev", "agent_label": "Software Developer",
             "model": "openrouter/meta-llama/llama-3:8b"},
        ]
        cards = await enforcer.handle_profile_change("confidential", assignments)
        # Both cloud models fail confidential
        assert len(cards) == 2
        agent_ids = [c.agent_id for c in cards]
        assert "pa" in agent_ids
        assert "dev" in agent_ids

    @pytest.mark.asyncio
    async def test_event_published_with_correct_profile(
        self, enforcer: PrivacyEnforcer, publisher: AsyncMock
    ):
        assignments = [
            {"agent_id": "dev", "agent_label": "Dev",
             "model": "openrouter/meta-llama/llama-3:8b"},
        ]
        await enforcer.handle_profile_change("internal", assignments)

        call_kwargs = publisher.publish.call_args[1]
        assert call_kwargs["event"] == "system:config_changed"
        assert call_kwargs["data"]["change"] == "privacy_profile"
        assert call_kwargs["data"]["new_profile"] == "internal"

    @pytest.mark.asyncio
    async def test_cards_have_suggested_models(
        self, enforcer: PrivacyEnforcer, publisher: AsyncMock
    ):
        assignments = [
            {"agent_id": "dev", "agent_label": "Dev",
             "model": "openrouter/meta-llama/llama-3:8b"},
        ]
        cards = await enforcer.handle_profile_change("internal", assignments)
        assert len(cards) == 1
        # For "internal" profile, suggested models should include at least one option
        assert len(cards[0].suggested_models) > 0

    @pytest.mark.asyncio
    async def test_no_forced_reassignment(
        self, enforcer: PrivacyEnforcer, publisher: AsyncMock
    ):
        """handle_profile_change must NOT modify agent assignments.

        The PA publishes cards; the user decides. Verify the original
        assignments list is not mutated.
        """
        assignments = [
            {"agent_id": "dev", "agent_label": "Dev",
             "model": "openrouter/meta-llama/llama-3:8b"},
        ]
        original_model = assignments[0]["model"]
        await enforcer.handle_profile_change("confidential", assignments)
        assert assignments[0]["model"] == original_model


# ---------------------------------------------------------------------------
# Test: get_recommended_models
# ---------------------------------------------------------------------------

class TestGetRecommendedModels:

    def test_confidential_returns_ollama_only(self, enforcer: PrivacyEnforcer):
        models = enforcer.get_recommended_models("confidential")
        assert all(m.startswith("ollama/") for m in models)

    def test_public_returns_all_providers(self, enforcer: PrivacyEnforcer):
        models = enforcer.get_recommended_models("public")
        assert any(m.startswith("ollama/") for m in models)
        assert any(m.startswith("openrouter/") for m in models)

    def test_ollama_listed_first(self, enforcer: PrivacyEnforcer):
        models = enforcer.get_recommended_models("internal")
        assert models[0].startswith("ollama/")

    def test_no_duplicates(self, enforcer: PrivacyEnforcer):
        models = enforcer.get_recommended_models("public")
        assert len(models) == len(set(models))
```

---

### 9. `tests/test_compliance_card.py`

```python
"""Tests for the ComplianceCard builder.

Covers: card creation, to_dict() serialisation, build_compliance_cards()
filtering, unknown provider handling, and suggested model population.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from faith.privacy.compliance_card import ComplianceCard, build_compliance_cards
from faith.privacy.knowledge_base import ProviderKnowledgeBase


MINIMAL_KB_JSON = {
    "_meta": {"version": "0.1.0"},
    "providers": [
        {
            "provider": "ollama",
            "model_pattern": "*",
            "training_opt_out": True,
            "human_review": False,
            "data_retention_days": 0,
            "dpa_available": False,
            "compliance": [],
            "faith_privacy_tier": "confidential",
        },
        {
            "provider": "openrouter/anthropic",
            "model_pattern": "claude-*",
            "training_opt_out": True,
            "human_review": False,
            "data_retention_days": 30,
            "dpa_available": True,
            "compliance": ["SOC2"],
            "faith_privacy_tier": "internal",
            "notes": "No training on API data.",
        },
        {
            "provider": "openrouter/meta-llama",
            "model_pattern": "llama-*",
            "training_opt_out": False,
            "human_review": True,
            "data_retention_days": 30,
            "dpa_available": False,
            "compliance": [],
            "faith_privacy_tier": "public",
        },
    ],
}


@pytest.fixture
def kb(tmp_path: Path) -> ProviderKnowledgeBase:
    p = tmp_path / "provider-privacy.json"
    p.write_text(json.dumps(MINIMAL_KB_JSON))
    return ProviderKnowledgeBase(kb_path=p)


class TestComplianceCardToDict:

    def test_to_dict_structure(self, kb: ProviderKnowledgeBase):
        record = kb.lookup("openrouter/meta-llama", "llama-3:8b")
        card = ComplianceCard(
            agent_id="dev",
            agent_label="Software Developer",
            current_model="openrouter/meta-llama/llama-3:8b",
            privacy_profile="internal",
            record=record,
            suggested_models=["ollama/llama3:8b"],
        )
        d = card.to_dict()
        assert d["type"] == "compliance_card"
        assert d["agent_id"] == "dev"
        assert d["current_model"] == "openrouter/meta-llama/llama-3:8b"
        assert d["privacy_profile"] == "internal"
        assert d["override_acknowledged"] is False
        assert d["suggested_models"] == ["ollama/llama3:8b"]
        assert d["provider_record"] is not None
        assert d["provider_record"]["faith_privacy_tier"] == "public"

    def test_to_dict_no_record(self):
        card = ComplianceCard(
            agent_id="unknown-agent",
            agent_label="Unknown",
            current_model="unknown/model",
            privacy_profile="confidential",
            record=None,
        )
        d = card.to_dict()
        assert d["provider_record"] is None


class TestBuildComplianceCards:

    def test_compliant_agents_produce_no_cards(self, kb: ProviderKnowledgeBase):
        assignments = [
            {"agent_id": "pa", "agent_label": "PA", "model": "ollama/llama3:8b"},
        ]
        cards = build_compliance_cards(assignments, "confidential", kb, ["ollama/llama3:8b"])
        assert cards == []

    def test_non_compliant_agent_produces_card(self, kb: ProviderKnowledgeBase):
        assignments = [
            {"agent_id": "dev", "agent_label": "Dev",
             "model": "openrouter/meta-llama/llama-3:8b"},
        ]
        cards = build_compliance_cards(assignments, "internal", kb, ["ollama/llama3:8b"])
        assert len(cards) == 1
        assert cards[0].agent_id == "dev"

    def test_mixed_assignments_filters_correctly(self, kb: ProviderKnowledgeBase):
        assignments = [
            {"agent_id": "pa", "agent_label": "PA",
             "model": "openrouter/anthropic/claude-sonnet-4-6"},
            {"agent_id": "dev", "agent_label": "Dev",
             "model": "openrouter/meta-llama/llama-3:8b"},
        ]
        cards = build_compliance_cards(assignments, "internal", kb, [])
        # Anthropic is internal-tier → compliant; meta-llama is public-only → card
        assert len(cards) == 1
        assert cards[0].agent_id == "dev"

    def test_suggested_models_populated(self, kb: ProviderKnowledgeBase):
        assignments = [
            {"agent_id": "dev", "agent_label": "Dev",
             "model": "openrouter/meta-llama/llama-3:8b"},
        ]
        suggestions = ["ollama/llama3:8b", "openrouter/anthropic/claude-sonnet-4-6"]
        cards = build_compliance_cards(assignments, "internal", kb, suggestions)
        assert cards[0].suggested_models == suggestions

    def test_unknown_provider_produces_card_with_no_record(
        self, kb: ProviderKnowledgeBase
    ):
        assignments = [
            {"agent_id": "mystery", "agent_label": "Mystery",
             "model": "openrouter/deepmind/gemma-2"},
        ]
        cards = build_compliance_cards(assignments, "public", kb, [])
        assert len(cards) == 1
        assert cards[0].record is None
```

---

## Acceptance Criteria

1. `~/.faith/data/provider-privacy.json` exists with valid JSON. The `_meta` block includes `version` and `updated` fields. Every record in `providers` has all eight required fields (`provider`, `model_pattern`, `training_opt_out`, `human_review`, `data_retention_days`, `dpa_available`, `compliance`, `faith_privacy_tier`).

2. `ProviderKnowledgeBase` loads the JSON on construction, validates structure, and raises `FileNotFoundError` if the file is absent and `ValueError` if any record is malformed.

3. `get_permitted_providers("confidential")` returns only records with `faith_privacy_tier == "confidential"` (Ollama only in the default dataset). `get_permitted_providers("internal")` excludes public-tier providers. `get_permitted_providers("public")` returns all records.

4. `lookup(provider, model)` uses `fnmatch` to match the `model_pattern` field. A pattern of `"*"` matches any model. A pattern of `"claude-*"` matches `"claude-sonnet-4-6"` but not `"gpt-4"`.

5. `is_compliant()` returns `False` for any provider/model not found in the KB (unknown providers are blocked by default, not permitted).

6. `PrivacyEnforcer.enforce_model_assignment()` raises `PrivacyViolation` when the model does not satisfy the active profile and `override_acknowledged` is `False`. It does not raise when `override_acknowledged` is `True`, regardless of compliance status.

7. `PrivacyEnforcer.handle_profile_change()` publishes exactly one `system:config_changed` event to the `system-events` channel, carrying `change: "privacy_profile"`, the new profile, and the `compliance_cards` list in the payload. It returns one `ComplianceCard` per non-compliant agent and zero cards when all assignments are compliant.

8. `handle_profile_change()` never modifies the `agent_assignments` list. The original model strings in the list are unchanged after the call returns.

9. `get_recommended_models()` returns Ollama options before cloud provider options. The returned list contains no duplicates. For the `"confidential"` profile the list contains only `ollama/` prefixed entries.

10. `ComplianceCard.to_dict()` returns a JSON-serialisable dict with keys `type`, `agent_id`, `agent_label`, `current_model`, `privacy_profile`, `provider_record`, `suggested_models`, and `override_acknowledged`. When `record` is `None`, `provider_record` is `None` (not omitted).

11. `faith/wizard/privacy_kb.py` is updated to delegate to `faith.privacy.knowledge_base`. The public API (`get_permitted_providers()`, `is_provider_permitted()`) behaves identically to the FAITH-049 implementation. All FAITH-049 wizard tests continue to pass.

12. All tests in `tests/test_privacy_knowledge_base.py`, `tests/test_privacy_enforcer.py`, and `tests/test_compliance_card.py` pass under Python 3.13.

---

## Notes for Implementer

- **Single source of truth.** The FAITH-049 task hard-codes the provider list inline in `privacy_kb.py`. FAITH-050 migrates that data to `provider-privacy.json` and replaces the inline list with a thin shim that delegates to `ProviderKnowledgeBase`. Do not maintain two separate datasets.

- **Unknown providers are blocked, not permitted.** `is_compliant()` returns `False` when no record is found. This is intentional — if FAITH does not recognise a provider, it cannot vouch for it. The PA warns the user and requires explicit `override_acknowledged` to proceed, same as a known non-compliant provider.

- **No forced reassignment ever.** The FRS (Section 7.3.1) is explicit: when the privacy profile changes mid-session, the PA surfaces compliance cards and the user decides per agent. `handle_profile_change()` must not modify agent configs, stop containers, or switch models automatically. Model changes take effect on the agent's next LLM call after the user acts on the card.

- **Active tasks are not interrupted.** The hot-reload handler for `system.yaml` (implemented in the config module, FAITH-004/FAITH-003) calls `handle_profile_change()` after writing the new config. Do not block the hot-reload pipeline — `handle_profile_change()` is async and should return promptly. The compliance cards are surfaced asynchronously in the Web UI.

- **Tier rank logic.** The tier rank is `public=0, internal=1, confidential=2`. A record's tier is the minimum profile it satisfies, expressed as the strictest profile where it is still permitted. Ollama is `confidential` because it satisfies even the most restrictive profile. This means the filtering direction in `get_permitted_providers()` is: `record.tier_rank >= requested_profile_rank`. Verify this by re-reading the FRS 9.3.2 profile table before touching this logic.

- **The `provider` field in provider-privacy.json uses compound keys.** Ollama is just `"ollama"`. Cloud providers are `"openrouter/<org>"`, e.g. `"openrouter/anthropic"`. The `_parse_provider_model()` helper in `enforcer.py` must reconstruct these compound keys from the full model strings used in `config.yaml` (e.g. `"openrouter/anthropic/claude-sonnet-4-6"` → provider key `"openrouter/anthropic"`, model name `"claude-sonnet-4-6"`). Test the parser explicitly for both `ollama/` and multi-segment `openrouter/` strings.

- **provider-privacy.json is committed to git.** It lives in `~/.faith/data/` which is the framework home created by `pip install faith-cli` and `faith init`. Unlike `model-prices.cache.json` (gitignored, live-scraped), this file is static and versioned — commit it alongside the Python source. Add a `_meta.updated` field update to the release checklist.

- **The wizard shim must not break FAITH-049 tests.** The shim in `faith/wizard/privacy_kb.py` exports `ProviderPrivacyRecord = ProviderRecord` as a backward-compatible alias. If FAITH-049 tests import `ProviderPrivacyRecord` from `faith.wizard.privacy_kb`, they will continue to get the same dataclass. Confirm the FAITH-049 test imports before shipping.

- **Event payload key name.** The event key is `system:config_changed` (colon separator), consistent with FRS Section 3.7 and the pattern used throughout the event system (e.g. `file:changed`, `agent:error`). The channel is `system-events` (hyphen separator). Do not mix these up.
