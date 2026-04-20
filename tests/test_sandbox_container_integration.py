"""Description:
    Verify sandbox-manager integration with the container-manager runtime abstraction.

Requirements:
    - Prove sandbox allocation results in a managed runtime container with the expected metadata.
"""

from __future__ import annotations

import pytest

from faith_pa.pa.container_manager import ContainerManager, InMemoryContainerRuntime
from faith_pa.pa.sandbox_manager import SandboxManager
from faith_pa.pa.sandbox_models import SandboxRequest


@pytest.mark.asyncio
async def test_sandbox_manager_uses_container_manager() -> None:
    """Description:
    Verify sandbox allocation creates a managed sandbox container through the container manager.

    Requirements:
        - This test is needed to prove the sandbox manager integrates cleanly with the container runtime abstraction.
        - Verify the created container uses the expected sandbox naming and type metadata.
    """

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
