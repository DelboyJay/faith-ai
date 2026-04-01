from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from faith.pa.container_manager import (
    ContainerManager,
    ContainerSpec,
    InMemoryContainerRuntime,
)
from faith.pa.secret_resolver import SecretResolver


@pytest.mark.asyncio
async def test_ensure_running_starts_container():
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
    runtime = InMemoryContainerRuntime()
    manager = ContainerManager(runtime)
    await manager.ensure_running(ContainerSpec(name="faith-tool-db", image="faith-tool-db:latest"))

    restarted = await manager.restart_container("faith-tool-db")
    assert restarted.status == "running"

    await manager.destroy("faith-tool-db")
    assert manager.list_containers() == []


def test_list_containers_returns_sorted_items():
    runtime = InMemoryContainerRuntime()
    runtime.create_or_update(ContainerSpec(name="b", image="two"))
    runtime.create_or_update(ContainerSpec(name="a", image="one"))
    manager = ContainerManager(runtime)
    names = [item.name for item in manager.list_containers()]
    assert names == ["a", "b"]
