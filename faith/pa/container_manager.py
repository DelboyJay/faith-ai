"""Container lifecycle management for the PA."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from faith.pa.secret_resolver import SecretResolver
from faith.protocol.events import EventPublisher


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class ContainerSpec:
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
    name: str
    image: str
    status: str
    labels: dict[str, str] = field(default_factory=dict)
    command: list[str] | None = None
    restart_count: int = 0
    container_type: str = "generic"
    created_at: str | None = None


class _RuntimeRecord:
    def __init__(self, spec: ContainerSpec, status: str = "running") -> None:
        self.spec = spec
        self.status = status
        self.restart_count = 0
        self.created_at = _utc_now()


class InMemoryContainerRuntime:
    def __init__(self) -> None:
        self.records: dict[str, _RuntimeRecord] = {}
        self.networks: set[str] = set()

    def ensure_network(self, name: str) -> None:
        self.networks.add(name)

    def create_or_update(self, spec: ContainerSpec) -> ContainerInfo:
        record = self.records.get(spec.name)
        if record is None:
            record = _RuntimeRecord(spec)
            self.records[spec.name] = record
        else:
            record.spec = spec
            record.status = "running"
        return self.inspect(spec.name)

    def stop(self, name: str) -> ContainerInfo:
        record = self.records[name]
        record.status = "stopped"
        return self.inspect(name)

    def restart(self, name: str) -> ContainerInfo:
        record = self.records[name]
        record.status = "running"
        record.restart_count += 1
        return self.inspect(name)

    def destroy(self, name: str) -> None:
        self.records.pop(name, None)

    def inspect(self, name: str) -> ContainerInfo:
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
        return [self.inspect(name) for name in sorted(self.records)]


class ContainerManager:
    """Manage container lifecycle against a Docker-like client or test runtime."""

    def __init__(
        self,
        client: Any,
        *,
        network_name: str = "maf-network",
        secret_resolver: SecretResolver | None = None,
        event_publisher: EventPublisher | Any | None = None,
        audit_logger: Any | None = None,
    ) -> None:
        self.client = client
        self.network_name = network_name
        self.secret_resolver = secret_resolver
        self.event_publisher = event_publisher
        self.audit_logger = audit_logger
        if hasattr(self.client, "ensure_network"):
            self.client.ensure_network(network_name)

    def _resolve_env(self, spec: ContainerSpec) -> dict[str, str]:
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
        if hasattr(self.client, "stop"):
            info = self.client.stop(name)
        else:
            raise RuntimeError("Unsupported container runtime")
        if self.event_publisher and hasattr(self.event_publisher, "system_container_stopped"):
            await self.event_publisher.system_container_stopped(name, reason=reason)
        return info

    async def restart_container(self, name: str) -> ContainerInfo:
        if hasattr(self.client, "restart"):
            info = self.client.restart(name)
        else:
            raise RuntimeError("Unsupported container runtime")
        if self.event_publisher and hasattr(self.event_publisher, "system_container_started"):
            await self.event_publisher.system_container_started(name, info.container_type)
        return info

    async def remove_container(self, name: str, *, force: bool = False) -> None:
        del force
        if hasattr(self.client, "destroy"):
            self.client.destroy(name)
        else:
            raise RuntimeError("Unsupported container runtime")
        if self.event_publisher and hasattr(self.event_publisher, "system_container_stopped"):
            await self.event_publisher.system_container_stopped(name, reason="destroyed")

    async def get_container_status(self, name: str) -> str:
        if hasattr(self.client, "inspect"):
            return self.client.inspect(name).status
        raise RuntimeError("Unsupported container runtime")

    async def inspect_container(self, name: str) -> ContainerInfo:
        if hasattr(self.client, "inspect"):
            return self.client.inspect(name)
        raise RuntimeError("Unsupported container runtime")

    def inspect(self, name: str) -> ContainerInfo:
        if hasattr(self.client, "inspect"):
            return self.client.inspect(name)
        raise RuntimeError("Unsupported container runtime")

    def list_containers(self) -> list[ContainerInfo]:
        if hasattr(self.client, "list"):
            return self.client.list()
        raise RuntimeError("Unsupported container runtime")

    async def ensure_running(self, spec: ContainerSpec, *, actor: str = "pa") -> ContainerInfo:
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
        del actor
        return await self.stop_container(name, reason=reason)

    async def restart(self, name: str, *, actor: str = "pa") -> ContainerInfo:
        del actor
        return await self.restart_container(name)

    async def destroy(self, name: str, *, actor: str = "pa") -> None:
        del actor
        await self.remove_container(name, force=True)
