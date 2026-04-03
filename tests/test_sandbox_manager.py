"""Description:
    Verify sandbox allocation, reuse, reset, quota enforcement, and runtime integration.

Requirements:
    - Prove shared sandboxes are reused when safe.
    - Prove isolated sandboxes receive distinct allocations.
    - Prove reset and quota-enforcement behaviour works as expected.
    - Prove sandbox allocation integrates with lifecycle event and audit reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from faith_pa.pa.sandbox_manager import SandboxManager, SandboxQuotaExceeded
from faith_pa.pa.sandbox_models import (
    ResourceQuota,
    SandboxAllocationMode,
    SandboxQuota,
    SandboxRequest,
    SandboxState,
)
from faith_shared.protocol.events import EventType, FaithEvent


@dataclass
class FakeEventPublisher:
    """Description:
        Capture sandbox lifecycle events for assertions.

    Requirements:
        - Preserve every published event in order.
    """

    published: list[Any] = field(default_factory=list)

    async def publish(self, event: Any) -> None:
        """Description:
            Record one published lifecycle event.

        Requirements:
            - Preserve the raw event payload for later assertions.

        :param event: Published event payload.
        """

        self.published.append(event)


@dataclass
class FakeAuditLogger:
    """Description:
        Capture sandbox audit entries for assertions.

    Requirements:
        - Preserve each recorded action payload.
    """

    entries: list[dict[str, Any]] = field(default_factory=list)

    async def record(self, **entry: Any) -> None:
        """Description:
            Record one sandbox audit entry.

        Requirements:
            - Preserve the supplied entry mapping unchanged.

        :param entry: Audit entry payload.
        """

        self.entries.append(entry)


@dataclass
class FakeContainerManager:
    """Description:
        Provide a minimal runtime double for sandbox-manager tests.

    Requirements:
        - Record created specs and destroyed sandbox runtime names.
    """

    created: list[Any] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)

    async def ensure_running(self, spec, *, actor: str = "pa"):
        """Description:
            Record sandbox runtime creation.

        Requirements:
            - Append the full container spec for later assertions.

        :param spec: Sandbox container specification.
        :param actor: Logical actor requesting the operation.
        :returns: Supplied sandbox spec.
        """

        del actor
        self.created.append(spec)
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
async def test_allocate_builds_hardened_runtime_spec() -> None:
    """Description:
        Verify sandbox allocation builds a hardened runtime spec with approved mounts only.

    Requirements:
        - This test is needed to prove sandbox creation follows the disposable-container isolation model from the FRS.
        - Verify the runtime spec omits any Docker socket mount, keeps network policy off host mode, and records only the approved mounts.
    """

    runtime = FakeContainerManager()
    manager = SandboxManager(runtime, quota=SandboxQuota(max_concurrent=2))

    record = await manager.allocate(
        SandboxRequest(
            session_id="sess-1",
            task_id="task-1",
            agent_id="agent-a",
            purpose="workspace-edit",
            workspace="E:/project",
            approved_mounts={"E:/project": "/workspace"},
        )
    )

    created_spec = runtime.created[0]
    assert created_spec.name == record.container_name
    assert created_spec.mounts == {"E:/project": "/workspace"}
    assert all("docker.sock" not in host_path for host_path in created_spec.mounts)
    assert record.policy.network_mode == "bridge"
    assert record.policy.privileged is False
    assert record.policy.docker_socket_allowed is False


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
    assert runtime.created[0].name.endswith(allocation.sandbox_id)
    assert runtime.created[1].name.endswith(allocation.sandbox_id)


@pytest.mark.asyncio
async def test_release_shared_sandbox_keeps_runtime_until_last_agent_leaves() -> None:
    """Description:
        Verify shared sandboxes remain alive until the last attached agent releases them.

    Requirements:
        - This test is needed to prove shared sandbox reuse does not destroy collaborating runtimes too early.
        - Verify the runtime stays active after the first release and is destroyed only after the last release.
    """

    runtime = FakeContainerManager()
    manager = SandboxManager(runtime, quota=SandboxQuota(max_concurrent=2))
    shared = await manager.allocate(
        SandboxRequest(session_id="sess-1", task_id="task-1", agent_id="agent-a", purpose="shared")
    )
    await manager.allocate(
        SandboxRequest(session_id="sess-1", task_id="task-1", agent_id="agent-b", purpose="shared")
    )

    await manager.release(shared.sandbox_id, agent_id="agent-a")
    assert runtime.destroyed == []
    assert manager.get(shared.sandbox_id).state is SandboxState.READY

    await manager.release(shared.sandbox_id, agent_id="agent-b")
    assert runtime.destroyed == [shared.container_name]
    assert manager.get(shared.sandbox_id).state is SandboxState.DESTROYED


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


@pytest.mark.asyncio
async def test_lifecycle_events_and_audit_entries_are_emitted() -> None:
    """Description:
        Verify sandbox lifecycle actions emit system events and audit entries.

    Requirements:
        - This test is needed to prove sandbox scheduling decisions are observable and auditable.
        - Verify allocation, reuse, reset, and release emit lifecycle records through both channels.
    """

    publisher = FakeEventPublisher()
    audit_logger = FakeAuditLogger()
    manager = SandboxManager(
        FakeContainerManager(),
        quota=ResourceQuota(max_concurrent=2),
        event_publisher=publisher,
        audit_logger=audit_logger,
    )

    first = await manager.allocate(
        SandboxRequest(session_id="sess-1", task_id="task-1", agent_id="agent-a", purpose="shared")
    )
    await manager.allocate(
        SandboxRequest(session_id="sess-1", task_id="task-1", agent_id="agent-b", purpose="shared")
    )
    await manager.reset(first.sandbox_id)
    await manager.release(first.sandbox_id, agent_id="agent-a")
    await manager.release(first.sandbox_id, agent_id="agent-b")

    event_types = [
        event.event if isinstance(event, FaithEvent) else event.get("event")
        for event in publisher.published
    ]
    assert EventType.SYSTEM_CONTAINER_STARTED in event_types
    assert "sandbox:reused" in event_types
    assert "sandbox:reset" in event_types
    assert "sandbox:destroyed" in event_types
    assert [entry["action"] for entry in audit_logger.entries] == [
        "create",
        "reuse",
        "reset",
        "release",
        "release",
        "destroy",
    ]
