"""Description:
    Verify sandbox quota behaviour using a minimal runtime double.

Requirements:
    - Prove the sandbox manager raises a quota error when the concurrent limit is exceeded.
"""

from __future__ import annotations

import pytest

from faith_pa.pa.sandbox_manager import SandboxManager, SandboxQuotaExceeded
from faith_pa.pa.sandbox_models import ResourceQuota, SandboxQuota, SandboxRequest


class FakeContainerManager:
    """Description:
        Provide a minimal runtime double for sandbox quota tests.

    Requirements:
        - Accept sandbox creation and destruction without side effects.
    """

    async def ensure_running(self, spec, *, actor: str = "pa"):
        """Description:
            Accept a sandbox creation request.

        Requirements:
            - Return the supplied spec unchanged.

        :param spec: Sandbox container specification.
        :param actor: Logical actor requesting the operation.
        :returns: Supplied sandbox spec.
        """

        del actor
        return spec

    async def destroy(self, name: str, *, actor: str = "pa"):
        """Description:
            Accept a sandbox destruction request.

        Requirements:
            - Return without side effects.

        :param name: Sandbox runtime name.
        :param actor: Logical actor requesting the operation.
        """

        del name, actor
        return None


@pytest.mark.asyncio
async def test_quota_is_enforced() -> None:
    """Description:
        Verify the sandbox manager raises a quota error when concurrent allocation exceeds the limit.

        Requirements:
            - This test is needed to prove sandbox scheduling fails closed when resources are exhausted.
            - Verify ``SandboxQuotaExceeded`` is raised for the second isolated allocation.
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


def test_request_defaults_to_isolated_for_destructive_work() -> None:
    """Description:
        Verify destructive requests derive isolated allocation automatically.

    Requirements:
        - This test is needed to prove the scheduler does not rely on callers to set the isolation mode manually for risky work.
        - Verify destructive requests become isolated and mark themselves as isolation-required.
    """

    request = SandboxRequest(
        session_id="sess-1",
        task_id="task-1",
        agent_id="agent-a",
        purpose="destructive-upgrade",
        destructive=True,
    )

    assert request.mode.value == "isolated"
    assert request.requires_isolation is True


def test_resource_quota_tracks_cpu_memory_disk_limits() -> None:
    """Description:
        Verify resource quotas preserve the scheduling limits needed by sandbox allocation.

    Requirements:
        - This test is needed to prove the sandbox scheduler can carry CPU, memory, and disk limits alongside the concurrency cap.
        - Verify the resource quota stores all configured limit values unchanged.
    """

    quota = ResourceQuota(max_concurrent=3, cpu_limit=2.0, memory_mb=4096, disk_mb=16384)

    assert quota.max_concurrent == 3
    assert quota.cpu_limit == 2.0
    assert quota.memory_mb == 4096
    assert quota.disk_mb == 16384
