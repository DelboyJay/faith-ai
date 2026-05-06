"""Description:
    Verify the FAITH token and cost logger primitives.

Requirements:
    - Prove token log entries round-trip through JSON-lines persistence.
    - Prove token totals, cost thresholds, and per-agent statistics are calculated correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

from faith_pa.logging.token_logger import DEFAULT_COST_THRESHOLD_USD, TokenEntry, TokenLogger


def test_token_entry_round_trip() -> None:
    """Description:
        Verify one token-log entry round-trips through JSON-lines serialisation.

    Requirements:
        - This test is needed to prove persisted token records keep all FRS-required fields.
        - Verify the restored entry preserves the agent, model, and estimated cost fields.
    """

    entry = TokenEntry(
        ts="2026-03-23T14:32:01Z",
        session_id="sess-0042",
        task_id="task-001-143201.456",
        agent="software-developer",
        model="ollama/llama3:8b",
        input_tokens=1240,
        output_tokens=380,
        estimated_cost=0.0,
        price_source="cache",
        price_age_days=1,
    )

    restored = TokenEntry.from_json_line(entry.to_json_line())

    assert restored.agent == "software-developer"
    assert restored.model == "ollama/llama3:8b"
    assert restored.estimated_cost == 0.0


def test_token_logger_tracks_costs_queries_and_thresholds(tmp_path: Path) -> None:
    """Description:
        Verify the token logger writes entries, tracks totals, and emits threshold state.

    Requirements:
        - This test is needed to prove FAITH can persist token usage and compute per-session cost warnings.
        - Verify query helpers, aggregate statistics, and threshold checks work on written entries.

    :param tmp_path: Temporary pytest directory fixture.
    """

    logger = TokenLogger(logs_dir=tmp_path / "logs", cost_threshold_usd=DEFAULT_COST_THRESHOLD_USD)
    logger.set_pricing_data("paid-model", 0.001, "cache", 1)
    logger.log_api_call(
        session_id="sess-1",
        task_id="task-1",
        agent="project-agent",
        model="paid-model",
        input_tokens=600,
        output_tokens=500,
    )

    entries = logger.read_entries()
    session_entries = logger.query_session("sess-1")
    agent_stats = logger.get_agent_stats("sess-1", "project-agent")
    log_content = (tmp_path / "logs" / "tokens.log").read_text(encoding="utf-8").splitlines()
    parsed_line = json.loads(log_content[0])

    assert len(entries) == 1
    assert len(session_entries) == 1
    assert logger.get_session_total_cost() == 1.1
    assert logger.should_warn_cost_threshold() is True
    assert agent_stats["total_calls"] == 1
    assert agent_stats["total_cost_usd"] == 1.1
    assert parsed_line["price_source"] == "cache"


def test_token_logger_loads_default_pricing_catalog(tmp_path: Path) -> None:
    """Description:
        Verify the token logger loads bundled FAITH pricing data and uses separate input/output rates.

    Requirements:
        - This test is needed to prove Phase 9 can estimate costs from FAITH's persisted pricing files without requiring a live pricing MCP server.
        - Verify the logger reads `model-prices.default.json`, records `default` as the source, and calculates cost from distinct input and output token prices.

    :param tmp_path: Temporary pytest directory fixture.
    """

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "model-prices.default.json").write_text(
        json.dumps(
            {
                "generated_date": "2026-05-01",
                "source": "openrouter.ai/models",
                "models": {
                    "openai/gpt-4o": {
                        "input_cost_per_token": 0.0000025,
                        "output_cost_per_token": 0.00001,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    logger = TokenLogger(logs_dir=tmp_path / "logs")

    loaded = logger.load_pricing_catalog(data_dir=data_dir)
    estimated_cost, price_source, price_age_days = logger.estimate_cost("openai/gpt-4o", 100, 50)

    assert loaded is True
    assert estimated_cost == 0.00075
    assert price_source == "default"
    assert price_age_days >= 0


def test_token_logger_prefers_cached_pricing_catalog(tmp_path: Path) -> None:
    """Description:
        Verify the token logger prefers the live cached pricing file over the bundled default file.

    Requirements:
        - This test is needed to prove FAITH uses the freshest available pricing source when both persisted files exist.
        - Verify `model-prices.cache.json` overrides the bundled default file for cost estimation and source metadata.

    :param tmp_path: Temporary pytest directory fixture.
    """

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    payload = {
        "generated_date": "2026-05-02",
        "source": "openrouter.ai/models",
        "models": {
            "openai/gpt-4o": {
                "input_cost_per_token": 0.000003,
                "output_cost_per_token": 0.000012,
            }
        },
    }
    (data_dir / "model-prices.default.json").write_text(
        json.dumps(
            {
                **payload,
                "models": {
                    "openai/gpt-4o": {
                        "input_cost_per_token": 0.0000025,
                        "output_cost_per_token": 0.00001,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "model-prices.cache.json").write_text(json.dumps(payload), encoding="utf-8")

    logger = TokenLogger(logs_dir=tmp_path / "logs")

    loaded = logger.load_pricing_catalog(data_dir=data_dir)
    estimated_cost, price_source, _ = logger.estimate_cost("openai/gpt-4o", 100, 50)

    assert loaded is True
    assert estimated_cost == 0.0009
    assert price_source == "cache"
