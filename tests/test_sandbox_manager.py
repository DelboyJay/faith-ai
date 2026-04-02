"""Description:
    Verify sandbox allocation, reuse, reset, quota enforcement, and runtime integration.

Requirements:
    - Prove shared sandboxes are reused when safe.
    - Prove isolated sandboxes receive distinct allocations.
    - Prove reset and quota-enforcement behaviour works as expected.
    - Prove sandbox allocation integrates with the container manager runtime abstraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from faith_pa.pa.sandbox_manager import SandboxManager, SandboxQuotaExceeded
from faith_pa.pa.sandbox_models import (
    SandboxAllocationMode,
    SandboxQuota,
    SandboxRequest,
    SandboxState,
)


@dataclass
class FakeContainerManager:
    """Description:
        Provide a minimal runtime double for sandbox-manager tests.

    Requirements:
        - Record created and destroyed sandbox runtime names.
    """

    created: list[str] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)

    async def ensure_running(self, spec, *, actor: str = "pa"):
        """Description:
            Record sandbox runtime creation.

        Requirements:
            - Append the created runtime name for later assertions.

        :param spec: Sandbox container specification.
        :param actor: Logical actor requesting the operation.
        :returns: Supplied sandbox spec.
        """

        del actor
        self.created.append(spec.name)
        return spec

    async def destroy(self, name: str, *, actor: str = "pa"):
        """Description:
            Record sandbox runtime destruction.

        Requirements:
            - Append the destroyed runtime name for later assertions.

        :param name: Sandbox runtime name.
        :param actor: Logical actor requesting the operation.
        """

        del actor
        self.destroyed.append(name)


@pytest.mark.asyncio
async def test_shared_sandbox_is_reused() -> None:
    """Description:
        Verify compatible requests reuse a shared sandbox.

        Requirements:
            - This test is needed to prove sandbox allocation does not waste resources for compatible work.
            - Verify both requests resolve to the same sandbox and the reuse count increases.
    """

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
    """Description:
        Verify isolation-required requests receive unique sandbox allocations.

        Requirements:
            - This test is needed to prove destructive or isolated work does not share runtime state.
            - Verify two isolation-requiring requests produce distinct isolated sandboxes.
    """

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
    """Description:
        Verify resetting a sandbox destroys and recreates its runtime.

        Requirements:
            - This test is needed to prove the PA can recover a sandbox to a clean state.
            - Verify the sandbox returns to the ready state and the runtime is destroyed then recreated.
    """

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


@pytest.mark.asyncio
async def test_quota_is_enforced() -> None:
    """Description:
        Verify sandbox allocation fails when the configured quota would be exceeded.

        Requirements:
            - This test is needed to prove the scheduler respects sandbox resource limits.
            - Verify the manager raises ``SandboxQuotaExceeded`` when a new allocation would exceed the limit.
    """

    manager = SandboxManager(FakeContainerManager(), quota=SandboxQuota(max_concurrent=1))
    await manager.allocate(
        SandboxRequest(
            session_id="sess-1",
            task_id="task-1",
            agent_id="agent-a",
            purpose="/workspace",
            requires_isolation=True,
        )
    )

    with pytest.raises(SandboxQuotaExceeded):
        await manager.allocate(
            SandboxRequest(
                session_id="sess-1",
                task_id="task-2",
                agent_id="agent-b",
                purpose="/workspace",
                requires_isolation=True,
            )
        )
