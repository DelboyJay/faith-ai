"""Description:
    Allocate, reuse, reset, and release disposable sandboxes for PA tasks and sub-agents.

Requirements:
    - Reuse one shared sandbox when the request does not require isolation.
    - Allocate isolated sandboxes for destructive or isolation-required work.
    - Enforce the configured concurrent sandbox quota.
    - Emit sandbox lifecycle events and audit entries when integrations are supplied.
"""

from __future__ import annotations

from typing import Any

from faith_pa.pa.container_manager import ContainerSpec
from faith_pa.pa.sandbox_models import (
    ResourceQuota,
    SandboxAllocationMode,
    SandboxPolicy,
    SandboxQuota,
    SandboxRecord,
    SandboxRequest,
    SandboxState,
)
from faith_shared.protocol.events import EventType, FaithEvent


class SandboxQuotaExceeded(RuntimeError):
    """Description:
        Raise when sandbox allocation would exceed the configured quota.

    Requirements:
        - Signal quota exhaustion as a runtime error.
    """


class SandboxManager:
    """Description:
        Allocate shared or isolated disposable sandboxes through a runtime abstraction.

    Requirements:
        - Reuse a shared sandbox when compatible requests can safely share one.
        - Create isolated sandboxes for destructive or isolation-required requests.
        - Reset or destroy runtime sandboxes through the supplied runtime abstraction.

    :param runtime: Runtime abstraction used to create and destroy sandboxes.
    :param quota: Sandbox quota configuration.
    :param image: Sandbox image reference.
    :param event_publisher: Optional event publisher used for sandbox lifecycle notifications.
    :param audit_logger: Optional audit logger used for sandbox lifecycle decisions.
    """

    def __init__(
        self,
        runtime,
        *,
        quota: SandboxQuota | ResourceQuota | None = None,
        image: str = "ghcr.io/faith/sandbox:latest",
        event_publisher: Any | None = None,
        audit_logger: Any | None = None,
    ):
        """Description:
            Initialise the sandbox manager.

        Requirements:
            - Start with no active sandboxes and no shared sandbox selection.

        :param runtime: Runtime abstraction used to create and destroy sandboxes.
        :param quota: Sandbox quota configuration.
        :param image: Sandbox image reference.
        :param event_publisher: Optional event publisher used for sandbox lifecycle notifications.
        :param audit_logger: Optional audit logger used for sandbox lifecycle decisions.
        """

        self.runtime = runtime
        self.quota = quota or SandboxQuota()
        self.image = image
        self.event_publisher = event_publisher
        self.audit_logger = audit_logger
        self._counter = 0
        self._sandboxes: dict[str, SandboxRecord] = {}
        self._shared_sandbox_id: str | None = None

    def _next_id(self) -> str:
        """Description:
            Return the next sequential sandbox identifier.

        Requirements:
            - Use the stable ``sbx-`` prefix with zero-padded numbering.

        :returns: Next sandbox identifier.
        """

        self._counter += 1
        return f"sbx-{self._counter:04d}"

    def _active_count(self) -> int:
        """Description:
            Return the number of non-destroyed sandbox records.

        Requirements:
            - Exclude records already marked as destroyed.

        :returns: Active sandbox count.
        """

        return sum(
            1 for record in self._sandboxes.values() if record.state is not SandboxState.DESTROYED
        )

    def _container_name(self, sandbox_id: str) -> str:
        """Description:
            Build the container name for one sandbox identifier.

        Requirements:
            - Use the stable ``faith-sandbox-`` prefix.

        :param sandbox_id: Sandbox identifier.
        :returns: Sandbox container name.
        """

        return f"faith-sandbox-{sandbox_id}"

    def _sandbox_spec(self, record: SandboxRecord) -> ContainerSpec:
        """Description:
            Build the runtime container spec for one sandbox record.

        Requirements:
            - Attach role, sandbox, session, and task labels to the sandbox container.

        :param record: Sandbox record to convert.
        :returns: Sandbox container specification.
        """

        return ContainerSpec(
            name=record.container_name,
            image=self.image,
            container_type="sandbox",
            mounts=dict(record.policy.approved_mounts),
            labels={
                "faith.role": "sandbox",
                "faith.sandbox_id": record.sandbox_id,
                "faith.session_id": record.session_id,
                "faith.task_id": record.task_id,
                "faith.allocation_mode": record.allocation_mode.value,
                "faith.network_mode": record.policy.network_mode,
            },
        )

    async def _publish_event(self, event: EventType | str, **data: Any) -> None:
        """Description:
            Publish one sandbox lifecycle event when an event publisher is configured.

        Requirements:
            - Prefer a generic ``publish`` method.
            - Preserve the event name and structured data unchanged.

        :param event: Sandbox lifecycle event name.
        :param data: Structured event data payload.
        """

        if self.event_publisher is None or not hasattr(self.event_publisher, "publish"):
            return
        if isinstance(event, EventType):
            payload: Any = FaithEvent(event=event, source="sandbox_manager", data=data)
        else:
            payload = {"event": event, "source": "sandbox_manager", "data": data}
        await self.event_publisher.publish(payload)

    async def _audit(self, action: str, record: SandboxRecord, **extra: Any) -> None:
        """Description:
            Emit one sandbox audit entry when an audit logger is configured.

        Requirements:
            - Include the sandbox identity, task ownership, allocation mode, and action name.

        :param action: Sandbox lifecycle action.
        :param record: Sandbox record associated with the audit entry.
        :param extra: Additional audit metadata.
        """

        if self.audit_logger is None or not hasattr(self.audit_logger, "record"):
            return
        await self.audit_logger.record(
            action=action,
            sandbox_id=record.sandbox_id,
            session_id=record.session_id,
            task_id=record.task_id,
            allocation_mode=record.allocation_mode.value,
            **extra,
        )

    def _build_policy(self, request: SandboxRequest) -> SandboxPolicy:
        """Description:
            Build the hardened sandbox policy for one allocation request.

        Requirements:
            - Allow approved mounts only.
            - Never allow privileged mode, host networking, or Docker socket access.
            - Carry the quota-derived CPU, memory, and disk settings into the policy.

        :param request: Sandbox allocation request.
        :returns: Hardened sandbox policy.
        """

        cleaned_mounts = {
            host_path: mount_path
            for host_path, mount_path in request.approved_mounts.items()
            if "docker.sock" not in host_path.lower()
        }
        return SandboxPolicy(
            approved_mounts=cleaned_mounts,
            network_mode="bridge",
            privileged=False,
            docker_socket_allowed=False,
            linux_capabilities=list(request.linux_capabilities),
            cpu_limit=self.quota.cpu_limit,
            memory_mb=self.quota.memory_mb,
            disk_mb=self.quota.disk_mb,
        )

    async def _create_runtime(self, record: SandboxRecord) -> None:
        """Description:
            Create or start the runtime container for one sandbox record.

        Requirements:
            - Prefer the higher-level ``ensure_running`` API when the runtime exposes it.
            - Fall back to a lower-level ``create`` API otherwise.

        :param record: Sandbox record to materialise.
        """

        if hasattr(self.runtime, "ensure_running"):
            await self.runtime.ensure_running(self._sandbox_spec(record))
        else:
            await self.runtime.create(record)

    async def _destroy_runtime(self, record: SandboxRecord) -> None:
        """Description:
            Destroy the runtime container for one sandbox record.

        Requirements:
            - Prefer the higher-level ``destroy`` API using the container name when available.
            - Fall back to sandbox-id destruction for older runtimes.

        :param record: Sandbox record to destroy.
        """

        if hasattr(self.runtime, "destroy") and hasattr(self.runtime, "ensure_running"):
            await self.runtime.destroy(record.container_name)
        else:
            await self.runtime.destroy(record.sandbox_id)

    async def allocate(self, request: SandboxRequest) -> SandboxRecord:
        """Description:
            Allocate or reuse a sandbox for one request.

        Requirements:
            - Reuse the current shared sandbox when the request is shareable.
            - Enforce the concurrent sandbox quota before creating a new sandbox.
            - Mark new runtime sandboxes as ready after creation.

        :param request: Sandbox allocation request.
        :returns: Allocated or reused sandbox record.
        :raises SandboxQuotaExceeded: If allocation would exceed the configured quota.
        """

        shared_ok = (
            request.mode is SandboxAllocationMode.SHARED
            and not request.requires_isolation
            and not request.destructive
        )
        if shared_ok and self._shared_sandbox_id:
            record = self._sandboxes[self._shared_sandbox_id]
            record.agents.add(request.agent_id)
            record.reuse_count += 1
            await self._publish_event(
                "sandbox:reused",
                sandbox_id=record.sandbox_id,
                task_id=record.task_id,
                agent_id=request.agent_id,
            )
            await self._audit("reuse", record, agent_id=request.agent_id)
            return record

        if self._active_count() >= self.quota.max_concurrent:
            raise SandboxQuotaExceeded("sandbox quota exceeded")

        sandbox_id = self._next_id()
        allocation_mode = (
            SandboxAllocationMode.SHARED if shared_ok else SandboxAllocationMode.ISOLATED
        )
        record = SandboxRecord(
            sandbox_id=sandbox_id,
            session_id=request.session_id,
            task_id=request.task_id,
            workspace=request.workspace,
            purpose=request.purpose,
            allocation_mode=allocation_mode,
            state=SandboxState.CREATING,
            image=self.image,
            container_name=self._container_name(sandbox_id),
            agents={request.agent_id},
            policy=self._build_policy(request),
        )
        self._sandboxes[sandbox_id] = record
        if allocation_mode is SandboxAllocationMode.SHARED:
            self._shared_sandbox_id = sandbox_id
        await self._create_runtime(record)
        record.state = SandboxState.READY
        await self._publish_event(
            EventType.SYSTEM_CONTAINER_STARTED,
            container_name=record.container_name,
            container_type="sandbox",
        )
        await self._publish_event(
            "sandbox:created",
            sandbox_id=record.sandbox_id,
            task_id=record.task_id,
            agent_id=request.agent_id,
            allocation_mode=record.allocation_mode.value,
        )
        await self._audit("create", record, agent_id=request.agent_id)
        return record

    async def reset(self, sandbox_id: str) -> SandboxRecord:
        """Description:
            Reset one sandbox by destroying and recreating its runtime container.

        Requirements:
            - Mark the sandbox as resetting during the reset operation.
            - Return the sandbox to the ready state afterward.

        :param sandbox_id: Sandbox identifier to reset.
        :returns: Reset sandbox record.
        """

        record = self._sandboxes[sandbox_id]
        record.state = SandboxState.RESETTING
        await self._destroy_runtime(record)
        await self._create_runtime(record)
        record.state = SandboxState.READY
        await self._publish_event("sandbox:reset", sandbox_id=record.sandbox_id)
        await self._audit("reset", record)
        return record

    async def release(self, sandbox_id: str, *, agent_id: str) -> None:
        """Description:
            Release one agent's claim on a sandbox and destroy it when no longer needed.

        Requirements:
            - Destroy isolated sandboxes immediately on release.
            - Destroy shared sandboxes once no agents remain attached.
            - Clear the shared-sandbox pointer when that sandbox is destroyed.

        :param sandbox_id: Sandbox identifier to release.
        :param agent_id: Agent identifier to detach from the sandbox.
        """

        record = self._sandboxes[sandbox_id]
        record.agents.discard(agent_id)
        await self._audit("release", record, agent_id=agent_id)
        if record.allocation_mode is SandboxAllocationMode.ISOLATED or not record.agents:
            await self._destroy_runtime(record)
            record.state = SandboxState.DESTROYED
            if self._shared_sandbox_id == sandbox_id:
                self._shared_sandbox_id = None
            await self._publish_event(
                EventType.SYSTEM_CONTAINER_STOPPED,
                container_name=record.container_name,
                reason="destroyed",
            )
            await self._publish_event("sandbox:destroyed", sandbox_id=record.sandbox_id)
            await self._audit("destroy", record)

    def get(self, sandbox_id: str) -> SandboxRecord:
        """Description:
            Return one tracked sandbox record by identifier.

        Requirements:
            - Expose the current sandbox record without mutating it.

        :param sandbox_id: Sandbox identifier to inspect.
        :returns: Tracked sandbox record.
        """

        return self._sandboxes[sandbox_id]
