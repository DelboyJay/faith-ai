"""Description:
    Verify model catalog persistence and lightweight runtime diagnostics.

Requirements:
    - Prove catalog entries preserve provenance and context-window metadata.
    - Prove safe usable context estimates remain deterministic and data oriented.
    - Prove per-agent overrides can be captured in the shared model-layer schema.
"""

from __future__ import annotations

from pathlib import Path

from faith_pa.agent.llm_client import LocalModelCapability
from faith_pa.logging.token_logger import TokenLogger
from faith_pa.model_catalog import ModelCatalog, estimate_safe_usable_context
from faith_shared.config.models import (
    AgentConfig,
    ModelCatalogEntry,
    ModelContextWindow,
    ModelProvenance,
    PAConfig,
    SystemConfig,
)


def test_model_catalog_round_trips_persisted_entries(tmp_path: Path) -> None:
    """Description:
        Verify catalog entries persist to disk and load back with provenance intact.

    Requirements:
        - This test is needed to prove the catalog can store discovered and effective metadata for later diagnostics.
        - Verify context-window and provider fields survive JSON persistence.

    :param tmp_path: Temporary pytest directory fixture.
    """

    catalog_path = tmp_path / "model-catalog.json"
    catalog = ModelCatalog()
    catalog.upsert(
        ModelCatalogEntry(
            provider="ollama",
            model="llama3:8b",
            context_window=ModelContextWindow(
                value=8192,
                provenance=ModelProvenance.DISCOVERED,
            ),
            runtime={"endpoint": "http://ollama:11434"},
        )
    )
    catalog.upsert(
        ModelCatalogEntry(
            provider="openrouter",
            model="openai/gpt-4o",
            context_window=ModelContextWindow(
                value=128000,
                provenance=ModelProvenance.USER_OVERRIDE,
            ),
            runtime={"caching": "supported"},
        )
    )

    catalog.dump(catalog_path)
    restored = ModelCatalog.load(catalog_path)

    assert (
        restored.entries["ollama/llama3:8b"].context_window.provenance == ModelProvenance.DISCOVERED
    )
    assert (
        restored.entries["openrouter/openai/gpt-4o"].context_window.provenance
        == ModelProvenance.USER_OVERRIDE
    )
    assert restored.entries["ollama/llama3:8b"].runtime["endpoint"] == "http://ollama:11434"


def test_model_catalog_supports_pa_and_agent_overrides() -> None:
    """Description:
        Verify the shared config schema can represent PA and per-agent model overrides.

    Requirements:
        - This test is needed to prove the model layer can carry direct PA selection and agent-specific overrides.
        - Verify the effective agent model can differ from the system default.

    """

    system_config = SystemConfig(
        pa=PAConfig(model="ollama/llama3:8b"),
        default_agent_model="openrouter/openai/gpt-4o",
    )
    agent_config = AgentConfig(name="researcher", role="Researcher", model="ollama/mistral:7b")

    assert system_config.pa.model == "ollama/llama3:8b"
    assert system_config.default_agent_model == "openrouter/openai/gpt-4o"
    assert agent_config.model == "ollama/mistral:7b"


def test_safe_usable_context_estimate_is_deterministic() -> None:
    """Description:
        Verify the local-context helper returns deterministic usable-context diagnostics.

    Requirements:
        - This test is needed to prove the warning heuristics stay data-oriented and repeatable.
        - Verify the helper returns a safe usable context below the nominal limit when penalties apply.

    """

    estimate = estimate_safe_usable_context(
        nominal_context_window=8192,
        usable_vram_mb=6144,
        system_ram_mb=32768,
        route_kind="container",
    )

    assert estimate.safe_usable_context == 7168
    assert estimate.warning == "usable_context_limited_by_vram"


def test_openrouter_caching_diagnostics_default_to_absent(tmp_path: Path) -> None:
    """Description:
        Verify cache diagnostics remain optional when provider metadata is unavailable.

    Requirements:
        - This test is needed to prove OpenRouter caching diagnostics degrade gracefully.
        - Verify the token logger preserves the absence of cache diagnostics.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = TokenLogger(logs_dir=tmp_path / "logs")
    entry = logger.log_api_call(
        session_id="sess-1",
        task_id="task-1",
        agent="project-agent",
        model="openrouter/openai/gpt-4o",
        input_tokens=10,
        output_tokens=5,
    )

    assert entry.cache_hit is None
    assert entry.cached_input_tokens is None
    assert entry.cached_output_tokens is None


def test_openrouter_caching_diagnostics_can_be_recorded(tmp_path: Path) -> None:
    """Description:
        Verify optional cache diagnostics are attached when the provider reports them.

    Requirements:
        - This test is needed to prove prompt-caching metadata can be stored without affecting normal token accounting.
        - Verify recorded cache diagnostics survive the persisted token entry.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = TokenLogger(logs_dir=tmp_path / "logs")
    logger.set_cache_diagnostics(
        "openrouter/openai/gpt-4o",
        cache_hit=True,
        cached_input_tokens=80,
        cached_output_tokens=0,
    )

    entry = logger.log_api_call(
        session_id="sess-1",
        task_id="task-1",
        agent="project-agent",
        model="openrouter/openai/gpt-4o",
        input_tokens=100,
        output_tokens=5,
    )

    assert entry.cache_hit is True
    assert entry.cached_input_tokens == 80
    assert entry.cached_output_tokens == 0


def test_local_model_capability_can_be_serialised() -> None:
    """Description:
        Verify the local-model capability payload can be embedded in catalog diagnostics.

    Requirements:
        - This test is needed to prove probe results stay structured enough for downstream catalog use.
        - Verify the capability carries the resource fields needed by model diagnostics.

    """

    capability = LocalModelCapability(
        endpoint="http://ollama:11434",
        route_kind="container",
        inference_available=True,
        gpu_acceleration=False,
        usable_vram_mb=6144,
        system_ram_mb=32768,
        probe_model="llama3:8b",
        notes=("ok",),
    )

    assert capability.usable_vram_mb == 6144
    assert capability.notes == ("ok",)
