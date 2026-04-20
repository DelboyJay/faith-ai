"""Description:
    Manage FAITH-owned container lifecycle for agents, tools, sandboxes, and project runtimes.

Requirements:
    - Support a Docker-like runtime abstraction for tests and future runtime integrations.
    - Resolve environment variables and secret references before container startup.
    - Expose discovery, start, stop, restart, inspect, and destroy operations.
    - Apply the FAITH network and label conventions consistently.
    - Start the shared `mcp-runtime` with the registry and external-tool metadata needed by Phase 7.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import docker
except ImportError:  # pragma: no cover - exercised when docker SDK is unavailable.
    docker = None

import yaml

from faith_pa.pa.secret_resolver import SecretResolver
from faith_shared.protocol.events import EventPublisher

FAITH_LABEL = "faith.managed"
FAITH_AGENT_LABEL = "faith.agent"
FAITH_TOOL_LABEL = "faith.tool"
NETWORK_NAME = "maf-network"
AGENT_BASE_IMAGE = "faith-agent-base:latest"
DEFAULT_MCP_REGISTRY_URL = "http://mcp-registry:8080"


def _utc_now() -> str:
    """Description:
        Return the current UTC timestamp in container metadata format.

    Requirements:
        - Use an ISO-8601 UTC format ending with ``Z``.

    :returns: Current UTC timestamp string.
    """

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class ContainerSpec:
    """Description:
        Describe the desired runtime configuration for one managed container.

    Requirements:
        - Preserve image, command, environment, mounts, labels, and logical container type.
        - Preserve network and privilege policy for hardened sandboxes.

    :param name: Managed container name.
    :param image: Container image reference.
    :param command: Container command override.
    :param env: Plain environment variables.
    :param env_secret_refs: Environment-variable secret references.
    :param mounts: Host-to-container mount mapping.
    :param labels: Container metadata labels.
    :param container_type: Logical container type label.
    :param network_mode: Runtime network mode.
    :param privileged: Whether privileged mode is enabled.
    :param capabilities: Linux capabilities granted to the container.
    :param restart_policy: Docker restart policy name.
    """

    name: str
    image: str
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    env_secret_refs: dict[str, str] = field(default_factory=dict)
    mounts: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    container_type: str = "generic"
    network_mode: str = NETWORK_NAME
    privileged: bool = False
    capabilities: list[str] = field(default_factory=list)
    restart_policy: str = "unless-stopped"


@dataclass(slots=True)
class ContainerInfo:
    """Description:
        Represent the current observed state of one managed container.

    Requirements:
        - Preserve image, status, command, labels, restart count, and logical type.
        - Preserve the resolved environment, mounts, network, and restart policy used at runtime.

    :param name: Managed container name.
    :param image: Container image reference.
    :param status: Current runtime status.
    :param labels: Container metadata labels.
    :param command: Effective command list.
    :param restart_count: Number of recorded restarts.
    :param container_type: Logical container type label.
    :param created_at: Creation timestamp.
    :param environment: Resolved runtime environment.
    :param mounts: Host-to-container mount mapping.
    :param network_mode: Runtime network name.
    :param privileged: Whether privileged mode is enabled.
    :param capabilities: Granted Linux capabilities.
    :param restart_policy: Docker restart policy name.
    """

    name: str
    image: str
    status: str
    labels: dict[str, str] = field(default_factory=dict)
    command: list[str] | None = None
    restart_count: int = 0
    container_type: str = "generic"
    created_at: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    mounts: dict[str, str] = field(default_factory=dict)
    network_mode: str = NETWORK_NAME
    privileged: bool = False
    capabilities: list[str] = field(default_factory=list)
    restart_policy: str = "unless-stopped"


class _RuntimeRecord:
    """Description:
        Store one in-memory runtime record for the test container runtime.

    Requirements:
        - Preserve the original spec, runtime status, restart count, and creation time.

    :param spec: Container spec associated with the record.
    :param status: Current runtime status.
    """

    def __init__(self, spec: ContainerSpec, status: str = "running") -> None:
        """Description:
            Initialise the runtime record.

        Requirements:
            - Start with a zero restart count and a creation timestamp.

        :param spec: Container spec associated with the record.
        :param status: Current runtime status.
        """

        self.spec = spec
        self.status = status
        self.restart_count = 0
        self.created_at = _utc_now()


class InMemoryContainerRuntime:
    """Description:
        Provide a lightweight in-memory container runtime for tests and POC flows.

    Requirements:
        - Track created containers and configured networks in memory only.
        - Support create, stop, restart, destroy, inspect, and list operations.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the in-memory runtime state.

        Requirements:
            - Start with empty container records and network state.
        """

        self.records: dict[str, _RuntimeRecord] = {}
        self.networks: set[str] = set()

    def ensure_network(self, name: str) -> None:
        """Description:
            Record that one runtime network exists.

        Requirements:
            - Preserve the supplied network name in the runtime state.

        :param name: Network name to record.
        """

        self.networks.add(name)

    def create_or_update(self, spec: ContainerSpec) -> ContainerInfo:
        """Description:
            Create or update one container record and return its current info.

        Requirements:
            - Create a fresh record when the container does not yet exist.
            - Mark existing containers as running after an update.

        :param spec: Desired container specification.
        :returns: Current container info.
        """

        record = self.records.get(spec.name)
        if record is None:
            record = _RuntimeRecord(spec)
            self.records[spec.name] = record
        else:
            record.spec = spec
            record.status = "running"
        return self.inspect(spec.name)

    def stop(self, name: str) -> ContainerInfo:
        """Description:
            Mark one container as stopped.

        Requirements:
            - Preserve the record while updating the runtime status.

        :param name: Container name to stop.
        :returns: Current container info.
        """

        record = self.records[name]
        record.status = "stopped"
        return self.inspect(name)

    def restart(self, name: str) -> ContainerInfo:
        """Description:
            Mark one container as restarted.

        Requirements:
            - Increment the restart counter and mark the container as running.

        :param name: Container name to restart.
        :returns: Current container info.
        """

        record = self.records[name]
        record.status = "running"
        record.restart_count += 1
        return self.inspect(name)

    def destroy(self, name: str) -> None:
        """Description:
            Remove one container record from the runtime.

        Requirements:
            - Succeed even when the record is already absent.

        :param name: Container name to remove.
        """

        self.records.pop(name, None)

    def inspect(self, name: str) -> ContainerInfo:
        """Description:
            Return the current info for one managed container.

        Requirements:
            - Reflect the stored spec, status, restart count, and creation time.

        :param name: Container name to inspect.
        :returns: Current container info.
        """

        record = self.records[name]
        return ContainerInfo(
            name=record.spec.name,
            image=record.spec.image,
            status=record.status,
            labels=dict(record.spec.labels),
            command=list(record.spec.command),
            restart_count=record.restart_count,
            container_type=record.spec.container_type,
            created_at=record.created_at,
            environment=dict(record.spec.env),
            mounts=dict(record.spec.mounts),
            network_mode=record.spec.network_mode,
            privileged=record.spec.privileged,
            capabilities=list(record.spec.capabilities),
            restart_policy=record.spec.restart_policy,
        )

    def list(self) -> list[ContainerInfo]:
        """Description:
            Return the managed containers sorted by name.

        Requirements:
            - Provide stable ordering for deterministic tests and UI rendering.

        :returns: Sorted container info payloads.
        """

        return [self.inspect(name) for name in sorted(self.records)]


class DockerContainerRuntime:
    """Description:
        Provide a Docker SDK-backed runtime for managed FAITH containers.

    Requirements:
        - Connect through the Docker SDK environment defaults.
        - Create or reuse the FAITH network when requested.
        - Reuse already-running containers and replace stopped ones when specs change.

    :param docker_client: Optional pre-built Docker client for tests or custom callers.
    :param network_name: Default FAITH network name for managed containers.
    """

    def __init__(
        self, docker_client: Any | None = None, *, network_name: str = NETWORK_NAME
    ) -> None:
        """Description:
            Initialise the Docker-backed runtime.

        Requirements:
            - Require the Docker Python SDK to be installed.
            - Use `docker.from_env()` when no explicit client is supplied.

        :param docker_client: Optional pre-built Docker client for tests or custom callers.
        :param network_name: Default FAITH network name for managed containers.
        :raises RuntimeError: If the Docker Python SDK is not installed.
        """

        if docker_client is None and docker is None:
            raise RuntimeError("Docker Python SDK is not installed")
        self.client = docker_client or docker.from_env()
        self.network_name = network_name

    def ensure_network(self, name: str) -> None:
        """Description:
        Ensure one Docker bridge network exists.

        Requirements:
            - Reuse an existing network when it is already present.
            - Create the network with the bridge driver otherwise.

        :param name: Docker network name.
        """

        try:
            self.client.networks.get(name)
        except Exception:
            self.client.networks.create(name, driver="bridge")

    def _to_volumes(self, mounts: dict[str, str]) -> dict[str, dict[str, str]]:
        """Description:
            Convert a simple mount mapping into the Docker SDK volumes format.

        Requirements:
            - Bind every host path read-write at the requested container path.

        :param mounts: Host-to-container mount mapping.
        :returns: Docker SDK volume mapping.
        """

        return {
            host_path: {"bind": container_path, "mode": "rw"}
            for host_path, container_path in mounts.items()
        }

    def create_or_update(self, spec: ContainerSpec) -> ContainerInfo:
        """Description:
            Create or update one Docker container to match the supplied spec.

        Requirements:
            - Reuse already-running containers unchanged.
            - Remove stopped containers before recreating them.
            - Start recreated containers detached with the requested restart policy.

        :param spec: Desired container specification.
        :returns: Current container info payload.
        """

        try:
            existing = self.client.containers.get(spec.name)
            existing.reload()
            status = getattr(existing, "status", "") or ""
            if status == "running":
                return self.inspect(spec.name)
            existing.remove(force=True)
        except Exception:
            pass

        run_kwargs: dict[str, Any] = {
            "name": spec.name,
            "detach": True,
            "environment": spec.env or None,
            "labels": spec.labels or None,
            "volumes": self._to_volumes(spec.mounts),
            "command": spec.command or None,
            "privileged": spec.privileged,
            "cap_add": list(spec.capabilities),
            "restart_policy": {"Name": spec.restart_policy},
        }
        if spec.network_mode == "host":
            run_kwargs["network_mode"] = "host"
        else:
            run_kwargs["network"] = self.network_name
        self.client.containers.run(image=spec.image, **run_kwargs)
        return self.inspect(spec.name)

    def stop(self, name: str) -> ContainerInfo:
        """Description:
            Stop one managed Docker container.

        Requirements:
            - Return the post-stop container info.

        :param name: Container name to stop.
        :returns: Current container info payload.
        """

        container = self.client.containers.get(name)
        container.stop()
        return self.inspect(name)

    def restart(self, name: str) -> ContainerInfo:
        """Description:
            Restart one managed Docker container.

        Requirements:
            - Return the post-restart container info.

        :param name: Container name to restart.
        :returns: Current container info payload.
        """

        container = self.client.containers.get(name)
        container.restart()
        return self.inspect(name)

    def destroy(self, name: str) -> None:
        """Description:
            Remove one managed Docker container when present.

        Requirements:
            - Succeed quietly when the container is already absent.

        :param name: Container name to remove.
        """

        try:
            self.client.containers.get(name).remove(force=True)
        except Exception:
            return

    def inspect(self, name: str) -> ContainerInfo:
        """Description:
            Return the current Docker inspection summary for one managed container.

        Requirements:
            - Preserve the image tag, lifecycle status, labels, restart count, and runtime execution details.

        :param name: Container name to inspect.
        :returns: Current container info payload.
        """

        container = self.client.containers.get(name)
        container.reload()
        attrs = getattr(container, "attrs", {}) or {}
        image_tags = attrs.get("Config", {}).get("Image") or container.image.tags
        restart_count = int(attrs.get("RestartCount", 0) or 0)
        labels = dict(attrs.get("Config", {}).get("Labels", {}) or {})
        container_type = labels.get("faith.role", "generic")
        created_at = attrs.get("Created")
        env_list = attrs.get("Config", {}).get("Env") or []
        binds = attrs.get("HostConfig", {}).get("Binds") or []
        environment: dict[str, str] = {}
        for item in env_list:
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            environment[key] = value
        mounts: dict[str, str] = {}
        for bind in binds:
            parts = bind.split(":")
            if len(parts) >= 2:
                mounts[parts[0]] = parts[1]
        network_mode = str(attrs.get("HostConfig", {}).get("NetworkMode") or self.network_name)
        privileged = bool(attrs.get("HostConfig", {}).get("Privileged", False))
        capabilities = list(attrs.get("HostConfig", {}).get("CapAdd") or [])
        restart_policy = str(
            (attrs.get("HostConfig", {}).get("RestartPolicy") or {}).get("Name", "unless-stopped")
        )
        return ContainerInfo(
            name=container.name,
            image=image_tags
            if isinstance(image_tags, str)
            else (image_tags[0] if image_tags else ""),
            status=getattr(container, "status", "unknown"),
            labels=labels,
            command=list(attrs.get("Config", {}).get("Cmd") or []),
            restart_count=restart_count,
            container_type=container_type,
            created_at=created_at,
            environment=environment,
            mounts=mounts,
            network_mode=network_mode,
            privileged=privileged,
            capabilities=capabilities,
            restart_policy=restart_policy,
        )

    def list(self) -> list[ContainerInfo]:
        """Description:
            Return FAITH-managed Docker containers in stable name order.

        Requirements:
            - Filter to containers carrying the FAITH-managed label.

        :returns: Sorted managed container info payloads.
        """

        managed: list[ContainerInfo] = []
        try:
            containers = self.client.containers.list(
                all=True, filters={"label": f"{FAITH_LABEL}=true"}
            )
        except TypeError:
            containers = self.client.containers.list(all=True)
        for container in containers:
            info = self.inspect(container.name)
            if info.labels.get(FAITH_LABEL) == "true":
                managed.append(info)
        return sorted(managed, key=lambda item: item.name)


class ContainerManager:
    """Description:
        Manage the lifecycle of FAITH-owned containers against a runtime abstraction.

    Requirements:
        - Support secret-aware environment resolution before startup.
        - Ensure the configured network exists when the runtime supports network management.
        - Publish container lifecycle events when an event publisher is supplied.
        - Discover project agent and tool configs under the active ``.faith`` tree.

    :param client: Docker-like runtime client or test runtime.
    :param network_name: Logical network name to ensure at startup.
    :param secret_resolver: Optional secret resolver used for environment preparation.
    :param event_publisher: Optional event publisher for lifecycle notifications.
    :param audit_logger: Optional audit logger reserved for future lifecycle audit support.
    :param faith_dir: Optional project ``.faith`` directory for discovery helpers.
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        network_name: str = NETWORK_NAME,
        secret_resolver: SecretResolver | None = None,
        event_publisher: EventPublisher | Any | None = None,
        audit_logger: Any | None = None,
        faith_dir: Path | None = None,
        config_dir: Path | None = None,
    ) -> None:
        """Description:
            Initialise the container manager.

        Requirements:
            - Ensure the configured runtime network exists when the client supports it.

        :param client: Docker-like runtime client or test runtime.
        :param network_name: Logical network name to ensure at startup.
        :param secret_resolver: Optional secret resolver used for environment preparation.
        :param event_publisher: Optional event publisher for lifecycle notifications.
        :param audit_logger: Optional audit logger reserved for future lifecycle audit support.
        :param faith_dir: Optional project ``.faith`` directory for discovery helpers.
        :param config_dir: Optional framework config directory used to build a secret resolver.
        """

        if client is None:
            self.client = DockerContainerRuntime(network_name=network_name)
        elif all(
            hasattr(client, attr)
            for attr in ("create_or_update", "stop", "restart", "destroy", "inspect", "list")
        ):
            self.client = client
        elif hasattr(client, "containers") and hasattr(client, "networks"):
            self.client = DockerContainerRuntime(client, network_name=network_name)
        else:
            self.client = client
        self.network_name = network_name
        self.secret_resolver = (
            secret_resolver
            if secret_resolver is not None
            else SecretResolver(config_dir)
            if config_dir is not None
            else None
        )
        self.event_publisher = event_publisher
        self.audit_logger = audit_logger
        self.faith_dir = Path(faith_dir).resolve() if faith_dir is not None else None
        self.config_dir = Path(config_dir).resolve() if config_dir is not None else None
        if hasattr(self.client, "ensure_network"):
            self.client.ensure_network(network_name)

    def _resolve_env(self, spec: ContainerSpec) -> dict[str, str]:
        """Description:
            Resolve the runtime environment for one container specification.

        Requirements:
            - Return the plain environment mapping when no secret resolver is configured.
            - Resolve both plain values and secret references when the resolver is available.

        :param spec: Container specification to resolve.
        :returns: Resolved environment mapping.
        """

        if self.secret_resolver is None:
            return dict(spec.env)
        return self.secret_resolver.resolve_environment(
            env=spec.env,
            env_secret_refs=spec.env_secret_refs,
        )

    async def _publish_started(self, info: ContainerInfo) -> None:
        """Description:
            Publish the started event for one container when supported.

        Requirements:
            - Use the canonical system-container-started helper when available.

        :param info: Container info payload.
        """

        if self.event_publisher and hasattr(self.event_publisher, "system_container_started"):
            await self.event_publisher.system_container_started(info.name, info.container_type)

    async def _publish_stopped(self, name: str, *, reason: str) -> None:
        """Description:
            Publish the stopped event for one container when supported.

        Requirements:
            - Use the canonical system-container-stopped helper when available.

        :param name: Container name.
        :param reason: Human-readable stop reason.
        """

        if self.event_publisher and hasattr(self.event_publisher, "system_container_stopped"):
            await self.event_publisher.system_container_stopped(name, reason=reason)

    async def start_container(
        self,
        name: str,
        *,
        image: str,
        labels: dict[str, str] | None = None,
        command: list[str] | None = None,
        environment: dict[str, str] | None = None,
        env_secret_refs: dict[str, str] | None = None,
        mounts: dict[str, str] | None = None,
        container_type: str = "generic",
        network_mode: str | None = None,
        privileged: bool = False,
        capabilities: list[str] | None = None,
    ) -> ContainerInfo:
        """Description:
            Start or update one managed container.

        Requirements:
            - Build a ``ContainerSpec`` from the supplied arguments.
            - Resolve the final environment before container creation.
            - Publish a container-started event when the publisher exposes that helper.

        :param name: Managed container name.
        :param image: Container image reference.
        :param labels: Optional container labels.
        :param command: Optional command override.
        :param environment: Optional environment mapping.
        :param env_secret_refs: Optional environment secret references.
        :param mounts: Optional host-to-container mount mapping.
        :param container_type: Logical container type label.
        :param network_mode: Runtime network mode.
        :param privileged: Whether privileged mode is enabled.
        :param capabilities: Linux capabilities granted to the container.
        :returns: Current container info.
        :raises RuntimeError: If the runtime client does not support container creation.
        """

        merged_labels = {
            FAITH_LABEL: "true",
            "faith.role": container_type,
            **(labels or {}),
        }
        spec = ContainerSpec(
            name=name,
            image=image,
            labels=merged_labels,
            command=command or [],
            env=environment or {},
            env_secret_refs=env_secret_refs or {},
            mounts=mounts or {},
            container_type=container_type,
            network_mode=network_mode or self.network_name,
            privileged=privileged,
            capabilities=list(capabilities or []),
        )
        spec.env = self._resolve_env(spec)
        if not hasattr(self.client, "create_or_update"):
            raise RuntimeError("Unsupported container runtime")
        info = self.client.create_or_update(spec)
        await self._publish_started(info)
        return info

    async def stop_container(self, name: str, *, reason: str = "normal") -> ContainerInfo:
        """Description:
            Stop one managed container.

        Requirements:
            - Publish a container-stopped event when the publisher exposes that helper.

        :param name: Container name to stop.
        :param reason: Human-readable stop reason.
        :returns: Current container info.
        :raises RuntimeError: If the runtime client does not support stop operations.
        """

        if not hasattr(self.client, "stop"):
            raise RuntimeError("Unsupported container runtime")
        info = self.client.stop(name)
        await self._publish_stopped(name, reason=reason)
        return info

    async def restart_container(self, name: str) -> ContainerInfo:
        """Description:
            Restart one managed container.

        Requirements:
            - Publish a started event after the restart succeeds.

        :param name: Container name to restart.
        :returns: Current container info.
        :raises RuntimeError: If the runtime client does not support restart operations.
        """

        if not hasattr(self.client, "restart"):
            raise RuntimeError("Unsupported container runtime")
        info = self.client.restart(name)
        await self._publish_started(info)
        return info

    async def remove_container(self, name: str, *, force: bool = False) -> None:
        """Description:
            Remove one managed container from the runtime.

        Requirements:
            - Publish a stopped event with the ``destroyed`` reason when supported.
            - Ignore the ``force`` flag for runtimes that do not model it directly.

        :param name: Container name to remove.
        :param force: Whether forced removal was requested.
        :raises RuntimeError: If the runtime client does not support container removal.
        """

        del force
        if not hasattr(self.client, "destroy"):
            raise RuntimeError("Unsupported container runtime")
        self.client.destroy(name)
        await self._publish_stopped(name, reason="destroyed")

    async def get_container_status(self, name: str) -> str:
        """Description:
            Return the current runtime status for one container.

        Requirements:
            - Delegate to the runtime ``inspect`` operation.

        :param name: Container name to inspect.
        :returns: Container runtime status string.
        :raises RuntimeError: If the runtime client does not support inspection.
        """

        return self.inspect(name).status

    async def inspect_container(self, name: str) -> ContainerInfo:
        """Description:
            Return the full container info payload for one container.

        Requirements:
            - Delegate to the runtime ``inspect`` operation.

        :param name: Container name to inspect.
        :returns: Container info payload.
        """

        return self.inspect(name)

    def inspect(self, name: str) -> ContainerInfo:
        """Description:
            Provide a synchronous inspect alias for callers that already run on the host thread.

        Requirements:
            - Delegate to the runtime ``inspect`` operation.

        :param name: Container name to inspect.
        :returns: Container info payload.
        :raises RuntimeError: If the runtime client does not support inspection.
        """

        if hasattr(self.client, "inspect"):
            return self.client.inspect(name)
        raise RuntimeError("Unsupported container runtime")

    def list_containers(self) -> list[ContainerInfo]:
        """Description:
            Return the current managed container list.

        Requirements:
            - Delegate to the runtime ``list`` operation.
            - Filter to containers carrying the FAITH managed label.

        :returns: Managed container list.
        :raises RuntimeError: If the runtime client does not support listing.
        """

        if hasattr(self.client, "list"):
            return [item for item in self.client.list() if item.labels.get(FAITH_LABEL) == "true"]
        raise RuntimeError("Unsupported container runtime")

    async def ensure_running(self, spec: ContainerSpec, *, actor: str = "pa") -> ContainerInfo:
        """Description:
            Ensure one container spec is running.

        Requirements:
            - Reuse the regular start path after resolving the environment.
            - Accept the actor argument for compatibility with higher-level callers.

        :param spec: Desired container specification.
        :param actor: Logical actor requesting the operation.
        :returns: Current container info.
        """

        del actor
        return await self.start_container(
            spec.name,
            image=spec.image,
            labels=spec.labels,
            command=spec.command,
            environment=spec.env,
            env_secret_refs=spec.env_secret_refs,
            mounts=spec.mounts,
            container_type=spec.container_type,
            network_mode=spec.network_mode,
            privileged=spec.privileged,
            capabilities=spec.capabilities,
        )

    async def stop(self, name: str, *, actor: str = "pa", reason: str = "normal") -> ContainerInfo:
        """Description:
            Stop one managed container through the higher-level compatibility API.

        Requirements:
            - Delegate to ``stop_container`` while accepting the actor argument.

        :param name: Container name to stop.
        :param actor: Logical actor requesting the stop.
        :param reason: Human-readable stop reason.
        :returns: Current container info.
        """

        del actor
        return await self.stop_container(name, reason=reason)

    async def restart(self, name: str, *, actor: str = "pa") -> ContainerInfo:
        """Description:
            Restart one managed container through the higher-level compatibility API.

        Requirements:
            - Delegate to ``restart_container`` while accepting the actor argument.

        :param name: Container name to restart.
        :param actor: Logical actor requesting the restart.
        :returns: Current container info.
        """

        del actor
        return await self.restart_container(name)

    async def destroy(self, name: str, *, actor: str = "pa") -> None:
        """Description:
            Destroy one managed container through the higher-level compatibility API.

        Requirements:
            - Delegate to ``remove_container`` while accepting the actor argument.

        :param name: Container name to destroy.
        :param actor: Logical actor requesting the removal.
        """

        del actor
        await self.remove_container(name, force=True)

    def discover_agents(self) -> dict[str, dict[str, Any]]:
        """Description:
            Discover project agent configuration files under the active ``.faith`` directory.

        Requirements:
            - Return an empty mapping when no project ``.faith`` directory is configured.
            - Skip malformed or missing config files.

        :returns: Agent configuration payloads keyed by agent identifier.
        """

        if self.faith_dir is None:
            return {}
        agents_dir = self.faith_dir / "agents"
        if not agents_dir.exists():
            return {}
        discovered: dict[str, dict[str, Any]] = {}
        for path in sorted(agents_dir.glob("*/config.yaml")):
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                discovered[path.parent.name] = loaded
        return discovered

    def discover_tools(self) -> dict[str, dict[str, Any]]:
        """Description:
            Discover project tool configuration files under the active ``.faith`` directory.

        Requirements:
            - Resolve secret-backed tool configuration values when a secret resolver is configured.
            - Return an empty mapping when no project ``.faith`` directory is configured.

        :returns: Tool configuration payloads keyed by tool name.
        """

        if self.faith_dir is None:
            return {}
        tools_dir = self.faith_dir / "tools"
        if not tools_dir.exists():
            return {}
        discovered: dict[str, dict[str, Any]] = {}
        for path in sorted(tools_dir.glob("*.yaml")):
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                continue
            discovered[path.stem] = (
                self.secret_resolver.resolve_tool_config(loaded)
                if self.secret_resolver is not None
                else loaded
            )
        return discovered

    def _normalize_mounts(
        self,
        mounts: dict[str, Any] | None,
        *,
        workspace_path: Path | None = None,
    ) -> dict[str, str]:
        """Description:
            Normalise tool mount configuration into a simple host-to-container mapping.

        Requirements:
            - Accept already-normalised mount mappings.
            - Accept structured mount entries carrying `path` and `bind` keys.
            - Resolve relative host paths against the supplied workspace when practical.

        :param mounts: Raw tool mount configuration.
        :param workspace_path: Optional project workspace path used for relative paths.
        :returns: Normalised host-to-container mount mapping.
        """

        if not mounts:
            return {}

        workspace_root = Path(workspace_path).resolve() if workspace_path is not None else None
        resolved: dict[str, str] = {}
        for host_key, raw_mount in mounts.items():
            if isinstance(raw_mount, str):
                resolved[str(Path(host_key).resolve())] = raw_mount
                continue
            if not isinstance(raw_mount, dict):
                continue
            raw_host = raw_mount.get("path") or raw_mount.get("host_path") or host_key
            raw_bind = raw_mount.get("bind") or raw_mount.get("container_path")
            if raw_bind is None:
                continue
            host_path = Path(str(raw_host))
            if not host_path.is_absolute() and workspace_root is not None:
                host_path = workspace_root / host_path
            resolved[str(host_path.resolve())] = str(raw_bind)
        return resolved

    def _split_tool_configs(
        self, tools: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Description:
            Split discovered tool configs into FAITH-owned tool containers and external MCP registrations.

        Requirements:
            - Treat `external-*` tool definitions as project-scoped external MCP registrations.
            - Preserve all remaining tool configs as FAITH-owned tool container definitions.

        :param tools: Discovered tool configuration mapping.
        :returns: Tuple of `(faith_owned_tools, external_mcp_tools)`.
        """

        faith_owned: dict[str, dict[str, Any]] = {}
        external_mcp: dict[str, dict[str, Any]] = {}
        for tool_name, config in tools.items():
            if tool_name.startswith("external-"):
                external_mcp[tool_name] = config
            else:
                faith_owned[tool_name] = config
        return faith_owned, external_mcp

    async def start_mcp_runtime(
        self,
        *,
        external_tools: dict[str, dict[str, Any]],
        workspace_path: Path,
    ) -> ContainerInfo:
        """Description:
            Start the shared project-scoped `mcp-runtime` container when external MCP tools are configured.

        Requirements:
            - Mount the project workspace and `.faith/tools` directory for registration access.
            - Publish the runtime as a managed `mcp-runtime` container on the shared network.
            - Expose the registry URL and version-pinned external tool metadata through the runtime environment.

        :param external_tools: External MCP tool configs keyed by tool name.
        :param workspace_path: Project workspace path.
        :returns: Started `mcp-runtime` container info payload.
        """

        workspace_path = Path(workspace_path).resolve()
        tools_dir = workspace_path / ".faith" / "tools"
        tool_names = ",".join(sorted(external_tools))
        pinned_specs = ",".join(
            sorted(
                f"{tool_name}:{config.get('registry_ref', '')}@{config.get('package_version', '')}"
                for tool_name, config in external_tools.items()
            )
        )
        return await self.start_container(
            "faith-mcp-runtime",
            image="faith-mcp-runtime:latest",
            labels={"faith.runtime": "mcp-runtime"},
            environment={
                "FAITH_EXTERNAL_MCP_COUNT": str(len(external_tools)),
                "FAITH_EXTERNAL_MCP_TOOLS": tool_names,
                "FAITH_EXTERNAL_MCP_PACKAGES": pinned_specs,
                "MCP_REGISTRY_URL": DEFAULT_MCP_REGISTRY_URL,
            },
            mounts={
                str(workspace_path): "/workspace",
                str(tools_dir): "/workspace/.faith/tools",
            },
            container_type="mcp-runtime",
        )

    async def start_agent(
        self,
        *,
        agent_id: str,
        agent_config: dict[str, Any],
        workspace_path: Path,
    ) -> ContainerInfo:
        """Description:
            Start one project agent container using the shared agent-base image.

        Requirements:
            - Mount the project workspace and the agent's own ``.faith`` directory.
            - Apply the canonical FAITH agent labels and environment variables.

        :param agent_id: Agent identifier to start.
        :param agent_config: Parsed agent configuration payload.
        :param workspace_path: Project workspace path.
        :returns: Started container info payload.
        """

        workspace_path = Path(workspace_path).resolve()
        agent_dir = workspace_path / ".faith" / "agents" / agent_id
        return await self.start_container(
            f"faith-agent-{agent_id}",
            image=AGENT_BASE_IMAGE,
            labels={FAITH_AGENT_LABEL: agent_id},
            environment={
                "FAITH_AGENT_ID": agent_id,
                "FAITH_AGENT_MODEL": str(agent_config.get("model", "")),
                "FAITH_AGENT_ROLE": str(agent_config.get("role", "")),
            },
            mounts={
                str(workspace_path): "/workspace",
                str(agent_dir): f"/agent/{agent_id}",
            },
            container_type="agent",
        )

    async def start_tool(
        self,
        *,
        tool_name: str,
        tool_config: dict[str, Any],
        workspace_path: Path,
    ) -> ContainerInfo:
        """Description:
            Start one FAITH-owned tool container from its resolved configuration.

        Requirements:
            - Use the configured image when present and fall back to the FAITH naming convention otherwise.
            - Mount the workspace when the tool configuration requests host mounts.

        :param tool_name: Tool name to start.
        :param tool_config: Parsed tool configuration payload.
        :param workspace_path: Project workspace path.
        :returns: Started container info payload.
        """

        workspace_path = Path(workspace_path).resolve()
        image = str(tool_config.get("image", f"faith-tool-{tool_name}:latest"))
        mounts = self._normalize_mounts(
            tool_config.get("mounts"),
            workspace_path=workspace_path,
        )
        if not mounts and tool_name == "filesystem":
            mounts[str(workspace_path)] = "/workspace"
        env = dict(tool_config.get("env", {}))
        for key, value in tool_config.items():
            if key in {"image", "mounts", "env", "env_secret_refs"}:
                continue
            if isinstance(value, (str, int, float, bool)):
                env[f"TOOL_{key.upper()}"] = str(value)
        env["FAITH_TOOL_NAME"] = tool_name
        return await self.start_container(
            f"faith-tool-{tool_name}",
            image=image,
            labels={FAITH_TOOL_LABEL: tool_name},
            environment=env,
            env_secret_refs=dict(tool_config.get("env_secret_refs", {})),
            mounts=mounts,
            container_type="tool",
        )

    async def start_all(self, workspace_path: Path) -> dict[str, bool]:
        """Description:
            Discover and start all configured agents and tools for the active project.

        Requirements:
            - Continue starting eligible runtimes after individual failures.
            - Return a name-to-success mapping for every attempted runtime.

        :param workspace_path: Project workspace path.
        :returns: Start result mapping keyed by container name.
        """

        results: dict[str, bool] = {}
        faith_owned_tools, external_mcp_tools = self._split_tool_configs(self.discover_tools())
        for agent_id, config in self.discover_agents().items():
            name = f"faith-agent-{agent_id}"
            try:
                await self.start_agent(
                    agent_id=agent_id, agent_config=config, workspace_path=workspace_path
                )
                results[name] = True
            except Exception:
                results[name] = False
        for tool_name, config in faith_owned_tools.items():
            name = f"faith-tool-{tool_name}"
            try:
                await self.start_tool(
                    tool_name=tool_name, tool_config=config, workspace_path=workspace_path
                )
                results[name] = True
            except Exception:
                results[name] = False
        if external_mcp_tools:
            try:
                await self.start_mcp_runtime(
                    external_tools=external_mcp_tools,
                    workspace_path=workspace_path,
                )
                results["faith-mcp-runtime"] = True
            except Exception:
                results["faith-mcp-runtime"] = False
        return results

    async def stop_all(self) -> None:
        """Description:
            Stop every currently managed container.

        Requirements:
            - Stop containers in deterministic name order.
        """

        for info in self.list_containers():
            if info.status == "running":
                await self.stop_container(info.name)

    async def reattach_running(self) -> int:
        """Description:
            Count and return running FAITH-managed containers already present in the runtime.

        Requirements:
            - Include only containers that are still in the running state.

        :returns: Number of running containers.
        """

        return sum(
            1
            for info in self.list_containers()
            if info.status == "running" and info.labels.get(FAITH_LABEL) == "true"
        )

    async def reconfigure_tool(
        self, tool_name: str, tool_config: dict[str, Any], workspace_path: Path
    ) -> ContainerInfo:
        """Description:
            Reconfigure one tool runtime by ensuring it is running with the latest config.

        Requirements:
            - Delegate to ``start_tool`` for idempotent create-or-update behaviour.

        :param tool_name: Tool name to reconfigure.
        :param tool_config: Parsed tool configuration payload.
        :param workspace_path: Project workspace path.
        :returns: Current tool container info.
        """

        return await self.start_tool(
            tool_name=tool_name, tool_config=tool_config, workspace_path=workspace_path
        )

    async def signal_agent_finish(self, agent_id: str) -> None:
        """Description:
            Yield once while simulating an agent finish signal for higher-level orchestrators.

        Requirements:
            - Provide an awaitable compatibility hook for project-switch teardown.

        :param agent_id: Agent identifier receiving the finish signal.
        """

        del agent_id
        await asyncio.sleep(0)

    async def get_agent_state(self, agent_id: str) -> dict[str, Any]:
        """Description:
            Return a lightweight placeholder agent-state payload for teardown flows.

        Requirements:
            - Preserve the agent identifier and an idle baseline summary.

        :param agent_id: Agent identifier to inspect.
        :returns: Lightweight agent-state payload.
        """

        return {
            "agent_id": agent_id,
            "status": "idle",
            "summary": "No persisted runtime state available.",
            "channel_assignments": [],
            "file_watches": [],
            "current_task": "",
            "progress": "",
        }
