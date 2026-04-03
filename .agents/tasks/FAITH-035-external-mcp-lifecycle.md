# FAITH-035 — External MCP Server Registration & Lifecycle

**Phase:** 7 — CAG & External MCP
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-014, FAITH-003
**FRS Reference:** Section 4.11

---

## Objective

Implement external MCP server registration and lifecycle management under PA control. For v1, external MCP servers are registered via dedicated YAML config files in `.faith/tools/` using **registry/package references only**, resolved through the self-hosted MCP Registry service included in the bootstrap Docker stack. Support **`stdio` only** and **npm package-backed registry entries only**. External servers run inside the project-scoped `mcp-runtime` container managed by the PA, not as one-container-per-server services. Locally launched servers are started on demand when a session involves agents with access, and stopped when the session ends. Environment variables are resolved from `config/secrets.yaml` via `secret_ref` references. Privacy tier enforcement prevents servers from starting when the active privacy profile is more restrictive than the server's declared tier. External packages must be version-pinned, installed or updated only with explicit user confirmation, and support rollback to the previously pinned version. The MCP adapter layer (FAITH-012) works transparently with external servers — models that do not natively support MCP can still use external tools via the same translation mechanism.

This task also defines the backend contract required for each external MCP server's dedicated Web UI configuration page: current install status, pinned version, available update target, assigned agents, privacy tier, secrets/env requirements, enable/disable state, and rollback metadata.

---

## Architecture

```
faith/pa/
├── __init__.py
├── external_mcp.py          ← ExternalMCPManager + ExternalMCPServer (this task)
└── ...

containers/
└── mcp-runtime/             ← Project-scoped runtime image for external stdio MCP servers

faith/config/
├── __init__.py
├── models.py                ← Pydantic models (FAITH-003, extended here)
└── secrets.py               ← Secret resolution helper (this task)

.faith/tools/
├── external-github.yaml     ← Example: GitHub MCP server config
└── external-slack.yaml      ← Example: Slack MCP server config

config/
├── secrets.yaml             ← Framework-level credentials (never in agent containers)
└── .env                     ← Environment variables referenced by secrets.yaml
```

**Implementation note:** this file contains older exploratory examples for broader MCP source/transport support. The agreed v1 scope is narrower and takes precedence: registry/package references only, npm package-backed entries only, self-hosted MCP Registry resolution, and stdio transport only.

---

## Files to Create

### 1. `faith/config/secrets.py`

```python
"""Secret resolution for external MCP server environment variables.

Resolves `secret_ref` and `${VAR}` references against config/secrets.yaml
and config/.env. This module runs exclusively within the PA container —
agent containers never have access to secrets.

FRS Reference: Section 2.4, 4.11.2, 7.1.1
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("faith.config.secrets")

# Pattern for ${VAR_NAME} substitution
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class SecretResolver:
    """Resolves secret references and environment variable substitutions.

    Loads config/secrets.yaml once and resolves ${VAR} references against
    the process environment (which includes config/.env values loaded at
    PA container startup).

    Attributes:
        secrets_path: Path to the secrets.yaml file.
        _secrets: Parsed secrets dict (loaded lazily).
        _loaded: Whether secrets have been loaded.
    """

    def __init__(self, config_dir: Path):
        """Initialise the secret resolver.

        Args:
            config_dir: Path to the framework config/ directory
                (containing secrets.yaml and .env).
        """
        self.secrets_path = config_dir / "secrets.yaml"
        self._secrets: dict[str, Any] = {}
        self._loaded = False

    def _load_secrets(self) -> None:
        """Load and parse secrets.yaml.

        Resolves ${VAR} references in secret values against the process
        environment. Called lazily on first access.
        """
        if self._loaded:
            return

        try:
            raw = self.secrets_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw) or {}
            self._secrets = self._resolve_env_vars_recursive(parsed)
            self._loaded = True
            logger.info(f"Loaded secrets from {self.secrets_path}")
        except FileNotFoundError:
            logger.warning(
                f"Secrets file not found at {self.secrets_path} — "
                f"external MCP servers requiring credentials will fail to start"
            )
            self._secrets = {}
            self._loaded = True
        except Exception as e:
            logger.error(f"Error loading secrets from {self.secrets_path}: {e}")
            self._secrets = {}
            self._loaded = True

    def _resolve_env_vars_recursive(self, obj: Any) -> Any:
        """Recursively resolve ${VAR} references in a parsed YAML structure.

        Args:
            obj: A parsed YAML value (str, dict, list, or scalar).

        Returns:
            The same structure with all ${VAR} references replaced
            by their environment variable values.
        """
        if isinstance(obj, str):
            return self._resolve_env_var_string(obj)
        elif isinstance(obj, dict):
            return {k: self._resolve_env_vars_recursive(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_env_vars_recursive(item) for item in obj]
        return obj

    def _resolve_env_var_string(self, value: str) -> str:
        """Resolve ${VAR} patterns in a single string.

        Args:
            value: A string potentially containing ${VAR} references.

        Returns:
            The string with all references resolved. Unresolvable
            references are left as-is and a warning is logged.
        """
        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            env_value = os.environ.get(var_name)
            if env_value is None:
                logger.warning(
                    f"Environment variable '{var_name}' not found — "
                    f"referenced in secrets.yaml but not set in config/.env"
                )
                return match.group(0)  # Leave unresolved
            return env_value

        return _ENV_VAR_PATTERN.sub(replacer, value)

    def resolve_env_block(self, env_config: dict[str, str]) -> dict[str, str]:
        """Resolve an external MCP server's env block.

        Each value in the env block may be:
        - A literal string (passed through as-is)
        - A ${VAR} reference (resolved from process environment)

        Args:
            env_config: The `env` dict from an external MCP server config.

        Returns:
            Dict with all values resolved to concrete strings.
            Unresolvable references remain as ${VAR} placeholders
            (the subprocess will see the literal string and fail,
            which is the correct behaviour — it surfaces the
            misconfiguration).
        """
        self._load_secrets()
        resolved: dict[str, str] = {}
        for key, value in env_config.items():
            if isinstance(value, str):
                resolved[key] = self._resolve_env_var_string(value)
            else:
                resolved[key] = str(value)
        return resolved

    def get_secret(self, secret_ref: str) -> Optional[dict[str, Any]]:
        """Look up a named secret block from secrets.yaml.

        Used by tools that reference credentials by key name
        (e.g. database.yaml's `secret_ref: prod-db`).

        Args:
            secret_ref: The key name in secrets.yaml.

        Returns:
            The resolved secret value (string or dict), or None
            if the key does not exist.
        """
        self._load_secrets()
        return self._secrets.get(secret_ref)

    def reload(self) -> None:
        """Force reload of secrets.yaml.

        Called by the config watcher (FAITH-004) when secrets.yaml
        changes on disk.
        """
        self._loaded = False
        self._secrets = {}
        self._load_secrets()
```

### 2. `faith/config/external_mcp_models.py`

```python
"""Pydantic models for external MCP server configuration.

Validates .faith/tools/external-*.yaml files against a strict schema.
Extends the configuration system from FAITH-003.

FRS Reference: Section 4.11.2, 7.5
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ExternalMCPServerConfig(BaseModel):
    """Configuration for a single external MCP server.

    Parsed from the `external_mcp.<name>` block in a
    .faith/tools/external-*.yaml file.

    Attributes:
        source_type: Where the MCP server comes from. In v1 this is
            always "registry".
        transport: MCP transport type. In v1 this is always "stdio".
        server: The executable command for stdio-launched servers.
        args: Arguments to pass to the server command.
        endpoint: Reserved for future remote transport support; unused in v1.
        env: Environment variables to set for locally launched servers.
            Values may contain ${VAR} references resolved at startup.
        privacy_tier: Minimum privacy profile required to use this
            server. One of: "public", "internal", "confidential".
        agents: List of agent IDs permitted to use this server.
            Empty list means no agents have access (server is
            registered but unused).
    """

    source_type: str = "registry"
    transport: str = "stdio"
    server: str = ""
    args: list[str] = Field(default_factory=list)
    endpoint: Optional[str] = None
    env: dict[str, str] = Field(default_factory=dict)
    privacy_tier: str = "internal"
    agents: list[str] = Field(default_factory=list)

    @field_validator("privacy_tier")
    @classmethod
    def validate_privacy_tier(cls, v: str) -> str:
        """Ensure privacy_tier is a valid profile name."""
        valid = {"public", "internal", "confidential"}
        if v not in valid:
            raise ValueError(
                f"Invalid privacy_tier '{v}'. Must be one of: {', '.join(sorted(valid))}"
            )
        return v

    @field_validator("server")
    @classmethod
    def validate_server_not_empty(cls, v: str) -> str:
        """Ensure the server command is not empty."""
        if not v.strip():
            raise ValueError("server must not be empty")
        return v


class ExternalMCPFileConfig(BaseModel):
    """Top-level schema for a .faith/tools/external-*.yaml file.

    Each file contains an `external_mcp` mapping of server name to
    server config. A single file may define multiple external servers.

    Example:
        external_mcp:
          github:
            source_type: registry
            transport: stdio
            server: npx
            args: ["-y", "@modelcontextprotocol/server-github"]
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
            privacy_tier: internal
            agents: [software-developer, qa-engineer]
    """

    external_mcp: dict[str, ExternalMCPServerConfig] = Field(default_factory=dict)
```

### 3. `faith/pa/external_mcp.py`

```python
"""External MCP server lifecycle management.

Handles registration, on-demand startup, privacy tier enforcement,
and session-scoped shutdown of external MCP servers. In v1, external
servers are registry-resolved stdio subprocesses launched within the
PA container/runtime.

FRS Reference: Section 4.11
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from faith.config.external_mcp_models import (
    ExternalMCPFileConfig,
    ExternalMCPServerConfig,
)
from faith.config.secrets import SecretResolver

logger = logging.getLogger("faith.pa.external_mcp")

# Privacy profile restrictiveness ordering (most permissive first).
# A server declared as "internal" cannot run when the active profile
# is "confidential" because confidential is more restrictive.
_PRIVACY_ORDER: dict[str, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
}


@dataclass
class ExternalMCPServer:
    """A running or registered external MCP server instance.

    Attributes:
        name: Unique server name (e.g. "github", "jira").
        config: Parsed server configuration.
        process: The subprocess if currently running, else None.
        resolved_env: Environment variables with secrets resolved.
        session_id: The session that started this server (for
            cleanup on session end).
    """

    name: str
    config: ExternalMCPServerConfig
    process: Optional[asyncio.subprocess.Process] = None
    resolved_env: dict[str, str] = field(default_factory=dict)
    session_id: Optional[str] = None

    @property
    def is_running(self) -> bool:
        """Check if the server subprocess is currently running."""
        return (
            self.process is not None
            and self.process.returncode is None
        )

    async def stop(self) -> None:
        """Stop the server subprocess gracefully.

        Sends SIGTERM, waits up to 5 seconds, then kills if
        still running.
        """
        if not self.is_running:
            return

        logger.info(f"Stopping external MCP server '{self.name}'")
        try:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    f"External MCP server '{self.name}' did not stop within "
                    f"5s — sending SIGKILL"
                )
                self.process.kill()
                await self.process.wait()
        except ProcessLookupError:
            pass  # Already exited
        except Exception as e:
            logger.error(
                f"Error stopping external MCP server '{self.name}': {e}"
            )
        finally:
            self.process = None
            self.session_id = None
            logger.info(f"External MCP server '{self.name}' stopped")


class ExternalMCPManager:
    """Manages the full lifecycle of external MCP servers.

    Responsibilities:
    - Load and validate external MCP config files from .faith/tools/
    - Enforce privacy tier restrictions against the active profile
    - Start servers on demand when a session requires them
    - Stop servers when their session ends
    - Provide server info to the MCP adapter layer (FAITH-012)

    Attributes:
        faith_dir: Path to the project's .faith directory.
        secret_resolver: Resolver for ${VAR} references in env blocks.
        active_privacy_profile: The current project privacy profile.
        _servers: Registry of all known external MCP servers.
    """

    def __init__(
        self,
        faith_dir: Path,
        secret_resolver: SecretResolver,
        active_privacy_profile: str = "internal",
    ):
        """Initialise the external MCP manager.

        Args:
            faith_dir: Path to the project's .faith directory.
            secret_resolver: SecretResolver instance (from PA).
            active_privacy_profile: The project's active privacy
                profile from .faith/system.yaml.
        """
        self.faith_dir = faith_dir
        self.secret_resolver = secret_resolver
        self.active_privacy_profile = active_privacy_profile
        self._servers: dict[str, ExternalMCPServer] = {}

    def load_configs(self) -> int:
        """Load all external MCP server configs from .faith/tools/.

        Scans for files matching `external-*.yaml` in the tools
        directory. Each file is validated against the Pydantic schema.
        Invalid files are logged and skipped.

        Returns:
            Number of servers successfully registered.
        """
        tools_dir = self.faith_dir / "tools"
        if not tools_dir.is_dir():
            logger.debug(f"Tools directory not found: {tools_dir}")
            return 0

        count = 0
        for config_path in sorted(tools_dir.glob("external-*.yaml")):
            try:
                raw = config_path.read_text(encoding="utf-8")
                parsed = yaml.safe_load(raw) or {}
                file_config = ExternalMCPFileConfig(**parsed)

                for name, server_config in file_config.external_mcp.items():
                    if name in self._servers:
                        logger.warning(
                            f"Duplicate external MCP server name '{name}' "
                            f"in {config_path.name} — skipping (already "
                            f"registered)"
                        )
                        continue

                    self._servers[name] = ExternalMCPServer(
                        name=name,
                        config=server_config,
                    )
                    count += 1
                    logger.info(
                        f"Registered external MCP server '{name}' "
                        f"from {config_path.name} "
                        f"(privacy_tier={server_config.privacy_tier}, "
                        f"agents={server_config.agents})"
                    )

            except Exception as e:
                logger.error(
                    f"Failed to load external MCP config from "
                    f"{config_path.name}: {e}"
                )

        logger.info(
            f"Loaded {count} external MCP server(s) from "
            f"{tools_dir}"
        )
        return count

    def is_privacy_allowed(self, server_name: str) -> bool:
        """Check if a server is allowed under the active privacy profile.

        A server is allowed if the active privacy profile is equally or
        less restrictive than the server's declared privacy_tier.

        For example:
        - Server tier "internal", active profile "public" → allowed
            (public is less restrictive than internal)
        - Server tier "internal", active profile "internal" → allowed
        - Server tier "internal", active profile "confidential" → blocked
            (confidential is more restrictive than internal)

        Args:
            server_name: The registered server name.

        Returns:
            True if the server is permitted, False otherwise.
        """
        server = self._servers.get(server_name)
        if server is None:
            return False

        active_level = _PRIVACY_ORDER.get(self.active_privacy_profile, 2)
        server_level = _PRIVACY_ORDER.get(server.config.privacy_tier, 1)

        return active_level <= server_level

    def get_servers_for_agent(self, agent_id: str) -> list[str]:
        """Return the names of external MCP servers an agent can access.

        Filters by both agent permission and privacy tier.

        Args:
            agent_id: The agent identifier.

        Returns:
            List of server names the agent is permitted to use.
        """
        result = []
        for name, server in self._servers.items():
            if agent_id not in server.config.agents:
                continue
            if not self.is_privacy_allowed(name):
                logger.debug(
                    f"Server '{name}' skipped for agent '{agent_id}' — "
                    f"privacy tier '{server.config.privacy_tier}' not "
                    f"allowed under profile '{self.active_privacy_profile}'"
                )
                continue
            result.append(name)
        return result

    async def start_server(
        self, server_name: str, session_id: str
    ) -> bool:
        """Start an external MCP server subprocess.

        Resolves environment variables, spawns the subprocess with
        stdio transport, and records the session association.

        Args:
            server_name: The registered server name.
            session_id: The session requesting the server.

        Returns:
            True if the server started successfully, False otherwise.
        """
        server = self._servers.get(server_name)
        if server is None:
            logger.error(
                f"Cannot start unknown external MCP server '{server_name}'"
            )
            return False

        if server.is_running:
            logger.debug(
                f"External MCP server '{server_name}' already running "
                f"(session={server.session_id})"
            )
            return True

        if not self.is_privacy_allowed(server_name):
            logger.warning(
                f"Cannot start external MCP server '{server_name}' — "
                f"privacy tier '{server.config.privacy_tier}' not "
                f"allowed under active profile "
                f"'{self.active_privacy_profile}'"
            )
            return False

        # Resolve environment variables from secrets
        try:
            server.resolved_env = self.secret_resolver.resolve_env_block(
                server.config.env
            )
        except Exception as e:
            logger.error(
                f"Failed to resolve environment variables for "
                f"external MCP server '{server_name}': {e}"
            )
            return False

        # Build subprocess environment: inherit current env + server-specific vars
        import os

        subprocess_env = os.environ.copy()
        subprocess_env.update(server.resolved_env)

        # Build the command
        cmd = [server.config.server] + server.config.args

        logger.info(
            f"Starting external MCP server '{server_name}': "
            f"{' '.join(cmd)}"
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=subprocess_env,
            )
            server.process = process
            server.session_id = session_id

            logger.info(
                f"External MCP server '{server_name}' started "
                f"(pid={process.pid}, session={session_id})"
            )
            return True

        except FileNotFoundError:
            logger.error(
                f"Command not found for external MCP server "
                f"'{server_name}': {server.config.server}. "
                f"Ensure the command is installed in the PA container."
            )
            return False
        except Exception as e:
            logger.error(
                f"Failed to start external MCP server "
                f"'{server_name}': {e}"
            )
            return False

    async def start_servers_for_session(
        self, agent_ids: list[str], session_id: str
    ) -> dict[str, bool]:
        """Start all external MCP servers needed for a session.

        Determines which servers are required based on the agents
        participating in the session, then starts each one.

        Args:
            agent_ids: List of agent IDs in the session.
            session_id: The session identifier.

        Returns:
            Dict mapping server name to start success (True/False).
        """
        needed: set[str] = set()
        for agent_id in agent_ids:
            needed.update(self.get_servers_for_agent(agent_id))

        results: dict[str, bool] = {}
        for server_name in sorted(needed):
            results[server_name] = await self.start_server(
                server_name, session_id
            )

        started = sum(1 for v in results.values() if v)
        logger.info(
            f"Session '{session_id}': started {started}/{len(results)} "
            f"external MCP server(s)"
        )
        return results

    async def stop_server(self, server_name: str) -> None:
        """Stop a specific external MCP server.

        Args:
            server_name: The registered server name.
        """
        server = self._servers.get(server_name)
        if server is not None:
            await server.stop()

    async def stop_session_servers(self, session_id: str) -> int:
        """Stop all external MCP servers associated with a session.

        Called when a session ends. Only stops servers whose
        session_id matches.

        Args:
            session_id: The session identifier.

        Returns:
            Number of servers stopped.
        """
        count = 0
        for server in self._servers.values():
            if server.session_id == session_id and server.is_running:
                await server.stop()
                count += 1

        logger.info(
            f"Session '{session_id}': stopped {count} external "
            f"MCP server(s)"
        )
        return count

    async def stop_all(self) -> None:
        """Stop all running external MCP servers.

        Called during PA shutdown.
        """
        for server in self._servers.values():
            if server.is_running:
                await server.stop()

    def get_server(self, server_name: str) -> Optional[ExternalMCPServer]:
        """Get a registered server by name.

        Used by the MCP adapter layer (FAITH-012) to obtain the
        subprocess stdio handles for MCP communication.

        Args:
            server_name: The registered server name.

        Returns:
            The ExternalMCPServer instance, or None if not registered.
        """
        return self._servers.get(server_name)

    def get_server_stdio(
        self, server_name: str
    ) -> Optional[tuple[asyncio.StreamWriter, asyncio.StreamReader]]:
        """Get the stdio handles for a running external MCP server.

        The MCP adapter layer (FAITH-012) uses these handles to
        communicate with the external server via the MCP stdio
        transport protocol.

        Args:
            server_name: The registered server name.

        Returns:
            Tuple of (stdin writer, stdout reader) if the server is
            running, None otherwise.
        """
        server = self._servers.get(server_name)
        if server is None or not server.is_running:
            return None
        return (server.process.stdin, server.process.stdout)

    def list_servers(self) -> list[dict[str, Any]]:
        """Return a summary of all registered external MCP servers.

        Used by the Web UI status panel and diagnostic commands.

        Returns:
            List of dicts with server name, status, privacy_tier,
            agents, and session_id.
        """
        result = []
        for name, server in sorted(self._servers.items()):
            privacy_allowed = self.is_privacy_allowed(name)
            result.append({
                "name": name,
                "running": server.is_running,
                "privacy_tier": server.config.privacy_tier,
                "privacy_allowed": privacy_allowed,
                "agents": server.config.agents,
                "session_id": server.session_id,
                "pid": (
                    server.process.pid
                    if server.is_running
                    else None
                ),
            })
        return result

    def reload_configs(self) -> int:
        """Reload external MCP configs from disk.

        Called by the config watcher (FAITH-004) when files in
        .faith/tools/ change. Running servers are not affected —
        only new or modified registrations are picked up. Servers
        removed from config are not stopped until their session ends.

        Returns:
            Number of servers registered after reload.
        """
        # Preserve running servers
        running: dict[str, ExternalMCPServer] = {
            name: server
            for name, server in self._servers.items()
            if server.is_running
        }

        self._servers.clear()
        count = self.load_configs()

        # Restore running server state
        for name, server in running.items():
            if name in self._servers:
                self._servers[name].process = server.process
                self._servers[name].session_id = server.session_id
                self._servers[name].resolved_env = server.resolved_env
            else:
                # Server removed from config but still running —
                # keep it alive until session ends
                self._servers[name] = server
                logger.info(
                    f"External MCP server '{name}' removed from config "
                    f"but still running — will stop when session "
                    f"'{server.session_id}' ends"
                )

        return count
```

---

## Example Config Files

### `.faith/tools/external-github.yaml`

```yaml
external_mcp:
  github:
    server: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}
    privacy_tier: internal
    agents: [software-developer, qa-engineer, security-expert]
```

### `.faith/tools/external-jira.yaml`

```yaml
external_mcp:
  jira:
    server: npx
    args: ["-y", "@modelcontextprotocol/server-atlassian"]
    env:
      ATLASSIAN_URL: ${ATLASSIAN_URL}
      ATLASSIAN_USERNAME: ${ATLASSIAN_USERNAME}
      ATLASSIAN_API_TOKEN: ${ATLASSIAN_API_TOKEN}
    privacy_tier: internal
    agents: [fds-architect, sys-architect, pa]
```

### `.faith/tools/external-slack.yaml`

```yaml
external_mcp:
  slack:
    server: npx
    args: ["-y", "@modelcontextprotocol/server-slack"]
    env:
      SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN}
    privacy_tier: internal
    agents: [pa]
```

---

## Tests

### `tests/test_external_mcp.py`

```python
"""Tests for external MCP server registration, lifecycle, and privacy enforcement.

Covers: config loading, Pydantic validation, secret resolution, privacy tier
checks, on-demand startup, session-scoped shutdown, agent access filtering,
config reload with running servers, and error handling.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from faith.config.external_mcp_models import (
    ExternalMCPFileConfig,
    ExternalMCPServerConfig,
)
from faith.config.secrets import SecretResolver
from faith.pa.external_mcp import (
    ExternalMCPManager,
    ExternalMCPServer,
)


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def tmp_faith_dir(tmp_path):
    """Create a minimal .faith directory with tools/ subdirectory."""
    faith_dir = tmp_path / ".faith"
    tools_dir = faith_dir / "tools"
    tools_dir.mkdir(parents=True)
    return faith_dir


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a minimal config/ directory with secrets.yaml."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    secrets = {
        "github_token": "ghp_test123",
        "atlassian_url": "https://test.atlassian.net",
        "atlassian_username": "user@test.com",
        "atlassian_api_token": "atl_test456",
    }
    (config_dir / "secrets.yaml").write_text(
        yaml.dump(secrets), encoding="utf-8"
    )
    return config_dir


@pytest.fixture
def secret_resolver(tmp_config_dir):
    """Create a SecretResolver with test secrets."""
    return SecretResolver(tmp_config_dir)


@pytest.fixture
def github_config_yaml():
    """Return a valid GitHub external MCP config as a dict."""
    return {
        "external_mcp": {
            "github": {
                "server": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
                "privacy_tier": "internal",
                "agents": ["software-developer", "qa-engineer"],
            }
        }
    }


@pytest.fixture
def write_github_config(tmp_faith_dir, github_config_yaml):
    """Write a GitHub external MCP config file to .faith/tools/."""
    path = tmp_faith_dir / "tools" / "external-github.yaml"
    path.write_text(yaml.dump(github_config_yaml), encoding="utf-8")
    return path


@pytest.fixture
def manager(tmp_faith_dir, secret_resolver):
    """Create an ExternalMCPManager with internal privacy profile."""
    return ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        active_privacy_profile="internal",
    )


# ──────────────────────────────────────────────────
# Pydantic model validation tests
# ──────────────────────────────────────────────────


def test_valid_server_config():
    """A well-formed server config parses without error."""
    config = ExternalMCPServerConfig(
        server="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
        privacy_tier="internal",
        agents=["software-developer"],
    )
    assert config.server == "npx"
    assert config.privacy_tier == "internal"
    assert len(config.agents) == 1


def test_invalid_privacy_tier_rejected():
    """An invalid privacy_tier raises a validation error."""
    with pytest.raises(Exception):  # Pydantic ValidationError
        ExternalMCPServerConfig(
            server="npx",
            privacy_tier="secret",
        )


def test_empty_server_rejected():
    """An empty server command raises a validation error."""
    with pytest.raises(Exception):
        ExternalMCPServerConfig(
            server="   ",
            privacy_tier="internal",
        )


def test_file_config_parses_multiple_servers():
    """A file config with multiple servers parses correctly."""
    config = ExternalMCPFileConfig(
        external_mcp={
            "github": ExternalMCPServerConfig(
                server="npx",
                args=["-y", "@modelcontextprotocol/server-github"],
                privacy_tier="internal",
                agents=["dev"],
            ),
            "slack": ExternalMCPServerConfig(
                server="npx",
                args=["-y", "@modelcontextprotocol/server-slack"],
                privacy_tier="internal",
                agents=["pa"],
            ),
        }
    )
    assert len(config.external_mcp) == 2


def test_defaults_applied():
    """Missing optional fields use defaults."""
    config = ExternalMCPServerConfig(server="npx")
    assert config.args == []
    assert config.env == {}
    assert config.privacy_tier == "internal"
    assert config.agents == []


# ──────────────────────────────────────────────────
# Secret resolution tests
# ──────────────────────────────────────────────────


def test_resolve_env_var_from_environment(secret_resolver):
    """${VAR} references are resolved from process environment."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_real_token"}):
        resolved = secret_resolver.resolve_env_block(
            {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
        )
    assert resolved["GITHUB_TOKEN"] == "ghp_real_token"


def test_unresolvable_env_var_left_as_placeholder(secret_resolver):
    """Unresolvable ${VAR} references are left as-is."""
    with patch.dict(os.environ, {}, clear=True):
        resolved = secret_resolver.resolve_env_block(
            {"MISSING_KEY": "${NONEXISTENT_VAR}"}
        )
    assert resolved["MISSING_KEY"] == "${NONEXISTENT_VAR}"


def test_literal_values_passed_through(secret_resolver):
    """Literal string values are passed through unchanged."""
    resolved = secret_resolver.resolve_env_block(
        {"BASE_URL": "https://api.example.com"}
    )
    assert resolved["BASE_URL"] == "https://api.example.com"


def test_missing_secrets_file_logs_warning(tmp_path):
    """Missing secrets.yaml logs a warning but does not crash."""
    resolver = SecretResolver(tmp_path / "nonexistent")
    resolved = resolver.resolve_env_block({"KEY": "value"})
    assert resolved["KEY"] == "value"


def test_secret_resolver_reload(tmp_config_dir):
    """reload() forces re-reading of secrets.yaml."""
    resolver = SecretResolver(tmp_config_dir)
    # Trigger initial load
    resolver.resolve_env_block({})
    assert resolver._loaded is True
    # Reload
    resolver.reload()
    assert resolver._loaded is True  # Re-loaded


# ──────────────────────────────────────────────────
# Config loading tests
# ──────────────────────────────────────────────────


def test_load_configs_from_disk(manager, write_github_config):
    """load_configs() discovers and parses external-*.yaml files."""
    count = manager.load_configs()
    assert count == 1
    assert "github" in manager._servers


def test_load_configs_skips_invalid_yaml(manager, tmp_faith_dir):
    """Invalid YAML files are skipped with an error log."""
    bad_path = tmp_faith_dir / "tools" / "external-broken.yaml"
    bad_path.write_text("external_mcp:\n  bad:\n    server: 123\n    privacy_tier: invalid_tier", encoding="utf-8")
    count = manager.load_configs()
    assert count == 0


def test_load_configs_no_tools_dir(tmp_path, secret_resolver):
    """Missing tools/ directory returns 0 servers."""
    mgr = ExternalMCPManager(
        faith_dir=tmp_path / "no-faith",
        secret_resolver=secret_resolver,
    )
    assert mgr.load_configs() == 0


def test_duplicate_server_name_skipped(manager, tmp_faith_dir, github_config_yaml):
    """Duplicate server names across files are skipped."""
    # Write same config to two files
    for name in ("external-github.yaml", "external-github2.yaml"):
        path = tmp_faith_dir / "tools" / name
        path.write_text(yaml.dump(github_config_yaml), encoding="utf-8")
    count = manager.load_configs()
    assert count == 1  # Second file's "github" is skipped


# ──────────────────────────────────────────────────
# Privacy tier enforcement tests
# ──────────────────────────────────────────────────


def test_privacy_allows_same_tier(manager, write_github_config):
    """Server with tier=internal is allowed when profile=internal."""
    manager.load_configs()
    assert manager.is_privacy_allowed("github") is True


def test_privacy_allows_less_restrictive_profile(
    tmp_faith_dir, secret_resolver, write_github_config
):
    """Server with tier=internal is allowed when profile=public."""
    mgr = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        active_privacy_profile="public",
    )
    mgr.load_configs()
    assert mgr.is_privacy_allowed("github") is True


def test_privacy_blocks_more_restrictive_profile(
    tmp_faith_dir, secret_resolver, write_github_config
):
    """Server with tier=internal is blocked when profile=confidential."""
    mgr = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        active_privacy_profile="confidential",
    )
    mgr.load_configs()
    assert mgr.is_privacy_allowed("github") is False


def test_privacy_unknown_server_returns_false(manager):
    """Unknown server name returns False for privacy check."""
    assert manager.is_privacy_allowed("nonexistent") is False


# ──────────────────────────────────────────────────
# Agent access filtering tests
# ──────────────────────────────────────────────────


def test_get_servers_for_permitted_agent(manager, write_github_config):
    """Agent listed in config.agents can access the server."""
    manager.load_configs()
    servers = manager.get_servers_for_agent("software-developer")
    assert "github" in servers


def test_get_servers_excludes_unpermitted_agent(manager, write_github_config):
    """Agent not listed in config.agents cannot access the server."""
    manager.load_configs()
    servers = manager.get_servers_for_agent("pa")
    assert "github" not in servers


def test_get_servers_excludes_privacy_blocked(
    tmp_faith_dir, secret_resolver, write_github_config
):
    """Agent is excluded even if permitted when privacy blocks."""
    mgr = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        active_privacy_profile="confidential",
    )
    mgr.load_configs()
    servers = mgr.get_servers_for_agent("software-developer")
    assert servers == []


# ──────────────────────────────────────────────────
# Server lifecycle tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_server_success(manager, write_github_config):
    """start_server() spawns subprocess and records session."""
    manager.load_configs()

    mock_process = AsyncMock()
    mock_process.pid = 12345
    mock_process.returncode = None
    mock_process.stdin = AsyncMock()
    mock_process.stdout = AsyncMock()
    mock_process.stderr = AsyncMock()

    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await manager.start_server("github", "session-001")

    assert result is True
    server = manager.get_server("github")
    assert server.process is mock_process
    assert server.session_id == "session-001"


@pytest.mark.asyncio
async def test_start_server_already_running(manager, write_github_config):
    """start_server() returns True if server is already running."""
    manager.load_configs()

    mock_process = AsyncMock()
    mock_process.returncode = None
    manager._servers["github"].process = mock_process
    manager._servers["github"].session_id = "session-001"

    result = await manager.start_server("github", "session-002")
    assert result is True


@pytest.mark.asyncio
async def test_start_server_privacy_blocked(
    tmp_faith_dir, secret_resolver, write_github_config
):
    """start_server() returns False when privacy blocks."""
    mgr = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        active_privacy_profile="confidential",
    )
    mgr.load_configs()
    result = await mgr.start_server("github", "session-001")
    assert result is False


@pytest.mark.asyncio
async def test_start_server_unknown_name(manager):
    """start_server() returns False for unknown server name."""
    result = await manager.start_server("nonexistent", "session-001")
    assert result is False


@pytest.mark.asyncio
async def test_start_server_command_not_found(manager, write_github_config):
    """start_server() handles FileNotFoundError gracefully."""
    manager.load_configs()

    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("npx not found"),
        ):
            result = await manager.start_server("github", "session-001")

    assert result is False


@pytest.mark.asyncio
async def test_stop_server(manager, write_github_config):
    """stop_server() terminates the subprocess."""
    manager.load_configs()

    mock_process = AsyncMock()
    mock_process.returncode = None
    mock_process.terminate = MagicMock()
    mock_process.wait = AsyncMock()
    mock_process.kill = MagicMock()

    server = manager._servers["github"]
    server.process = mock_process
    server.session_id = "session-001"

    await manager.stop_server("github")

    mock_process.terminate.assert_called_once()
    assert server.process is None
    assert server.session_id is None


@pytest.mark.asyncio
async def test_stop_session_servers(manager, write_github_config):
    """stop_session_servers() stops only servers for the given session."""
    manager.load_configs()

    mock_process = AsyncMock()
    mock_process.returncode = None
    mock_process.terminate = MagicMock()
    mock_process.wait = AsyncMock()
    mock_process.kill = MagicMock()

    server = manager._servers["github"]
    server.process = mock_process
    server.session_id = "session-001"

    # Stop different session — should not stop our server
    count = await manager.stop_session_servers("session-999")
    assert count == 0
    assert server.is_running

    # Stop correct session
    count = await manager.stop_session_servers("session-001")
    assert count == 1


@pytest.mark.asyncio
async def test_start_servers_for_session(manager, write_github_config):
    """start_servers_for_session() starts servers needed by agents."""
    manager.load_configs()

    mock_process = AsyncMock()
    mock_process.pid = 12345
    mock_process.returncode = None
    mock_process.stdin = AsyncMock()
    mock_process.stdout = AsyncMock()
    mock_process.stderr = AsyncMock()

    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            results = await manager.start_servers_for_session(
                agent_ids=["software-developer", "qa-engineer"],
                session_id="session-001",
            )

    assert results == {"github": True}


# ──────────────────────────────────────────────────
# stdio handle access tests
# ──────────────────────────────────────────────────


def test_get_server_stdio_running(manager, write_github_config):
    """get_server_stdio() returns handles for a running server."""
    manager.load_configs()

    mock_process = MagicMock()
    mock_process.returncode = None
    mock_process.stdin = MagicMock()
    mock_process.stdout = MagicMock()

    manager._servers["github"].process = mock_process

    result = manager.get_server_stdio("github")
    assert result == (mock_process.stdin, mock_process.stdout)


def test_get_server_stdio_not_running(manager, write_github_config):
    """get_server_stdio() returns None when server is not running."""
    manager.load_configs()
    assert manager.get_server_stdio("github") is None


def test_get_server_stdio_unknown(manager):
    """get_server_stdio() returns None for unknown server."""
    assert manager.get_server_stdio("nonexistent") is None


# ──────────────────────────────────────────────────
# list_servers and reload tests
# ──────────────────────────────────────────────────


def test_list_servers(manager, write_github_config):
    """list_servers() returns summary dicts for all servers."""
    manager.load_configs()
    listing = manager.list_servers()
    assert len(listing) == 1
    assert listing[0]["name"] == "github"
    assert listing[0]["running"] is False
    assert listing[0]["privacy_allowed"] is True


def test_reload_preserves_running_servers(
    manager, tmp_faith_dir, write_github_config
):
    """reload_configs() preserves running server state."""
    manager.load_configs()

    mock_process = MagicMock()
    mock_process.returncode = None
    manager._servers["github"].process = mock_process
    manager._servers["github"].session_id = "session-001"

    count = manager.reload_configs()
    assert count == 1

    server = manager._servers["github"]
    assert server.process is mock_process
    assert server.session_id == "session-001"


@pytest.mark.asyncio
async def test_stop_all(manager, write_github_config):
    """stop_all() stops every running server."""
    manager.load_configs()

    mock_process = AsyncMock()
    mock_process.returncode = None
    mock_process.terminate = MagicMock()
    mock_process.wait = AsyncMock()
    mock_process.kill = MagicMock()

    manager._servers["github"].process = mock_process
    manager._servers["github"].session_id = "session-001"

    await manager.stop_all()
    assert manager._servers["github"].process is None
```

---

## Integration Points

The ExternalMCPManager integrates with several FAITH components:

```python
# PA session startup (FAITH-014/FAITH-015) — start external servers on demand
manager = ExternalMCPManager(
    faith_dir=Path(".faith"),
    secret_resolver=secret_resolver,
    active_privacy_profile=system_config["privacy_profile"],
)
manager.load_configs()

# When a session starts with specific agents:
results = await manager.start_servers_for_session(
    agent_ids=["software-developer", "qa-engineer"],
    session_id="session-abc123",
)
# results: {"github": True}

# MCP adapter layer (FAITH-012) — get stdio handles for communication
stdio = manager.get_server_stdio("github")
if stdio:
    stdin_writer, stdout_reader = stdio
    # Send MCP JSON-RPC messages via stdio transport...

# When session ends — clean up
await manager.stop_session_servers("session-abc123")

# PA shutdown — stop everything
await manager.stop_all()
```

```python
# Config watcher (FAITH-004) — reload on file change
# When .faith/tools/external-*.yaml changes:
manager.reload_configs()
# Running servers are preserved; new registrations are picked up

# Secret reload (FAITH-004) — when config/secrets.yaml changes:
secret_resolver.reload()
# Next server start will use updated credentials
```

---

## Acceptance Criteria

1. `ExternalMCPServerConfig` validates `source_type`, `transport`, and `privacy_tier` plus the launch/endpoint fields required by the selected transport.
2. `ExternalMCPFileConfig` parses the `external_mcp` mapping from YAML files, supporting multiple servers per file.
3. `SecretResolver` loads `config/secrets.yaml`, resolves `${VAR}` references from the process environment, and handles missing files gracefully.
4. `SecretResolver.resolve_env_block()` resolves all `${VAR}` references in an env dict, leaving unresolvable references as-is (surfacing misconfiguration when the subprocess fails).
5. `ExternalMCPManager.load_configs()` discovers all `external-*.yaml` files in `.faith/tools/`, validates each against the Pydantic schema, and registers servers. Invalid files are logged and skipped.
6. `ExternalMCPManager.is_privacy_allowed()` correctly compares the server's `privacy_tier` against the active privacy profile: a server is allowed only when the active profile is equally or less restrictive.
7. `ExternalMCPManager.get_servers_for_agent()` returns only servers where the agent is listed in `agents` AND the privacy tier is allowed.
8. `ExternalMCPManager.start_server()` handles local stdio launches for registry-resolved npm packages, recording the session association. Returns `False` if privacy-blocked, launch fails, registry resolution fails, or the server is unknown.
9. `ExternalMCPManager.start_servers_for_session()` determines which servers are needed based on the session's agent list and starts each one.
10. `ExternalMCPManager.stop_session_servers()` stops only servers associated with the given session ID. Other sessions' servers are unaffected.
11. `ExternalMCPServer.stop()` sends SIGTERM, waits up to 5 seconds, then SIGKILL if the process has not exited.
12. `ExternalMCPManager` exposes stdio pipes for locally launched registry-resolved servers.
13. `ExternalMCPManager.reload_configs()` re-reads config files from disk while preserving running server state (process handles and session associations survive the reload).
14. `ExternalMCPManager.stop_all()` stops every running server — called during PA shutdown.
15. All tests in `tests/test_external_mcp.py` pass, covering Pydantic validation, registry resolution, secret resolution, config loading, privacy enforcement, agent filtering, stdio server start/stop lifecycle, session scoping, config reload, and error handling.

---

## Notes for Implementer

- **V1 scope is intentionally narrow**: registry/package references only, npm package-backed entries only, self-hosted MCP Registry resolution, and `stdio` only. Defer git/local-path/ZIP sources and `streamable-http`.
- **Runtime form in v1**: registry-resolved stdio servers run as subprocesses within the PA container/runtime after metadata resolution through the self-hosted MCP Registry.
- **Environment inheritance**: The subprocess inherits the PA container's full environment plus the server-specific env vars. This ensures `npx`, `node`, and other tools installed in the PA container are available.
- **Secret security**: `SecretResolver` runs only in the PA container. Agent containers never have access to `config/secrets.yaml` or `config/.env`. The PA resolves secrets and passes them as environment variables to the external MCP subprocess.
- **Config file naming convention**: External MCP config files must match `external-*.yaml` in `.faith/tools/`. This distinguishes them from built-in tool configs (e.g. `filesystem.yaml`, `database.yaml`) and allows the config watcher to filter file change events efficiently.
- **Graceful degradation**: If an external MCP server fails to start (command not found, invalid credentials, etc.), the session continues without it. The PA logs the failure and agents that need the server will receive an error when they attempt to use it — they can adapt their approach or report the issue.
- **Config watcher integration**: FAITH-004's file watcher will detect changes to `external-*.yaml` files and call `reload_configs()`. Running servers are not restarted — only the registration table is updated. New servers become available for future sessions. To apply credential changes to a running server, the session must be restarted.
- **Privacy profile from system.yaml**: The `active_privacy_profile` is read from `.faith/system.yaml` (set during the first-run wizard, FAITH-049). When the profile changes mid-session (via FAITH-004 hot-reload), the PA should re-evaluate which external servers are permitted and stop any that are no longer allowed.
- **MCP adapter transparency**: The MCP adapter layer (FAITH-012) handles external MCP servers identically to built-in ones. When an agent makes a `tool_call` request, the PA routes it to the appropriate stdio session. The agent does not know (or need to know) whether the tool is built-in or external.

