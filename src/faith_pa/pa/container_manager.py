"""Description:
    Manage container lifecycle for PA-owned runtime components and sandbox infrastructure.

Requirements:
    - Support a Docker-like runtime abstraction for tests and future runtime integrations.
    - Resolve environment variables and secret references before container startup.
    - Expose start, stop, restart, inspect, and destroy operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from faith_pa.pa.secret_resolver import SecretResolver
from faith_shared.protocol.events import EventPublisher


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

    :param name: Managed container name.
    :param image: Container image reference.
    :param command: Container command override.
    :param env: Plain environment variables.
    :param env_secret_refs: Environment-variable secret references.
    :param mounts: Host-to-container mount mapping.
    :param labels: Container metadata labels.
    :param container_type: Logical container type label.
    """

    name: str
    image: str
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    env_secret_refs: dict[str, str] = field(default_factory=dict)
    mounts: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    container_type: str = "generic"


@dataclass(slots=True)
class ContainerInfo:
    """Description:
        Represent the current observed state of one managed container.

    Requirements:
        - Preserve image, status, command, labels, restart count, and logical type.

    :param name: Managed container name.
    :param image: Container image reference.
    :param status: Current runtime status.
    :param labels: Container metadata labels.
    :param command: Effective command list.
    :param restart_count: Number of recorded restarts.
    :param container_type: Logical container type label.
    :param created_at: Creation timestamp.
    """

    name: str
    image: str
    status: str
    labels: dict[str, str] = field(default_factory=dict)
    command: list[str] | None = None
    restart_count: int = 0
    container_type: str = "generic"
    created_at: str | None = None


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
            labels=record.spec.labels,
            command=record.spec.command,
            restart_count=record.restart_count,
            container_type=record.spec.container_type,
            created_at=record.created_at,
        )

    def list(self) -> list[ContainerInfo]:
        """Description:
            Return the managed containers sorted by name.

        Requirements:
            - Provide stable ordering for deterministic tests and UI rendering.

        :returns: Sorted container info payloads.
        """

        return [self.inspect(name) for name in sorted(self.records)]


class ContainerManager:
    """Description:
        Manage the lifecycle of FAITH-owned containers against a runtime abstraction.

    Requirements:
        - Support secret-aware environment resolution before startup.
        - Ensure the configured network exists when the runtime supports network management.
        - Publish container lifecycle events when an event publisher is supplied.

    :param client: Docker-like runtime client or test runtime.
    :param network_name: Logical network name to ensure at startup.
    :param secret_resolver: Optional secret resolver used for environment preparation.
    :param event_publisher: Optional event publisher for lifecycle notifications.
    :param audit_logger: Optional audit logger reserved for future lifecycle audit support.
    """

    def __init__(
        self,
        client: Any,
        *,
        network_name: str = "maf-network",
        secret_resolver: SecretResolver | None = None,
        event_publisher: EventPublisher | Any | None = None,
        audit_logger: Any | None = None,
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
        """

        self.client = client
        self.network_name = network_name
        self.secret_resolver = secret_resolver
        self.event_publisher = event_publisher
        self.audit_logger = audit_logger
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

    async def start_container(
        self,
        name: str,
        *,
        image: str,
        labels: dict[str, str] | None = None,
        command: list[str] | None = None,
        environment: dict[str, str] | None = None,
        env_secret_refs: dict[str, str] | None = None,
        container_type: str = "generic",
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
        :param container_type: Logical container type label.
        :returns: Current container info.
        :raises RuntimeError: If the runtime client does not support container creation.
        """

        spec = ContainerSpec(
            name=name,
            image=image,
            labels=labels or {},
            command=command or [],
            env=environment or {},
            env_secret_refs=env_secret_refs or {},
            container_type=container_type,
        )
        resolved_env = environment or self._resolve_env(spec)
        spec.env = resolved_env
        if hasattr(self.client, "create_or_update"):
            info = self.client.create_or_update(spec)
        else:
            raise RuntimeError("Unsupported container runtime")
        if self.event_publisher and hasattr(self.event_publisher, "system_container_started"):
            await self.event_publisher.system_container_started(name, info.container_type)
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

        if hasattr(self.client, "stop"):
            info = self.client.stop(name)
        else:
            raise RuntimeError("Unsupported container runtime")
        if self.event_publisher and hasattr(self.event_publisher, "system_container_stopped"):
            await self.event_publisher.system_container_stopped(name, reason=reason)
        return info

    async def restart_container(self, name: str) -> ContainerInfo:
        """Description:
            Restart one managed container.

        Requirements:
            - Publish a container-started event when the publisher exposes that helper.

        :param name: Container name to restart.
        :returns: Current container info.
        :raises RuntimeError: If the runtime client does not support restart operations.
        """

        if hasattr(self.client, "restart"):
            info = self.client.restart(name)
        else:
            raise RuntimeError("Unsupported container runtime")
        if self.event_publisher and hasattr(self.event_publisher, "system_container_started"):
            await self.event_publisher.system_container_started(name, info.container_type)
        return info

    async def remove_container(self, name: str, *, force: bool = False) -> None:
        """Description:
            Remove one managed container from the runtime.

        Requirements:
            - Publish a container-stopped event with the ``destroyed`` reason when supported.
            - Ignore the ``force`` flag for runtimes that do not model it directly.

        :param name: Container name to remove.
        :param force: Whether forced removal was requested.
        :raises RuntimeError: If the runtime client does not support container removal.
        """

        del force
        if hasattr(self.client, "destroy"):
            self.client.destroy(name)
        else:
            raise RuntimeError("Unsupported container runtime")
        if self.event_publisher and hasattr(self.event_publisher, "system_container_stopped"):
            await self.event_publisher.system_container_stopped(name, reason="destroyed")

    async def get_container_status(self, name: str) -> str:
        """Description:
            Return the current runtime status for one container.

        Requirements:
            - Delegate to the runtime ``inspect`` operation.

        :param name: Container name to inspect.
        :returns: Container runtime status string.
        :raises RuntimeError: If the runtime client does not support inspection.
        """

        if hasattr(self.client, "inspect"):
            return self.client.inspect(name).status
        raise RuntimeError("Unsupported container runtime")

    async def inspect_container(self, name: str) -> ContainerInfo:
        """Description:
            Return the full container info payload for one container.

        Requirements:
            - Delegate to the runtime ``inspect`` operation.

        :param name: Container name to inspect.
        :returns: Container info payload.
        :raises RuntimeError: If the runtime client does not support inspection.
        """

        if hasattr(self.client, "inspect"):
            return self.client.inspect(name)
        raise RuntimeError("Unsupported container runtime")

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

        :returns: Managed container list.
        :raises RuntimeError: If the runtime client does not support listing.
        """

        if hasattr(self.client, "list"):
            return self.client.list()
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
            environment=self._resolve_env(spec),
            env_secret_refs=spec.env_secret_refs,
            container_type=spec.container_type,
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
