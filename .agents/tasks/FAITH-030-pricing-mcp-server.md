# FAITH-030 — Pricing MCP Server

**Phase:** 6 — MCP Tool Servers
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-026 (Browser Automation / Playwright), FAITH-008 (Event System)
**FRS Reference:** Section 4.9

---

## Objective

Optional future work. A dedicated Pricing MCP Tool server is not required for the minimal open-source v1 architecture. If FAITH later implements it, the server would provide up-to-date LLM pricing data to the PA and Web UI by scraping OpenRouter's model list, caching results locally, and serving pricing queries via MCP commands.

---

## Architecture

```
faith/tools/pricing/
├── __init__.py
├── server.py            ← MCP server entry point and command registration
├── scraper.py           ← Playwright-based OpenRouter scraper
├── cache.py             ← Cache management (load, validate, write, age)
├── models.py            ← Pydantic models for pricing data
└── fallback.py          ← PA-as-fallback-parser pattern implementation

data/
├── model-prices.default.json   ← Bundled pricing (committed to git)
└── model-prices.cache.json     ← Live scraped pricing (gitignored)

tests/
└── test_pricing_server.py      ← Full test suite
```

---

## Files to Create

### 1. `faith/tools/pricing/models.py`

```python
"""Pydantic models for the Pricing MCP Tool.

Defines the schema for model pricing entries, the full price list,
and validation logic for realistic pricing ranges.

FRS Reference: Section 4.9.2
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger("faith.tools.pricing.models")

# Validation constants — realistic per-token price bounds (USD).
# These catch obviously broken scrape results (e.g. $100/token or negative).
MIN_COST_PER_TOKEN = 0.0           # Free models exist (Ollama, some OpenRouter)
MAX_COST_PER_TOKEN = 0.001         # ~$1 per 1K tokens is extreme upper bound
MIN_MODEL_COUNT = 10               # A valid scrape should find at least this many


class ModelPricing(BaseModel):
    """Pricing data for a single LLM model.

    Attributes:
        input_cost_per_token: Cost in USD per input token.
        output_cost_per_token: Cost in USD per output token.
        privacy_tier: The model's privacy classification
            ("public", "internal", "confidential").
        training_opt_out: Whether the provider honours training opt-out.
        context_window: Maximum context window size in tokens (optional).
    """

    input_cost_per_token: float = Field(ge=0.0)
    output_cost_per_token: float = Field(ge=0.0)
    privacy_tier: str = Field(default="public")
    training_opt_out: bool = Field(default=False)
    context_window: Optional[int] = Field(default=None, ge=0)

    @field_validator("input_cost_per_token", "output_cost_per_token")
    @classmethod
    def validate_cost_range(cls, v: float) -> float:
        """Reject prices that are unrealistically high."""
        if v > MAX_COST_PER_TOKEN:
            raise ValueError(
                f"Cost per token {v} exceeds maximum realistic value "
                f"{MAX_COST_PER_TOKEN}"
            )
        return v

    @field_validator("privacy_tier")
    @classmethod
    def validate_privacy_tier(cls, v: str) -> str:
        """Ensure privacy tier is one of the recognised values."""
        allowed = {"public", "internal", "confidential"}
        if v not in allowed:
            raise ValueError(f"privacy_tier must be one of {allowed}, got '{v}'")
        return v


class PriceList(BaseModel):
    """Complete pricing data set for all known models.

    Attributes:
        generated_date: ISO date string when this data was generated.
        source: Where the data came from (e.g. "openrouter.ai/models").
        models: Mapping of model identifier to its pricing data.
    """

    generated_date: str = Field(
        default_factory=lambda: date.today().isoformat()
    )
    source: str = Field(default="openrouter.ai/models")
    models: dict[str, ModelPricing] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_minimum_model_count(self) -> "PriceList":
        """Warn (but don't reject) if the model count is suspiciously low.

        A valid OpenRouter scrape should return many models. A very small
        count suggests a partial failure. We log a warning but still
        accept the data — the caller decides whether to use it.
        """
        if self.models and len(self.models) < MIN_MODEL_COUNT:
            logger.warning(
                f"Price list contains only {len(self.models)} models "
                f"(expected at least {MIN_MODEL_COUNT}). "
                f"Data may be incomplete."
            )
        return self

    def is_valid_for_cache(self) -> bool:
        """Check whether this price list meets the minimum bar for caching.

        Returns True only if the list contains at least MIN_MODEL_COUNT
        models. This prevents a broken scrape from overwriting good data.
        """
        return len(self.models) >= MIN_MODEL_COUNT


class CostEstimate(BaseModel):
    """Result of a cost calculation for a specific LLM call.

    Attributes:
        model: The model identifier.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.
        input_cost: Cost for input tokens in USD.
        output_cost: Cost for output tokens in USD.
        total_cost: Total cost in USD.
        currency: Currency code (always "USD").
        price_source: Where the pricing data came from
            ("cache", "default", "pa_fallback").
        price_age_days: How many days old the pricing data is.
    """

    model: str
    input_tokens: int
    output_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float
    currency: str = "USD"
    price_source: str = "cache"
    price_age_days: int = 0
```

### 2. `faith/tools/pricing/cache.py`

```python
"""Cache management for the Pricing MCP Tool.

Handles loading, validating, writing, and age-checking of pricing data.
Implements the two-tier cache priority: live cache > bundled default.

FRS Reference: Section 4.9.4
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from faith.tools.pricing.models import PriceList

logger = logging.getLogger("faith.tools.pricing.cache")

# Default paths relative to the FAITH root directory.
DEFAULT_CACHE_PATH = "data/model-prices.cache.json"
DEFAULT_BUNDLED_PATH = "data/model-prices.default.json"

# Staleness threshold: warn after this many days without refresh.
STALE_THRESHOLD_DAYS = 7


class PriceCache:
    """Manages the two-tier pricing cache.

    Priority order:
    1. data/model-prices.cache.json (live scraped, if present and valid)
    2. data/model-prices.default.json (bundled with FAITH release)

    Attributes:
        cache_path: Path to the live scraped cache file.
        bundled_path: Path to the bundled default price file.
        _active: The currently active PriceList (loaded on init).
        _source: Which tier the active data came from ("cache" or "default").
    """

    def __init__(self, faith_root: Path):
        """Initialise the cache manager.

        Args:
            faith_root: Path to the FAITH project root directory.
        """
        self.cache_path = faith_root / DEFAULT_CACHE_PATH
        self.bundled_path = faith_root / DEFAULT_BUNDLED_PATH
        self._active: Optional[PriceList] = None
        self._source: str = "none"

    def load(self) -> PriceList:
        """Load pricing data using the two-tier priority.

        Tries the live cache first, then falls back to the bundled default.
        If neither is available, returns an empty PriceList.

        Returns:
            The best available PriceList.
        """
        # Tier 1: live scraped cache
        cached = self._load_file(self.cache_path)
        if cached is not None and cached.is_valid_for_cache():
            self._active = cached
            self._source = "cache"
            logger.info(
                f"Loaded live cache: {len(cached.models)} models "
                f"(generated {cached.generated_date})"
            )
            return self._active

        # Tier 2: bundled default
        bundled = self._load_file(self.bundled_path)
        if bundled is not None:
            self._active = bundled
            self._source = "default"
            logger.info(
                f"Loaded bundled default: {len(bundled.models)} models "
                f"(generated {bundled.generated_date})"
            )
            return self._active

        # Neither available — return empty
        logger.warning(
            "No pricing data available — neither cache nor bundled default found"
        )
        self._active = PriceList()
        self._source = "none"
        return self._active

    def _load_file(self, path: Path) -> Optional[PriceList]:
        """Load and validate a price list from a JSON file.

        Args:
            path: Path to the JSON file.

        Returns:
            Parsed PriceList, or None if the file is missing or invalid.
        """
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return PriceList.model_validate(data)
        except FileNotFoundError:
            logger.debug(f"Price file not found: {path}")
            return None
        except Exception as e:
            logger.warning(f"Failed to load price file {path}: {e}")
            return None

    def write(self, price_list: PriceList) -> bool:
        """Validate and write a new price list to the live cache.

        Only overwrites the cache if the new data passes validation:
        - Minimum model count (MIN_MODEL_COUNT)
        - All individual model prices within realistic ranges

        Args:
            price_list: The new pricing data to cache.

        Returns:
            True if the write succeeded, False if validation failed.
        """
        if not price_list.is_valid_for_cache():
            logger.warning(
                f"Refusing to write price list with only "
                f"{len(price_list.models)} models (minimum: "
                f"{price_list.__class__.__name__} requires "
                f"{__import__('faith.tools.pricing.models', fromlist=['MIN_MODEL_COUNT']).MIN_MODEL_COUNT})"
            )
            return False

        try:
            # Ensure data directory exists
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)

            # Write atomically: write to temp file, then rename
            tmp_path = self.cache_path.with_suffix(".tmp")
            tmp_path.write_text(
                price_list.model_dump_json(indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.cache_path)

            self._active = price_list
            self._source = "cache"
            logger.info(
                f"Wrote {len(price_list.models)} models to cache at "
                f"{self.cache_path}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to write price cache: {e}")
            return False

    @property
    def active(self) -> PriceList:
        """Return the currently active price list."""
        if self._active is None:
            return self.load()
        return self._active

    @property
    def source(self) -> str:
        """Return the source tier of the active data ("cache" or "default")."""
        return self._source

    def age_days(self) -> int:
        """Calculate how many days old the active pricing data is.

        Returns:
            Number of days since the data was generated.
            Returns -1 if no data is loaded or the date cannot be parsed.
        """
        if self._active is None or not self._active.generated_date:
            return -1

        try:
            generated = date.fromisoformat(self._active.generated_date)
            return (date.today() - generated).days
        except (ValueError, TypeError):
            logger.warning(
                f"Cannot parse generated_date: {self._active.generated_date}"
            )
            return -1

    def is_stale(self) -> bool:
        """Check whether the active pricing data is stale.

        Data is considered stale if it is older than STALE_THRESHOLD_DAYS
        (default 7 days).

        Returns:
            True if data is stale or age cannot be determined.
        """
        age = self.age_days()
        if age < 0:
            return True
        return age > STALE_THRESHOLD_DAYS
```

### 3. `faith/tools/pricing/scraper.py`

```python
"""OpenRouter pricing scraper using Playwright.

Scrapes the OpenRouter model listing page to extract current pricing
data for all available models. Uses the Playwright browser automation
instance provided by FAITH-026 (Browser Automation MCP Server).

FRS Reference: Section 4.9.4
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

logger = logging.getLogger("faith.tools.pricing.scraper")

# The URL to scrape for model pricing.
OPENROUTER_MODELS_URL = "https://openrouter.ai/models"

# Timeout for the full scrape operation (seconds).
SCRAPE_TIMEOUT_SECONDS = 60


class ScrapeResult:
    """Result of an OpenRouter scrape attempt.

    Attributes:
        success: Whether the structured parse succeeded.
        price_list: Parsed PriceList if successful, else None.
        raw_content: Raw page content (available for PA fallback on failure).
        error: Error message if the scrape or parse failed.
    """

    def __init__(
        self,
        success: bool,
        price_list=None,
        raw_content: Optional[str] = None,
        error: Optional[str] = None,
    ):
        self.success = success
        self.price_list = price_list
        self.raw_content = raw_content
        self.error = error


async def scrape_openrouter(playwright_page) -> ScrapeResult:
    """Scrape OpenRouter's model listing page for pricing data.

    Uses a Playwright page instance (provided by the Browser Automation
    MCP Server, FAITH-026) to navigate to OpenRouter and extract
    model pricing.

    Args:
        playwright_page: An active Playwright page object from FAITH-026.

    Returns:
        ScrapeResult with parsed data or raw content for PA fallback.
    """
    from faith.tools.pricing.models import ModelPricing, PriceList

    try:
        # Navigate to the models page
        logger.info(f"Scraping {OPENROUTER_MODELS_URL}")
        await playwright_page.goto(
            OPENROUTER_MODELS_URL,
            wait_until="networkidle",
            timeout=SCRAPE_TIMEOUT_SECONDS * 1000,
        )

        # Wait for model data to render
        await playwright_page.wait_for_timeout(3000)

        # Attempt structured extraction via page evaluation.
        # This looks for JSON-LD, API responses in the page, or
        # structured DOM elements containing pricing.
        raw_content = await playwright_page.content()

        # Strategy 1: Try to intercept API response data embedded in page
        pricing_data = await _try_extract_from_page_data(playwright_page)
        if pricing_data:
            price_list = _parse_structured_data(pricing_data)
            if price_list and price_list.is_valid_for_cache():
                logger.info(
                    f"Structured scrape succeeded: "
                    f"{len(price_list.models)} models"
                )
                return ScrapeResult(
                    success=True,
                    price_list=price_list,
                )

        # Strategy 2: Try to extract from visible DOM elements
        dom_data = await _try_extract_from_dom(playwright_page)
        if dom_data:
            price_list = _parse_structured_data(dom_data)
            if price_list and price_list.is_valid_for_cache():
                logger.info(
                    f"DOM scrape succeeded: {len(price_list.models)} models"
                )
                return ScrapeResult(
                    success=True,
                    price_list=price_list,
                )

        # Both strategies failed — return raw content for PA fallback
        logger.warning(
            "Structured scrape failed — raw content available for PA fallback"
        )
        return ScrapeResult(
            success=False,
            raw_content=raw_content,
            error="scrape_parse_failed",
        )

    except Exception as e:
        logger.error(f"Scrape failed with exception: {e}", exc_info=True)
        return ScrapeResult(
            success=False,
            error=f"scrape_exception: {e}",
        )


async def _try_extract_from_page_data(page) -> Optional[list[dict]]:
    """Attempt to extract model data from Next.js/React hydration state
    or embedded JSON in the page.

    Returns a list of raw model dicts if found, else None.
    """
    try:
        result = await page.evaluate("""
            () => {
                // Try __NEXT_DATA__ (Next.js pages)
                if (window.__NEXT_DATA__?.props?.pageProps?.models) {
                    return window.__NEXT_DATA__.props.pageProps.models;
                }

                // Try any script tag containing model data
                const scripts = document.querySelectorAll(
                    'script[type="application/json"]'
                );
                for (const script of scripts) {
                    try {
                        const data = JSON.parse(script.textContent);
                        if (data?.models || data?.data?.models) {
                            return data.models || data.data.models;
                        }
                    } catch {}
                }

                return null;
            }
        """)
        return result
    except Exception as e:
        logger.debug(f"Page data extraction failed: {e}")
        return None


async def _try_extract_from_dom(page) -> Optional[list[dict]]:
    """Attempt to extract model data from visible DOM elements.

    Looks for structured model cards or table rows containing
    model names and pricing information.

    Returns a list of raw model dicts if found, else None.
    """
    try:
        result = await page.evaluate("""
            () => {
                // Look for model cards or list items with pricing info.
                // This is intentionally broad — specific selectors will
                // break when OpenRouter updates their layout.
                const models = [];

                // Common patterns: cards, table rows, list items
                const candidates = document.querySelectorAll(
                    '[data-model], [class*="model"], tr[data-id]'
                );

                for (const el of candidates) {
                    const text = el.textContent || '';
                    // Look for elements that contain a price pattern
                    // like "$0.003 / 1K tokens" or "0.003/1K"
                    const priceMatch = text.match(
                        /\\$?([\\d.]+)\\s*\\/\\s*(?:1[Kk]|1,?000)/
                    );
                    if (priceMatch) {
                        // Try to extract model name from the element
                        const nameEl = el.querySelector(
                            'h3, h4, [class*="name"], [class*="title"], a'
                        );
                        if (nameEl) {
                            models.push({
                                name: nameEl.textContent.trim(),
                                raw_text: text.substring(0, 500),
                            });
                        }
                    }
                }

                return models.length > 0 ? models : null;
            }
        """)
        return result
    except Exception as e:
        logger.debug(f"DOM extraction failed: {e}")
        return None


def _parse_structured_data(raw_models: list[dict]) -> Optional["PriceList"]:
    """Parse raw extracted model data into a validated PriceList.

    Args:
        raw_models: List of dicts with model pricing information.

    Returns:
        A PriceList if parsing succeeds, else None.
    """
    from faith.tools.pricing.models import ModelPricing, PriceList

    models = {}

    for raw in raw_models:
        try:
            # Handle various possible field names from different sources
            model_id = (
                raw.get("id")
                or raw.get("slug")
                or raw.get("name")
                or ""
            )
            if not model_id:
                continue

            # Normalise model ID (OpenRouter uses "provider/model" format)
            model_id = model_id.strip()

            # Extract pricing — field names vary by source
            pricing = raw.get("pricing") or raw.get("price") or raw
            input_cost = float(
                pricing.get("prompt")
                or pricing.get("input")
                or pricing.get("input_cost_per_token")
                or 0
            )
            output_cost = float(
                pricing.get("completion")
                or pricing.get("output")
                or pricing.get("output_cost_per_token")
                or 0
            )

            # Extract optional fields
            context_window = raw.get("context_length") or raw.get(
                "context_window"
            )

            models[model_id] = ModelPricing(
                input_cost_per_token=input_cost,
                output_cost_per_token=output_cost,
                context_window=int(context_window) if context_window else None,
            )

        except (ValueError, TypeError, KeyError) as e:
            logger.debug(f"Skipping model entry: {e}")
            continue

    if not models:
        return None

    return PriceList(
        generated_date=date.today().isoformat(),
        source="openrouter.ai/models",
        models=models,
    )
```

### 4. `faith/tools/pricing/fallback.py`

```python
"""PA-as-Fallback-Parser pattern for the Pricing MCP Tool.

When the structured scraper fails (page layout changed, unexpected
content), this module implements the named FAITH resilience pattern:

1. Tool publishes tool:error with reason: scrape_parse_failed
   and raw_content_available: true.
2. Raw page content is returned to the PA alongside the error.
3. PA uses its LLM to extract pricing data semantically.
4. PA calls write_prices(data) with the parsed result.
5. Tool validates and writes to cache.

If the PA's parse also fails, the tool falls back to the cached or
bundled price list and alerts the user that pricing data may be outdated.

FRS Reference: Section 4.9.5
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from faith.protocol.events import EventPublisher, EventType

logger = logging.getLogger("faith.tools.pricing.fallback")


async def request_pa_fallback_parse(
    event_publisher: EventPublisher,
    raw_content: str,
    error_reason: str,
) -> None:
    """Publish a tool:error event requesting PA fallback parsing.

    The PA subscribes to tool:error events. When it sees
    `raw_content_available: true`, it retrieves the raw content,
    uses its LLM to extract pricing data, and calls `write_prices`
    on the Pricing MCP Server to save the result.

    Args:
        event_publisher: The tool's event publisher instance.
        raw_content: The raw HTML content from the failed scrape.
        error_reason: A short description of why the scrape failed
            (e.g. "scrape_parse_failed").
    """
    # Truncate raw content if excessively large to stay within
    # reasonable event payload sizes. The PA only needs enough
    # content to semantically understand the page structure.
    max_content_length = 100_000  # ~100KB should be sufficient
    truncated = raw_content[:max_content_length]
    was_truncated = len(raw_content) > max_content_length

    from faith.protocol.events import FaithEvent

    event = FaithEvent(
        event=EventType.TOOL_ERROR,
        source="pricing-tool",
        data={
            "tool": "pricing",
            "reason": error_reason,
            "raw_content_available": True,
            "raw_content": truncated,
            "raw_content_truncated": was_truncated,
            "recovery_action": "pa_parse_and_write_prices",
            "instructions": (
                "The structured pricing scraper failed. The raw HTML content "
                "of the OpenRouter models page is attached. Please extract "
                "model pricing data (model ID, input cost per token, output "
                "cost per token) and call write_prices with the structured "
                "result. Use the model ID format 'provider/model-name'."
            ),
        },
    )
    await event_publisher.publish(event)
    logger.info(
        f"Published PA fallback parse request "
        f"(content length: {len(truncated)}, truncated: {was_truncated})"
    )


async def notify_stale_prices(
    event_publisher: EventPublisher,
    age_days: int,
    source: str,
) -> None:
    """Publish a warning event when pricing data is stale.

    The PA surfaces this warning in the Web UI so the user can
    trigger a manual refresh.

    Args:
        event_publisher: The tool's event publisher instance.
        age_days: How many days old the current pricing data is.
        source: Where the pricing data came from ("cache" or "default").
    """
    from faith.protocol.events import FaithEvent

    event = FaithEvent(
        event=EventType.TOOL_ERROR,
        source="pricing-tool",
        data={
            "tool": "pricing",
            "reason": "stale_prices",
            "severity": "warning",
            "age_days": age_days,
            "source": source,
            "message": (
                f"Pricing data is {age_days} days old (source: {source}). "
                f"Run refresh_prices to update, or the user can trigger "
                f"a manual refresh from the Web UI."
            ),
        },
    )
    await event_publisher.publish(event)
    logger.warning(f"Pricing data is stale: {age_days} days old (source: {source})")
```

### 5. `faith/tools/pricing/server.py`

```python
"""Pricing MCP Server — provides LLM pricing data to FAITH agents.

Exposes six MCP commands:
- get_price:      Look up pricing for a specific model.
- calculate_cost: Estimate the cost of an LLM call.
- list_models:    List all known models with pricing, optionally filtered.
- refresh_prices: Trigger a fresh scrape of OpenRouter pricing.
- price_age:      Report the age and source of the current pricing data.
- write_prices:   Validate and write structured pricing data to cache.

FRS Reference: Section 4.9
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from faith.tools.pricing.cache import PriceCache, STALE_THRESHOLD_DAYS
from faith.tools.pricing.fallback import (
    notify_stale_prices,
    request_pa_fallback_parse,
)
from faith.tools.pricing.models import CostEstimate, ModelPricing, PriceList
from faith.tools.pricing.scraper import scrape_openrouter
from faith.protocol.events import EventPublisher

logger = logging.getLogger("faith.tools.pricing.server")

# Default refresh interval (seconds). Configurable via system.yaml.
DEFAULT_REFRESH_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours


class PricingServer:
    """MCP server for LLM pricing data.

    Manages the pricing cache, handles scraping via Playwright,
    and serves MCP commands.

    Attributes:
        cache: The PriceCache instance managing the two-tier cache.
        event_publisher: For publishing tool events (errors, warnings).
        privacy_mode: The active privacy profile ("public", "internal",
            "confidential"). Scraping is disabled in "confidential" mode.
        refresh_interval: Seconds between automatic refresh cycles.
        _refresh_task: Background asyncio task for periodic refresh.
        _playwright_page_factory: Callable that returns a Playwright page
            from FAITH-026 Browser Automation.
    """

    def __init__(
        self,
        faith_root: Path,
        event_publisher: EventPublisher,
        privacy_mode: str = "public",
        refresh_interval: int = DEFAULT_REFRESH_INTERVAL_SECONDS,
        playwright_page_factory=None,
    ):
        """Initialise the Pricing MCP Server.

        Args:
            faith_root: Path to the FAITH project root.
            event_publisher: EventPublisher for system-events.
            privacy_mode: Active privacy profile from system.yaml.
            refresh_interval: Seconds between automatic refreshes.
            playwright_page_factory: Async callable that returns a
                Playwright page instance from FAITH-026. If None,
                scraping is disabled.
        """
        self.cache = PriceCache(faith_root)
        self.event_publisher = event_publisher
        self.privacy_mode = privacy_mode
        self.refresh_interval = refresh_interval
        self._playwright_page_factory = playwright_page_factory
        self._refresh_task: Optional[asyncio.Task] = None

    async def startup(self) -> None:
        """Start the pricing server.

        1. Load existing cache (live cache > bundled default).
        2. If not in Confidential mode, attempt initial scrape.
        3. Start periodic refresh background task.
        4. Check for stale data and warn if needed.
        """
        logger.info("Pricing MCP Server starting up")

        # Load existing data
        self.cache.load()

        # Attempt initial scrape (unless Confidential mode)
        if self.privacy_mode == "confidential":
            logger.info(
                "Confidential privacy mode — scraping disabled. "
                "Using bundled default pricing."
            )
        elif self._playwright_page_factory is not None:
            await self._do_scrape()
        else:
            logger.warning(
                "No Playwright page factory configured — "
                "scraping disabled. Using cached/bundled data."
            )

        # Start periodic refresh (unless Confidential)
        if self.privacy_mode != "confidential" and self._playwright_page_factory:
            self._refresh_task = asyncio.create_task(
                self._periodic_refresh_loop(),
                name="pricing-refresh",
            )
            logger.info(
                f"Periodic price refresh started "
                f"(interval: {self.refresh_interval}s)"
            )

        # Warn if data is stale
        if self.cache.is_stale():
            await notify_stale_prices(
                self.event_publisher,
                age_days=self.cache.age_days(),
                source=self.cache.source,
            )

    async def shutdown(self) -> None:
        """Stop the pricing server and cancel background tasks."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        logger.info("Pricing MCP Server shut down")

    # ──────────────────────────────────────────────────
    # MCP Commands
    # ──────────────────────────────────────────────────

    async def get_price(self, model_name: str) -> dict[str, Any]:
        """Look up pricing for a specific model.

        MCP Command: get_price
        Input: model_name (str)
        Output: Input/output cost per token, currency, data age.

        Args:
            model_name: The model identifier (e.g. "anthropic/claude-sonnet-4-6").

        Returns:
            Dict with pricing data, or error if model not found.
        """
        price_list = self.cache.active

        if model_name not in price_list.models:
            return {
                "error": f"Model '{model_name}' not found in pricing data",
                "available_models": len(price_list.models),
                "suggestion": "Use list_models to see available models",
            }

        model = price_list.models[model_name]
        return {
            "model": model_name,
            "input_cost_per_token": model.input_cost_per_token,
            "output_cost_per_token": model.output_cost_per_token,
            "privacy_tier": model.privacy_tier,
            "training_opt_out": model.training_opt_out,
            "context_window": model.context_window,
            "currency": "USD",
            "price_source": self.cache.source,
            "price_age_days": self.cache.age_days(),
        }

    async def calculate_cost(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
    ) -> dict[str, Any]:
        """Calculate the estimated cost of an LLM call.

        MCP Command: calculate_cost
        Input: model_name, input_tokens, output_tokens
        Output: Estimated cost breakdown.

        Args:
            model_name: The model identifier.
            input_tokens: Number of input (prompt) tokens.
            output_tokens: Number of output (completion) tokens.

        Returns:
            Dict with cost breakdown, or error if model not found.
        """
        price_list = self.cache.active

        if model_name not in price_list.models:
            return {
                "error": f"Model '{model_name}' not found in pricing data",
            }

        model = price_list.models[model_name]
        input_cost = model.input_cost_per_token * input_tokens
        output_cost = model.output_cost_per_token * output_tokens

        estimate = CostEstimate(
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=input_cost + output_cost,
            price_source=self.cache.source,
            price_age_days=self.cache.age_days(),
        )
        return estimate.model_dump()

    async def list_models(
        self, privacy_tier: Optional[str] = None
    ) -> dict[str, Any]:
        """List all known models with pricing, optionally filtered by tier.

        MCP Command: list_models
        Input: privacy_tier (optional — "public", "internal", "confidential")
        Output: All models matching the tier with pricing.

        Args:
            privacy_tier: If provided, only return models matching this tier.

        Returns:
            Dict with model list and metadata.
        """
        price_list = self.cache.active
        models = price_list.models

        if privacy_tier:
            models = {
                k: v for k, v in models.items()
                if v.privacy_tier == privacy_tier
            }

        return {
            "models": {
                k: v.model_dump() for k, v in models.items()
            },
            "count": len(models),
            "total_available": len(price_list.models),
            "filter": privacy_tier,
            "price_source": self.cache.source,
            "price_age_days": self.cache.age_days(),
        }

    async def refresh_prices(self) -> dict[str, Any]:
        """Trigger a fresh scrape of OpenRouter pricing.

        MCP Command: refresh_prices
        Input: None
        Output: Success/failure and timestamp.

        Returns:
            Dict with result status and details.
        """
        if self.privacy_mode == "confidential":
            return {
                "success": False,
                "error": "Scraping disabled in Confidential privacy mode",
            }

        if self._playwright_page_factory is None:
            return {
                "success": False,
                "error": "No Playwright page factory configured",
            }

        result = await self._do_scrape()
        return result

    async def price_age(self) -> dict[str, Any]:
        """Report the age and source of the current pricing data.

        MCP Command: price_age
        Input: None
        Output: Timestamp of last successful scrape, source, staleness.

        Returns:
            Dict with age information.
        """
        return {
            "generated_date": self.cache.active.generated_date,
            "age_days": self.cache.age_days(),
            "source": self.cache.source,
            "is_stale": self.cache.is_stale(),
            "stale_threshold_days": STALE_THRESHOLD_DAYS,
            "model_count": len(self.cache.active.models),
        }

    async def write_prices(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate and write structured pricing data to the local cache.

        MCP Command: write_prices
        Input: data (structured pricing JSON matching PriceList schema)
        Output: Validation result and write status.

        This is the write endpoint used by the PA-as-fallback-parser
        pattern (FRS 4.9.5). The PA calls this after semantically
        parsing raw page content with its LLM.

        Args:
            data: Dict matching the PriceList schema.

        Returns:
            Dict with success status and details.
        """
        try:
            price_list = PriceList.model_validate(data)
        except Exception as e:
            return {
                "success": False,
                "error": f"Validation failed: {e}",
            }

        if self.cache.write(price_list):
            return {
                "success": True,
                "models_written": len(price_list.models),
                "generated_date": price_list.generated_date,
                "source": "pa_fallback",
            }
        else:
            return {
                "success": False,
                "error": (
                    f"Cache write rejected — data has "
                    f"{len(price_list.models)} models "
                    f"(minimum required for cache write)"
                ),
            }

    # ──────────────────────────────────────────────────
    # Internal Methods
    # ──────────────────────────────────────────────────

    async def _do_scrape(self) -> dict[str, Any]:
        """Execute a scrape cycle: fetch, parse, cache, or fallback.

        Returns:
            Dict with the result of the scrape attempt.
        """
        try:
            page = await self._playwright_page_factory()
            result = await scrape_openrouter(page)
        except Exception as e:
            logger.error(f"Failed to create Playwright page: {e}")
            return {
                "success": False,
                "error": f"Playwright unavailable: {e}",
                "timestamp": datetime.now().isoformat(),
            }

        if result.success and result.price_list:
            # Structured parse succeeded — write to cache
            if self.cache.write(result.price_list):
                return {
                    "success": True,
                    "models_scraped": len(result.price_list.models),
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                return {
                    "success": False,
                    "error": "Scrape succeeded but cache validation failed",
                    "timestamp": datetime.now().isoformat(),
                }
        else:
            # Structured parse failed — invoke PA fallback if we have content
            if result.raw_content:
                await request_pa_fallback_parse(
                    self.event_publisher,
                    raw_content=result.raw_content,
                    error_reason=result.error or "scrape_parse_failed",
                )
                return {
                    "success": False,
                    "error": result.error,
                    "pa_fallback_requested": True,
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                return {
                    "success": False,
                    "error": result.error or "Scrape failed with no content",
                    "pa_fallback_requested": False,
                    "timestamp": datetime.now().isoformat(),
                }

    async def _periodic_refresh_loop(self) -> None:
        """Background loop that refreshes pricing data on a schedule."""
        try:
            while True:
                await asyncio.sleep(self.refresh_interval)
                logger.info("Periodic price refresh triggered")
                result = await self._do_scrape()
                if result.get("success"):
                    logger.info(
                        f"Periodic refresh succeeded: "
                        f"{result.get('models_scraped', 0)} models"
                    )
                else:
                    logger.warning(
                        f"Periodic refresh failed: {result.get('error')}"
                    )

                # Check staleness after refresh attempt
                if self.cache.is_stale():
                    await notify_stale_prices(
                        self.event_publisher,
                        age_days=self.cache.age_days(),
                        source=self.cache.source,
                    )
        except asyncio.CancelledError:
            logger.debug("Periodic refresh loop cancelled")
```

### 6. `faith/tools/pricing/__init__.py`

```python
"""FAITH Pricing MCP Tool — LLM pricing data from OpenRouter.

Provides accurate, up-to-date pricing for real-time cost tracking,
session cost summaries, and proactive cost warnings.

FRS Reference: Section 4.9
"""

from faith.tools.pricing.server import PricingServer

__all__ = ["PricingServer"]
```

### 7. `tests/test_pricing_server.py`

```python
"""Tests for the FAITH Pricing MCP Server.

Covers cache management, validation, MCP commands, scrape cycle,
privacy mode enforcement, PA fallback pattern, and staleness detection.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.tools.pricing.cache import PriceCache, STALE_THRESHOLD_DAYS
from faith.tools.pricing.models import (
    CostEstimate,
    MIN_MODEL_COUNT,
    ModelPricing,
    PriceList,
)
from faith.tools.pricing.server import PricingServer


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


def _make_models(count: int = 15) -> dict[str, ModelPricing]:
    """Generate a dict of N fake model pricing entries."""
    models = {}
    for i in range(count):
        models[f"provider-{i}/model-{i}"] = ModelPricing(
            input_cost_per_token=0.000001 * (i + 1),
            output_cost_per_token=0.000003 * (i + 1),
            privacy_tier="public",
            training_opt_out=True,
            context_window=128_000,
        )
    return models


def _make_price_list(
    count: int = 15,
    generated_date: str | None = None,
) -> PriceList:
    """Create a valid PriceList with N models."""
    return PriceList(
        generated_date=generated_date or date.today().isoformat(),
        source="test",
        models=_make_models(count),
    )


def _write_price_file(path: Path, price_list: PriceList) -> None:
    """Write a PriceList to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(price_list.model_dump_json(indent=2), encoding="utf-8")


@pytest.fixture
def tmp_faith_root(tmp_path):
    """Create a temporary FAITH root with data directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return tmp_path


@pytest.fixture
def mock_event_publisher():
    """Create a mock EventPublisher."""
    publisher = AsyncMock()
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def price_cache(tmp_faith_root):
    """Create a PriceCache pointed at the tmp directory."""
    return PriceCache(tmp_faith_root)


@pytest.fixture
def server(tmp_faith_root, mock_event_publisher):
    """Create a PricingServer with defaults (public mode, no Playwright)."""
    return PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
        privacy_mode="public",
    )


# ──────────────────────────────────────────────────
# Model validation tests
# ──────────────────────────────────────────────────


def test_model_pricing_accepts_valid_data():
    """Valid pricing data is accepted."""
    mp = ModelPricing(
        input_cost_per_token=0.000003,
        output_cost_per_token=0.000015,
        privacy_tier="internal",
        training_opt_out=True,
    )
    assert mp.input_cost_per_token == 0.000003


def test_model_pricing_rejects_negative_cost():
    """Negative costs are rejected."""
    with pytest.raises(Exception):
        ModelPricing(
            input_cost_per_token=-0.001,
            output_cost_per_token=0.000015,
        )


def test_model_pricing_rejects_excessive_cost():
    """Unrealistically high costs are rejected."""
    with pytest.raises(Exception):
        ModelPricing(
            input_cost_per_token=0.01,  # $10/1K tokens — unrealistic
            output_cost_per_token=0.000015,
        )


def test_model_pricing_rejects_invalid_privacy_tier():
    """Invalid privacy tier values are rejected."""
    with pytest.raises(Exception):
        ModelPricing(
            input_cost_per_token=0.000003,
            output_cost_per_token=0.000015,
            privacy_tier="invalid_tier",
        )


def test_model_pricing_accepts_free_model():
    """Free models (cost = 0) are accepted."""
    mp = ModelPricing(
        input_cost_per_token=0.0,
        output_cost_per_token=0.0,
    )
    assert mp.input_cost_per_token == 0.0


def test_price_list_valid_for_cache():
    """Price list with enough models passes cache validation."""
    pl = _make_price_list(count=15)
    assert pl.is_valid_for_cache() is True


def test_price_list_too_few_models_not_valid_for_cache():
    """Price list with too few models fails cache validation."""
    pl = _make_price_list(count=3)
    assert pl.is_valid_for_cache() is False


# ──────────────────────────────────────────────────
# Cache tests
# ──────────────────────────────────────────────────


def test_cache_load_prefers_live_cache(tmp_faith_root):
    """Live cache takes priority over bundled default."""
    cache = PriceCache(tmp_faith_root)

    # Write both files with different model counts
    live = _make_price_list(count=20)
    bundled = _make_price_list(count=15)
    _write_price_file(cache.cache_path, live)
    _write_price_file(cache.bundled_path, bundled)

    result = cache.load()
    assert len(result.models) == 20
    assert cache.source == "cache"


def test_cache_falls_back_to_bundled(tmp_faith_root):
    """Falls back to bundled default when no live cache exists."""
    cache = PriceCache(tmp_faith_root)

    bundled = _make_price_list(count=15)
    _write_price_file(cache.bundled_path, bundled)

    result = cache.load()
    assert len(result.models) == 15
    assert cache.source == "default"


def test_cache_returns_empty_when_nothing_available(tmp_faith_root):
    """Returns empty PriceList when neither file exists."""
    cache = PriceCache(tmp_faith_root)
    result = cache.load()
    assert len(result.models) == 0
    assert cache.source == "none"


def test_cache_write_validates_minimum_count(tmp_faith_root):
    """Cache write is rejected if model count is below minimum."""
    cache = PriceCache(tmp_faith_root)
    small = _make_price_list(count=3)
    assert cache.write(small) is False
    assert not cache.cache_path.exists()


def test_cache_write_succeeds_with_valid_data(tmp_faith_root):
    """Cache write succeeds with sufficient models."""
    cache = PriceCache(tmp_faith_root)
    valid = _make_price_list(count=20)
    assert cache.write(valid) is True
    assert cache.cache_path.exists()
    assert cache.source == "cache"


def test_cache_age_days_today():
    """Data generated today has age 0."""
    pl = _make_price_list(generated_date=date.today().isoformat())
    cache = MagicMock()
    cache._active = pl

    # Test via direct calculation
    generated = date.fromisoformat(pl.generated_date)
    assert (date.today() - generated).days == 0


def test_cache_staleness_detection(tmp_faith_root):
    """Data older than STALE_THRESHOLD_DAYS is detected as stale."""
    cache = PriceCache(tmp_faith_root)

    old_date = (date.today() - timedelta(days=STALE_THRESHOLD_DAYS + 1)).isoformat()
    old_list = _make_price_list(count=15, generated_date=old_date)
    _write_price_file(cache.bundled_path, old_list)

    cache.load()
    assert cache.is_stale() is True


# ──────────────────────────────────────────────────
# MCP Command tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_price_existing_model(tmp_faith_root, mock_event_publisher):
    """get_price returns pricing for a known model."""
    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
    )
    pl = _make_price_list(count=15)
    server.cache._active = pl
    server.cache._source = "cache"

    model_name = list(pl.models.keys())[0]
    result = await server.get_price(model_name)

    assert "error" not in result
    assert result["model"] == model_name
    assert "input_cost_per_token" in result
    assert result["currency"] == "USD"


@pytest.mark.asyncio
async def test_get_price_unknown_model(tmp_faith_root, mock_event_publisher):
    """get_price returns error for an unknown model."""
    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
    )
    server.cache._active = _make_price_list(count=15)
    server.cache._source = "cache"

    result = await server.get_price("nonexistent/model")
    assert "error" in result


@pytest.mark.asyncio
async def test_calculate_cost(tmp_faith_root, mock_event_publisher):
    """calculate_cost returns correct cost breakdown."""
    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
    )

    # Create a model with known prices
    models = {
        "test/model": ModelPricing(
            input_cost_per_token=0.000003,
            output_cost_per_token=0.000015,
        )
    }
    server.cache._active = PriceList(models=models)
    server.cache._source = "cache"

    result = await server.calculate_cost("test/model", 1000, 500)

    assert result["input_cost"] == pytest.approx(0.003)
    assert result["output_cost"] == pytest.approx(0.0075)
    assert result["total_cost"] == pytest.approx(0.0105)


@pytest.mark.asyncio
async def test_list_models_no_filter(tmp_faith_root, mock_event_publisher):
    """list_models returns all models when no filter is applied."""
    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
    )
    server.cache._active = _make_price_list(count=15)
    server.cache._source = "cache"

    result = await server.list_models()
    assert result["count"] == 15
    assert result["filter"] is None


@pytest.mark.asyncio
async def test_list_models_with_privacy_filter(
    tmp_faith_root, mock_event_publisher
):
    """list_models filters by privacy_tier when specified."""
    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
    )

    models = {
        "pub/model": ModelPricing(
            input_cost_per_token=0.000001,
            output_cost_per_token=0.000003,
            privacy_tier="public",
        ),
        "int/model": ModelPricing(
            input_cost_per_token=0.000002,
            output_cost_per_token=0.000006,
            privacy_tier="internal",
        ),
    }
    server.cache._active = PriceList(models=models)
    server.cache._source = "cache"

    result = await server.list_models(privacy_tier="internal")
    assert result["count"] == 1
    assert "int/model" in result["models"]


@pytest.mark.asyncio
async def test_refresh_prices_blocked_in_confidential_mode(
    tmp_faith_root, mock_event_publisher
):
    """refresh_prices refuses to scrape in Confidential privacy mode."""
    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
        privacy_mode="confidential",
    )
    result = await server.refresh_prices()
    assert result["success"] is False
    assert "Confidential" in result["error"]


@pytest.mark.asyncio
async def test_price_age_returns_metadata(
    tmp_faith_root, mock_event_publisher
):
    """price_age returns age, source, and staleness information."""
    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
    )
    server.cache._active = _make_price_list(count=15)
    server.cache._source = "default"

    result = await server.price_age()
    assert "age_days" in result
    assert result["source"] == "default"
    assert "is_stale" in result


@pytest.mark.asyncio
async def test_write_prices_valid_data(
    tmp_faith_root, mock_event_publisher
):
    """write_prices accepts and caches valid structured data."""
    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
    )
    server.cache = PriceCache(tmp_faith_root)

    pl = _make_price_list(count=20)
    result = await server.write_prices(pl.model_dump())

    assert result["success"] is True
    assert result["models_written"] == 20


@pytest.mark.asyncio
async def test_write_prices_rejects_invalid_data(
    tmp_faith_root, mock_event_publisher
):
    """write_prices rejects data that fails validation."""
    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
    )

    result = await server.write_prices({"models": "not_a_dict"})
    assert result["success"] is False
    assert "Validation failed" in result["error"]


# ──────────────────────────────────────────────────
# Startup and privacy mode tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_startup_confidential_mode_skips_scraping(
    tmp_faith_root, mock_event_publisher
):
    """In Confidential mode, startup loads bundled default only."""
    bundled = _make_price_list(count=15)
    bundled_path = tmp_faith_root / "data" / "model-prices.default.json"
    _write_price_file(bundled_path, bundled)

    mock_factory = AsyncMock()

    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
        privacy_mode="confidential",
        playwright_page_factory=mock_factory,
    )
    await server.startup()

    # Playwright factory should never be called
    mock_factory.assert_not_called()
    assert server.cache.source == "default"
    assert server._refresh_task is None

    await server.shutdown()


@pytest.mark.asyncio
async def test_startup_public_mode_attempts_scrape(
    tmp_faith_root, mock_event_publisher
):
    """In public mode, startup attempts to scrape OpenRouter."""
    bundled = _make_price_list(count=15)
    _write_price_file(
        tmp_faith_root / "data" / "model-prices.default.json", bundled
    )

    # Mock the Playwright factory and scraper
    mock_page = AsyncMock()
    mock_factory = AsyncMock(return_value=mock_page)

    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
        privacy_mode="public",
        playwright_page_factory=mock_factory,
    )

    with patch(
        "faith.tools.pricing.server.scrape_openrouter"
    ) as mock_scrape:
        scraped = _make_price_list(count=25)
        from faith.tools.pricing.scraper import ScrapeResult

        mock_scrape.return_value = ScrapeResult(
            success=True, price_list=scraped
        )
        await server.startup()

        mock_factory.assert_called_once()
        mock_scrape.assert_called_once_with(mock_page)

    await server.shutdown()


# ──────────────────────────────────────────────────
# PA fallback pattern tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scrape_failure_triggers_pa_fallback(
    tmp_faith_root, mock_event_publisher
):
    """When structured scrape fails, PA fallback is requested."""
    mock_page = AsyncMock()
    mock_factory = AsyncMock(return_value=mock_page)

    server = PricingServer(
        faith_root=tmp_faith_root,
        event_publisher=mock_event_publisher,
        privacy_mode="public",
        playwright_page_factory=mock_factory,
    )
    server.cache._active = PriceList()
    server.cache._source = "none"

    with patch(
        "faith.tools.pricing.server.scrape_openrouter"
    ) as mock_scrape:
        from faith.tools.pricing.scraper import ScrapeResult

        mock_scrape.return_value = ScrapeResult(
            success=False,
            raw_content="<html>...page content...</html>",
            error="scrape_parse_failed",
        )
        result = await server.refresh_prices()

    assert result["success"] is False
    assert result["pa_fallback_requested"] is True

    # Verify the fallback event was published
    mock_event_publisher.publish.assert_called()
```

---

## Configuration

### `system.yaml` excerpt (pricing-related fields)

```yaml
# Pricing tool configuration
pricing:
  refresh_interval_hours: 24      # How often to re-scrape (default 24h)
  stale_warning_days: 7           # Warn user after this many days without refresh
```

The server reads `pricing.refresh_interval_hours` from the project's `.faith/system.yaml` at startup and converts to seconds for the periodic refresh loop. The `stale_warning_days` threshold controls when the PA surfaces a staleness warning in the Web UI.

---

## Data Files

### `data/model-prices.default.json`

This file is committed to the FAITH git repository and updated with each release. Schema:

```json
{
  "generated_date": "2026-03-23",
  "source": "openrouter.ai/models",
  "models": {
    "anthropic/claude-sonnet-4-6": {
      "input_cost_per_token": 0.000003,
      "output_cost_per_token": 0.000015,
      "privacy_tier": "internal",
      "training_opt_out": true,
      "context_window": 200000
    }
  }
}
```

### `data/model-prices.cache.json`

This file is gitignored. Same schema as above but generated by the scraper at runtime. Written atomically (write to `.tmp`, then rename) to avoid corruption on crash.

---

## Key Design Decisions

1. **Validation before overwrite.** The cache write method enforces minimum model count and realistic price ranges. A broken scrape cannot silently overwrite valid data.

2. **Atomic writes.** Cache files are written to a `.tmp` file first, then renamed. This prevents partial writes from corrupting the cache if the process crashes mid-write.

3. **Two-tier cache priority.** Live scraped data always takes precedence over the bundled default. The bundled default ensures FAITH works offline and on first run without any network access.

4. **Confidential mode isolation.** When the privacy profile is "confidential", zero outbound connections are made. The Playwright scraper is never invoked, and the periodic refresh task is never started. Only the bundled default is used.

5. **PA-as-fallback-parser.** This is a named FAITH resilience pattern. Rather than maintaining brittle CSS selectors that break when OpenRouter updates their page, the tool delegates semantic parsing to the PA's LLM when the structured scraper fails. The PA understands page meaning, extracts pricing, and calls `write_prices` to save the result.

6. **Staleness warnings.** When data is older than 7 days, the server proactively publishes a warning event. The PA surfaces this in the Web UI. The user can trigger a manual refresh.

---

## Acceptance Criteria

- [ ] `get_price` returns correct pricing for any model in the active cache, including cost per token, currency, privacy tier, and data age.
- [ ] `calculate_cost` returns accurate cost breakdowns (input cost, output cost, total) matching the formula: `cost = tokens * cost_per_token`.
- [ ] `list_models` returns all models when called without a filter, and correctly filters by `privacy_tier` when one is provided.
- [ ] `refresh_prices` triggers a Playwright scrape, validates the result, and writes to cache on success.
- [ ] `refresh_prices` returns an error (not an exception) when called in Confidential privacy mode.
- [ ] `price_age` returns the generated date, age in days, source tier, and staleness status.
- [ ] `write_prices` validates incoming data against the PriceList schema and writes to cache only if validation passes (minimum model count, realistic price ranges).
- [ ] On startup, the server loads cached data (tier 1) or bundled default (tier 2) and attempts an initial scrape unless in Confidential mode.
- [ ] Periodic refresh runs every 24 hours (configurable) and does not run in Confidential mode.
- [ ] When the structured scraper fails but raw HTML is available, a `tool:error` event is published with `raw_content_available: true` for PA fallback parsing.
- [ ] When the PA's fallback parse also fails, the server falls back to the cached or bundled price list and publishes a staleness warning.
- [ ] Cache writes are atomic (write to `.tmp`, rename) to prevent corruption.
- [ ] Cache writes are rejected if the new data contains fewer than `MIN_MODEL_COUNT` models.
- [ ] ModelPricing validation rejects negative costs, unrealistically high costs, and invalid privacy tier values.
- [ ] All tests in `tests/test_pricing_server.py` pass.
- [ ] No outbound network connections are made when the privacy profile is "confidential".
