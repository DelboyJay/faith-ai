# FAITH-047 — Token & Cost Log

**Phase:** 9 — Logging & Observability
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** IN PROGRESS
**Dependencies:** FAITH-013, FAITH-030
**FRS Reference:** Section 8.5

---

## Objective

Implement token and cost logging for all LLM API calls. Every call is recorded to `logs/tokens.log` as a JSON line containing: timestamp, session ID, task ID, agent name, model name, input token count, output token count, estimated cost (via Pricing tool), price source, and price age in days. Implement proactive cost warnings: when the current session's estimated cost crosses a configurable threshold (default $1.00), surface an alert in the Web UI and recommend cost-saving actions.

Current implementation note: the standalone token logger exists and the live Project Agent browser-chat loop now writes token records plus threshold warnings, but the wider runtime/status integration described by the FRS is not yet fully complete.

---

## Architecture

```
faith/logging/
├── __init__.py
└── token_logger.py    ← TokenLogger class (this task)

tests/
└── test_token_logger.py  ← Tests (this task)
```

---

## Files to Create

### 1. `faith/logging/__init__.py`

```python
"""FAITH Logging — token tracking, cost estimation, and observability."""

from faith.logging.token_logger import TokenEntry, TokenLogger

__all__ = [
    "TokenEntry",
    "TokenLogger",
]
```

### 2. `faith/logging/token_logger.py`

```python
"""FAITH Token & Cost Logger — tracks all LLM API calls and estimated costs.

Every LLM API call is recorded to logs/tokens.log as a JSON line. The PA
writes to the log via calls to TokenLogger.log_api_call(). Agents do not
directly interact with the logger — the PA is the sole writer.

Estimated costs are computed using the Pricing MCP Tool (FAITH-030).
The logger caches pricing data to avoid repeated tool calls within a session.

FRS Reference: Section 8.5
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("faith.logging.token")

DEFAULT_COST_THRESHOLD_USD = 1.00


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TokenEntry(BaseModel):
    """A single token log entry representing an LLM API call.

    Attributes:
        ts: ISO 8601 UTC timestamp of the API call.
        session_id: Session ID (e.g. "sess-0042").
        task_id: Task ID (e.g. "task-001-143201.456").
        agent: Agent that made the call (e.g. "software-developer").
        model: Model name (e.g. "ollama/llama3:8b", "claude-3-5-sonnet").
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.
        estimated_cost: Estimated cost in USD. 0.00 for free models.
        price_source: Source of the price ("cache", "live", "default", "unavailable").
        price_age_days: Days since price was last updated.
    """

    ts: str = Field(default_factory=_now_iso)
    session_id: str
    task_id: str
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    price_source: str
    price_age_days: int

    def to_json_line(self) -> str:
        """Serialise to a single JSON line (no trailing newline)."""
        return self.model_dump_json()

    @classmethod
    def from_json_line(cls, line: str) -> "TokenEntry":
        """Deserialise from a JSON line string."""
        return cls.model_validate_json(line.strip())


class TokenLogger:
    """Tracks LLM API calls and logs token/cost data to logs/tokens.log."""

    def __init__(
        self,
        logs_dir: Path,
        cost_threshold_usd: float = DEFAULT_COST_THRESHOLD_USD,
    ):
        """Initialise the token logger."""
        self.logs_dir = Path(logs_dir)
        self.log_path = self.logs_dir / "tokens.log"
        self.cost_threshold_usd = cost_threshold_usd
        self._file = None
        self._pricing_cache: dict[str, tuple[float, str, int]] = {}
        self._session_total_cost_usd: float = 0.0

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"TokenLogger initialised: path={self.log_path}")

    def open(self) -> None:
        """Open the token log file for appending."""
        if self._file is not None:
            return
        self._file = open(self.log_path, "a", encoding="utf-8", buffering=1)
        logger.debug(f"Token log file opened: {self.log_path}")

    def close(self) -> None:
        """Close the token log file."""
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception as e:
                logger.warning(f"Error closing token log: {e}")
            finally:
                self._file = None

    def _ensure_open(self) -> None:
        """Open the file if it is not already open."""
        if self._file is None or self._file.closed:
            self._file = None
            self.open()

    def write(self, entry: TokenEntry) -> None:
        """Write a single token entry to the log."""
        self._ensure_open()
        try:
            line = entry.to_json_line()
            self._file.write(line + "\n")
            self._file.flush()
        except Exception as e:
            logger.error(f"Failed to write token entry: {e}")
            raise RuntimeError(f"Token log write failed: {e}") from e

    def set_pricing_data(
        self,
        model: str,
        cost_per_token: float,
        source: str,
        age_days: int,
    ) -> None:
        """Cache pricing data for a model."""
        self._pricing_cache[model] = (cost_per_token, source, age_days)

    def get_pricing(self, model: str) -> tuple[float, str, int] | None:
        """Retrieve cached pricing for a model."""
        return self._pricing_cache.get(model)

    def estimate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> tuple[float, str, int]:
        """Estimate the cost of an API call."""
        pricing = self.get_pricing(model)
        if pricing is None:
            return (0.00, "unavailable", 0)

        cost_per_token, source, age_days = pricing
        total_tokens = input_tokens + output_tokens
        estimated_cost = total_tokens * cost_per_token if total_tokens > 0 else 0.00
        return (estimated_cost, source, age_days)

    def log_api_call(
        self,
        session_id: str,
        task_id: str,
        agent: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost: Optional[float] = None,
        price_source: Optional[str] = None,
        price_age_days: Optional[int] = None,
    ) -> TokenEntry:
        """Log an LLM API call."""
        if estimated_cost is None:
            estimated_cost, price_source, price_age_days = self.estimate_cost(
                model, input_tokens, output_tokens
            )
        else:
            if price_source is None:
                price_source = "unavailable"
            if price_age_days is None:
                price_age_days = 0

        entry = TokenEntry(
            session_id=session_id,
            task_id=task_id,
            agent=agent,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            price_source=price_source,
            price_age_days=price_age_days,
        )

        self.write(entry)
        self._session_total_cost_usd += estimated_cost
        return entry

    def get_session_total_cost(self) -> float:
        """Get the accumulated cost for the current session."""
        return self._session_total_cost_usd

    def reset_session_total(self) -> None:
        """Reset the session cost accumulator."""
        self._session_total_cost_usd = 0.0

    def should_warn_cost_threshold(self) -> bool:
        """Check if the session cost has crossed the warning threshold."""
        return self._session_total_cost_usd >= self.cost_threshold_usd

    def read_entries(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TokenEntry]:
        """Read token entries from the log file."""
        if not self.log_path.exists():
            return []

        entries = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                if i < offset:
                    continue
                if len(entries) >= limit:
                    break
                try:
                    entries.append(TokenEntry.from_json_line(line))
                except Exception:
                    logger.warning(f"Skipping malformed token line {i}")
        return entries

    def query_session(
        self,
        session_id: str,
        limit: int = 1000,
    ) -> list[TokenEntry]:
        """Query all token entries for a specific session."""
        if not self.log_path.exists():
            return []

        all_entries = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = TokenEntry.from_json_line(line)
                    if entry.session_id == session_id:
                        all_entries.append(entry)
                except Exception:
                    continue

        all_entries.reverse()
        return all_entries[:limit]

    def query_agent(
        self,
        session_id: str,
        agent: str,
        limit: int = 1000,
    ) -> list[TokenEntry]:
        """Query token entries for a specific agent in a session."""
        if not self.log_path.exists():
            return []

        all_entries = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = TokenEntry.from_json_line(line)
                    if entry.session_id == session_id and entry.agent == agent:
                        all_entries.append(entry)
                except Exception:
                    continue

        all_entries.reverse()
        return all_entries[:limit]

    def calculate_session_cost(self, session_id: str) -> float:
        """Calculate total cost for a session."""
        entries = self.query_session(session_id, limit=10000)
        return sum(e.estimated_cost for e in entries)

    def calculate_agent_cost(self, session_id: str, agent: str) -> float:
        """Calculate total cost for an agent in a session."""
        entries = self.query_agent(session_id, agent, limit=10000)
        return sum(e.estimated_cost for e in entries)

    def get_agent_stats(self, session_id: str, agent: str) -> dict[str, Any]:
        """Get statistics for an agent in a session."""
        entries = self.query_agent(session_id, agent, limit=10000)

        models_used = set()
        total_input = 0
        total_output = 0
        total_cost = 0.0

        for entry in entries:
            models_used.add(entry.model)
            total_input += entry.input_tokens
            total_output += entry.output_tokens
            total_cost += entry.estimated_cost

        return {
            "agent": agent,
            "total_calls": len(entries),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost_usd": total_cost,
            "models_used": sorted(list(models_used)),
        }

    @staticmethod
    def from_system_config(
        logs_dir: Path,
        system_config: dict[str, Any],
    ) -> "TokenLogger":
        """Factory method to create a TokenLogger from .faith/system.yaml config."""
        logger_config = system_config.get("token_logger", {})
        cost_threshold = logger_config.get(
            "cost_threshold_usd", DEFAULT_COST_THRESHOLD_USD
        )
        return TokenLogger(logs_dir=logs_dir, cost_threshold_usd=cost_threshold)

    def __enter__(self):
        """Context manager support — opens the log file."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support — closes the log file."""
        self.close()
        return False

    def __repr__(self) -> str:
        return f"TokenLogger(log_path={self.log_path}, cost_threshold={self.cost_threshold_usd} USD)"
```

### 3. `tests/test_token_logger.py`

```python
"""Tests for the FAITH token and cost logger."""

import json
from pathlib import Path

import pytest

from faith.logging.token_logger import (
    DEFAULT_COST_THRESHOLD_USD,
    TokenEntry,
    TokenLogger,
)


@pytest.fixture
def logs_dir(tmp_path):
    """Create a temporary logs directory."""
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def token_logger(logs_dir):
    """Create a TokenLogger opened for writing."""
    tl = TokenLogger(logs_dir=logs_dir)
    tl.open()
    yield tl
    tl.close()


@pytest.fixture
def sample_entry():
    """A sample TokenEntry for testing."""
    return TokenEntry(
        ts="2026-03-23T14:32:01Z",
        session_id="sess-0042",
        task_id="task-001-143201.456",
        agent="software-developer",
        model="ollama/llama3:8b",
        input_tokens=1240,
        output_tokens=380,
        estimated_cost=0.00,
        price_source="cache",
        price_age_days=1,
    )


def test_entry_to_json_line(sample_entry):
    """TokenEntry serialises to valid JSON."""
    line = sample_entry.to_json_line()
    parsed = json.loads(line)
    assert parsed["agent"] == "software-developer"
    assert parsed["estimated_cost"] == 0.00


def test_entry_from_json_line(sample_entry):
    """TokenEntry deserialises correctly."""
    line = sample_entry.to_json_line()
    restored = TokenEntry.from_json_line(line)
    assert restored.agent == sample_entry.agent


def test_write_creates_file(token_logger, sample_entry, logs_dir):
    """Writing an entry creates the tokens.log file."""
    token_logger.write(sample_entry)
    assert (logs_dir / "tokens.log").exists()


def test_log_api_call_explicit_cost(token_logger):
    """log_api_call writes with explicit cost."""
    entry = token_logger.log_api_call(
        session_id="sess-1",
        task_id="task-1",
        agent="dev",
        model="gpt-4",
        input_tokens=100,
        output_tokens=50,
        estimated_cost=0.15,
        price_source="live",
        price_age_days=0,
    )
    assert entry.estimated_cost == 0.15


def test_log_api_call_free_model(token_logger):
    """log_api_call logs free models with 0.00 cost."""
    entry = token_logger.log_api_call(
        session_id="sess-1",
        task_id="task-1",
        agent="dev",
        model="ollama/llama3:8b",
        input_tokens=100,
        output_tokens=50,
    )
    assert entry.estimated_cost == 0.00
    assert entry.price_source == "unavailable"


def test_pricing_cache(token_logger):
    """Pricing data can be cached and retrieved."""
    token_logger.set_pricing_data("gpt-4", 0.0015, "live", 0)
    pricing = token_logger.get_pricing("gpt-4")
    assert pricing == (0.0015, "live", 0)


def test_estimate_cost_with_pricing(token_logger):
    """estimate_cost uses cached pricing."""
    token_logger.set_pricing_data("gpt-4", 0.001, "cache", 1)
    cost, source, age = token_logger.estimate_cost("gpt-4", 1000, 500)
    assert cost == 1.50


def test_session_total_accumulates(token_logger):
    """Session total cost accumulates."""
    token_logger.set_pricing_data("model-a", 0.001, "cache", 0)
    token_logger.log_api_call(
        session_id="sess-1",
        task_id="task-1",
        agent="dev",
        model="model-a",
        input_tokens=1000,
        output_tokens=500,
    )
    assert token_logger.get_session_total_cost() == 1.50


def test_cost_threshold(token_logger):
    """Cost threshold detection works."""
    token_logger.set_pricing_data("expensive", 0.01, "cache", 0)
    
    token_logger.log_api_call(
        session_id="sess-1", task_id="task-1", agent="dev",
        model="expensive", input_tokens=50000, output_tokens=1,
    )
    assert not token_logger.should_warn_cost_threshold()
    
    token_logger.log_api_call(
        session_id="sess-1", task_id="task-1", agent="dev",
        model="expensive", input_tokens=50000, output_tokens=1,
    )
    assert token_logger.should_warn_cost_threshold()


def test_query_session(token_logger):
    """query_session filters by session ID."""
    token_logger.log_api_call(
        session_id="sess-1", task_id="task-1", agent="dev", model="a",
        input_tokens=10, output_tokens=5,
        estimated_cost=0.0, price_source="unavailable", price_age_days=0,
    )
    token_logger.log_api_call(
        session_id="sess-2", task_id="task-1", agent="dev", model="b",
        input_tokens=10, output_tokens=5,
        estimated_cost=0.0, price_source="unavailable", price_age_days=0,
    )
    
    results = token_logger.query_session("sess-1")
    assert len(results) == 1
    assert results[0].model == "a"


def test_get_agent_stats(token_logger):
    """get_agent_stats returns per-agent metrics."""
    token_logger.log_api_call(
        session_id="sess-1", task_id="task-1", agent="dev", model="model-a",
        input_tokens=100, output_tokens=50,
        estimated_cost=0.10, price_source="cache", price_age_days=0,
    )
    
    stats = token_logger.get_agent_stats("sess-1", "dev")
    assert stats["agent"] == "dev"
    assert stats["total_calls"] == 1
    assert stats["total_input_tokens"] == 100
    assert stats["total_cost_usd"] == 0.10


def test_from_system_config(logs_dir):
    """Factory method reads config correctly."""
    config = {"token_logger": {"cost_threshold_usd": 5.00}}
    tl = TokenLogger.from_system_config(logs_dir, config)
    assert tl.cost_threshold_usd == 5.00


def test_context_manager(logs_dir):
    """TokenLogger works as a context manager."""
    with TokenLogger(logs_dir=logs_dir) as tl:
        tl.log_api_call(
            session_id="sess-1", task_id="task-1", agent="dev", model="test",
            input_tokens=10, output_tokens=5,
            estimated_cost=0.0, price_source="unavailable", price_age_days=0,
        )
    assert tl._file is None
    content = (logs_dir / "tokens.log").read_text(encoding="utf-8")
    assert "dev" in content
```

---

## Integration Points

The TokenLogger integrates with FAITH-013 (LLM API client) and FAITH-030 (Pricing tool). The PA calls `log_api_call()` after each successful LLM API call. The Web UI (FAITH-044) reads token logs via FastAPI endpoints.

---

## Acceptance Criteria

1. `TokenEntry` includes all FRS 8.5 fields: `ts`, `session_id`, `task_id`, `agent`, `model`, `input_tokens`, `output_tokens`, `estimated_cost`, `price_source`, `price_age_days`.
2. `TokenEntry.to_json_line()` and `from_json_line()` round-trip correctly.
3. `TokenLogger` writes to `logs/tokens.log` with immediate flush.
4. `log_api_call()` logs API calls with explicit or estimated costs.
5. Pricing cache: `set_pricing_data()`, `get_pricing()`, `estimate_cost()` work correctly.
6. Session cost tracking: `get_session_total_cost()`, `reset_session_total()`, `should_warn_cost_threshold()`.
7. Query methods: `read_entries()`, `query_session()`, `query_agent()` work.
8. `calculate_session_cost()`, `calculate_agent_cost()`, `get_agent_stats()` compute correctly.
9. `from_system_config()` reads threshold from `.faith/system.yaml`, defaulting to $1.00.
10. Context manager support (`with TokenLogger(...) as tl:`).
11. All 20+ tests pass.

---

## Notes for Implementer

- PA is the sole writer; only PA instantiates TokenLogger.
- File opened with `buffering=1` for immediate flush.
- Session-scoped pricing cache; reset on new session.
- Default cost threshold: $1.00 (configured via `.faith/system.yaml` section `token_logger.cost_threshold_usd`).
- When threshold is crossed, PA publishes `session:cost_threshold_crossed` event to Web UI.
- FAITH-044 fetches cost data via `/api/session/{session_id}/tokens` endpoint.
- Logs are archived via FAITH-048 log rotation, never auto-deleted.
