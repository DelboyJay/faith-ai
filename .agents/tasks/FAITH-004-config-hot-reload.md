# FAITH-004 — Config Hot-Reload Watcher

**Phase:** 1 — Foundation
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-003, FAITH-002
**FRS Reference:** Section 7.3, 2.6, 3.7.7

---

## Objective

Implement a polling-based file watcher that detects changes to project-level config files (`.faith/**/*.yaml`, `.faith/agents/*/prompt.md`, `.faith/skills/*.md`) and framework-level files (`~/.faith/config/secrets.yaml`), validates changes, applies them via file-specific handlers, and publishes events to the Redis `system-events` channel. This is a core PA component — the watcher runs as an async background task within the PA process.

---

## Architecture

```
faith/config/
├── watcher.py        ← ConfigWatcher class (this task)
├── handlers.py       ← Per-file reload handler dispatch (this task)
├── loader.py         ← (FAITH-003 — already exists)
└── models.py         ← (FAITH-003 — already exists)
```

The watcher polls files every 5 seconds using SHA256 checksums. On change detection, it validates the new file, and if valid, dispatches to the appropriate handler. Invalid changes are rejected — the previous config remains active.

---

## Files to Create

### 1. `faith/config/watcher.py`

```python
"""Config file watcher — polls for changes using SHA256 checksums."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Callable, Awaitable, Optional

import redis.asyncio as aioredis

from faith.config.loader import load_config, ConfigValidationError
from faith.config.models import SystemConfig, AgentsConfig, ToolsConfig, SecurityConfig

logger = logging.getLogger("faith.config.watcher")


class ConfigWatcher:
    """Watches config files for changes and dispatches reload handlers.

    Attributes:
        config_dir: Path to the config directory.
        poll_interval: Seconds between polls (default 5).
        redis_client: Async Redis client for publishing events.
    """

    def __init__(
        self,
        config_dir: Path,
        redis_client: aioredis.Redis,
        poll_interval: float = 5.0,
    ):
        self.config_dir = config_dir
        self.redis_client = redis_client
        self.poll_interval = poll_interval

        # Current SHA256 hashes of watched files
        self._hashes: dict[Path, str] = {}

        # Registered handlers: file path -> async callback
        self._handlers: dict[Path, Callable[[Path], Awaitable[None]]] = {}

        # Currently loaded valid configs (rollback target on validation failure)
        self._current_configs: dict[str, object] = {}

        # Running flag
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def register(
        self,
        file_path: Path,
        handler: Callable[[Path], Awaitable[None]],
    ) -> None:
        """Register a file to watch and its reload handler.

        Args:
            file_path: Absolute path to the file to watch.
            handler: Async function called when the file changes and
                passes validation.
        """
        self._handlers[file_path] = handler
        self._hashes[file_path] = self._compute_hash(file_path)
        logger.info(f"Watching: {file_path}")

    @staticmethod
    def _compute_hash(file_path: Path) -> str:
        """Compute SHA256 hash of a file's contents.

        Returns empty string if file doesn't exist.
        """
        try:
            content = file_path.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except FileNotFoundError:
            return ""

    async def start(self) -> None:
        """Start the watcher as a background async task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            f"ConfigWatcher started — polling every {self.poll_interval}s"
        )

    async def stop(self) -> None:
        """Stop the watcher."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ConfigWatcher stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop — runs until stop() is called."""
        while self._running:
            try:
                await self._check_all()
            except Exception:
                logger.exception("Error in config watcher poll")
            await asyncio.sleep(self.poll_interval)

    async def _check_all(self) -> None:
        """Check all registered files for changes."""
        for file_path, handler in list(self._handlers.items()):
            new_hash = self._compute_hash(file_path)
            old_hash = self._hashes.get(file_path, "")

            if new_hash != old_hash and new_hash != "":
                logger.info(f"Change detected: {file_path}")
                await self._handle_change(file_path, handler, new_hash)

    async def _handle_change(
        self,
        file_path: Path,
        handler: Callable[[Path], Awaitable[None]],
        new_hash: str,
    ) -> None:
        """Validate and apply a config file change.

        On validation failure: previous config kept, error published.
        On success: handler called, hash updated, event published.
        """
        filename = file_path.name

        # Validate config files (skip validation for .md files — prompt.md and skills/*.md)
        if filename.endswith(".yaml"):
            try:
                new_config = load_config(file_path)
                self._current_configs[filename] = new_config
            except ConfigValidationError as e:
                logger.warning(f"Invalid config change rejected: {e.human_message}")
                await self._publish_config_error(file_path, e.human_message)
                return
            except Exception as e:
                logger.warning(f"Failed to load config: {e}")
                await self._publish_config_error(file_path, str(e))
                return

        # Update hash BEFORE calling handler (prevent re-trigger)
        self._hashes[file_path] = new_hash

        # Call the file-specific handler
        try:
            await handler(file_path)
        except Exception:
            logger.exception(f"Handler error for {file_path}")

        # Publish success event
        await self._publish_config_changed(file_path)

    async def _publish_config_changed(self, file_path: Path) -> None:
        """Publish a system:config_changed event to Redis."""
        import json
        event = json.dumps({
            "event": "system:config_changed",
            "source": "config-watcher",
            "channel": "system-events",
            "ts": _now_iso(),
            "data": {
                "file": file_path.name,
                "path": str(file_path),
            },
        })
        await self.redis_client.publish("system-events", event)

    async def _publish_config_error(
        self, file_path: Path, message: str
    ) -> None:
        """Publish a config validation error event."""
        import json
        event = json.dumps({
            "event": "system:config_error",
            "source": "config-watcher",
            "channel": "system-events",
            "ts": _now_iso(),
            "data": {
                "file": file_path.name,
                "path": str(file_path),
                "error": message,
            },
        })
        await self.redis_client.publish("system-events", event)

    def get_current_config(self, filename: str) -> Optional[object]:
        """Get the currently loaded valid config for a file.

        Args:
            filename: Config filename (e.g. "system.yaml").

        Returns:
            The validated Pydantic model, or None if not loaded.
        """
        return self._current_configs.get(filename)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

### 2. `faith/config/handlers.py`

Per-file handler definitions — these are called by the watcher when a file changes and passes validation.

```python
"""Per-file reload handlers for FAITH config hot-reload.

Each handler receives the path to the changed file and applies the
update to the running system. Handlers are async functions.

Files are split across two locations:
- Framework-level: ~/.faith/config/secrets.yaml, ~/.faith/config/.env
- Project-level: .faith/system.yaml, .faith/security.yaml,
  .faith/tools/*.yaml, .faith/agents/*/config.yaml, .faith/agents/*/prompt.md,
  .faith/skills/*.md
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from faith.config.watcher import ConfigWatcher

logger = logging.getLogger("faith.config.handlers")


# --- Project-level handlers (.faith/) ---

async def handle_system_yaml(file_path: Path) -> None:
    """Handle .faith/system.yaml changes.

    Actions:
    - Reload global project settings (log retention, stall timeout, etc.)
    - Update PA model config
    - Reload editor preference
    - Update loop detection params
    - If privacy profile changed: check agent compliance (FAITH-050)

    Note: Full implementation depends on PA components from Phase 4.
    This stub logs the change and will be extended.
    """
    logger.info(f"system.yaml reloaded: {file_path}")
    # TODO (FAITH-016): propagate settings to PA event dispatcher
    # TODO (FAITH-050): check privacy profile compliance


async def handle_agent_config(file_path: Path) -> None:
    """Handle .faith/agents/{id}/config.yaml changes.

    Actions:
    - Diff against running state for this agent
    - Update model/tool/watch assignments
    - Restart agent container if model changed
    - Update file watch subscriptions

    Note: Full implementation depends on PA container orchestration
    from FAITH-014.
    """
    agent_id = file_path.parent.name
    logger.info(f"Agent config reloaded for '{agent_id}': {file_path}")
    # TODO (FAITH-014): diff and reconcile agent container


async def handle_tool_config(file_path: Path) -> None:
    """Handle .faith/tools/*.yaml changes.

    Actions:
    - Register new mounts/DB connections with existing MCP servers
    - Start new tool containers if new tool type added
    - Update permission rules
    - Toggle internet access on Python tool

    Note: Full implementation depends on tool MCP servers from Phase 6.
    """
    tool_name = file_path.stem  # e.g. "filesystem", "database"
    logger.info(f"Tool config reloaded for '{tool_name}': {file_path}")
    # TODO (FAITH-014): diff and reconcile tool containers


async def handle_security_yaml(file_path: Path) -> None:
    """Handle .faith/security.yaml changes.

    Actions:
    - Reload all approval rules immediately
    - Takes effect on next approval request
    - No agent notification required
    """
    logger.info(f"security.yaml reloaded: {file_path}")
    # TODO (FAITH-019): reload approval engine rules


async def handle_prompt_md(file_path: Path) -> None:
    """Handle .faith/agents/{id}/prompt.md changes.

    Actions:
    - The prompt is read fresh on each LLM call, so no in-memory
      update is needed.
    - Publish notification for Web UI display.

    The watcher detects the change and publishes system:config_changed.
    The Web UI subscribes and surfaces a notification to the user.
    """
    agent_id = file_path.parent.name
    logger.info(f"Prompt updated for agent '{agent_id}': {file_path}")


async def handle_skill_md(file_path: Path) -> None:
    """Handle .faith/skills/*.md changes.

    Actions:
    - Publish system:config_changed so the skill scheduler (FAITH-056)
      can re-parse the skill file and update/add/remove schedule registrations
      without a process restart.
    - The scheduler subscribes to system:config_changed and filters by file
      path to determine whether a schedule registration needs updating.

    Note: Skill files are markdown with YAML frontmatter — they are not
    validated as standalone YAML files. The scheduler owns frontmatter parsing.
    """
    skill_name = file_path.stem
    logger.info(f"Skill file updated '{skill_name}': {file_path}")
    # TODO (FAITH-056): skill scheduler re-parses frontmatter on this event


# --- Framework-level handlers (~/.faith/config/) ---

async def handle_secrets_yaml(file_path: Path) -> None:
    """Handle ~/.faith/config/secrets.yaml changes.

    Actions:
    - Reload credentials (re-apply .env substitution)
    - Hot-apply to active tool connections via secret_ref resolution

    Note: secrets.yaml is NEVER exposed to agents — only the PA reads it.
    """
    logger.info(f"secrets.yaml reloaded: {file_path}")
    # TODO (FAITH-014): re-resolve secret_refs for running tool containers


async def handle_env_file(file_path: Path) -> None:
    """Handle ~/.faith/config/.env file changes.

    Actions:
    - Reload all credentials into os.environ
    - Trigger secrets.yaml re-resolution (since secrets.yaml uses ${VAR} from .env)
    """
    logger.info(f".env reloaded: {file_path}")
    from dotenv import load_dotenv
    load_dotenv(file_path, override=True)


def register_all_handlers(
    watcher: "ConfigWatcher",
    framework_config_dir: Path,
    faith_dir: Path,
) -> None:
    """Register all standard config file watchers.

    Args:
        watcher: The ConfigWatcher instance.
        framework_config_dir: Path to the framework config/ directory.
        faith_dir: Path to the project's .faith/ directory.
    """
    # Framework-level files
    watcher.register(framework_config_dir / "secrets.yaml", handle_secrets_yaml)
    watcher.register(framework_config_dir / ".env", handle_env_file)

    # Project-level files
    watcher.register(faith_dir / "system.yaml", handle_system_yaml)
    watcher.register(faith_dir / "security.yaml", handle_security_yaml)

    # Per-tool configs
    tools_dir = faith_dir / "tools"
    if tools_dir.exists():
        for tool_file in tools_dir.glob("*.yaml"):
            watcher.register(tool_file, handle_tool_config)

    # Per-agent configs and prompts
    agents_dir = faith_dir / "agents"
    if agents_dir.exists():
        for agent_dir in agents_dir.iterdir():
            if agent_dir.is_dir():
                config_path = agent_dir / "config.yaml"
                if config_path.exists():
                    watcher.register(config_path, handle_agent_config)
                prompt_path = agent_dir / "prompt.md"
                if prompt_path.exists():
                    watcher.register(prompt_path, handle_prompt_md)

    # Skill files (markdown + YAML frontmatter) — triggers scheduler re-parse
    skills_dir = faith_dir / "skills"
    if skills_dir.exists():
        for skill_file in skills_dir.glob("*.md"):
            watcher.register(skill_file, handle_skill_md)
```

### 3. `tests/test_config_watcher.py`

```python
"""Tests for the config hot-reload watcher."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from faith.config.watcher import ConfigWatcher


class FakeRedis:
    """Minimal fake async Redis client for testing."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def valid_system_data():
    return {
        "schema_version": "1.0",
        "privacy_profile": "internal",
        "pa": {"model": "test-model"},
        "default_agent_model": "test-model",
    }


@pytest.mark.asyncio
async def test_detects_file_change(tmp_path, fake_redis, valid_system_data):
    """Watcher should detect when a file changes."""
    config_file = tmp_path / "system.yaml"
    config_file.write_text(yaml.dump(valid_system_data))

    handler_called = asyncio.Event()

    async def handler(path: Path):
        handler_called.set()

    watcher = ConfigWatcher(tmp_path, fake_redis, poll_interval=0.1)
    watcher.register(config_file, handler)

    await watcher.start()

    # Modify the file
    valid_system_data["editor"] = "vim"
    config_file.write_text(yaml.dump(valid_system_data))

    # Wait for detection
    try:
        await asyncio.wait_for(handler_called.wait(), timeout=2.0)
    finally:
        await watcher.stop()

    assert handler_called.is_set()


@pytest.mark.asyncio
async def test_rejects_invalid_config(tmp_path, fake_redis, valid_system_data):
    """Watcher should reject invalid config changes."""
    config_file = tmp_path / "system.yaml"
    config_file.write_text(yaml.dump(valid_system_data))

    handler_called = False

    async def handler(path: Path):
        nonlocal handler_called
        handler_called = True

    watcher = ConfigWatcher(tmp_path, fake_redis, poll_interval=0.1)
    watcher.register(config_file, handler)

    await watcher.start()

    # Write invalid config
    config_file.write_text(yaml.dump({"bad": "data"}))

    await asyncio.sleep(0.5)
    await watcher.stop()

    # Handler should NOT have been called
    assert not handler_called

    # Error event should have been published
    assert len(fake_redis.published) >= 1
    channel, msg = fake_redis.published[0]
    assert channel == "system-events"
    event = json.loads(msg)
    assert event["event"] == "system:config_error"


@pytest.mark.asyncio
async def test_publishes_success_event(tmp_path, fake_redis, valid_system_data):
    """Watcher should publish system:config_changed on valid change."""
    config_file = tmp_path / "system.yaml"
    config_file.write_text(yaml.dump(valid_system_data))

    async def handler(path: Path):
        pass

    watcher = ConfigWatcher(tmp_path, fake_redis, poll_interval=0.1)
    watcher.register(config_file, handler)

    await watcher.start()

    valid_system_data["editor"] = "code"
    config_file.write_text(yaml.dump(valid_system_data))

    await asyncio.sleep(0.5)
    await watcher.stop()

    config_events = [
        (ch, json.loads(msg))
        for ch, msg in fake_redis.published
        if json.loads(msg)["event"] == "system:config_changed"
    ]
    assert len(config_events) >= 1


@pytest.mark.asyncio
async def test_ignores_unchanged_files(tmp_path, fake_redis, valid_system_data):
    """Watcher should not trigger for unchanged files."""
    config_file = tmp_path / "system.yaml"
    config_file.write_text(yaml.dump(valid_system_data))

    call_count = 0

    async def handler(path: Path):
        nonlocal call_count
        call_count += 1

    watcher = ConfigWatcher(tmp_path, fake_redis, poll_interval=0.1)
    watcher.register(config_file, handler)

    await watcher.start()
    await asyncio.sleep(0.5)
    await watcher.stop()

    assert call_count == 0


@pytest.mark.asyncio
async def test_prompt_md_no_validation(tmp_path, fake_redis):
    """Prompt files should not be validated as YAML."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# System Prompt\nYou are a developer.")

    handler_called = asyncio.Event()

    async def handler(path: Path):
        handler_called.set()

    watcher = ConfigWatcher(tmp_path, fake_redis, poll_interval=0.1)
    watcher.register(prompt_file, handler)

    await watcher.start()

    prompt_file.write_text("# Updated Prompt\nYou are a senior developer.")

    try:
        await asyncio.wait_for(handler_called.wait(), timeout=2.0)
    finally:
        await watcher.stop()

    assert handler_called.is_set()
```

---

## Integration Points

| Component | How it connects |
|---|---|
| **PA main loop** (FAITH-016) | Creates `ConfigWatcher`, calls `register_all_handlers()`, calls `watcher.start()` in the PA's async startup |
| **Event system** (FAITH-008) | Watcher publishes `system:config_changed` and `system:config_error` to `system-events` |
| **Web UI** (FAITH-036) | Subscribes to `system:config_changed` events and displays hot-reload indicator |
| **Approval engine** (FAITH-019) | Calls `watcher.get_current_config("security.yaml")` to access current rules |
| **Skill scheduler** (FAITH-056) | Subscribes to `system:config_changed`; re-parses `.faith/skills/*.md` frontmatter to update/add/remove schedule registrations without restart |

---

## Acceptance Criteria

1. `ConfigWatcher` detects file changes within 5 seconds (configurable).
2. Changed YAML config files are validated before handlers fire. Invalid changes are rejected with a `system:config_error` event published to Redis.
3. Valid changes trigger the appropriate handler and publish a `system:config_changed` event.
4. Non-YAML files (e.g. `prompt.md`, `.faith/skills/*.md`) skip YAML validation and trigger handlers on any content change.
5. Unchanged files do not trigger handlers (SHA256 match prevents false positives).
6. The watcher runs as a non-blocking async task that does not interfere with other PA operations.
7. All tests in `tests/test_config_watcher.py` pass.

---

## Notes for Implementer

- The watcher uses SHA256 hashing, NOT filesystem events (inotify/FSEvents/ReadDirectoryChanges). This is intentional — filesystem events behave differently across OS/Docker volume mount combinations. Polling is slower but reliable everywhere.
- Handler functions in `handlers.py` are stubs that log the change. They will be extended by later tasks (FAITH-014, FAITH-016, FAITH-019, FAITH-050) as those components are built.
- The `register_all_handlers()` function is called once during PA startup. When the PA dynamically creates a new agent (writing `config.yaml` and `prompt.md` to `.faith/agents/{id}/`), it should also register those new files with the watcher dynamically.
- The `.env` file handler re-calls `load_dotenv(override=True)` to refresh environment variables. This triggers `secrets.yaml` re-resolution since `secrets.yaml` uses `${VAR}` substitution from `.env`.
- The watcher watches files from TWO locations: framework-level `~/.faith/config/` (secrets.yaml, .env) and project-level `<project>/.faith/` (everything else). When the user switches projects, the PA must unregister all project-level `.faith/` watchers for the old project and register new ones for the new project.
