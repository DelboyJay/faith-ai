from __future__ import annotations

import pytest

from faith_pa.pa.sandbox_manager import SandboxManager, SandboxQuotaExceeded
from faith_pa.pa.sandbox_models import SandboxQuota, SandboxRequest


class FakeContainerManager:
    async def ensure_running(self, spec, *, actor: str = "pa"):
        return spec

    async def destroy(self, name: str, *, actor: str = "pa"):
        return None


@pytest.mark.asyncio
async def test_quota_is_enforced() -> None:
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

