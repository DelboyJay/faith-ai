# FAITH-034 — CAG Implementation

**Phase:** 7 — CAG & External MCP
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-010, FAITH-022
**FRS Reference:** Section 4.10

---

## Objective

Implement the full Cache-Augmented Generation (CAG) subsystem. CAG is a `faith_pa` runtime/context-assembly feature, not an MCP server. It pre-loads small-to-medium static reference documents (coding standards, API specs, schema definitions) into an agent's assembled context at session start, inserted between the Context Summary and Recent Messages. This eliminates repeated retrieval costs for documents agents reference on nearly every LLM call. The implementation covers: a dedicated CAG manager module, PA-side session-start validation, provider-specific prompt caching hints where available, and live reload when a `file:changed` event is detected for a CAG document.

FAITH-010 already scaffolded basic CAG loading in `BaseAgent._load_cag_documents()` and `ContextAssembler.format_cag_documents()`. This task replaces those stubs with a production-grade implementation that enforces token budgets, tracks document metadata, integrates with the event system for freshness, and enables provider prompt caching.

---

## Architecture

```
faith/agent/
├── cag.py               ← CAGManager class (this task)
├── context.py           ← Updated: delegate CAG formatting to CAGManager
└── base.py              ← Updated: wire CAGManager, handle file:changed reload

faith/pa/
└── validation.py        ← Updated: add CAG validation at session start

faith/llm/
└── caching.py           ← Provider prompt caching utilities (this task)

tests/
└── test_cag.py          ← Unit tests for CAG subsystem (this task)
```

---

## Files to Create

### 1. `faith/agent/cag.py`

```python
"""CAG (Cache-Augmented Generation) document manager.

Loads static reference documents into agent context at session start.
Documents are declared in the agent's config.yaml under `cag_documents`
with a total token budget of `cag_max_tokens` (default: 8000).

Handles:
- Loading and caching document contents with metadata
- Token budget enforcement
- Live reload on file:changed events
- Formatting for context assembly with optional cache_control hints

FRS Reference: Section 4.10
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from faith.utils.tokens import estimate_tokens

logger = logging.getLogger("faith.agent.cag")

DEFAULT_CAG_MAX_TOKENS = 8000


@dataclass
class CAGDocument:
    """A single pre-loaded CAG reference document.

    Attributes:
        path: Absolute path to the source file.
        relative_path: Path as declared in config.yaml (for display).
        content: The full text content of the document.
        token_count: Estimated token count (model-specific).
        sha256: SHA-256 hash of the content (for change detection).
        loaded: Whether the document was successfully loaded.
        error: Error message if loading failed.
    """

    path: Path
    relative_path: str
    content: str = ""
    token_count: int = 0
    sha256: str = ""
    loaded: bool = False
    error: str = ""


class CAGManager:
    """Manages pre-loaded CAG documents for a single agent.

    Lifecycle:
    1. Constructed with the agent's config (cag_documents + cag_max_tokens).
    2. `load_all()` reads all documents and validates the token budget.
    3. `format_for_context()` returns the formatted block for context assembly.
    4. `reload_document(path)` reloads a single document after file:changed.

    Attributes:
        faith_dir: Path to the project's .faith directory.
        model: LLM model name (for token estimation).
        max_tokens: Total CAG token budget for this agent.
        documents: Ordered list of loaded CAG documents.
    """

    def __init__(
        self,
        faith_dir: Path,
        model: str,
        cag_document_paths: list[str],
        cag_max_tokens: int = DEFAULT_CAG_MAX_TOKENS,
    ):
        """Initialise the CAG manager.

        Args:
            faith_dir: Path to the .faith directory.
            model: LLM model name (for token counting).
            cag_document_paths: List of document paths from config.yaml.
                Relative paths are resolved against faith_dir.
            cag_max_tokens: Maximum total tokens for all CAG documents.
        """
        self.faith_dir = faith_dir
        self.model = model
        self.max_tokens = cag_max_tokens
        self._raw_paths = cag_document_paths
        self.documents: list[CAGDocument] = []

    def _resolve_path(self, path_str: str) -> Path:
        """Resolve a document path from config.yaml to an absolute path.

        Relative paths are resolved against the .faith directory.
        Absolute paths are used as-is.

        Args:
            path_str: The path string from config.yaml.

        Returns:
            Resolved absolute Path.
        """
        path = Path(path_str)
        if not path.is_absolute():
            path = self.faith_dir / path_str
        return path.resolve()

    @staticmethod
    def _hash_content(content: str) -> str:
        """Compute SHA-256 hash of document content.

        Args:
            content: Document text.

        Returns:
            Hex-encoded SHA-256 digest.
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _load_single(self, path_str: str) -> CAGDocument:
        """Load a single CAG document from disk.

        Args:
            path_str: The path string from config.yaml.

        Returns:
            Populated CAGDocument (check .loaded for success).
        """
        abs_path = self._resolve_path(path_str)
        doc = CAGDocument(path=abs_path, relative_path=path_str)

        try:
            content = abs_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            doc.error = f"File not found: {abs_path}"
            logger.warning(f"CAG document not found: {abs_path}")
            return doc
        except PermissionError:
            doc.error = f"Permission denied: {abs_path}"
            logger.warning(f"CAG document permission denied: {abs_path}")
            return doc
        except Exception as e:
            doc.error = f"Read error: {e}"
            logger.warning(f"CAG document read error for {abs_path}: {e}")
            return doc

        doc.content = content
        doc.token_count = estimate_tokens(content, self.model)
        doc.sha256 = self._hash_content(content)
        doc.loaded = True

        logger.debug(
            f"Loaded CAG document: {path_str} "
            f"({doc.token_count} tokens, sha256={doc.sha256[:12]}...)"
        )
        return doc

    def load_all(self) -> CAGValidationResult:
        """Load all configured CAG documents and validate the token budget.

        Returns:
            CAGValidationResult with success status, total tokens,
            and any errors or warnings.
        """
        self.documents = []
        errors: list[str] = []
        warnings: list[str] = []

        for path_str in self._raw_paths:
            doc = self._load_single(path_str)
            self.documents.append(doc)
            if not doc.loaded:
                errors.append(doc.error)

        total_tokens = sum(d.token_count for d in self.documents if d.loaded)

        if total_tokens > self.max_tokens:
            # Sort by size descending to suggest which to move to RAG
            oversized = sorted(
                [d for d in self.documents if d.loaded],
                key=lambda d: d.token_count,
                reverse=True,
            )
            largest = oversized[0] if oversized else None
            warnings.append(
                f"CAG token budget exceeded: {total_tokens} tokens used "
                f"of {self.max_tokens} allowed. "
                f"Consider moving '{largest.relative_path}' "
                f"({largest.token_count} tokens) to RAG instead."
                if largest
                else f"CAG token budget exceeded: {total_tokens} / {self.max_tokens}."
            )

        success = len(errors) == 0 and total_tokens <= self.max_tokens

        logger.info(
            f"CAG load complete: {len(self.documents)} documents, "
            f"{total_tokens}/{self.max_tokens} tokens, "
            f"{'OK' if success else 'ISSUES FOUND'}"
        )

        return CAGValidationResult(
            success=success,
            total_tokens=total_tokens,
            max_tokens=self.max_tokens,
            document_count=len(self.documents),
            loaded_count=sum(1 for d in self.documents if d.loaded),
            errors=errors,
            warnings=warnings,
        )

    def reload_document(self, changed_path: str | Path) -> Optional[CAGDocument]:
        """Reload a single CAG document after a file:changed event.

        Finds the document matching the changed path, reloads it from
        disk, and updates the stored content and metadata.

        Args:
            changed_path: The path of the changed file (absolute or relative).

        Returns:
            The updated CAGDocument if found and reloaded, None if the
            changed path does not match any configured CAG document.
        """
        changed_resolved = Path(changed_path).resolve()

        for i, doc in enumerate(self.documents):
            if doc.path.resolve() == changed_resolved:
                old_hash = doc.sha256
                new_doc = self._load_single(doc.relative_path)
                self.documents[i] = new_doc

                if new_doc.loaded and new_doc.sha256 != old_hash:
                    logger.info(
                        f"CAG document reloaded: {doc.relative_path} "
                        f"(sha256 changed: {old_hash[:12]}... -> "
                        f"{new_doc.sha256[:12]}...)"
                    )
                elif new_doc.loaded:
                    logger.debug(
                        f"CAG document reloaded (unchanged): {doc.relative_path}"
                    )

                return new_doc

        return None

    def is_cag_path(self, path: str | Path) -> bool:
        """Check whether a file path matches a configured CAG document.

        Used to filter file:changed events — only CAG-relevant events
        trigger a reload.

        Args:
            path: The file path to check.

        Returns:
            True if the path matches a configured CAG document.
        """
        resolved = Path(path).resolve()
        return any(doc.path.resolve() == resolved for doc in self.documents)

    def get_absolute_paths(self) -> list[Path]:
        """Return the resolved absolute paths of all configured CAG documents.

        Used by the PA to register file watches at session start.

        Returns:
            List of absolute Path objects.
        """
        return [doc.path for doc in self.documents]

    @property
    def total_tokens(self) -> int:
        """Total token count across all loaded CAG documents."""
        return sum(d.token_count for d in self.documents if d.loaded)

    @property
    def loaded_contents(self) -> list[str]:
        """Return content strings of all successfully loaded documents.

        Convenience property for passing to ContextAssembler.
        """
        return [d.content for d in self.documents if d.loaded]

    def format_for_context(self) -> str:
        """Format all loaded CAG documents for inclusion in LLM context.

        Each document is wrapped with a header showing the source path
        for agent awareness. Documents that failed to load are skipped.

        Returns:
            Formatted string with all CAG documents, or empty string
            if none are loaded.
        """
        if not any(d.loaded for d in self.documents):
            return ""

        sections = []
        for doc in self.documents:
            if not doc.loaded:
                continue
            sections.append(
                f"--- CAG Reference: {doc.relative_path} ---\n{doc.content}"
            )

        return "\n\n".join(sections)


@dataclass
class CAGValidationResult:
    """Result of CAG document loading and validation.

    Returned by CAGManager.load_all() and used by the PA to report
    issues to the user at session start.

    Attributes:
        success: True if all documents loaded within budget.
        total_tokens: Combined token count of loaded documents.
        max_tokens: The configured cag_max_tokens budget.
        document_count: Total number of configured documents.
        loaded_count: Number successfully loaded.
        errors: List of error messages (missing files, read failures).
        warnings: List of warning messages (budget exceeded).
    """

    success: bool
    total_tokens: int
    max_tokens: int
    document_count: int
    loaded_count: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary for PA to relay to the user.

        Returns:
            Multi-line summary string.
        """
        lines = [
            f"CAG: {self.loaded_count}/{self.document_count} documents loaded, "
            f"{self.total_tokens}/{self.max_tokens} tokens used."
        ]
        for err in self.errors:
            lines.append(f"  ERROR: {err}")
        for warn in self.warnings:
            lines.append(f"  WARNING: {warn}")
        return "\n".join(lines)
```

### 2. `faith/llm/caching.py`

```python
"""Provider-specific prompt caching utilities for CAG content.

Different LLM providers handle prompt caching differently:
- Claude (Anthropic): Explicit `cache_control` block on message content.
- OpenAI: Automatic prompt prefix caching (no client-side action needed).
- Ollama (local): KV cache reuse when prompt prefix is stable.

This module provides utilities to annotate context messages with the
appropriate caching hints based on the detected provider.

FRS Reference: Section 4.10.2
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger("faith.llm.caching")


class LLMProvider(Enum):
    """Known LLM provider types."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OLLAMA = "ollama"
    UNKNOWN = "unknown"


def detect_provider(model: str, api_base: str = "") -> LLMProvider:
    """Detect the LLM provider from the model name or API base URL.

    Args:
        model: The model identifier (e.g. "claude-3.5-sonnet", "gpt-4o").
        api_base: The API base URL if known.

    Returns:
        Detected LLMProvider enum value.
    """
    model_lower = model.lower()
    api_lower = api_base.lower()

    if "claude" in model_lower or "anthropic" in api_lower:
        return LLMProvider.ANTHROPIC
    if "gpt" in model_lower or "o1" in model_lower or "openai" in api_lower:
        return LLMProvider.OPENAI
    if "localhost" in api_lower or "11434" in api_lower or "ollama" in api_lower:
        return LLMProvider.OLLAMA

    # OpenRouter models may include provider prefixes
    if "/" in model_lower:
        prefix = model_lower.split("/")[0]
        if prefix in ("anthropic", "claude"):
            return LLMProvider.ANTHROPIC
        if prefix in ("openai",):
            return LLMProvider.OPENAI

    return LLMProvider.UNKNOWN


def apply_cache_hints(
    messages: list[dict[str, Any]],
    provider: LLMProvider,
    cag_present: bool = False,
) -> list[dict[str, Any]]:
    """Apply provider-specific prompt caching hints to LLM messages.

    Modifies the messages list in-place to add caching annotations
    appropriate for the detected provider. The CAG content within the
    system message is the primary caching target — it is large, static,
    and repeated identically across consecutive LLM calls.

    **Claude (Anthropic):**
    The system message content is converted to the Anthropic block
    format with a `cache_control` annotation of type "ephemeral" on
    the block containing CAG content. This tells Claude's API to cache
    the prefix up to and including the CAG content.

    **OpenAI:**
    No modification needed. OpenAI automatically caches prompt prefixes
    of 1024+ tokens. As long as the system prompt + CAG content is
    stable between calls (which it is by design), the cache is hit.

    **Ollama:**
    No modification needed. Ollama's KV cache automatically reuses
    cached key-value pairs for matching prompt prefixes. Keeping the
    system message stable across calls maximises cache reuse.

    Args:
        messages: The LLM message list (typically [system, user]).
        provider: The detected LLM provider.
        cag_present: Whether CAG content is included in the context.

    Returns:
        The messages list (modified in-place and also returned for
        convenience).
    """
    if not cag_present or not messages:
        return messages

    if provider == LLMProvider.ANTHROPIC:
        return _apply_anthropic_cache_hints(messages)

    # OpenAI and Ollama: no client-side action required.
    # Log the expected caching behaviour for observability.
    if provider == LLMProvider.OPENAI:
        logger.debug(
            "OpenAI provider: automatic prompt prefix caching active. "
            "CAG content in stable system prefix will be cached."
        )
    elif provider == LLMProvider.OLLAMA:
        logger.debug(
            "Ollama provider: KV cache reuse active for stable prompt prefix. "
            "CAG content will benefit from KV cache on repeated calls."
        )

    return messages


def _apply_anthropic_cache_hints(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply Claude-specific cache_control annotations.

    Converts the system message content to Anthropic's block format:
    ```json
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "<system prompt + role + context summary + CAG>",
                "cache_control": {"type": "ephemeral"}
            }
        ]
    }
    ```

    The `cache_control` with type "ephemeral" marks the content block
    as cacheable for the duration of the session. Subsequent calls with
    the same prefix hit the cache, reducing token costs to near zero
    for the cached portion.

    Args:
        messages: The LLM message list to annotate.

    Returns:
        The annotated messages list.
    """
    if not messages or messages[0].get("role") != "system":
        return messages

    system_msg = messages[0]
    content = system_msg.get("content", "")

    if isinstance(content, str):
        # Convert to block format with cache_control
        system_msg["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        logger.debug(
            "Applied Anthropic cache_control to system message "
            f"({len(content)} chars)"
        )
    elif isinstance(content, list):
        # Already in block format — add cache_control to the last block
        # (Anthropic caches the prefix up to and including the annotated block)
        if content and isinstance(content[-1], dict):
            content[-1]["cache_control"] = {"type": "ephemeral"}
            logger.debug("Applied Anthropic cache_control to last content block")

    return messages
```

### 3. Updates to `faith/agent/context.py`

Update `ContextAssembler` to accept a `CAGManager` instead of raw string lists, and delegate CAG formatting to the manager.

```python
# --- Changes to ContextAssembler.__init__ ---
# Replace the existing cag_docs parameter:

def __init__(
    self,
    agent_dir: Path,
    model: str,
    recent_message_limit: int = 20,
    cag_docs: Optional[list[str]] = None,
    cag_manager: Optional["CAGManager"] = None,
):
    self.agent_dir = agent_dir
    self.model = model
    self.recent_message_limit = recent_message_limit
    # Prefer CAGManager if provided; fall back to raw list for
    # backwards compatibility with FAITH-010 stubs.
    self._cag_manager = cag_manager
    self.cag_docs = cag_docs or []
```

```python
# --- Changes to ContextAssembler.format_cag_documents ---
# Replace the existing method:

def format_cag_documents(self) -> str:
    """Format pre-loaded CAG documents for inclusion in context.

    Delegates to CAGManager if available, otherwise falls back to
    the raw cag_docs list (FAITH-010 compatibility).

    Returns:
        Formatted string with all CAG documents, or empty string
        if none configured.
    """
    if self._cag_manager is not None:
        return self._cag_manager.format_for_context()

    # Fallback: raw string list (FAITH-010 compatibility)
    if not self.cag_docs:
        return ""

    sections = []
    for i, doc in enumerate(self.cag_docs, 1):
        sections.append(f"--- Reference Document {i} ---\n{doc}")
    return "\n\n".join(sections)
```

### 4. Updates to `faith/agent/base.py`

Replace the stub `_load_cag_documents` with `CAGManager` integration and add `file:changed` reload handling.

```python
# --- Add import at top of base.py ---
from faith.agent.cag import CAGManager, CAGValidationResult
from faith.llm.caching import apply_cache_hints, detect_provider

# --- Replace _load_cag_documents and update __init__ ---

def __init__(
    self,
    agent_id: str,
    faith_dir: Path,
    redis_client: aioredis.Redis,
    llm_client: Optional[LLMClient] = None,
):
    # ... existing init code up to config loading ...

    self.config = self._load_config()

    # Set up CAG manager
    model = self.config.get("model", "gpt-4o")
    self.cag_manager = CAGManager(
        faith_dir=self.faith_dir,
        model=model,
        cag_document_paths=self.config.get("cag_documents", []),
        cag_max_tokens=self.config.get("cag_max_tokens", 8000),
    )
    self._cag_validation: Optional[CAGValidationResult] = None

    # Set up context assembler with CAGManager
    recent_limit = self.config.get("recent_message_limit", 20)
    self.context_assembler = ContextAssembler(
        agent_dir=self.agent_dir,
        model=model,
        recent_message_limit=recent_limit,
        cag_manager=self.cag_manager,
    )

    # Detect LLM provider for caching hints
    api_base = self.config.get("api_base", "")
    self._llm_provider = detect_provider(model, api_base)

    # ... rest of existing init ...


def load_cag_documents(self) -> CAGValidationResult:
    """Load all CAG documents and validate the token budget.

    Called by the PA at session start during agent initialisation.
    The result is stored and can be retrieved for validation reporting.

    Returns:
        CAGValidationResult with success status and any issues.
    """
    self._cag_validation = self.cag_manager.load_all()
    return self._cag_validation


async def handle_cag_file_changed(self, changed_path: str) -> None:
    """Handle a file:changed event for a CAG document.

    Checks if the changed file is a configured CAG document. If so,
    reloads it and logs a notification message to the agent's context.

    Called from _handle_message when a file:changed event is received.

    Args:
        changed_path: Absolute path of the changed file.
    """
    if not self.cag_manager.is_cag_path(changed_path):
        return

    reloaded = self.cag_manager.reload_document(changed_path)
    if reloaded and reloaded.loaded:
        logger.info(
            f"CAG document reloaded for agent '{self.agent_id}': "
            f"{reloaded.relative_path}"
        )
        # The ContextAssembler will pick up the new content on the
        # next LLM call via CAGManager.format_for_context().
    elif reloaded:
        logger.warning(
            f"CAG document reload failed for agent '{self.agent_id}': "
            f"{reloaded.error}"
        )
```

```python
# --- Update _call_llm to apply caching hints ---
# In the existing _call_llm method, after context assembly:

async def _call_llm(
    self, message: CompactMessage, channel: str
) -> str:
    """Assemble context and call the LLM."""
    messages = self.context_assembler.assemble(
        agent_id=self.agent_id,
        agent_role=self.config.get("role", "general assistant"),
        recent_messages=self._get_recent_messages(channel),
        current_task=message.summary or "",
    )

    # Apply provider-specific prompt caching hints for CAG content
    cag_present = bool(self.cag_manager.total_tokens > 0)
    apply_cache_hints(messages, self._llm_provider, cag_present=cag_present)

    model = self.config.get("model", "gpt-4o")
    temperature = self.config.get("temperature", 0.7)

    return await self.llm_client.chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
    )
```

### 5. Updates to `faith/pa/validation.py`

Add CAG validation to the PA's session-start checks.

```python
"""PA-side CAG validation at session start.

The PA validates each agent's CAG configuration before the session
begins. This catches issues early — missing files, budget overruns —
so the user can fix them before agents start making LLM calls with
incomplete context.

FRS Reference: Section 4.10.3
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from faith.agent.base import BaseAgent
    from faith.agent.cag import CAGValidationResult

logger = logging.getLogger("faith.pa.validation")


async def validate_agent_cag(agent: "BaseAgent") -> "CAGValidationResult":
    """Validate an agent's CAG configuration at session start.

    Called by the PA for each agent during session initialisation.
    Loads all declared CAG documents and checks:
    1. All declared documents exist and are readable.
    2. Combined token count does not exceed cag_max_tokens.

    If validation fails, the PA should warn the user with the
    result's summary() before proceeding. The session can still
    start — agents simply operate without the missing documents.

    Args:
        agent: The BaseAgent instance to validate.

    Returns:
        CAGValidationResult with details of any issues.
    """
    result = agent.load_cag_documents()

    if not result.success:
        logger.warning(
            f"CAG validation issues for agent '{agent.agent_id}':\n"
            f"{result.summary()}"
        )
    else:
        logger.info(
            f"CAG validation passed for agent '{agent.agent_id}': "
            f"{result.loaded_count} documents, "
            f"{result.total_tokens}/{result.max_tokens} tokens"
        )

    return result


async def validate_all_agents_cag(
    agents: list["BaseAgent"],
) -> dict[str, "CAGValidationResult"]:
    """Validate CAG for all agents and return a summary dict.

    Called once by the PA at session start. Results are keyed by agent_id.

    Args:
        agents: List of all BaseAgent instances in the session.

    Returns:
        Dict mapping agent_id to their CAGValidationResult.
    """
    results: dict[str, CAGValidationResult] = {}

    for agent in agents:
        cag_paths = agent.config.get("cag_documents", [])
        if not cag_paths:
            logger.debug(
                f"Agent '{agent.agent_id}' has no CAG documents configured."
            )
            continue

        results[agent.agent_id] = await validate_agent_cag(agent)

    # Log overall summary
    total_agents = len(results)
    failed = sum(1 for r in results.values() if not r.success)
    if failed:
        logger.warning(
            f"CAG validation: {failed}/{total_agents} agents have issues."
        )
    elif total_agents:
        logger.info(f"CAG validation: all {total_agents} agents passed.")

    return results


def format_cag_validation_for_user(
    results: dict[str, "CAGValidationResult"],
) -> str:
    """Format CAG validation results for display to the user.

    The PA uses this to present a clear summary at session start
    when CAG issues are detected.

    Args:
        results: Dict from validate_all_agents_cag().

    Returns:
        Human-readable summary string, or empty string if all OK.
    """
    issues = {aid: r for aid, r in results.items() if not r.success}
    if not issues:
        return ""

    lines = ["CAG configuration issues detected:"]
    for agent_id, result in issues.items():
        lines.append(f"\n  Agent '{agent_id}':")
        for err in result.errors:
            lines.append(f"    - {err}")
        for warn in result.warnings:
            lines.append(f"    - {warn}")

    lines.append(
        "\nThe session will proceed, but affected agents will operate "
        "without the missing reference documents. Fix the issues in "
        ".faith/agents/{id}/config.yaml and restart the session."
    )
    return "\n".join(lines)
```

### 6. `tests/test_cag.py`

```python
"""Tests for the CAG (Cache-Augmented Generation) subsystem.

Covers:
- CAGManager: document loading, token budget, reload, path matching
- Provider caching hints: Anthropic, OpenAI, Ollama
- PA validation: session-start checks
- Integration: CAGManager with ContextAssembler

FRS Reference: Section 4.10
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from faith.agent.cag import CAGDocument, CAGManager, CAGValidationResult
from faith.llm.caching import (
    LLMProvider,
    apply_cache_hints,
    detect_provider,
    _apply_anthropic_cache_hints,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_faith_dir(tmp_path: Path) -> Path:
    """Create a temporary .faith directory with sample CAG documents."""
    faith_dir = tmp_path / ".faith"
    faith_dir.mkdir()

    # Create workspace/docs with sample documents
    docs_dir = faith_dir / "workspace" / "docs"
    docs_dir.mkdir(parents=True)

    (docs_dir / "coding-standards.md").write_text(
        "# Coding Standards\n\n- Use type hints\n- Max line length: 120\n",
        encoding="utf-8",
    )
    (docs_dir / "api-spec.md").write_text(
        "# API Specification\n\n## Endpoints\n\nGET /api/v1/users\nPOST /api/v1/users\n",
        encoding="utf-8",
    )
    (docs_dir / "large-doc.md").write_text(
        "x " * 5000,  # ~2500 tokens at 4 chars/token
        encoding="utf-8",
    )

    return faith_dir


# ---------------------------------------------------------------------------
# CAGManager Tests
# ---------------------------------------------------------------------------


class TestCAGManager:
    """Tests for CAGManager document loading and management."""

    def test_load_all_success(self, tmp_faith_dir: Path):
        """All documents load successfully within budget."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=[
                "workspace/docs/coding-standards.md",
                "workspace/docs/api-spec.md",
            ],
            cag_max_tokens=8000,
        )
        result = mgr.load_all()

        assert result.success
        assert result.loaded_count == 2
        assert result.document_count == 2
        assert result.total_tokens > 0
        assert result.total_tokens <= 8000
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_load_all_missing_file(self, tmp_faith_dir: Path):
        """Missing document produces an error in validation result."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=["workspace/docs/nonexistent.md"],
            cag_max_tokens=8000,
        )
        result = mgr.load_all()

        assert not result.success
        assert result.loaded_count == 0
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].lower()

    def test_load_all_budget_exceeded(self, tmp_faith_dir: Path):
        """Exceeding token budget produces a warning."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=["workspace/docs/large-doc.md"],
            cag_max_tokens=100,  # very small budget
        )
        result = mgr.load_all()

        assert not result.success
        assert result.total_tokens > 100
        assert len(result.warnings) == 1
        assert "exceeded" in result.warnings[0].lower()

    def test_load_empty_config(self, tmp_faith_dir: Path):
        """No configured documents produces a clean success."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=[],
            cag_max_tokens=8000,
        )
        result = mgr.load_all()

        assert result.success
        assert result.loaded_count == 0
        assert result.total_tokens == 0

    def test_resolve_relative_path(self, tmp_faith_dir: Path):
        """Relative paths are resolved against faith_dir."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=["workspace/docs/coding-standards.md"],
        )
        resolved = mgr._resolve_path("workspace/docs/coding-standards.md")
        assert resolved.is_absolute()
        assert resolved.exists()

    def test_resolve_absolute_path(self, tmp_faith_dir: Path):
        """Absolute paths are used as-is."""
        abs_path = tmp_faith_dir / "workspace" / "docs" / "coding-standards.md"
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=[str(abs_path)],
        )
        resolved = mgr._resolve_path(str(abs_path))
        assert resolved == abs_path.resolve()

    def test_is_cag_path(self, tmp_faith_dir: Path):
        """is_cag_path correctly identifies configured documents."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=["workspace/docs/coding-standards.md"],
        )
        mgr.load_all()

        abs_path = tmp_faith_dir / "workspace" / "docs" / "coding-standards.md"
        assert mgr.is_cag_path(abs_path)
        assert not mgr.is_cag_path(tmp_faith_dir / "workspace" / "docs" / "other.md")

    def test_reload_document(self, tmp_faith_dir: Path):
        """Reloading a document picks up content changes."""
        doc_path = tmp_faith_dir / "workspace" / "docs" / "coding-standards.md"
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=["workspace/docs/coding-standards.md"],
        )
        mgr.load_all()

        original_hash = mgr.documents[0].sha256

        # Modify the file
        doc_path.write_text("# Updated Standards\n\n- New rule\n", encoding="utf-8")

        reloaded = mgr.reload_document(doc_path)
        assert reloaded is not None
        assert reloaded.loaded
        assert reloaded.sha256 != original_hash
        assert "Updated Standards" in reloaded.content

    def test_reload_non_cag_path_returns_none(self, tmp_faith_dir: Path):
        """Reloading a path that isn't a CAG document returns None."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=["workspace/docs/coding-standards.md"],
        )
        mgr.load_all()

        result = mgr.reload_document("/some/other/file.md")
        assert result is None

    def test_format_for_context(self, tmp_faith_dir: Path):
        """Formatted output includes document headers and content."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=[
                "workspace/docs/coding-standards.md",
                "workspace/docs/api-spec.md",
            ],
        )
        mgr.load_all()

        formatted = mgr.format_for_context()
        assert "CAG Reference: workspace/docs/coding-standards.md" in formatted
        assert "CAG Reference: workspace/docs/api-spec.md" in formatted
        assert "Coding Standards" in formatted
        assert "API Specification" in formatted

    def test_format_for_context_empty(self, tmp_faith_dir: Path):
        """No loaded documents returns empty string."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=[],
        )
        mgr.load_all()

        assert mgr.format_for_context() == ""

    def test_total_tokens_property(self, tmp_faith_dir: Path):
        """total_tokens sums only loaded documents."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=[
                "workspace/docs/coding-standards.md",
                "workspace/docs/nonexistent.md",
            ],
        )
        mgr.load_all()

        assert mgr.total_tokens > 0
        # Should only count the loaded document, not the missing one
        assert mgr.total_tokens == mgr.documents[0].token_count

    def test_get_absolute_paths(self, tmp_faith_dir: Path):
        """get_absolute_paths returns resolved paths for all documents."""
        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=[
                "workspace/docs/coding-standards.md",
                "workspace/docs/api-spec.md",
            ],
        )
        mgr.load_all()

        paths = mgr.get_absolute_paths()
        assert len(paths) == 2
        assert all(p.is_absolute() for p in paths)


# ---------------------------------------------------------------------------
# CAGValidationResult Tests
# ---------------------------------------------------------------------------


class TestCAGValidationResult:
    """Tests for CAGValidationResult summary formatting."""

    def test_summary_success(self):
        result = CAGValidationResult(
            success=True,
            total_tokens=3000,
            max_tokens=8000,
            document_count=2,
            loaded_count=2,
        )
        summary = result.summary()
        assert "2/2 documents loaded" in summary
        assert "3000/8000 tokens" in summary

    def test_summary_with_errors(self):
        result = CAGValidationResult(
            success=False,
            total_tokens=0,
            max_tokens=8000,
            document_count=1,
            loaded_count=0,
            errors=["File not found: /path/to/doc.md"],
        )
        summary = result.summary()
        assert "ERROR" in summary
        assert "File not found" in summary

    def test_summary_with_warnings(self):
        result = CAGValidationResult(
            success=False,
            total_tokens=9000,
            max_tokens=8000,
            document_count=2,
            loaded_count=2,
            warnings=["CAG token budget exceeded"],
        )
        summary = result.summary()
        assert "WARNING" in summary
        assert "budget exceeded" in summary


# ---------------------------------------------------------------------------
# Provider Detection Tests
# ---------------------------------------------------------------------------


class TestProviderDetection:
    """Tests for LLM provider detection."""

    def test_detect_anthropic_from_model(self):
        assert detect_provider("claude-3.5-sonnet") == LLMProvider.ANTHROPIC

    def test_detect_openai_from_model(self):
        assert detect_provider("gpt-4o") == LLMProvider.OPENAI

    def test_detect_ollama_from_api_base(self):
        assert (
            detect_provider("llama3", "http://ollama:11434")
            == LLMProvider.OLLAMA
        )

    def test_detect_anthropic_from_openrouter_prefix(self):
        assert (
            detect_provider("anthropic/claude-3.5-sonnet")
            == LLMProvider.ANTHROPIC
        )

    def test_detect_unknown(self):
        assert detect_provider("some-custom-model") == LLMProvider.UNKNOWN


# ---------------------------------------------------------------------------
# Cache Hints Tests
# ---------------------------------------------------------------------------


class TestCacheHints:
    """Tests for provider-specific prompt caching annotations."""

    def test_anthropic_cache_control_applied(self):
        """Claude messages get cache_control on the system block."""
        messages = [
            {"role": "system", "content": "System prompt with CAG content"},
            {"role": "user", "content": "User message"},
        ]
        result = apply_cache_hints(
            messages, LLMProvider.ANTHROPIC, cag_present=True
        )

        system = result[0]
        assert isinstance(system["content"], list)
        assert system["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert system["content"][0]["text"] == "System prompt with CAG content"

    def test_anthropic_no_cag_skips_hints(self):
        """No cache hints when CAG is not present."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "User message"},
        ]
        result = apply_cache_hints(
            messages, LLMProvider.ANTHROPIC, cag_present=False
        )

        # Content should remain a plain string
        assert isinstance(result[0]["content"], str)

    def test_openai_messages_unchanged(self):
        """OpenAI messages are not modified (automatic caching)."""
        messages = [
            {"role": "system", "content": "System prompt with CAG content"},
            {"role": "user", "content": "User message"},
        ]
        original_content = messages[0]["content"]
        apply_cache_hints(messages, LLMProvider.OPENAI, cag_present=True)

        assert messages[0]["content"] == original_content
        assert isinstance(messages[0]["content"], str)

    def test_ollama_messages_unchanged(self):
        """Ollama messages are not modified (KV cache handles it)."""
        messages = [
            {"role": "system", "content": "System prompt with CAG content"},
            {"role": "user", "content": "User message"},
        ]
        original_content = messages[0]["content"]
        apply_cache_hints(messages, LLMProvider.OLLAMA, cag_present=True)

        assert messages[0]["content"] == original_content

    def test_anthropic_block_format_existing(self):
        """Anthropic hints work when content is already in block format."""
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "First block"},
                    {"type": "text", "text": "CAG block"},
                ],
            },
        ]
        result = apply_cache_hints(
            messages, LLMProvider.ANTHROPIC, cag_present=True
        )

        blocks = result[0]["content"]
        assert "cache_control" not in blocks[0]
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}

    def test_empty_messages_safe(self):
        """Empty message list does not raise."""
        result = apply_cache_hints([], LLMProvider.ANTHROPIC, cag_present=True)
        assert result == []


# ---------------------------------------------------------------------------
# Integration: CAGManager + ContextAssembler
# ---------------------------------------------------------------------------


class TestCAGContextIntegration:
    """Test CAGManager integration with ContextAssembler."""

    def test_assembler_uses_cag_manager(self, tmp_faith_dir: Path):
        """ContextAssembler delegates to CAGManager for formatting."""
        from faith.agent.context import ContextAssembler

        mgr = CAGManager(
            faith_dir=tmp_faith_dir,
            model="gpt-4o",
            cag_document_paths=["workspace/docs/coding-standards.md"],
        )
        mgr.load_all()

        assembler = ContextAssembler(
            agent_dir=tmp_faith_dir / "agents" / "test-agent",
            model="gpt-4o",
            cag_manager=mgr,
        )
        formatted = assembler.format_cag_documents()
        assert "CAG Reference" in formatted
        assert "Coding Standards" in formatted

    def test_assembler_fallback_without_manager(self, tmp_faith_dir: Path):
        """ContextAssembler falls back to raw cag_docs without a manager."""
        from faith.agent.context import ContextAssembler

        agent_dir = tmp_faith_dir / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)

        assembler = ContextAssembler(
            agent_dir=agent_dir,
            model="gpt-4o",
            cag_docs=["Some raw document content"],
        )
        formatted = assembler.format_cag_documents()
        assert "Reference Document 1" in formatted
        assert "Some raw document content" in formatted
```

---

## Integration Points

### Dependency: FAITH-010 (Base Agent Class)

FAITH-010 defines `BaseAgent._load_cag_documents()` and `ContextAssembler.format_cag_documents()` as basic stubs. This task replaces them with `CAGManager` while maintaining backwards compatibility through the `cag_docs` fallback parameter.

### Dependency: FAITH-022 (Filesystem MCP Server)

The filesystem tool publishes `file:changed` events when watched files change. CAG documents are registered as watched paths at session start. When a `file:changed` event arrives for a CAG document path, `BaseAgent.handle_cag_file_changed()` triggers the reload via `CAGManager.reload_document()`.

### Downstream: FAITH-013 (LLM API Client)

The `apply_cache_hints()` function from `faith/llm/caching.py` modifies the messages list before it is sent to the LLM client. FAITH-013's client should pass the messages through unmodified — the Anthropic block format with `cache_control` is valid for the Anthropic API, and OpenAI/Ollama messages are unmodified.

### Downstream: FAITH-014 (PA Session Management)

The PA calls `validate_all_agents_cag()` during session initialisation to check all agents' CAG configurations. It uses `format_cag_validation_for_user()` to present any issues to the user before the session begins.

### Downstream: FAITH-004 (Config Hot-Reload)

If `config.yaml` is modified to add or remove CAG documents, the config watcher detects the change. The agent should re-initialise its `CAGManager` with the new document list. This is handled by the existing config reload mechanism calling `load_cag_documents()` again.

### Event Flow: file:changed Reload

```
1. Agent session starts
   └─> PA calls validate_agent_cag(agent)
       └─> agent.load_cag_documents()
           └─> CAGManager.load_all() reads files, validates budget
   └─> PA registers file watches for CAG paths via filesystem tool

2. During session, a CAG document is modified
   └─> Filesystem tool detects SHA256 change
       └─> Publishes file:changed event to subscribing agent's channel
           └─> BaseAgent._handle_message() receives event
               └─> BaseAgent.handle_cag_file_changed(path)
                   └─> CAGManager.reload_document(path)
   └─> Next LLM call: ContextAssembler picks up updated content
   └─> Agent receives notification:
       "[filename] has been updated and reloaded into your reference context."
```

---

## Acceptance Criteria

1. `CAGManager.load_all()` reads all configured documents from disk and returns a `CAGValidationResult` with accurate token counts.
2. `CAGManager.load_all()` detects missing files and reports them as errors in the validation result.
3. `CAGManager.load_all()` detects token budget overruns and produces a warning that suggests moving the largest document to RAG.
4. `CAGManager.reload_document(path)` reloads a single document from disk, updates the stored content and SHA-256 hash, and returns the updated `CAGDocument`.
5. `CAGManager.is_cag_path(path)` correctly identifies whether a file path matches a configured CAG document.
6. `CAGManager.format_for_context()` produces formatted output with document headers showing source paths.
7. `ContextAssembler` delegates CAG formatting to `CAGManager` when provided, falling back to raw `cag_docs` list for FAITH-010 compatibility.
8. `detect_provider()` correctly identifies Anthropic, OpenAI, and Ollama providers from model names and API base URLs.
9. `apply_cache_hints()` converts Claude system messages to block format with `cache_control: {"type": "ephemeral"}`.
10. `apply_cache_hints()` leaves OpenAI and Ollama messages unmodified.
11. `apply_cache_hints()` is a no-op when `cag_present=False`.
12. PA calls `validate_all_agents_cag()` at session start and surfaces issues to the user via `format_cag_validation_for_user()`.
13. `BaseAgent.handle_cag_file_changed()` triggers reload only for paths matching configured CAG documents.
14. All tests in `tests/test_cag.py` pass, covering CAGManager loading, budget validation, reload, path matching, formatting, provider detection, cache hints, and ContextAssembler integration.

---

## Notes for Implementer

- **Backwards compatibility**: The `ContextAssembler` changes must not break FAITH-010. The `cag_docs` parameter is retained alongside the new `cag_manager` parameter. If both are provided, `cag_manager` takes precedence.
- **Token estimation model**: `CAGManager` uses the agent's configured model for token estimation via `estimate_tokens()` from `faith/utils/tokens.py`. This means token counts may differ slightly between agents using different models for the same document. This is intentional — the budget should reflect the actual cost for each agent's model.
- **Path resolution**: All document paths in `config.yaml` are relative to the `.faith` directory unless absolute. The `workspace/` prefix in the FRS examples means the documents live under `.faith/workspace/docs/`. In Docker, the workspace is bind-mounted into the container, so these paths resolve correctly.
- **SHA-256 for change detection**: `CAGManager` computes SHA-256 hashes of document content. This matches the filesystem tool's change detection mechanism (Section 4.3), ensuring consistency between the `file:changed` event trigger and the reload check.
- **Anthropic cache_control placement**: The `cache_control` annotation is placed on the system message content block. Anthropic caches the prefix up to and including the annotated block. Since CAG content is part of the system message (layers 1-4), the entire prefix including system prompt, role reminder, context summary, and CAG documents is cached together. This is the optimal placement for maximum cache hits.
- **OpenAI automatic caching**: OpenAI caches prompt prefixes of 1024+ tokens automatically. No client-side annotation is needed. The CAG content in the system message is stable across calls, so the cache is hit naturally.
- **Ollama KV cache**: Ollama reuses its KV cache when the prompt prefix matches. Keeping the system message (including CAG) identical between calls ensures maximum cache reuse. No client-side action is required.
- **file:changed event handling**: The agent subscribes to `file:changed` events as part of its normal event system (FAITH-009). The `handle_cag_file_changed()` method filters for CAG-relevant paths only. Non-CAG file changes are ignored by this handler. The PA registers file watches for CAG paths using the filesystem tool (FAITH-022) during session start.
- **No eager re-validation on reload**: When a single document is reloaded, the total token count may temporarily exceed the budget. This is acceptable — the budget is a session-start validation, not a runtime hard limit. The user was warned at session start if the budget was tight.
- **FakeRedis in tests**: Following the FAITH-010 pattern, tests that need Redis use a custom `FakeRedis` / `FakePubSub` pair rather than the `fakeredis` library.

