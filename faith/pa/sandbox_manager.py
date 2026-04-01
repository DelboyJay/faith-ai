"""Disposable sandbox lifecycle and scheduling."""

from __future__ import annotations

from faith.pa.container_manager import ContainerSpec
from faith.pa.sandbox_models import (
    ResourceQuota,
    SandboxAllocationMode,
    SandboxQuota,
    SandboxRecord,
    SandboxRequest,
    SandboxState,
)


class SandboxQuotaExceeded(RuntimeError):
    """Raised when sandbox allocation exceeds the configured quota."""


class SandboxManager:
    """Allocate shared or isolated sandboxes through a runtime abstraction."""

    def __init__(
        self,
        runtime,
        *,
        quota: SandboxQuota | ResourceQuota | None = None,
        image: str = "ghcr.io/faith/sandbox:latest",
    ):
        self.runtime = runtime
        self.quota = quota or SandboxQuota()
        self.image = image
        self._counter = 0
        self._sandboxes: dict[str, SandboxRecord] = {}
        self._shared_sandbox_id: str | None = None

    def _next_id(self) -> str:
        self._counter += 1
        return f"sbx-{self._counter:04d}"

    def _active_count(self) -> int:
        return sum(
            1 for record in self._sandboxes.values() if record.state is not SandboxState.DESTROYED
        )

    def _container_name(self, sandbox_id: str) -> str:
        return f"faith-sandbox-{sandbox_id}"

    def _sandbox_spec(self, record: SandboxRecord) -> ContainerSpec:
        return ContainerSpec(
            name=record.container_name,
            image=self.image,
            container_type="sandbox",
            labels={
                "faith.role": "sandbox",
                "faith.sandbox_id": record.sandbox_id,
                "faith.session_id": record.session_id,
                "faith.task_id": record.task_id,
            },
        )

    async def _create_runtime(self, record: SandboxRecord) -> None:
        if hasattr(self.runtime, "ensure_running"):
            await self.runtime.ensure_running(self._sandbox_spec(record))
        else:
            await self.runtime.create(record)

    async def _destroy_runtime(self, record: SandboxRecord) -> None:
        if hasattr(self.runtime, "destroy") and hasattr(self.runtime, "ensure_running"):
            await self.runtime.destroy(record.container_name)
        else:
            await self.runtime.destroy(record.sandbox_id)

    async def allocate(self, request: SandboxRequest) -> SandboxRecord:
        shared_ok = (
            request.mode is SandboxAllocationMode.SHARED
            and not request.requires_isolation
            and not request.destructive
        )
        if shared_ok and self._shared_sandbox_id:
            record = self._sandboxes[self._shared_sandbox_id]
            record.agents.add(request.agent_id)
            record.reuse_count += 1
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
        )
        self._sandboxes[sandbox_id] = record
        if allocation_mode is SandboxAllocationMode.SHARED:
            self._shared_sandbox_id = sandbox_id
        await self._create_runtime(record)
        record.state = SandboxState.READY
        return record

    async def reset(self, sandbox_id: str) -> SandboxRecord:
        record = self._sandboxes[sandbox_id]
        record.state = SandboxState.RESETTING
        await self._destroy_runtime(record)
        await self._create_runtime(record)
        record.state = SandboxState.READY
        return record

    async def release(self, sandbox_id: str, *, agent_id: str) -> None:
        record = self._sandboxes[sandbox_id]
        record.agents.discard(agent_id)
        if record.allocation_mode is SandboxAllocationMode.ISOLATED or not record.agents:
            await self._destroy_runtime(record)
            record.state = SandboxState.DESTROYED
            if self._shared_sandbox_id == sandbox_id:
                self._shared_sandbox_id = None
