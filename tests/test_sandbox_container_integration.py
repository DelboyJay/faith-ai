from __future__ import annotations

import pytest

from faith.pa.container_manager import ContainerManager, InMemoryContainerRuntime
from faith.pa.sandbox_manager import SandboxManager
from faith.pa.sandbox_models import SandboxRequest


@pytest.mark.asyncio
async def test_sandbox_manager_uses_container_manager() -> None:
    runtime = InMemoryContainerRuntime()
    manager = ContainerManager(runtime)
    sandbox_manager = SandboxManager(manager)

    allocation = await sandbox_manager.allocate(
        SandboxRequest(
            session_id="sess-0001",
            task_id="task-1",
            agent_id="developer",
            purpose="workspace",
            requires_isolation=True,
        )
    )

    info = manager.inspect(f"faith-sandbox-{allocation.sandbox_id}")

    assert allocation.sandbox_id == "sbx-0001"
    assert info.name == f"faith-sandbox-{allocation.sandbox_id}"
    assert info.status == "running"
    assert info.container_type == "sandbox"
