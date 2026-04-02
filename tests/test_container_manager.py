"""Description:
    Verify container lifecycle management and project switching behaviour.

Requirements:
    - Prove containers can be started, restarted, listed, and destroyed through the manager.
    - Prove project switching recognises already-active projects and updates recent-project state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from faith_pa.pa.container_manager import (
    ContainerManager,
    ContainerSpec,
    InMemoryContainerRuntime,
)
from faith_pa.pa.secret_resolver import SecretResolver


@pytest.mark.asyncio
async def test_ensure_running_starts_container():
    """Description:
        Verify ensuring a container is running starts it and emits a started event.

        Requirements:
            - This test is needed to prove the PA can create and track managed containers.
            - Verify the container is marked running, the default network exists, and the start event is published.
    """

    runtime = InMemoryContainerRuntime()
    publisher = AsyncMock()
    manager = ContainerManager(runtime, event_publisher=publisher)

    info = await manager.ensure_running(
        ContainerSpec(
            name="faith-agent-dev", image="faith-agent-base:latest", container_type="agent"
        )
    )

    assert info.status == "running"
    assert runtime.networks == {"maf-network"}
    publisher.system_container_started.assert_awaited_once()


@pytest.mark.asyncio
async def test_secret_refs_are_resolved():
    """Description:
        Verify environment secret references are resolved before container startup.

        Requirements:
            - This test is needed to prove managed containers do not receive unresolved secret references.
            - Verify the container starts successfully when secret references are present.
    """

    runtime = InMemoryContainerRuntime()
    resolver = SecretResolver.__new__(SecretResolver)
    resolver.secrets = {"api": {"token": "secret"}}
    manager = ContainerManager(runtime, secret_resolver=resolver)

    info = await manager.ensure_running(
        ContainerSpec(
            name="faith-tool-api",
            image="faith-tool-api:latest",
            env_secret_refs={"API_TOKEN": "api"},
        )
    )

    assert info.name == "faith-tool-api"
    assert runtime.inspect("faith-tool-api").status == "running"


@pytest.mark.asyncio
async def test_restart_and_destroy_container():
    """Description:
        Verify containers can be restarted and then removed.

        Requirements:
            - This test is needed to prove the manager supports basic lifecycle recovery and teardown.
            - Verify the restarted container returns to running state and disappears after destroy.
    """

    runtime = InMemoryContainerRuntime()
    manager = ContainerManager(runtime)
    await manager.ensure_running(ContainerSpec(name="faith-tool-db", image="faith-tool-db:latest"))

    restarted = await manager.restart_container("faith-tool-db")
    assert restarted.status == "running"

    await manager.destroy("faith-tool-db")
    assert manager.list_containers() == []


def test_list_containers_returns_sorted_items():
    """Description:
        Verify container listing is returned in sorted name order.

        Requirements:
            - This test is needed to prove container output is deterministic for status views and tests.
            - Verify the container names are returned alphabetically.
    """

    runtime = InMemoryContainerRuntime()
    runtime.create_or_update(ContainerSpec(name="b", image="two"))
    runtime.create_or_update(ContainerSpec(name="a", image="one"))
    manager = ContainerManager(runtime)
    names = [item.name for item in manager.list_containers()]
    assert names == ["a", "b"]
