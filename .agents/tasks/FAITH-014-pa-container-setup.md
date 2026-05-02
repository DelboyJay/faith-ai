# FAITH-014 — PA Container Setup & Docker SDK Integration

**Phase:** 4 — PA Core
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-001, FAITH-002, FAITH-010
**FRS Reference:** Section 4.6.1, 4.6.2

---

## Objective

Build the Project Agent's container orchestration layer. The PA is responsible for the full lifecycle of all project-scoped agent containers, disposable sandbox containers, FAITH-owned tool containers, and the project-scoped `mcp-runtime` container used for external MCP servers. It uses the Docker Python SDK (via a mounted Docker socket) to start, stop, restart, list, and network-attach containers. After project config validation succeeds, the PA discovers agents from `.faith/agents/*/config.yaml` and tools from `.faith/tools/*.yaml`, resolves credentials via `secret_ref` from `config/secrets.yaml` (with `${VAR}` substitution from `.env`), and brings up the required project-scoped runtimes on the shared `maf-network`. The PA must never mount the Docker socket into agent, tool, or sandbox containers; sandbox containers must not run in privileged mode, must not use host networking, and must receive only approved host mounts plus the minimum required Linux capabilities.

This task also produces the PA Dockerfile (`containers/pa/Dockerfile`) and a `SecretResolver` utility that the PA uses to inject credentials into tool runtimes as environment variables. The PA consumes config/protocol/API contracts from the `faith_shared` package (`src/faith_shared/`); it should not redefine shared schemas locally.

---

## Architecture

```
src/faith_pa/
├── __init__.py
├── container_manager.py   ← ContainerManager class (this task)
└── secret_resolver.py     ← SecretResolver class (this task)

containers/pa/
└── Dockerfile             ← PA container definition (this task)

tests/
├── test_container_manager.py
├── test_secret_resolver.py
└── test_sandbox_container_integration.py
```

---

## Files to Create

### 1. `faith/pa/secret_resolver.py`

```python
"""Secret resolution for FAITH container orchestration.

Loads config/secrets.yaml once and resolves `secret_ref` keys found in
tool configuration files. Supports ${VAR} substitution from environment
variables (loaded from config/.env via dotenv).

FRS Reference: Section 2.4.1, 4.6.1
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("faith.pa.secret_resolver")

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


class SecretResolver:
    """Resolves secret_ref keys in tool configs against secrets.yaml.

    The resolution flow:
    1. Load config/secrets.yaml from the framework installation directory.
    2. Apply ${VAR} substitution from environment variables (which may
       be loaded from config/.env by the caller or Docker).
    3. When a tool config contains a `secret_ref` key, look up the
       referenced path in the resolved secrets dict and return the
       credential values.

    Attributes:
        secrets: The fully resolved secrets dictionary.
    """

    def __init__(self, config_dir: Path):
        """Initialise the resolver from the framework config directory.

        Args:
            config_dir: Path to the framework-level config/ directory
                containing secrets.yaml and optionally .env.
        """
        self._config_dir = config_dir
        self._load_dotenv()
        self.secrets = self._load_secrets()

    def _load_dotenv(self) -> None:
        """Load config/.env into the process environment if it exists.

        Does not override variables that are already set — existing
        environment takes precedence over .env values.
        """
        env_path = self._config_dir / ".env"
        if not env_path.is_file():
            logger.debug(f".env not found at {env_path} — skipping")
            return

        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue

                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")

                if key not in os.environ:
                    os.environ[key] = value
                    logger.debug(f"Loaded env var from .env: {key}")
        except Exception as e:
            logger.warning(f"Failed to load .env from {env_path}: {e}")

    def _load_secrets(self) -> dict[str, Any]:
        """Load and resolve config/secrets.yaml.

        All ${VAR} placeholders are replaced with their corresponding
        environment variable values. Unresolvable variables are replaced
        with empty strings and logged as warnings.

        Returns:
            Fully resolved secrets dictionary.
        """
        secrets_path = self._config_dir / "secrets.yaml"
        if not secrets_path.is_file():
            logger.warning(
                f"secrets.yaml not found at {secrets_path} — "
                f"no secrets available for resolution"
            )
            return {}

        try:
            raw = secrets_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read secrets.yaml: {e}")
            return {}

        # Resolve ${VAR} references before YAML parsing
        resolved = self._substitute_env_vars(raw)

        try:
            secrets = yaml.safe_load(resolved) or {}
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse secrets.yaml: {e}")
            return {}

        logger.info(
            f"Loaded secrets.yaml with {len(secrets)} top-level keys"
        )
        return secrets

    def _substitute_env_vars(self, text: str) -> str:
        """Replace ${VAR} placeholders with environment variable values.

        Args:
            text: Raw text containing ${VAR} placeholders.

        Returns:
            Text with all placeholders resolved.
        """
        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            value = os.environ.get(var_name)
            if value is None:
                logger.warning(
                    f"Environment variable '{var_name}' referenced in "
                    f"secrets.yaml is not set — using empty string"
                )
                return ""
            return value

        return _ENV_VAR_PATTERN.sub(_replace, text)

    def resolve_secret_ref(self, secret_ref: str) -> Optional[dict[str, Any]]:
        """Resolve a secret_ref key to its credential values.

        Tool configs use `secret_ref: <key>` to reference a named
        entry in secrets.yaml. For example:
            secret_ref: prod-db  ->  secrets["databases"]["prod-db"]
            secret_ref: confluence  ->  secrets["confluence"]

        The resolver searches:
        1. Top-level keys in secrets.yaml directly.
        2. Nested keys under known grouping keys (databases, services).

        Args:
            secret_ref: The reference key from the tool config.

        Returns:
            Dict of resolved credential key-value pairs, or None if
            the reference cannot be resolved.
        """
        # Direct top-level match
        if secret_ref in self.secrets:
            value = self.secrets[secret_ref]
            if isinstance(value, dict):
                return value
            # Scalar value — wrap it
            return {"value": value}

        # Search nested grouping keys
        for group_key in ("databases", "services", "credentials"):
            group = self.secrets.get(group_key)
            if isinstance(group, dict) and secret_ref in group:
                value = group[secret_ref]
                if isinstance(value, dict):
                    return value
                return {"value": value}

        logger.warning(
            f"secret_ref '{secret_ref}' not found in secrets.yaml"
        )
        return None

    def resolve_tool_config(
        self, tool_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve all secret_ref entries in a tool configuration.

        Walks the tool config dict. For any dict that contains a
        `secret_ref` key, resolves it and merges the credential
        values into that dict (removing the secret_ref key).

        Args:
            tool_config: The parsed tool YAML config.

        Returns:
            A new dict with all secret_ref entries resolved.
        """
        return self._resolve_recursive(tool_config)

    def _resolve_recursive(self, obj: Any) -> Any:
        """Recursively resolve secret_ref entries in a nested structure."""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                result[key] = self._resolve_recursive(value)

            # If this dict has a secret_ref, resolve and merge
            if "secret_ref" in result:
                ref = result.pop("secret_ref")
                credentials = self.resolve_secret_ref(ref)
                if credentials:
                    # Merge credentials, but don't overwrite explicit values
                    for cred_key, cred_value in credentials.items():
                        if cred_key not in result:
                            result[cred_key] = cred_value
                else:
                    logger.warning(
                        f"Could not resolve secret_ref '{ref}' — "
                        f"credentials will be missing"
                    )
            return result

        if isinstance(obj, list):
            return [self._resolve_recursive(item) for item in obj]

        return obj

    def build_env_dict(self, secret_ref: str) -> dict[str, str]:
        """Build an environment variable dict from a secret_ref.

        Converts resolved credentials into uppercase environment
        variable names suitable for passing to Docker containers.

        Example:
            secret_ref "prod-db" resolving to
            {"host": "db.example.com", "password": "secret"}
            becomes
            {"DB_HOST": "db.example.com", "DB_PASSWORD": "secret"}

        Args:
            secret_ref: The reference key from the tool config.

        Returns:
            Dict of environment variable name -> value pairs.
        """
        credentials = self.resolve_secret_ref(secret_ref)
        if not credentials:
            return {}

        env = {}
        for key, value in credentials.items():
            env_key = key.upper()
            env[env_key] = str(value)
        return env
```

### 2. `faith/pa/container_manager.py`

```python
"""Docker container lifecycle management for the FAITH PA.

Uses the Docker Python SDK to start, stop, restart, list, and
network-attach containers. The PA is the sole orchestrator — all
agent and tool containers are managed through this class.

FRS Reference: Section 4.6.1, 4.6.2
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import docker
import docker.errors
import yaml

from faith.pa.secret_resolver import SecretResolver
from faith.protocol.events import EventPublisher, EventType

logger = logging.getLogger("faith.pa.container_manager")

# Label applied to all containers managed by FAITH
FAITH_LABEL = "faith.managed"
FAITH_AGENT_LABEL = "faith.agent"
FAITH_TOOL_LABEL = "faith.tool"
NETWORK_NAME = "maf-network"
AGENT_BASE_IMAGE = "faith-agent-base:latest"


class ContainerManager:
    """Manages Docker container lifecycles for FAITH agents and tools.

    On startup, discovers agents from .faith/agents/*/config.yaml and
    tools from .faith/tools/*.yaml. Starts containers, attaches them
    to maf-network, and injects resolved credentials as environment
    variables.

    Attributes:
        docker_client: The Docker SDK client.
        secret_resolver: Resolver for secret_ref credentials.
        event_publisher: EventPublisher for lifecycle events.
        faith_dir: Path to the project's .faith directory.
    """

    def __init__(
        self,
        faith_dir: Path,
        config_dir: Path,
        event_publisher: EventPublisher,
        docker_client: Optional[docker.DockerClient] = None,
    ):
        """Initialise the container manager.

        Args:
            faith_dir: Path to the project's .faith directory.
            config_dir: Path to the framework-level config/ directory.
            event_publisher: EventPublisher for system-events.
            docker_client: Optional Docker client (defaults to
                connecting via unix socket).
        """
        self.faith_dir = faith_dir
        self.config_dir = config_dir
        self.event_publisher = event_publisher

        self.docker_client = docker_client or docker.from_env()
        self.secret_resolver = SecretResolver(config_dir)

        # Track managed containers: name -> container object
        self._managed: dict[str, docker.models.containers.Container] = {}

    # ──────────────────────────────────────────────────
    # Network management
    # ──────────────────────────────────────────────────

    def ensure_network(self) -> docker.models.networks.Network:
        """Ensure the maf-network Docker network exists.

        Creates it if it does not exist. Returns the network object.

        Returns:
            The maf-network Network object.
        """
        try:
            network = self.docker_client.networks.get(NETWORK_NAME)
            logger.debug(f"Network '{NETWORK_NAME}' already exists")
            return network
        except docker.errors.NotFound:
            logger.info(f"Creating Docker network '{NETWORK_NAME}'")
            network = self.docker_client.networks.create(
                NETWORK_NAME, driver="bridge"
            )
            return network

    def attach_to_network(
        self, container_name: str
    ) -> None:
        """Attach a running container to maf-network.

        No-op if the container is already attached.

        Args:
            container_name: Name of the container to attach.

        Raises:
            docker.errors.NotFound: If the container does not exist.
        """
        network = self.ensure_network()
        container = self.docker_client.containers.get(container_name)

        # Check if already attached
        container.reload()
        attached_networks = container.attrs.get(
            "NetworkSettings", {}
        ).get("Networks", {})

        if NETWORK_NAME in attached_networks:
            logger.debug(
                f"Container '{container_name}' already on "
                f"'{NETWORK_NAME}'"
            )
            return

        network.connect(container)
        logger.info(
            f"Attached container '{container_name}' to "
            f"'{NETWORK_NAME}'"
        )

    # ──────────────────────────────────────────────────
    # Discovery
    # ──────────────────────────────────────────────────

    def discover_agents(self) -> dict[str, dict[str, Any]]:
        """Discover agent configurations from .faith/agents/*/config.yaml.

        Returns:
            Dict mapping agent_id to parsed config dict.
        """
        agents_dir = self.faith_dir / "agents"
        agents: dict[str, dict[str, Any]] = {}

        if not agents_dir.is_dir():
            logger.warning(
                f"Agents directory not found: {agents_dir}"
            )
            return agents

        for agent_dir in sorted(agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue

            config_path = agent_dir / "config.yaml"
            if not config_path.is_file():
                logger.warning(
                    f"Agent directory '{agent_dir.name}' has no "
                    f"config.yaml — skipping"
                )
                continue

            try:
                config = yaml.safe_load(
                    config_path.read_text(encoding="utf-8")
                ) or {}
                agents[agent_dir.name] = config
                logger.info(
                    f"Discovered agent: {agent_dir.name} "
                    f"(model: {config.get('model', 'default')})"
                )
            except Exception as e:
                logger.error(
                    f"Failed to load agent config "
                    f"{config_path}: {e}"
                )

        return agents

    def discover_tools(self) -> dict[str, dict[str, Any]]:
        """Discover tool configurations from .faith/tools/*.yaml.

        Returns:
            Dict mapping tool name (stem of yaml file) to parsed
            and secret-resolved config dict.
        """
        tools_dir = self.faith_dir / "tools"
        tools: dict[str, dict[str, Any]] = {}

        if not tools_dir.is_dir():
            logger.warning(
                f"Tools directory not found: {tools_dir}"
            )
            return tools

        for tool_path in sorted(tools_dir.glob("*.yaml")):
            try:
                raw_config = yaml.safe_load(
                    tool_path.read_text(encoding="utf-8")
                ) or {}

                # Resolve secret_ref entries
                resolved = self.secret_resolver.resolve_tool_config(
                    raw_config
                )

                tools[tool_path.stem] = resolved
                logger.info(f"Discovered tool: {tool_path.stem}")
            except Exception as e:
                logger.error(
                    f"Failed to load tool config {tool_path}: {e}"
                )

        return tools

    # ──────────────────────────────────────────────────
    # Container lifecycle
    # ──────────────────────────────────────────────────

    def start_container(
        self,
        name: str,
        image: str,
        *,
        environment: Optional[dict[str, str]] = None,
        volumes: Optional[dict[str, dict[str, str]]] = None,
        labels: Optional[dict[str, str]] = None,
        command: Optional[str] = None,
        detach: bool = True,
    ) -> docker.models.containers.Container:
        """Start a new container and attach it to maf-network.

        If a container with the given name already exists and is
        running, returns it. If it exists but is stopped, removes
        it and starts fresh.

        Args:
            name: Container name (e.g. "faith-agent-software-developer").
            image: Docker image to run.
            environment: Environment variables to inject.
            volumes: Volume mount specification.
            labels: Docker labels to apply.
            command: Optional command override.
            detach: Run in detached mode (default True).

        Returns:
            The started Container object.

        Raises:
            docker.errors.ImageNotFound: If the image does not exist.
            docker.errors.APIError: On Docker API errors.
        """
        # Check for existing container with the same name
        try:
            existing = self.docker_client.containers.get(name)
            if existing.status == "running":
                logger.info(
                    f"Container '{name}' is already running — "
                    f"reusing"
                )
                self._managed[name] = existing
                self.attach_to_network(name)
                return existing

            # Stopped or other state — remove and recreate
            logger.info(
                f"Removing stopped container '{name}' "
                f"(status: {existing.status})"
            )
            existing.remove(force=True)
        except docker.errors.NotFound:
            pass

        # Ensure network exists
        self.ensure_network()

        # Merge labels
        all_labels = {FAITH_LABEL: "true"}
        if labels:
            all_labels.update(labels)

        # Start container
        logger.info(f"Starting container '{name}' from image '{image}'")
        container = self.docker_client.containers.run(
            image=image,
            name=name,
            environment=environment or {},
            volumes=volumes or {},
            labels=all_labels,
            command=command,
            detach=detach,
            network=NETWORK_NAME,
            restart_policy={"Name": "unless-stopped"},
        )

        self._managed[name] = container
        logger.info(
            f"Container '{name}' started (id: {container.short_id})"
        )

        # Publish lifecycle event
        self._publish_lifecycle_event(
            "container:started", name, image
        )

        return container

    def stop_container(
        self, name: str, timeout: int = 10
    ) -> None:
        """Stop a running container.

        Args:
            name: Container name.
            timeout: Seconds to wait before killing (default 10).
        """
        try:
            container = self.docker_client.containers.get(name)
            logger.info(
                f"Stopping container '{name}' "
                f"(timeout: {timeout}s)"
            )
            container.stop(timeout=timeout)
            self._managed.pop(name, None)
            logger.info(f"Container '{name}' stopped")

            self._publish_lifecycle_event(
                "container:stopped", name
            )
        except docker.errors.NotFound:
            logger.warning(
                f"Container '{name}' not found — already removed?"
            )
            self._managed.pop(name, None)
        except Exception as e:
            logger.error(f"Failed to stop container '{name}': {e}")
            raise

    def restart_container(
        self, name: str, timeout: int = 10
    ) -> None:
        """Restart a container.

        Args:
            name: Container name.
            timeout: Seconds to wait for stop before killing.
        """
        try:
            container = self.docker_client.containers.get(name)
            logger.info(f"Restarting container '{name}'")
            container.restart(timeout=timeout)

            # Refresh reference
            container.reload()
            self._managed[name] = container
            logger.info(f"Container '{name}' restarted")

            self._publish_lifecycle_event(
                "container:restarted", name
            )
        except docker.errors.NotFound:
            logger.error(
                f"Container '{name}' not found — cannot restart"
            )
            raise
        except Exception as e:
            logger.error(
                f"Failed to restart container '{name}': {e}"
            )
            raise

    def list_containers(
        self, *, managed_only: bool = True
    ) -> list[dict[str, Any]]:
        """List containers, optionally filtered to FAITH-managed only.

        Args:
            managed_only: If True, only return containers with the
                faith.managed label.

        Returns:
            List of container info dicts with keys: name, id, status,
            image, labels.
        """
        filters = {}
        if managed_only:
            filters["label"] = FAITH_LABEL

        containers = self.docker_client.containers.list(
            all=True, filters=filters
        )

        result = []
        for c in containers:
            result.append({
                "name": c.name,
                "id": c.short_id,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else "unknown",
                "labels": c.labels,
            })

        return result

    # ──────────────────────────────────────────────────
    # High-level orchestration
    # ──────────────────────────────────────────────────

    def start_agent(
        self,
        agent_id: str,
        agent_config: dict[str, Any],
        workspace_path: Path,
    ) -> docker.models.containers.Container:
        """Start an agent container from its config.

        All agents use the shared agent-base image. The agent is
        differentiated by its config directory (mounted into the
        container) and environment variables.

        Args:
            agent_id: The agent identifier (directory name).
            agent_config: Parsed agent config from config.yaml.
            workspace_path: Path to the project workspace root.

        Returns:
            The started Container object.
        """
        container_name = f"faith-agent-{agent_id}"
        image = agent_config.get("image", AGENT_BASE_IMAGE)

        agent_dir = self.faith_dir / "agents" / agent_id

        environment = {
            "FAITH_AGENT_ID": agent_id,
            "FAITH_AGENT_MODEL": agent_config.get("model", ""),
            "REDIS_URL": "redis://redis:6379",
        }

        # Mount agent directory and workspace
        volumes = {
            str(agent_dir): {
                "bind": "/agent",
                "mode": "rw",
            },
            str(workspace_path): {
                "bind": "/workspace",
                "mode": "rw",
            },
        }

        labels = {
            FAITH_AGENT_LABEL: agent_id,
        }

        return self.start_container(
            name=container_name,
            image=image,
            environment=environment,
            volumes=volumes,
            labels=labels,
        )

    def start_tool(
        self,
        tool_name: str,
        tool_config: dict[str, Any],
        workspace_path: Path,
    ) -> docker.models.containers.Container:
        """Start a tool container from its resolved config.

        Tool configs have already been processed by SecretResolver,
        so credential values are available directly.

        Args:
            tool_name: The tool identifier (yaml file stem).
            tool_config: Parsed and secret-resolved tool config.
            workspace_path: Path to the project workspace root.

        Returns:
            The started Container object.
        """
        container_name = f"faith-tool-{tool_name}"
        image = tool_config.get(
            "image", f"faith-tool-{tool_name}:latest"
        )

        # Build environment from resolved credentials
        environment = {
            "FAITH_TOOL_NAME": tool_name,
            "REDIS_URL": "redis://redis:6379",
        }

        # Inject any secret_ref-resolved credential fields
        for key, value in tool_config.items():
            if key in ("image", "container", "mounts", "volumes"):
                continue
            if isinstance(value, str):
                env_key = f"TOOL_{key.upper()}"
                environment[env_key] = value

        # Mount workspace (tools get read access by default)
        volumes = {
            str(workspace_path): {
                "bind": "/workspace",
                "mode": "ro",
            },
        }

        # Apply custom mount definitions from tool config
        mounts = tool_config.get("mounts", {})
        for mount_name, mount_def in mounts.items():
            if isinstance(mount_def, dict):
                host_path = mount_def.get("path", "")
                bind_path = mount_def.get("bind", f"/mnt/{mount_name}")
                mode = mount_def.get("mode", "ro")
                if host_path:
                    volumes[host_path] = {
                        "bind": bind_path,
                        "mode": mode,
                    }

        labels = {
            FAITH_TOOL_LABEL: tool_name,
        }

        return self.start_container(
            name=container_name,
            image=image,
            environment=environment,
            volumes=volumes,
            labels=labels,
        )

    def start_all(self, workspace_path: Path) -> dict[str, bool]:
        """Discover and start all configured agents and tools.

        Called on PA startup. Discovers agents from
        .faith/agents/*/config.yaml and tools from
        .faith/tools/*.yaml, resolves credentials, and starts
        all containers on maf-network.

        Args:
            workspace_path: Path to the project workspace root.

        Returns:
            Dict mapping container names to success booleans.
        """
        results: dict[str, bool] = {}

        # Ensure network exists
        self.ensure_network()

        # Start agents
        agents = self.discover_agents()
        for agent_id, agent_config in agents.items():
            container_name = f"faith-agent-{agent_id}"
            try:
                self.start_agent(agent_id, agent_config, workspace_path)
                results[container_name] = True
            except Exception as e:
                logger.error(
                    f"Failed to start agent '{agent_id}': {e}"
                )
                results[container_name] = False

        # Start tools
        tools = self.discover_tools()
        for tool_name, tool_config in tools.items():
            container_name = f"faith-tool-{tool_name}"
            try:
                self.start_tool(tool_name, tool_config, workspace_path)
                results[container_name] = True
            except Exception as e:
                logger.error(
                    f"Failed to start tool '{tool_name}': {e}"
                )
                results[container_name] = False

        started = sum(1 for v in results.values() if v)
        failed = sum(1 for v in results.values() if not v)
        logger.info(
            f"Container startup complete: {started} started, "
            f"{failed} failed"
        )

        return results

    def stop_all(self, timeout: int = 10) -> None:
        """Stop all FAITH-managed containers.

        Used during session teardown or project switching.

        Args:
            timeout: Seconds to wait per container before killing.
        """
        containers = self.list_containers(managed_only=True)
        for info in containers:
            if info["status"] == "running":
                try:
                    self.stop_container(info["name"], timeout=timeout)
                except Exception as e:
                    logger.error(
                        f"Error stopping '{info['name']}': {e}"
                    )

    def reattach_running(self) -> int:
        """Re-attach to containers that survived a PA restart.

        Called during PA crash recovery (FRS Section 4.6.4).
        Finds all running FAITH-managed containers and adds them
        to the internal tracking dict.

        Returns:
            Number of containers re-attached.
        """
        containers = self.list_containers(managed_only=True)
        count = 0
        for info in containers:
            if info["status"] == "running":
                try:
                    container = self.docker_client.containers.get(
                        info["name"]
                    )
                    self._managed[info["name"]] = container
                    count += 1
                    logger.info(
                        f"Re-attached to running container: "
                        f"{info['name']}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to re-attach to "
                        f"'{info['name']}': {e}"
                    )

        logger.info(f"Re-attached to {count} running containers")
        return count

    # ──────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────

    def _publish_lifecycle_event(
        self,
        event_name: str,
        container_name: str,
        image: str = "",
    ) -> None:
        """Publish a container lifecycle event to system-events.

        Uses synchronous publishing since Docker SDK operations
        are synchronous. The EventPublisher is expected to support
        a sync publish_sync() method, or this wraps the async
        call.

        Args:
            event_name: Event name (e.g. "container:started").
            container_name: Name of the container.
            image: Docker image name (optional).
        """
        try:
            self.event_publisher.publish_sync(
                event=event_name,
                data={
                    "container": container_name,
                    "image": image,
                },
            )
        except AttributeError:
            # EventPublisher may only have async publish —
            # log the event instead
            logger.info(
                f"Lifecycle event: {event_name} — "
                f"container={container_name}, image={image}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to publish lifecycle event "
                f"'{event_name}': {e}"
            )
```

### 3. `faith/pa/__init__.py`

```python
"""FAITH Project Agent — container orchestration and secret resolution."""

from faith.pa.container_manager import ContainerManager
from faith.pa.secret_resolver import SecretResolver

__all__ = [
    "ContainerManager",
    "SecretResolver",
]
```

### 4. `containers/pa/Dockerfile`

```dockerfile
# FAITH Project Agent (PA) container
#
# The PA orchestrates all agent and tool containers via the Docker
# Python SDK. It requires the Docker socket to be mounted from the
# host. This grants effective root access to the host and is
# disclosed to the user during installation (FRS Section 4.6.1).
#
# Bootstrap: started by docker-compose.yml alongside Redis and Web UI.
# FRS Reference: Section 4.6.2

FROM python:3.12-slim

LABEL maintainer="FAITH Framework" \
      description="Project Agent — container orchestrator and session manager"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Copy the FAITH Python package
COPY faith/ /app/faith/

WORKDIR /app

# The Docker socket is mounted at runtime via docker-compose.yml:
#   volumes:
#     - /var/run/docker.sock:/var/run/docker.sock
#
# The framework config directory is mounted read-only:
#   volumes:
#     - ./config:/config:ro

ENV PYTHONUNBUFFERED=1 \
    FAITH_CONFIG_DIR=/config \
    FAITH_LOG_LEVEL=INFO

# Health check — the PA publishes heartbeats; this is a basic
# process liveness check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import docker; docker.from_env().ping()" || exit 1

ENTRYPOINT ["python", "-m", "faith.pa"]
```

### 5. `containers/pa/requirements.txt`

```
docker>=7.0.0
redis>=5.0.0
pyyaml>=6.0
pydantic>=2.0
tiktoken>=0.7.0
```

### 6. `tests/test_secret_resolver.py`

```python
"""Tests for FAITH SecretResolver.

Covers .env loading, ${VAR} substitution, secret_ref resolution,
recursive tool config resolution, and error handling.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from faith.pa.secret_resolver import SecretResolver


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def config_dir(tmp_path):
    """Create a temporary config directory with secrets.yaml and .env."""
    config = tmp_path / "config"
    config.mkdir()

    # Write .env
    env_content = (
        "OPENROUTER_API_KEY=sk-or-test-key-123\n"
        "PROD_DB_PASSWORD=supersecret\n"
        "TEST_DB_PASSWORD=testpass\n"
        "# This is a comment\n"
        "\n"
        "GITHUB_TOKEN=ghp_abc123\n"
    )
    (config / ".env").write_text(env_content, encoding="utf-8")

    # Write secrets.yaml
    secrets = {
        "openrouter_api_key": "${OPENROUTER_API_KEY}",
        "github_token": "${GITHUB_TOKEN}",
        "databases": {
            "prod-db": {
                "host": "db.example.com",
                "port": 5432,
                "user": "readonly_user",
                "password": "${PROD_DB_PASSWORD}",
            },
            "test-db": {
                "host": "localhost",
                "port": 5432,
                "user": "test_user",
                "password": "${TEST_DB_PASSWORD}",
            },
        },
    }
    (config / "secrets.yaml").write_text(
        yaml.dump(secrets), encoding="utf-8"
    )

    return config


@pytest.fixture
def resolver(config_dir):
    """Create a SecretResolver with test config."""
    # Clear relevant env vars first to ensure .env loading works
    for var in (
        "OPENROUTER_API_KEY", "PROD_DB_PASSWORD",
        "TEST_DB_PASSWORD", "GITHUB_TOKEN",
    ):
        os.environ.pop(var, None)

    return SecretResolver(config_dir)


# ──────────────────────────────────────────────────
# .env loading tests
# ──────────────────────────────────────────────────


def test_dotenv_loaded(resolver):
    """Variables from .env are loaded into the environment."""
    assert os.environ.get("OPENROUTER_API_KEY") == "sk-or-test-key-123"
    assert os.environ.get("GITHUB_TOKEN") == "ghp_abc123"


def test_dotenv_does_not_override_existing(config_dir):
    """Existing environment variables are not overridden by .env."""
    os.environ["OPENROUTER_API_KEY"] = "already-set"
    resolver = SecretResolver(config_dir)
    assert os.environ["OPENROUTER_API_KEY"] == "already-set"
    # Cleanup
    os.environ.pop("OPENROUTER_API_KEY", None)


def test_missing_dotenv(tmp_path):
    """Missing .env file is handled gracefully."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "secrets.yaml").write_text("{}", encoding="utf-8")
    resolver = SecretResolver(config)
    assert resolver.secrets == {}


# ──────────────────────────────────────────────────
# ${VAR} substitution tests
# ──────────────────────────────────────────────────


def test_env_var_substitution(resolver):
    """${VAR} placeholders in secrets.yaml are resolved."""
    assert resolver.secrets["openrouter_api_key"] == "sk-or-test-key-123"
    assert resolver.secrets["github_token"] == "ghp_abc123"


def test_nested_env_var_substitution(resolver):
    """${VAR} placeholders in nested dicts are resolved."""
    prod_db = resolver.secrets["databases"]["prod-db"]
    assert prod_db["password"] == "supersecret"
    assert prod_db["host"] == "db.example.com"


def test_unresolvable_env_var(config_dir):
    """Unresolvable ${VAR} produces empty string and warning."""
    # Write a secrets.yaml with an undefined variable
    secrets = {"missing_key": "${TOTALLY_UNDEFINED_VAR_XYZ}"}
    (config_dir / "secrets.yaml").write_text(
        yaml.dump(secrets), encoding="utf-8"
    )
    os.environ.pop("TOTALLY_UNDEFINED_VAR_XYZ", None)

    resolver = SecretResolver(config_dir)
    assert resolver.secrets["missing_key"] == ""


# ──────────────────────────────────────────────────
# secret_ref resolution tests
# ──────────────────────────────────────────────────


def test_resolve_top_level_scalar_ref(resolver):
    """Top-level scalar secret resolves to wrapped dict."""
    result = resolver.resolve_secret_ref("openrouter_api_key")
    assert result == {"value": "sk-or-test-key-123"}


def test_resolve_nested_database_ref(resolver):
    """Nested database secret_ref resolves correctly."""
    result = resolver.resolve_secret_ref("prod-db")
    assert result["host"] == "db.example.com"
    assert result["port"] == 5432
    assert result["password"] == "supersecret"


def test_resolve_unknown_ref(resolver):
    """Unknown secret_ref returns None."""
    assert resolver.resolve_secret_ref("nonexistent") is None


# ──────────────────────────────────────────────────
# Tool config resolution tests
# ──────────────────────────────────────────────────


def test_resolve_tool_config_with_secret_ref(resolver):
    """Tool config secret_ref entries are resolved and merged."""
    tool_config = {
        "connections": {
            "prod-db": {
                "secret_ref": "prod-db",
                "database": "myapp_production",
                "access": "readonly",
            }
        }
    }

    resolved = resolver.resolve_tool_config(tool_config)

    conn = resolved["connections"]["prod-db"]
    assert "secret_ref" not in conn
    assert conn["database"] == "myapp_production"
    assert conn["host"] == "db.example.com"
    assert conn["password"] == "supersecret"


def test_resolve_tool_config_explicit_override(resolver):
    """Explicit values in tool config are not overwritten by secrets."""
    tool_config = {
        "connections": {
            "prod-db": {
                "secret_ref": "prod-db",
                "host": "custom-host.example.com",
                "access": "readonly",
            }
        }
    }

    resolved = resolver.resolve_tool_config(tool_config)
    conn = resolved["connections"]["prod-db"]
    # Explicit host value should be preserved
    assert conn["host"] == "custom-host.example.com"


def test_resolve_tool_config_no_secret_ref(resolver):
    """Tool config without secret_ref is returned unchanged."""
    tool_config = {
        "timeout": 60,
        "internet": True,
    }

    resolved = resolver.resolve_tool_config(tool_config)
    assert resolved == tool_config


def test_resolve_tool_config_missing_ref(resolver):
    """Missing secret_ref is removed with warning, not crash."""
    tool_config = {
        "connections": {
            "unknown-db": {
                "secret_ref": "nonexistent-ref",
                "database": "mydb",
            }
        }
    }

    resolved = resolver.resolve_tool_config(tool_config)
    conn = resolved["connections"]["unknown-db"]
    assert "secret_ref" not in conn
    assert conn["database"] == "mydb"


# ──────────────────────────────────────────────────
# build_env_dict tests
# ──────────────────────────────────────────────────


def test_build_env_dict(resolver):
    """build_env_dict converts secret_ref to uppercase env vars."""
    env = resolver.build_env_dict("prod-db")
    assert env["HOST"] == "db.example.com"
    assert env["PORT"] == "5432"
    assert env["PASSWORD"] == "supersecret"
    assert env["USER"] == "readonly_user"


def test_build_env_dict_unknown_ref(resolver):
    """build_env_dict returns empty dict for unknown ref."""
    assert resolver.build_env_dict("nonexistent") == {}


# ──────────────────────────────────────────────────
# Missing secrets.yaml tests
# ──────────────────────────────────────────────────


def test_missing_secrets_yaml(tmp_path):
    """Missing secrets.yaml results in empty secrets dict."""
    config = tmp_path / "config"
    config.mkdir()
    resolver = SecretResolver(config)
    assert resolver.secrets == {}


def test_invalid_secrets_yaml(tmp_path):
    """Invalid YAML in secrets.yaml is handled gracefully."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "secrets.yaml").write_text(
        "{{invalid yaml: [", encoding="utf-8"
    )
    resolver = SecretResolver(config)
    assert resolver.secrets == {}
```

### 7. `tests/test_container_manager.py`

```python
"""Tests for FAITH ContainerManager.

Uses a mock Docker client to test container lifecycle operations,
agent/tool discovery, and network management without requiring
a real Docker daemon.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch, call

import pytest
import yaml

from faith.pa.container_manager import (
    ContainerManager,
    FAITH_LABEL,
    FAITH_AGENT_LABEL,
    FAITH_TOOL_LABEL,
    NETWORK_NAME,
    AGENT_BASE_IMAGE,
)
from faith.pa.secret_resolver import SecretResolver


# ──────────────────────────────────────────────────
# Mock Docker client
# ──────────────────────────────────────────────────


class MockContainer:
    """Mock Docker container object."""

    def __init__(self, name, status="running", image_tags=None):
        self.name = name
        self.status = status
        self.short_id = "abc123"
        self.labels = {FAITH_LABEL: "true"}
        self.image = MagicMock()
        self.image.tags = image_tags or ["test:latest"]
        self.attrs = {
            "NetworkSettings": {
                "Networks": {},
            },
        }

    def stop(self, timeout=10):
        self.status = "exited"

    def restart(self, timeout=10):
        self.status = "running"

    def remove(self, force=False):
        pass

    def reload(self):
        pass


class MockNetwork:
    """Mock Docker network object."""

    def __init__(self, name):
        self.name = name
        self._connected: list[str] = []

    def connect(self, container):
        self._connected.append(container.name)


class MockDockerClient:
    """Mock Docker SDK client."""

    def __init__(self):
        self.containers = MockContainersAPI()
        self.networks = MockNetworksAPI()


class MockContainersAPI:
    """Mock containers API."""

    def __init__(self):
        self._containers: dict[str, MockContainer] = {}
        self._run_calls: list[dict] = []

    def get(self, name):
        if name in self._containers:
            return self._containers[name]
        import docker.errors
        raise docker.errors.NotFound(f"Container '{name}' not found")

    def list(self, all=False, filters=None):
        return list(self._containers.values())

    def run(self, image, name, **kwargs):
        container = MockContainer(name, status="running")
        self._containers[name] = container
        self._run_calls.append({"image": image, "name": name, **kwargs})
        return container


class MockNetworksAPI:
    """Mock networks API."""

    def __init__(self):
        self._networks: dict[str, MockNetwork] = {}

    def get(self, name):
        if name in self._networks:
            return self._networks[name]
        import docker.errors
        raise docker.errors.NotFound(f"Network '{name}' not found")

    def create(self, name, driver="bridge"):
        network = MockNetwork(name)
        self._networks[name] = network
        return network


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def faith_dir(tmp_path):
    """Create a temporary .faith directory with agents and tools."""
    faith = tmp_path / ".faith"

    # Create agent configs
    dev_dir = faith / "agents" / "software-developer"
    dev_dir.mkdir(parents=True)
    dev_config = {
        "role": "Senior software developer",
        "model": "claude-3.5-sonnet",
        "tools": ["filesystem", "python", "git"],
    }
    (dev_dir / "config.yaml").write_text(
        yaml.dump(dev_config), encoding="utf-8"
    )
    (dev_dir / "prompt.md").write_text(
        "You are a software developer.", encoding="utf-8"
    )

    qa_dir = faith / "agents" / "test-engineer"
    qa_dir.mkdir(parents=True)
    qa_config = {
        "role": "Test engineer",
        "model": "gpt-4o",
        "tools": ["filesystem", "python"],
    }
    (qa_dir / "config.yaml").write_text(
        yaml.dump(qa_config), encoding="utf-8"
    )

    # Create tool configs
    tools_dir = faith / "tools"
    tools_dir.mkdir(parents=True)

    fs_config = {
        "image": "faith-tool-filesystem:latest",
        "mounts": {
            "workspace": {
                "path": "/home/user/project",
                "bind": "/workspace",
                "mode": "rw",
            }
        },
    }
    (tools_dir / "filesystem.yaml").write_text(
        yaml.dump(fs_config), encoding="utf-8"
    )

    db_config = {
        "image": "faith-tool-code-index:latest",
        "connections": {
            "prod-db": {
                "secret_ref": "prod-db",
                "database": "myapp",
                "access": "readonly",
            }
        },
    }
    (tools_dir / "database.yaml").write_text(
        yaml.dump(db_config), encoding="utf-8"
    )

    return faith


@pytest.fixture
def config_dir(tmp_path):
    """Create a temporary config directory."""
    config = tmp_path / "config"
    config.mkdir()

    secrets = {
        "databases": {
            "prod-db": {
                "host": "db.example.com",
                "port": 5432,
                "user": "readonly_user",
                "password": "secret123",
            }
        }
    }
    (config / "secrets.yaml").write_text(
        yaml.dump(secrets), encoding="utf-8"
    )

    return config


@pytest.fixture
def mock_docker():
    """Create a mock Docker client."""
    return MockDockerClient()


@pytest.fixture
def mock_event_publisher():
    """Create a mock EventPublisher."""
    pub = MagicMock()
    pub.publish_sync = MagicMock()
    return pub


@pytest.fixture
def manager(faith_dir, config_dir, mock_docker, mock_event_publisher):
    """Create a ContainerManager with test fixtures."""
    return ContainerManager(
        faith_dir=faith_dir,
        config_dir=config_dir,
        event_publisher=mock_event_publisher,
        docker_client=mock_docker,
    )


@pytest.fixture
def workspace_path(tmp_path):
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ──────────────────────────────────────────────────
# Network tests
# ──────────────────────────────────────────────────


def test_ensure_network_creates_when_missing(manager):
    """ensure_network creates maf-network if it doesn't exist."""
    network = manager.ensure_network()
    assert network.name == NETWORK_NAME


def test_ensure_network_returns_existing(manager):
    """ensure_network returns existing network without creating."""
    # Create it first
    first = manager.ensure_network()
    # Call again — should return the same one
    second = manager.ensure_network()
    assert second.name == NETWORK_NAME


def test_attach_to_network(manager, mock_docker):
    """attach_to_network connects a container to maf-network."""
    manager.ensure_network()

    # Add a container to the mock
    container = MockContainer("test-container")
    mock_docker.containers._containers["test-container"] = container

    manager.attach_to_network("test-container")

    network = mock_docker.networks.get(NETWORK_NAME)
    assert "test-container" in network._connected


def test_attach_to_network_already_attached(manager, mock_docker):
    """attach_to_network is a no-op if already connected."""
    manager.ensure_network()

    container = MockContainer("test-container")
    container.attrs["NetworkSettings"]["Networks"][NETWORK_NAME] = {}
    mock_docker.containers._containers["test-container"] = container

    # Should not raise or duplicate
    manager.attach_to_network("test-container")
    network = mock_docker.networks.get(NETWORK_NAME)
    assert "test-container" not in network._connected


# ──────────────────────────────────────────────────
# Discovery tests
# ──────────────────────────────────────────────────


def test_discover_agents(manager):
    """discover_agents finds all agents with config.yaml."""
    agents = manager.discover_agents()
    assert "software-developer" in agents
    assert "test-engineer" in agents
    assert agents["software-developer"]["model"] == "claude-3.5-sonnet"
    assert agents["test-engineer"]["role"] == "Test engineer"


def test_discover_agents_empty_dir(manager, tmp_path):
    """discover_agents returns empty dict for missing agents dir."""
    manager.faith_dir = tmp_path / "empty"
    agents = manager.discover_agents()
    assert agents == {}


def test_discover_agents_skips_missing_config(manager):
    """Agents without config.yaml are skipped."""
    # Create an agent dir without config.yaml
    bad_dir = manager.faith_dir / "agents" / "no-config-agent"
    bad_dir.mkdir(parents=True)

    agents = manager.discover_agents()
    assert "no-config-agent" not in agents
    assert "software-developer" in agents


def test_discover_tools(manager):
    """discover_tools finds all .yaml files in tools dir."""
    tools = manager.discover_tools()
    assert "filesystem" in tools
    assert "database" in tools


def test_discover_tools_resolves_secrets(manager):
    """discover_tools resolves secret_ref entries in tool configs."""
    tools = manager.discover_tools()
    db = tools["database"]
    conn = db["connections"]["prod-db"]
    # secret_ref should be resolved — merged credentials
    assert "secret_ref" not in conn
    assert conn["host"] == "db.example.com"
    assert conn["password"] == "secret123"


def test_discover_tools_empty_dir(manager, tmp_path):
    """discover_tools returns empty dict for missing tools dir."""
    manager.faith_dir = tmp_path / "empty"
    tools = manager.discover_tools()
    assert tools == {}


# ──────────────────────────────────────────────────
# Container lifecycle tests
# ──────────────────────────────────────────────────


def test_start_container_creates_new(manager, mock_docker):
    """start_container creates and starts a new container."""
    container = manager.start_container(
        name="faith-test",
        image="test:latest",
        environment={"FOO": "bar"},
    )

    assert container.name == "faith-test"
    assert container.status == "running"
    assert "faith-test" in manager._managed

    # Verify Docker run was called
    assert len(mock_docker.containers._run_calls) == 1
    run_call = mock_docker.containers._run_calls[0]
    assert run_call["image"] == "test:latest"
    assert run_call["name"] == "faith-test"
    assert run_call["network"] == NETWORK_NAME


def test_start_container_reuses_running(manager, mock_docker):
    """start_container reuses an already-running container."""
    existing = MockContainer("faith-test", status="running")
    mock_docker.containers._containers["faith-test"] = existing

    # Ensure network exists for attach
    manager.ensure_network()

    container = manager.start_container(
        name="faith-test",
        image="test:latest",
    )

    assert container is existing
    # No new run call
    assert len(mock_docker.containers._run_calls) == 0


def test_start_container_replaces_stopped(manager, mock_docker):
    """start_container removes and replaces a stopped container."""
    stopped = MockContainer("faith-test", status="exited")
    mock_docker.containers._containers["faith-test"] = stopped

    container = manager.start_container(
        name="faith-test",
        image="test:latest",
    )

    assert container.status == "running"
    assert len(mock_docker.containers._run_calls) == 1


def test_start_container_publishes_event(manager, mock_event_publisher):
    """start_container publishes a container:started event."""
    manager.start_container(
        name="faith-test",
        image="test:latest",
    )

    mock_event_publisher.publish_sync.assert_called_once()
    call_args = mock_event_publisher.publish_sync.call_args
    assert call_args.kwargs["event"] == "container:started"
    assert call_args.kwargs["data"]["container"] == "faith-test"


def test_stop_container(manager, mock_docker):
    """stop_container stops a running container."""
    container = MockContainer("faith-test")
    mock_docker.containers._containers["faith-test"] = container
    manager._managed["faith-test"] = container

    manager.stop_container("faith-test")

    assert container.status == "exited"
    assert "faith-test" not in manager._managed


def test_stop_container_not_found(manager):
    """stop_container handles missing container gracefully."""
    # Should not raise
    manager.stop_container("nonexistent")


def test_restart_container(manager, mock_docker):
    """restart_container restarts a running container."""
    container = MockContainer("faith-test")
    mock_docker.containers._containers["faith-test"] = container
    manager._managed["faith-test"] = container

    manager.restart_container("faith-test")

    assert container.status == "running"
    assert "faith-test" in manager._managed


def test_restart_container_not_found(manager):
    """restart_container raises for missing container."""
    import docker.errors

    with pytest.raises(docker.errors.NotFound):
        manager.restart_container("nonexistent")


def test_list_containers(manager, mock_docker):
    """list_containers returns info for all managed containers."""
    mock_docker.containers._containers["faith-agent-dev"] = MockContainer(
        "faith-agent-dev"
    )
    mock_docker.containers._containers["faith-tool-fs"] = MockContainer(
        "faith-tool-fs"
    )

    result = manager.list_containers(managed_only=True)
    assert len(result) == 2

    names = {c["name"] for c in result}
    assert "faith-agent-dev" in names
    assert "faith-tool-fs" in names


def test_list_containers_includes_status(manager, mock_docker):
    """list_containers includes container status."""
    mock_docker.containers._containers["faith-test"] = MockContainer(
        "faith-test", status="running"
    )

    result = manager.list_containers()
    assert result[0]["status"] == "running"


# ──────────────────────────────────────────────────
# High-level orchestration tests
# ──────────────────────────────────────────────────


def test_start_agent(manager, workspace_path):
    """start_agent creates an agent container with correct config."""
    agent_config = {
        "role": "Developer",
        "model": "claude-3.5-sonnet",
    }

    container = manager.start_agent(
        agent_id="software-developer",
        agent_config=agent_config,
        workspace_path=workspace_path,
    )

    assert container.name == "faith-agent-software-developer"

    # Verify run call
    run_call = manager.docker_client.containers._run_calls[0]
    assert run_call["environment"]["FAITH_AGENT_ID"] == "software-developer"
    assert run_call["environment"]["FAITH_AGENT_MODEL"] == "claude-3.5-sonnet"
    assert run_call["image"] == AGENT_BASE_IMAGE


def test_start_tool(manager, workspace_path):
    """start_tool creates a tool container with resolved secrets."""
    tool_config = {
        "image": "faith-tool-code-index:latest",
        "timeout": "60",
    }

    container = manager.start_tool(
        tool_name="database",
        tool_config=tool_config,
        workspace_path=workspace_path,
    )

    assert container.name == "faith-tool-code-index"

    run_call = manager.docker_client.containers._run_calls[0]
    assert run_call["environment"]["FAITH_TOOL_NAME"] == "database"
    assert run_call["environment"]["TOOL_TIMEOUT"] == "60"


def test_start_all(manager, workspace_path):
    """start_all discovers and starts all agents and tools."""
    results = manager.start_all(workspace_path)

    # 2 agents + 2 tools = 4 containers
    assert len(results) == 4
    assert results["faith-agent-software-developer"] is True
    assert results["faith-agent-test-engineer"] is True
    assert results["faith-tool-filesystem"] is True
    assert results["faith-tool-code-index"] is True


def test_start_all_handles_failures(manager, workspace_path, mock_docker):
    """start_all records failures without stopping other starts."""
    # Make containers.run raise for a specific name
    original_run = mock_docker.containers.run

    def failing_run(image, name, **kwargs):
        if "test-engineer" in name:
            raise Exception("Simulated failure")
        return original_run(image, name, **kwargs)

    mock_docker.containers.run = failing_run

    results = manager.start_all(workspace_path)

    assert results["faith-agent-test-engineer"] is False
    assert results["faith-agent-software-developer"] is True


def test_stop_all(manager, mock_docker):
    """stop_all stops all running managed containers."""
    mock_docker.containers._containers["faith-agent-dev"] = MockContainer(
        "faith-agent-dev"
    )
    mock_docker.containers._containers["faith-tool-fs"] = MockContainer(
        "faith-tool-fs"
    )

    manager.stop_all()

    for c in mock_docker.containers._containers.values():
        assert c.status == "exited"


# ──────────────────────────────────────────────────
# Crash recovery tests
# ──────────────────────────────────────────────────


def test_reattach_running(manager, mock_docker):
    """reattach_running finds and tracks surviving containers."""
    mock_docker.containers._containers["faith-agent-dev"] = MockContainer(
        "faith-agent-dev", status="running"
    )
    mock_docker.containers._containers["faith-tool-fs"] = MockContainer(
        "faith-tool-fs", status="exited"
    )

    count = manager.reattach_running()

    # Only running containers are re-attached
    assert count == 1
    assert "faith-agent-dev" in manager._managed
    assert "faith-tool-fs" not in manager._managed
```

---

## Integration Points

The ContainerManager integrates with several other FAITH components:

```python
# PA startup sequence (FAITH-015 will orchestrate this):
from pathlib import Path
from faith.pa.container_manager import ContainerManager
from faith.protocol.events import EventPublisher

event_publisher = EventPublisher(redis_client, source="pa")
manager = ContainerManager(
    faith_dir=Path("/workspace/.faith"),
    config_dir=Path("/config"),
    event_publisher=event_publisher,
)

# Start all validated project-scoped agents and runtimes
results = manager.start_all(workspace_path=Path("/workspace"))
# results: {"faith-agent-software-developer": True, "faith-tool-filesystem": True, ...}
```

```python
# PA crash recovery (FRS Section 4.6.4):
count = manager.reattach_running()
# PA then re-reads session.meta.json files for active sessions
# and re-subscribes to channels — handled by FAITH-015/016
```

```python
# Tool config with secret_ref resolution:
# .faith/tools/database.yaml:
#   connections:
#     prod-db:
#       secret_ref: prod-db       # <- resolved from config/secrets.yaml
#       database: myapp_production
#       access: readonly
#
# After SecretResolver processes it:
#   connections:
#     prod-db:
#       host: db.example.com
#       port: 5432
#       user: readonly_user
#       password: <actual password>
#       database: myapp_production
#       access: readonly
```

```python
# Config hot-reload triggers container restart (FAITH-004 -> FAITH-014):
# When an agent's config.yaml changes, the PA may restart the agent:
manager.restart_container("faith-agent-software-developer")
```

---

## Acceptance Criteria

1. `SecretResolver.__init__` loads `config/.env` into environment variables (without overriding existing vars) and parses `config/secrets.yaml` with full `${VAR}` substitution.
2. `SecretResolver.resolve_secret_ref()` resolves top-level keys and nested keys under grouping sections (`databases`, `services`, `credentials`).
3. `SecretResolver.resolve_tool_config()` recursively walks a tool config dict, resolves all `secret_ref` entries, merges credentials, and removes the `secret_ref` key. Explicit values in the tool config are never overwritten by secrets.
4. `ContainerManager.ensure_network()` creates `maf-network` if it does not exist, or returns the existing one.
5. `ContainerManager.attach_to_network()` connects a container to `maf-network`, with no-op behaviour if already attached.
6. `ContainerManager.discover_agents()` scans `.faith/agents/*/config.yaml` and returns all valid agent configs. Directories without `config.yaml` are skipped with a warning.
7. `ContainerManager.discover_tools()` scans `.faith/tools/*.yaml`, parses each tool config, and resolves `secret_ref` entries via `SecretResolver`.
8. `ContainerManager.start_container()` starts a new container on `maf-network` with the `faith.managed` label and restart policy `unless-stopped`. Reuses already-running containers. Removes and replaces stopped containers. Publishes `container:started` lifecycle event.
9. `ContainerManager.stop_container()` stops a running container and publishes `container:stopped`. Handles not-found gracefully.
10. `ContainerManager.restart_container()` restarts a container in place and publishes `container:restarted`.
11. `ContainerManager.list_containers()` returns info dicts for all FAITH-managed containers (filtered by `faith.managed` label).
12. `ContainerManager.start_all()` discovers all validated project-scoped agents and runtimes, starts the required containers, and returns a name-to-success dict. Individual failures do not prevent other eligible project-scoped runtimes from starting, but this method is not called until project config validation has already succeeded.
13. `ContainerManager.stop_all()` stops all running FAITH-managed containers.
14. `ContainerManager.reattach_running()` finds running FAITH-managed containers after a PA restart and re-adds them to internal tracking.
15. PA Dockerfile installs the Docker Python SDK, runs as `python -m faith.pa`, and includes a Docker-ping health check.
16. All tests in `tests/test_secret_resolver.py` pass (16 tests covering .env loading, `${VAR}` substitution, `secret_ref` resolution, tool config resolution, and error handling).
17. All tests in `tests/test_container_manager.py` pass (22 tests covering network management, discovery, container lifecycle, orchestration, and crash recovery).

---

## Notes for Implementer

- **Docker socket mount**: The PA container mounts `/var/run/docker.sock` from the host, configured in `docker-compose.yml` (FAITH-001). This gives the PA the ability to manage all Docker containers. The Docker SDK connects via `docker.from_env()` which auto-detects the socket.
- **Synchronous Docker SDK**: The Docker Python SDK (`docker` package) is synchronous. The `ContainerManager` methods are therefore sync. The PA's async event loop (FAITH-015/016) should call these methods via `asyncio.to_thread()` or from a sync startup phase.
- **Event publishing bridge**: The `_publish_lifecycle_event` method attempts `publish_sync()` on the EventPublisher. If FAITH-008's `EventPublisher` only exposes async `publish()`, the implementer should either add a `publish_sync()` convenience method or log the event directly. The fallback is already in the code.
- **No secrets in agent containers**: Agents never receive `config/secrets.yaml`. API keys for LLM calls are injected as environment variables by the ContainerManager when starting agent containers. Tool containers receive only the credentials relevant to their function.
- **Container naming convention**: Agents are named `faith-agent-{id}`, tools are named `faith-tool-{name}`. This makes Docker label filtering and log identification straightforward.
- **AGENT_BASE_IMAGE**: All specialist agents use the shared `faith-agent-base:latest` image (built from `containers/agent-base/Dockerfile`, which is a separate task). The PA differentiates agents via mounted config directories and environment variables, not separate images.
- **Restart policy**: All managed containers use `unless-stopped` restart policy. This means containers survive Docker daemon restarts but can be explicitly stopped by the PA.
- **Mock Docker client in tests**: Tests use a custom `MockDockerClient` / `MockContainer` / `MockNetwork` set rather than `docker`'s test utilities. This avoids requiring a Docker daemon for unit tests. Integration tests against a real Docker daemon would be a separate test suite.
- **Config hot-reload**: When FAITH-004's config watcher detects changes to `.faith/agents/*/config.yaml` or `.faith/tools/*.yaml`, the PA may need to restart affected containers. The restart logic itself lives in FAITH-016 (PA Event Dispatcher); this task provides the `restart_container()` primitive.
- **Project switching**: `stop_all()` is called during project switching (FAITH-015) to tear down project-scoped agent containers and re-synchronise project-scoped runtimes before mounting a new project workspace.



