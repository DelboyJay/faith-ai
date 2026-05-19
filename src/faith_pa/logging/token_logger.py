"""Description:
    Provide token and estimated-cost logging for FAITH LLM API calls.

Requirements:
    - Persist every logged LLM call as one JSON line.
    - Track per-session totals and expose helpers for threshold warnings and basic cost statistics.
    - Bootstrap pricing data from FAITH's persisted bundled or cached pricing files when available.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_COST_THRESHOLD_USD = 1.0


def _now_iso() -> str:
    """Description:
        Return the current UTC time as an ISO-8601 string.

    Requirements:
        - Always emit a trailing `Z` suffix for UTC timestamps.

    :returns: Current UTC timestamp string.
    """

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TokenEntry(BaseModel):
    """Description:
        Represent one persisted token-log entry.

    Requirements:
        - Preserve all fields required by FRS section 8.5.
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
    cache_hit: bool | None = None
    cached_input_tokens: int | None = None
    cached_output_tokens: int | None = None
    effective_context_snapshot_id: str | None = None
    effective_context_turn_id: str | None = None
    context_window_percentage: int | None = None
    context_files: list[dict[str, Any]] = Field(default_factory=list)

    def to_json_line(self) -> str:
        """Description:
            Serialise the token entry as one JSON-lines record.

        Requirements:
            - Return one compact JSON object with no trailing newline.

        :returns: JSON-lines representation of the token entry.
        """

        return self.model_dump_json()

    @classmethod
    def from_json_line(cls, payload: str) -> TokenEntry:
        """Description:
            Restore one token entry from JSON-lines text.

        Requirements:
            - Strip surrounding whitespace before validation.

        :param payload: JSON-lines text to parse.
        :returns: Parsed token entry.
        """

        return cls.model_validate_json(payload.strip())


class TokenLogger:
    """Description:
        Append and query FAITH token and cost log entries.

    Requirements:
        - Write newline-delimited JSON records to `tokens.log`.
        - Track the running cost for the active session.
        - Cache per-model price metadata for later cost estimation.
    """

    def __init__(
        self, *, logs_dir: Path, cost_threshold_usd: float = DEFAULT_COST_THRESHOLD_USD
    ) -> None:
        """Description:
            Initialise the token logger.

        Requirements:
            - Create the target logs directory eagerly.

        :param logs_dir: Directory that contains `tokens.log`.
        :param cost_threshold_usd: Cost threshold that triggers warnings.
        """

        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.logs_dir / "tokens.log"
        self.cost_threshold_usd = cost_threshold_usd
        self._pricing_cache: dict[str, tuple[float, str, int]] = {}
        self._pricing_breakdown: dict[str, tuple[float, float, str, int]] = {}
        self._session_total_cost_usd = 0.0
        self._warning_emitted = False
        self._cache_diagnostics: dict[str, dict[str, int | bool | None]] = {}

    def set_pricing_data(
        self, model: str, cost_per_token: float, source: str, age_days: int
    ) -> None:
        """Description:
            Cache pricing data for one model.

        Requirements:
            - Preserve the price source and price age for later logging.

        :param model: Model name.
        :param cost_per_token: Estimated cost per token in USD.
        :param source: Source of the price data.
        :param age_days: Age of the cached price in days.
        """

        self._pricing_cache[model] = (cost_per_token, source, age_days)
        self._pricing_breakdown[model] = (cost_per_token, cost_per_token, source, age_days)

    def set_detailed_pricing_data(
        self,
        model: str,
        *,
        input_cost_per_token: float,
        output_cost_per_token: float,
        source: str,
        age_days: int,
    ) -> None:
        """Description:
            Cache distinct input and output token prices for one model.

        Requirements:
            - Preserve separate input and output token rates for more accurate cost estimation.
            - Keep the compatibility cache populated so legacy callers can still query one representative rate.

        :param model: Model name.
        :param input_cost_per_token: Input token price in USD.
        :param output_cost_per_token: Output token price in USD.
        :param source: Source of the price data.
        :param age_days: Age of the cached price in days.
        """

        representative_rate = (input_cost_per_token + output_cost_per_token) / 2
        self._pricing_cache[model] = (representative_rate, source, age_days)
        self._pricing_breakdown[model] = (
            input_cost_per_token,
            output_cost_per_token,
            source,
            age_days,
        )

    def set_cache_diagnostics(
        self,
        model: str,
        *,
        cache_hit: bool | None = None,
        cached_input_tokens: int | None = None,
        cached_output_tokens: int | None = None,
    ) -> None:
        """Description:
            Cache optional prompt-caching diagnostics for one model.

        Requirements:
            - Preserve diagnostics only when the provider reports them.
            - Leave cost estimation unchanged when the provider has no cache data.

        :param model: Model name.
        :param cache_hit: Whether the provider reported a cache hit.
        :param cached_input_tokens: Input tokens served from cache.
        :param cached_output_tokens: Output tokens served from cache.
        """

        self._cache_diagnostics[model] = {
            "cache_hit": cache_hit,
            "cached_input_tokens": cached_input_tokens,
            "cached_output_tokens": cached_output_tokens,
        }

    def get_pricing(self, model: str) -> tuple[float, str, int] | None:
        """Description:
            Return cached pricing data for one model.

        Requirements:
            - Return `None` when no cached price is available.

        :param model: Model name.
        :returns: Cached pricing tuple, if present.
        """

        return self._pricing_cache.get(model)

    def estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> tuple[float, str, int]:
        """Description:
            Estimate the cost for one LLM API call.

        Requirements:
            - Return zero cost with `unavailable` pricing metadata when no price is cached.

        :param model: Model name.
        :param input_tokens: Prompt token count.
        :param output_tokens: Completion token count.
        :returns: Estimated cost, price source, and price age in days.
        """

        pricing_breakdown = self._pricing_breakdown.get(model)
        if pricing_breakdown is not None:
            input_rate, output_rate, source, age_days = pricing_breakdown
            return (
                round((input_tokens * input_rate) + (output_tokens * output_rate), 12),
                source,
                age_days,
            )

        pricing = self.get_pricing(model)
        if pricing is None:
            return (0.0, "unavailable", 0)
        cost_per_token, source, age_days = pricing
        return (round((input_tokens + output_tokens) * cost_per_token, 12), source, age_days)

    def load_pricing_catalog(self, *, data_dir: Path) -> bool:
        """Description:
            Load persisted FAITH model pricing from the live cache or bundled default file.

        Requirements:
            - Prefer `model-prices.cache.json` when it exists and is valid.
            - Fall back to `model-prices.default.json` when no cache is available.
            - Ignore malformed files without raising and return `False` when no valid catalog can be loaded.

        :param data_dir: Directory containing the FAITH pricing files.
        :returns: `True` when a valid pricing catalog was loaded.
        """

        data_root = Path(data_dir)
        candidates = (
            (data_root / "model-prices.cache.json", "cache"),
            (data_root / "model-prices.default.json", "default"),
        )
        for path, source_label in candidates:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            models = payload.get("models")
            if not isinstance(models, dict):
                continue
            age_days = self._catalog_age_days(payload.get("generated_date"))
            loaded_any = False
            for model_name, model_payload in models.items():
                if not isinstance(model_payload, dict):
                    continue
                input_cost = model_payload.get("input_cost_per_token")
                output_cost = model_payload.get("output_cost_per_token")
                if not isinstance(input_cost, (int, float)) or not isinstance(
                    output_cost, (int, float)
                ):
                    continue
                self.set_detailed_pricing_data(
                    str(model_name),
                    input_cost_per_token=float(input_cost),
                    output_cost_per_token=float(output_cost),
                    source=source_label,
                    age_days=age_days,
                )
                loaded_any = True
            if loaded_any:
                return True
        return False

    @staticmethod
    def _catalog_age_days(generated_date: object) -> int:
        """Description:
            Convert one pricing-catalog generated-date value into an age in days.

        Requirements:
            - Return zero when the supplied value is missing or malformed.
            - Never return a negative age when the generated date is in the future.

        :param generated_date: Raw generated-date value from the pricing catalog payload.
        :returns: Non-negative age in days.
        """

        if not isinstance(generated_date, str) or not generated_date.strip():
            return 0
        try:
            generated_at = datetime.strptime(generated_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            return 0
        current_date = datetime.now(timezone.utc).date()
        return max((current_date - generated_at).days, 0)

    def write(self, entry: TokenEntry) -> None:
        """Description:
            Append one token entry to the log file.

        Requirements:
            - Flush the record immediately so Web UI readers can see it without delay.

        :param entry: Token entry to persist.
        """

        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_json_line() + "\n")
            handle.flush()

    def log_api_call(
        self,
        *,
        session_id: str,
        task_id: str,
        agent: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float | None = None,
        price_source: str | None = None,
        price_age_days: int | None = None,
        cache_hit: bool | None = None,
        cached_input_tokens: int | None = None,
        cached_output_tokens: int | None = None,
        effective_context_snapshot_id: str | None = None,
        effective_context_turn_id: str | None = None,
        context_window_percentage: int | None = None,
        context_files: list[dict[str, Any]] | None = None,
    ) -> TokenEntry:
        """Description:
            Create and persist one token log entry.

        Requirements:
            - Estimate cost from cached price data when the caller does not supply it.
            - Add the written cost onto the running session total.

        :param session_id: Session identifier.
        :param task_id: Task identifier.
        :param agent: Agent identifier.
        :param model: Model name.
        :param input_tokens: Prompt token count.
        :param output_tokens: Completion token count.
        :param estimated_cost: Optional precomputed cost.
        :param price_source: Optional explicit price source.
        :param price_age_days: Optional explicit price age.
        :param cache_hit: Optional provider-reported cache-hit flag.
        :param cached_input_tokens: Optional provider-reported cached input-token count.
        :param cached_output_tokens: Optional provider-reported cached output-token count.
        :param effective_context_snapshot_id: Optional persisted effective-context snapshot identifier.
        :param effective_context_turn_id: Optional turn identifier associated with the effective-context snapshot.
        :param context_window_percentage: Optional estimated context-window usage percentage.
        :param context_files: Optional per-file context attribution payload.
        :returns: Persisted token entry.
        """

        if estimated_cost is None:
            estimated_cost, price_source, price_age_days = self.estimate_cost(
                model, input_tokens, output_tokens
            )
        cache_diagnostics = self._cache_diagnostics.get(model, {})
        entry = TokenEntry(
            session_id=session_id,
            task_id=task_id,
            agent=agent,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            price_source=price_source or "unavailable",
            price_age_days=price_age_days or 0,
            cache_hit=cache_hit if cache_hit is not None else cache_diagnostics.get("cache_hit"),
            cached_input_tokens=cached_input_tokens
            if cached_input_tokens is not None
            else cache_diagnostics.get("cached_input_tokens"),
            cached_output_tokens=cached_output_tokens
            if cached_output_tokens is not None
            else cache_diagnostics.get("cached_output_tokens"),
            effective_context_snapshot_id=effective_context_snapshot_id,
            effective_context_turn_id=effective_context_turn_id,
            context_window_percentage=context_window_percentage,
            context_files=list(context_files or []),
        )
        self.write(entry)
        self._session_total_cost_usd += estimated_cost
        return entry

    def get_session_total_cost(self) -> float:
        """Description:
            Return the current running session cost total.

        Requirements:
            - Return the accumulated in-memory session total in USD.

        :returns: Running session cost in USD.
        """

        return self._session_total_cost_usd

    def reset_session_total(self) -> None:
        """Description:
            Reset the in-memory session total and warning state.

        Requirements:
            - Clear both the accumulated cost and the threshold-warning latch.
        """

        self._session_total_cost_usd = 0.0
        self._warning_emitted = False

    def should_warn_cost_threshold(self) -> bool:
        """Description:
            Return whether the session cost has crossed the configured threshold.

        Requirements:
            - Compare the running session total against the configured threshold.

        :returns: `True` when the warning threshold has been crossed.
        """

        return self._session_total_cost_usd >= self.cost_threshold_usd

    def consume_threshold_warning(self) -> bool:
        """Description:
            Return whether a new threshold warning should be surfaced now.

        Requirements:
            - Emit `True` only once per session unless the total is reset.

        :returns: `True` when the threshold has just been crossed for the first time.
        """

        if not self.should_warn_cost_threshold() or self._warning_emitted:
            return False
        self._warning_emitted = True
        return True

    def read_entries(self, *, limit: int = 100, offset: int = 0) -> list[TokenEntry]:
        """Description:
            Read a slice of token entries from disk.

        Requirements:
            - Skip malformed JSON-lines records without raising.

        :param limit: Maximum number of entries to return.
        :param offset: Number of valid entries to skip first.
        :returns: Parsed token entries in file order.
        """

        if not self.log_path.exists():
            return []
        entries: list[TokenEntry] = []
        valid_index = 0
        for raw_line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = TokenEntry.from_json_line(line)
            except Exception:
                continue
            if valid_index < offset:
                valid_index += 1
                continue
            if len(entries) >= limit:
                break
            entries.append(entry)
            valid_index += 1
        return entries

    def query_session(self, session_id: str, *, limit: int = 1000) -> list[TokenEntry]:
        """Description:
            Return token entries for one session in reverse chronological order.

        Requirements:
            - Filter by the requested session identifier.

        :param session_id: Session identifier to query.
        :param limit: Maximum number of entries to return.
        :returns: Matching token entries ordered newest first.
        """

        return self._query(lambda entry: entry.session_id == session_id, limit=limit)

    def query_agent(self, session_id: str, agent: str, *, limit: int = 1000) -> list[TokenEntry]:
        """Description:
            Return token entries for one agent inside one session.

        Requirements:
            - Filter by both session identifier and agent identifier.

        :param session_id: Session identifier to query.
        :param agent: Agent identifier to query.
        :param limit: Maximum number of entries to return.
        :returns: Matching token entries ordered newest first.
        """

        return self._query(
            lambda entry: entry.session_id == session_id and entry.agent == agent,
            limit=limit,
        )

    def _query(self, predicate: Any, *, limit: int) -> list[TokenEntry]:
        """Description:
            Read and filter token entries using one predicate.

        Requirements:
            - Skip malformed JSON-lines records without raising.
            - Return newest matching entries first.

        :param predicate: Callable deciding whether one entry matches.
        :param limit: Maximum number of entries to return.
        :returns: Matching token entries ordered newest first.
        """

        if not self.log_path.exists():
            return []
        matched: list[TokenEntry] = []
        for raw_line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = TokenEntry.from_json_line(line)
            except Exception:
                continue
            if predicate(entry):
                matched.append(entry)
        matched.reverse()
        return matched[:limit]

    def calculate_session_cost(self, session_id: str) -> float:
        """Description:
            Calculate the persisted total estimated cost for one session.

        Requirements:
            - Sum the stored cost field across the matching entries.

        :param session_id: Session identifier to query.
        :returns: Persisted session cost total in USD.
        """

        return sum(entry.estimated_cost for entry in self.query_session(session_id, limit=10_000))

    def calculate_agent_cost(self, session_id: str, agent: str) -> float:
        """Description:
            Calculate the persisted total estimated cost for one agent in one session.

        Requirements:
            - Sum the stored cost field across the matching entries.

        :param session_id: Session identifier to query.
        :param agent: Agent identifier to query.
        :returns: Persisted agent cost total in USD.
        """

        return sum(
            entry.estimated_cost for entry in self.query_agent(session_id, agent, limit=10_000)
        )

    def get_agent_stats(self, session_id: str, agent: str) -> dict[str, Any]:
        """Description:
            Build aggregate token and cost statistics for one agent in one session.

        Requirements:
            - Include total calls, token totals, cost total, and the list of models used.

        :param session_id: Session identifier to query.
        :param agent: Agent identifier to query.
        :returns: Aggregate statistics payload.
        """

        entries = self.query_agent(session_id, agent, limit=10_000)
        return {
            "agent": agent,
            "total_calls": len(entries),
            "total_input_tokens": sum(entry.input_tokens for entry in entries),
            "total_output_tokens": sum(entry.output_tokens for entry in entries),
            "total_cost_usd": sum(entry.estimated_cost for entry in entries),
            "models_used": sorted({entry.model for entry in entries}),
        }

    def highest_cost_agent(self, session_id: str) -> dict[str, Any] | None:
        """Description:
            Return the highest-cost agent summary for one session.

        Requirements:
            - Return `None` when the session has no token entries.

        :param session_id: Session identifier to query.
        :returns: Highest-cost agent summary, if any.
        """

        entries = self.query_session(session_id, limit=10_000)
        if not entries:
            return None
        totals: dict[str, float] = {}
        for entry in entries:
            totals[entry.agent] = totals.get(entry.agent, 0.0) + entry.estimated_cost
        agent_name = max(totals, key=totals.get)
        stats = self.get_agent_stats(session_id, agent_name)
        stats["total_cost_usd"] = totals[agent_name]
        return stats

    def cheaper_model_option(self, current_model: str) -> str | None:
        """Description:
            Return one cheaper available model suggestion for the supplied model.

        Requirements:
            - Compare models using the sum of input and output token rates.
            - Return `None` when no cheaper configured model is known.

        :param current_model: Current model name to compare against.
        :returns: Cheaper model identifier, if one is available.
        """

        current_pricing = self._pricing_breakdown.get(current_model)
        if current_pricing is None:
            return None
        current_total_rate = current_pricing[0] + current_pricing[1]
        cheaper_candidates = [
            (
                model_name,
                pricing[0] + pricing[1],
            )
            for model_name, pricing in self._pricing_breakdown.items()
            if model_name != current_model and (pricing[0] + pricing[1]) < current_total_rate
        ]
        if not cheaper_candidates:
            return None
        cheaper_candidates.sort(key=lambda item: (item[1], item[0]))
        return cheaper_candidates[0][0]

    @classmethod
    def from_system_config(cls, logs_dir: Path, system_config: dict[str, Any]) -> TokenLogger:
        """Description:
            Build a token logger from `.faith/system.yaml`-style config.

        Requirements:
            - Read `cost_warning.threshold_usd` when it is present.
            - Fall back to the default threshold when the config omits it.

        :param logs_dir: Directory that contains `tokens.log`.
        :param system_config: Parsed system configuration payload.
        :returns: Configured token logger.
        """

        threshold = system_config.get("cost_warning", {}).get(
            "threshold_usd", DEFAULT_COST_THRESHOLD_USD
        )
        return cls(logs_dir=logs_dir, cost_threshold_usd=threshold)
