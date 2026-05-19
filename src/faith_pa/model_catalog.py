"""Description:
    Provide persistent model-catalog helpers for FAITH runtime diagnostics.

Requirements:
    - Persist model metadata for Ollama and OpenRouter in a simple on-disk JSON catalog.
    - Preserve provenance labels for discovered, effective, and user-overridden values.
    - Offer deterministic safe-usable-context estimates for local-model diagnostics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from faith_shared.config.models import (
    ModelCatalogEntry,
    ModelContextWindow,
    ModelProvenance,
)

OPENROUTER_MODELS_API_URL = "https://openrouter.ai/api/v1/models"


class ModelCatalog(BaseModel):
    """Description:
        Represent the persisted on-disk model catalog.

    Requirements:
        - Keep the catalog keyed by fully qualified provider/model name.
        - Support incremental merge helpers for pricing, provider metadata, and user overrides.
    """

    entries: dict[str, ModelCatalogEntry] = Field(default_factory=dict)

    def upsert(self, entry: ModelCatalogEntry) -> None:
        """Description:
            Insert or replace one catalog entry.

        Requirements:
            - Preserve the provider-qualified key for later load/save round-trips.

        :param entry: Catalog entry to persist.
        """

        self.entries[entry.key] = entry

    def dump(self, path: Path) -> None:
        """Description:
            Persist the catalog as deterministic JSON.

        Requirements:
            - Create parent directories when needed.
            - Write stable, indented JSON for diagnostics reuse.

        :param path: Catalog file path.
        """

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> ModelCatalog:
        """Description:
            Load one catalog file from disk.

        Requirements:
            - Return an empty catalog when the file is missing.
            - Validate the JSON structure before returning it.

        :param path: Catalog file path.
        :returns: Loaded catalog instance.
        """

        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return cls()
        return cls.model_validate(payload)

    def ensure_entry(
        self,
        *,
        provider: str,
        model: str,
        context_window_value: int = 0,
        provenance: ModelProvenance = ModelProvenance.DISCOVERED,
        runtime: dict[str, Any] | None = None,
    ) -> ModelCatalogEntry:
        """Description:
            Return one existing catalog entry or create it with baseline metadata.

        Requirements:
            - Keep catalog creation deterministic for callers that seed known models from config or pricing data.

        :param provider: Provider identifier such as ``ollama`` or ``openrouter``.
        :param model: Provider-native model identifier.
        :param context_window_value: Known or discovered context-window size.
        :param provenance: Provenance label for the supplied context size.
        :param runtime: Optional runtime metadata to merge.
        :returns: Existing or newly created catalog entry.
        """

        key = f"{provider}/{model}"
        existing = self.entries.get(key)
        if existing is not None:
            if runtime:
                existing.runtime.update(runtime)
            return existing
        entry = ModelCatalogEntry(
            provider=provider,
            model=model,
            context_window=ModelContextWindow(
                value=max(int(context_window_value or 0), 0),
                provenance=provenance,
            ),
            runtime=dict(runtime or {}),
        )
        self.entries[key] = entry
        return entry

    def apply_context_window_override(self, model_key: str, value: int) -> ModelCatalogEntry:
        """Description:
            Apply one user-supplied context-window override to the catalog.

        Requirements:
            - Persist the overridden value with ``user_override`` provenance.
            - Create the entry lazily when the catalog does not already know the model.

        :param model_key: Fully qualified model key such as ``openrouter/openai/gpt-4o``.
        :param value: Overridden context-window size.
        :returns: Updated catalog entry.
        """

        provider, model = model_key.split("/", 1)
        entry = self.ensure_entry(provider=provider, model=model)
        entry.context_window = ModelContextWindow(
            value=int(value),
            provenance=ModelProvenance.USER_OVERRIDE,
        )
        return entry

    def merge_pricing_catalog(self, path: Path) -> None:
        """Description:
            Merge context-window hints from one persisted FAITH pricing catalog.

        Requirements:
            - Ignore missing or malformed files safely.
            - Use pricing-derived context windows only when the catalog entry has no stronger user override.

        :param path: Pricing catalog JSON file path.
        """

        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        models = payload.get("models")
        if not isinstance(models, dict):
            return
        for model_key, metadata in models.items():
            if (
                not isinstance(model_key, str)
                or "/" not in model_key
                or not isinstance(metadata, dict)
            ):
                continue
            context_window = metadata.get("context_window")
            if not isinstance(context_window, int):
                continue
            provider, model = model_key.split("/", 1)
            entry = self.ensure_entry(
                provider=provider,
                model=model,
                context_window_value=context_window,
                provenance=ModelProvenance.DISCOVERED,
            )
            if entry.context_window.provenance != ModelProvenance.USER_OVERRIDE:
                entry.context_window = ModelContextWindow(
                    value=context_window,
                    provenance=ModelProvenance.DISCOVERED,
                )

    def merge_openrouter_models_payload(self, payload: dict[str, Any]) -> None:
        """Description:
            Merge model metadata from one OpenRouter models API payload.

        Requirements:
            - Read model identifiers from ``data[].id``.
            - Prefer ``context_length`` and fall back to ``top_provider.context_length`` when needed.
            - Ignore malformed entries safely.

        :param payload: Parsed OpenRouter models API payload.
        """

        rows = payload.get("data")
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = row.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            context_length = row.get("context_length")
            if not isinstance(context_length, int):
                top_provider = row.get("top_provider")
                if isinstance(top_provider, dict) and isinstance(
                    top_provider.get("context_length"), int
                ):
                    context_length = top_provider["context_length"]
            if not isinstance(context_length, int):
                context_length = 0
            entry = self.ensure_entry(
                provider="openrouter",
                model=model_id,
                context_window_value=context_length,
                provenance=ModelProvenance.DISCOVERED,
                runtime={"source": "openrouter_models_api"},
            )
            if entry.context_window.provenance != ModelProvenance.USER_OVERRIDE:
                entry.context_window = ModelContextWindow(
                    value=context_length,
                    provenance=ModelProvenance.DISCOVERED,
                )

    def sorted_entries(self) -> list[ModelCatalogEntry]:
        """Description:
            Return catalog entries in stable key order.

        Requirements:
            - Keep UI option ordering deterministic across requests.

        :returns: Sorted catalog entries.
        """

        return [self.entries[key] for key in sorted(self.entries)]

    def model_options(self) -> list[dict[str, str]]:
        """Description:
            Return stable UI option payloads for known models.

        Requirements:
            - Use the provider-qualified key as both the value and the default label.

        :returns: Ordered model-option payloads.
        """

        return [{"value": key, "label": key} for key in sorted(self.entries)]


@dataclass(frozen=True, slots=True)
class SafeUsableContextEstimate:
    """Description:
        Represent a deterministic safe-usable-context estimate for local models.

    Requirements:
        - Preserve the nominal and effective context signals used by diagnostics.
    """

    nominal_context_window: int
    safe_usable_context: int
    warning: str | None = None


def estimate_safe_usable_context(
    *,
    nominal_context_window: int,
    usable_vram_mb: int | None,
    system_ram_mb: int | None,
    route_kind: str,
) -> SafeUsableContextEstimate:
    """Description:
        Estimate a safe usable context window for a local/Ollama route.

    Requirements:
        - Stay deterministic and data-oriented.
        - Prefer a conservative context when VRAM is constrained.

    :param nominal_context_window: Declared or discovered nominal window size.
    :param usable_vram_mb: Estimated usable VRAM budget.
    :param system_ram_mb: Estimated system RAM budget.
    :param route_kind: Local route kind such as ``container`` or ``host``.
    :returns: Deterministic safe-usable-context estimate.
    """

    del system_ram_mb, route_kind
    safe_context = nominal_context_window
    warning: str | None = None
    if usable_vram_mb is not None and usable_vram_mb < 8192:
        safe_context = min(nominal_context_window, 7168)
        warning = "usable_context_limited_by_vram"
    return SafeUsableContextEstimate(
        nominal_context_window=nominal_context_window,
        safe_usable_context=safe_context,
        warning=warning,
    )
