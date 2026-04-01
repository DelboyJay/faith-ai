from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from faith.pa.sandbox_manager import SandboxManager
from faith.pa.sandbox_models import (
    SandboxAllocationMode,
    SandboxQuota,
    SandboxRequest,
    SandboxState,
)


@dataclass
class FakeContainerManager:
    created: list[str] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)

    async def ensure_running(self, spec, *, actor: str = "pa"):
        self.created.append(spec.name)
        return spec

    async def destroy(self, name: str, *, actor: str = "pa"):
        self.destroyed.append(name)


@pytest.mark.asyncio
async def test_shared_sandbox_is_reused() -> None:
    manager = SandboxManager(FakeContainerManager(), quota=SandboxQuota(max_concurrent=2))

    first = await manager.allocate(
        SandboxRequest(
            session_id="sess-1", task_id="task-1", agent_id="agent-a", purpose="/workspace"
        )
    )
    second = await manager.allocate(
        SandboxRequest(
            session_id="sess-1", task_id="task-1", agent_id="agent-b", purpose="/workspace"
        )
    )

    assert first.sandbox_id == second.sandbox_id
    assert second.reuse_count == 1
    assert second.allocation_mode is SandboxAllocationMode.SHARED


@pytest.mark.asyncio
async def test_isolated_sandbox_gets_unique_container() -> None:
    manager = SandboxManager(FakeContainerManager(), quota=SandboxQuota(max_concurrent=3))

    first = await manager.allocate(
        SandboxRequest(
            session_id="sess-1",
            task_id="task-1",
            agent_id="agent-a",
            purpose="/workspace",
            requires_isolation=True,
        )
    )
    second = await manager.allocate(
        SandboxRequest(
            session_id="sess-1",
            task_id="task-2",
            agent_id="agent-b",
            purpose="/workspace",
            destructive=True,
        )
    )

    assert first.sandbox_id != second.sandbox_id
    assert first.allocation_mode is SandboxAllocationMode.ISOLATED
    assert second.allocation_mode is SandboxAllocationMode.ISOLATED


@pytest.mark.asyncio
async def test_reset_recreates_sandbox() -> None:
    runtime = FakeContainerManager()
    manager = SandboxManager(runtime, quota=SandboxQuota(max_concurrent=2))
    allocation = await manager.allocate(
        SandboxRequest(
            session_id="sess-1",
            task_id="task-1",
            agent_id="agent-a",
            purpose="/workspace",
            requires_isolation=True,
        )
    )

    record = await manager.reset(allocation.sandbox_id)

    assert record.state is SandboxState.READY
    assert runtime.destroyed[0].endswith(allocation.sandbox_id)
    assert runtime.created[0].endswith(allocation.sandbox_id)
    assert runtime.created[1].endswith(allocation.sandbox_id)
