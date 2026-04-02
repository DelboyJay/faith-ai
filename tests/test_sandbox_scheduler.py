"""Description:
    Verify sandbox quota behaviour using a minimal runtime double.

Requirements:
    - Prove the sandbox manager raises a quota error when the concurrent limit is exceeded.
"""

from __future__ import annotations

import pytest

from faith_pa.pa.sandbox_manager import SandboxManager, SandboxQuotaExceeded
from faith_pa.pa.sandbox_models import SandboxQuota, SandboxRequest


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
