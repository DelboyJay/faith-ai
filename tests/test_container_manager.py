"""Description:
    Verify FAITH Phase 4 container lifecycle behaviour for agents, tools, and external MCP runtime support.

Requirements:
    - Prove the container manager starts managed runtimes with the expected labels, network, and restart policy.
    - Prove secret-backed tool configuration is resolved before container startup.
    - Prove project startup can bootstrap agent, tool, and `mcp-runtime` containers from the `.faith` tree.
    - Prove the manager can drive both the in-memory runtime and a Docker-SDK-style client surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from faith_pa.pa.container_manager import (
    AGENT_BASE_IMAGE,
    FAITH_LABEL,
    NETWORK_NAME,
    ContainerManager,
    ContainerSpec,
    InMemoryContainerRuntime,
)
from faith_pa.pa.secret_resolver import SecretResolver


class FakeLifecyclePublisher:
    """Description:
        Record container lifecycle notifications without using mocks.

    Requirements:
        - Preserve started and stopped calls for later assertions.
        - Expose the same async helper names used by the container manager.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the lifecycle call history.

        Requirements:
            - Start with empty started and stopped call lists.
        """

        self.started: list[tuple[str, str]] = []
        self.stopped: list[tuple[str, str]] = []

    async def system_container_started(self, name: str, container_type: str) -> None:
        """Description:
            Record one container-started event.

        Requirements:
            - Preserve the container name and logical type.

        :param name: Container name.
        :param container_type: Logical container type.
        """

        self.started.append((name, container_type))

    async def system_container_stopped(self, name: str, *, reason: str) -> None:
        """Description:
            Record one container-stopped event.

        Requirements:
            - Preserve the container name and stop reason.

        :param name: Container name.
        :param reason: Human-readable stop reason.
        """

        self.stopped.append((name, reason))


class FakeDockerNetwork:
    """Description:
        Model one Docker-SDK-style network object for unit tests.

    Requirements:
        - Preserve the network name.
        - Record connected container names.

    :param name: Network name.
    """

    def __init__(self, name: str) -> None:
        """Description:
            Initialise the fake network state.

        Requirements:
            - Start with no connected containers.

        :param name: Network name.
        """

        self.name = name
        self.connected: list[str] = []

    def connect(self, container: FakeDockerContainer) -> None:
        """Description:
            Record a container attachment to the fake network.

        Requirements:
            - Track the container name once per network.
            - Reflect the attached network on the container attrs payload.

        :param container: Container being connected.
        """

        if container.name not in self.connected:
            self.connected.append(container.name)
        container.attrs.setdefault("NetworkSettings", {}).setdefault("Networks", {})[self.name] = {}


class FakeDockerNetworks:
    """Description:
        Provide a Docker-SDK-style networks collection.

    Requirements:
        - Support `get()` and `create()` operations used by the container manager.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the network registry.

        Requirements:
            - Start with no known networks.
        """

        self.items: dict[str, FakeDockerNetwork] = {}

    def get(self, name: str) -> FakeDockerNetwork:
        """Description:
            Return one fake network by name.

        Requirements:
            - Raise `KeyError` when the network is absent.

        :param name: Network name.
        :returns: Fake network instance.
        :raises KeyError: If the network does not exist.
        """

        return self.items[name]

    def create(self, name: str, driver: str = "bridge") -> FakeDockerNetwork:
        """Description:
            Create one fake network entry.

        Requirements:
            - Ignore the driver value while preserving the created network name.

        :param name: Network name.
        :param driver: Docker driver name.
        :returns: Created fake network instance.
        """

        del driver
        network = FakeDockerNetwork(name)
        self.items[name] = network
        return network


class FakeDockerContainer:
    """Description:
        Model one Docker-SDK-style container object for unit tests.

    Requirements:
        - Preserve lifecycle-relevant metadata such as labels, environment, mounts, and restart policy.
        - Support reload, stop, restart, and remove operations.
    """

    def __init__(
        self,
        *,
        name: str,
        image: str,
        labels: dict[str, str] | None = None,
        command: list[str] | None = None,
        environment: dict[str, str] | None = None,
        mounts: dict[str, str] | None = None,
        network_mode: str = NETWORK_NAME,
        restart_policy: str = "unless-stopped",
        privileged: bool = False,
        capabilities: list[str] | None = None,
        status: str = "running",
    ) -> None:
        """Description:
            Initialise the fake container metadata.

        Requirements:
            - Start with a stable attrs payload that mirrors the fields used by the manager.

        :param name: Container name.
        :param image: Container image.
        :param labels: Container labels.
        :param command: Effective command list.
        :param environment: Runtime environment.
        :param mounts: Host-to-container mount mapping.
        :param network_mode: Runtime network name.
        :param restart_policy: Restart policy name.
        :param privileged: Whether privileged mode is enabled.
        :param capabilities: Granted Linux capabilities.
        :param status: Current runtime status.
        """

        self.name = name
        self.image = image
        self.status = status
        self.labels = dict(labels or {})
        self.command = list(command or [])
        self.environment = dict(environment or {})
        self.mounts = dict(mounts or {})
        self.network_mode = network_mode
        self.restart_policy = restart_policy
        self.privileged = privileged
        self.capabilities = list(capabilities or [])
        self.short_id = f"{name[:12]}-id"
        self.removed = False
        self.attrs = {
            "Config": {
                "Labels": self.labels,
                "Image": image,
                "Cmd": self.command,
                "Env": [f"{key}={value}" for key, value in self.environment.items()],
            },
            "HostConfig": {
                "NetworkMode": self.network_mode,
                "Privileged": self.privileged,
                "CapAdd": self.capabilities,
                "RestartPolicy": {"Name": self.restart_policy},
                "Binds": [f"{key}:{value}" for key, value in self.mounts.items()],
            },
            "NetworkSettings": {"Networks": {self.network_mode: {}}},
        }

    def reload(self) -> None:
        """Description:
            Provide the Docker reload compatibility hook.

        Requirements:
            - Leave the fake container state unchanged.
        """

    def stop(self, timeout: int = 10) -> None:
        """Description:
            Mark the fake container as stopped.

        Requirements:
            - Ignore the timeout while preserving the stopped status.

            :param timeout: Stop timeout in seconds.
        """

        del timeout
        self.status = "stopped"

    def restart(self, timeout: int = 10) -> None:
        """Description:
            Mark the fake container as running again.

        Requirements:
            - Ignore the timeout while preserving the running status.

            :param timeout: Restart timeout in seconds.
        """

        del timeout
        self.status = "running"

    def remove(self, force: bool = False) -> None:
        """Description:
            Mark the fake container as removed.

        Requirements:
            - Preserve whether force removal was requested.

        :param force: Whether forced removal was requested.
        """

        del force
        self.removed = True


class FakeDockerContainers:
    """Description:
        Provide a Docker-SDK-style container collection for unit tests.

    Requirements:
        - Support `get()`, `run()`, and `list()` operations.
        - Reuse stored container objects so restart and reattach flows can inspect existing state.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake container registry.

        Requirements:
            - Start with no known containers and no run-call history.
        """

        self.items: dict[str, FakeDockerContainer] = {}
        self.run_calls: list[dict[str, object]] = []

    def get(self, name: str) -> FakeDockerContainer:
        """Description:
            Return one fake container by name.

        Requirements:
            - Raise `KeyError` when the container is absent.

        :param name: Container name.
        :returns: Fake container instance.
        :raises KeyError: If the container does not exist.
        """

        return self.items[name]

    def run(
        self,
        *,
        image: str,
        name: str,
        environment: dict[str, str],
        volumes: dict[str, str],
        labels: dict[str, str],
        command: list[str] | None,
        detach: bool,
        network: str,
        restart_policy: dict[str, str],
        privileged: bool,
        cap_add: list[str],
    ) -> FakeDockerContainer:
        """Description:
            Create and store one fake running container.

        Requirements:
            - Preserve the Docker-style run arguments for later assertions.

        :param image: Container image.
        :param name: Container name.
        :param environment: Runtime environment mapping.
        :param volumes: Host-to-container mount mapping.
        :param labels: Container labels.
        :param command: Effective command list.
        :param detach: Whether detached mode was requested.
        :param network: Runtime network name.
        :param restart_policy: Restart policy payload.
        :param privileged: Whether privileged mode is enabled.
        :param cap_add: Granted Linux capabilities.
        :returns: Created fake container instance.
        """

        self.run_calls.append(
            {
                "image": image,
                "name": name,
                "environment": dict(environment),
                "volumes": dict(volumes),
                "labels": dict(labels),
                "command": list(command or []),
                "detach": detach,
                "network": network,
                "restart_policy": dict(restart_policy),
                "privileged": privileged,
                "cap_add": list(cap_add),
            }
        )
        container = FakeDockerContainer(
            name=name,
            image=image,
            labels=labels,
            command=command,
            environment=environment,
            mounts=volumes,
            network_mode=network,
            restart_policy=str(restart_policy.get("Name", "")),
            privileged=privileged,
            capabilities=cap_add,
        )
        self.items[name] = container
        return container

    def list(self, all: bool = True) -> list[FakeDockerContainer]:
        """Description:
            Return the stored fake containers.

        Requirements:
            - Ignore the `all` flag while preserving insertion order.

        :param all: Whether all containers were requested.
        :returns: Stored fake container objects.
        """

        del all
        return list(self.items.values())


class FakeDockerClient:
    """Description:
        Provide a Docker-SDK-style client surface for container-manager tests.

    Requirements:
        - Expose `containers` and `networks` collections with the methods used by the manager.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake Docker client collections.

        Requirements:
            - Start with empty container and network state.
        """

        self.containers = FakeDockerContainers()
        self.networks = FakeDockerNetworks()


@pytest.mark.asyncio
async def test_ensure_running_starts_container() -> None:
    """Description:
        Verify ensuring a container is running starts it and emits a started event.

        Requirements:
            - This test is needed to prove the PA can create and track managed containers.
            - Verify the container is marked running, the default network exists, and the start event is published.
    """

    runtime = InMemoryContainerRuntime()
    publisher = FakeLifecyclePublisher()
    manager = ContainerManager(runtime, event_publisher=publisher)

    info = await manager.ensure_running(
        ContainerSpec(
            name="faith-agent-dev",
            image="faith-agent-base:latest",
            container_type="agent",
        )
    )

    assert info.status == "running"
    assert runtime.networks == {"maf-network"}
    assert publisher.started == [("faith-agent-dev", "agent")]


def test_container_manager_builds_default_docker_runtime(monkeypatch) -> None:
    """Description:
        Verify the container manager constructs the Docker-backed runtime when no client is supplied.

        Requirements:
            - This test is needed to prove the Phase 4 container layer has a real Docker SDK integration path by default.
            - Verify the default runtime factory receives the requested network name.
    """

    built: list[str] = []

    class FakeDockerRuntime:
        """Description:
            Provide a minimal default-runtime double for manager construction tests.

            Requirements:
                - Record the requested network name and accept ensure-network calls.
        """

        def __init__(self, *, network_name: str):
            self.network_name = network_name
            built.append(network_name)

        def ensure_network(self, name: str) -> None:
            """Description:
                Accept the requested network creation call.

                Requirements:
                    - Assert the manager passed the configured network name through unchanged.

                :param name: Requested network name.
            """

            assert name == self.network_name

    monkeypatch.setattr("faith_pa.pa.container_manager.DockerContainerRuntime", FakeDockerRuntime)

    manager = ContainerManager(network_name="faith-phase4")

    assert built == ["faith-phase4"]
    assert manager.network_name == "faith-phase4"


@pytest.mark.asyncio
async def test_secret_refs_are_resolved() -> None:
    """Description:
        Verify environment secret references are resolved before container startup.

        Requirements:
            - This test is needed to prove managed containers do not receive unresolved secret references.
            - Verify the container starts successfully when secret references are present.
    """

    runtime = InMemoryContainerRuntime()
    resolver = SecretResolver.__new__(SecretResolver)
    resolver.secrets = {"api": {"value": "secret"}}
    resolver.environment = {}
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
async def test_restart_and_destroy_container() -> None:
    """Description:
        Verify containers can be restarted and then removed.

        Requirements:
            - This test is needed to prove the manager supports basic lifecycle recovery and teardown.
            - Verify the restarted container returns to running state and disappears after destroy.
    """

    runtime = InMemoryContainerRuntime()
    manager = ContainerManager(runtime)
    await manager.ensure_running(
        ContainerSpec(name="faith-tool-db", image="faith-tool-db:latest")
    )

    restarted = await manager.restart_container("faith-tool-db")
    assert restarted.status == "running"

    await manager.destroy("faith-tool-db")
    assert manager.list_containers() == []


def test_list_containers_returns_sorted_items() -> None:
    """Description:
        Verify container listing is returned in sorted name order.

        Requirements:
            - This test is needed to prove container output is deterministic for status views and tests.
            - Verify the container names are returned alphabetically.
    """

    runtime = InMemoryContainerRuntime()
    runtime.create_or_update(
        ContainerSpec(name="b", image="two", labels={FAITH_LABEL: "true"})
    )
    runtime.create_or_update(
        ContainerSpec(name="a", image="one", labels={FAITH_LABEL: "true"})
    )
    manager = ContainerManager(runtime)
    names = [item.name for item in manager.list_containers()]
    assert names == ["a", "b"]


def test_secret_resolver_prefers_explicit_environment_over_dotenv(tmp_path: Path) -> None:
    """Description:
        Verify secret resolution keeps explicit environment overrides ahead of `.env` values.

        Requirements:
            - This test is needed to prove caller-supplied environment values are not silently replaced by `.env` values.
            - Verify `${TOKEN}` resolves to the explicit override instead of the `.env` file value.

        :param tmp_path: Temporary pytest directory fixture.
    """

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text("TOKEN=dotenv-value\n", encoding="utf-8")
    (config_dir / "secrets.yaml").write_text("api_key: ${TOKEN}\n", encoding="utf-8")

    resolver = SecretResolver(config_dir, environment={"TOKEN": "explicit-value"})

    assert resolver.resolve_secret_ref("api_key") == "explicit-value"


def test_resolve_tool_config_merges_nested_secret_refs_without_overwriting_explicit_values(
    tmp_path: Path,
) -> None:
    """Description:
        Verify tool configs recursively merge `secret_ref` payloads while preserving explicit values.

        Requirements:
            - This test is needed to prove nested tool credentials can be materialised from `secrets.yaml`.
            - Verify explicit tool-config values win over matching secret keys and `secret_ref` is removed.

        :param tmp_path: Temporary pytest directory fixture.
    """

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "secrets.yaml").write_text(
        yaml.safe_dump(
            {
                "databases": {
                    "prod-db": {
                        "host": "db.example.com",
                        "user": "readonly-user",
                        "password": "secret123",
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    resolver = SecretResolver(config_dir)

    resolved = resolver.resolve_tool_config(
        {
            "connections": {
                "prod-db": {
                    "secret_ref": "prod-db",
                    "user": "explicit-user",
                    "database": "myapp",
                }
            }
        }
    )

    connection = resolved["connections"]["prod-db"]

    assert "secret_ref" not in connection
    assert connection["host"] == "db.example.com"
    assert connection["password"] == "secret123"
    assert connection["user"] == "explicit-user"
    assert connection["database"] == "myapp"


@pytest.mark.asyncio
async def test_start_container_supports_docker_sdk_style_client() -> None:
    """Description:
        Verify the container manager can drive a Docker-SDK-style client directly.

        Requirements:
            - This test is needed to prove the Phase 4 manager is not limited to the in-memory runtime.
            - Verify the manager creates the shared network, labels the container, and applies the restart policy.
    """

    docker_client = FakeDockerClient()
    manager = ContainerManager(docker_client)

    info = await manager.start_container(
        "faith-agent-developer",
        image=AGENT_BASE_IMAGE,
        labels={"faith.agent": "developer"},
        environment={"FAITH_AGENT_ID": "developer"},
        container_type="agent",
    )

    assert info.name == "faith-agent-developer"
    assert info.labels[FAITH_LABEL] == "true"
    assert info.restart_policy == "unless-stopped"
    assert info.network_mode == NETWORK_NAME
    assert NETWORK_NAME in docker_client.networks.items
    assert docker_client.containers.run_calls[0]["network"] == NETWORK_NAME


@pytest.mark.asyncio
async def test_start_tool_supports_structured_mounts_and_secret_env(tmp_path: Path) -> None:
    """Description:
        Verify tool startup handles structured mount config and resolved secret-backed environment values.

        Requirements:
            - This test is needed to prove FAITH-owned tool configs can be translated into runtime mounts and env vars.
            - Verify structured mount entries become host-to-container mounts and secret-backed env values are injected.

        :param tmp_path: Temporary pytest directory fixture.
    """

    project = tmp_path / "project"
    workspace = project / "workspace"
    config_dir = tmp_path / "config"
    workspace.mkdir(parents=True)
    config_dir.mkdir()
    (config_dir / "secrets.yaml").write_text(
        yaml.safe_dump({"credentials": {"api-token": "secret-token"}}, sort_keys=False),
        encoding="utf-8",
    )

    manager = ContainerManager(
        InMemoryContainerRuntime(),
        config_dir=config_dir,
        faith_dir=project / ".faith",
    )

    info = await manager.start_tool(
        tool_name="filesystem",
        tool_config={
            "image": "faith-tool-filesystem:latest",
            "mounts": {
                "workspace": {
                    "path": str(workspace),
                    "bind": "/workspace",
                    "mode": "rw",
                }
            },
            "env_secret_refs": {"API_TOKEN": "api-token"},
        },
        workspace_path=workspace,
    )

    assert info.name == "faith-tool-filesystem"
    assert info.mounts[str(workspace)] == "/workspace"
    assert info.environment["API_TOKEN"] == "secret-token"


@pytest.mark.asyncio
async def test_discover_agents_and_tools_resolve_configs(tmp_path: Path) -> None:
    """Description:
        Verify the container manager discovers project agents and tools from the `.faith` tree.

        Requirements:
            - This test is needed to prove Phase 4 startup can discover project-scoped runtimes from disk.
            - Verify agent configs are loaded and tool secret references are resolved before use.

        :param tmp_path: Temporary pytest directory fixture.
    """

    project = tmp_path / "project"
    faith_dir = project / ".faith"
    agent_dir = faith_dir / "agents" / "developer"
    tool_dir = faith_dir / "tools"
    config_dir = tmp_path / "config"
    agent_dir.mkdir(parents=True)
    tool_dir.mkdir(parents=True)
    config_dir.mkdir()

    (agent_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {"name": "Developer", "role": "implementation", "model": "gpt-5.4-mini"}
        ),
        encoding="utf-8",
    )
    (tool_dir / "database.yaml").write_text(
        yaml.safe_dump(
            {
                "image": "faith-tool-db:latest",
                "env_secret_refs": {"DB_PASSWORD": "db-password"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        yaml.safe_dump({"db-password": "super-secret"}, sort_keys=False),
        encoding="utf-8",
    )

    manager = ContainerManager(
        InMemoryContainerRuntime(),
        faith_dir=faith_dir,
        config_dir=config_dir,
    )

    agents = manager.discover_agents()
    tools = manager.discover_tools()

    assert agents["developer"]["role"] == "implementation"
    assert tools["database"]["env"]["DB_PASSWORD"] == "super-secret"


@pytest.mark.asyncio
async def test_start_all_starts_external_mcp_runtime(tmp_path: Path) -> None:
    """Description:
        Verify project startup also brings up the shared `mcp-runtime` container when external MCP tools exist.

        Requirements:
            - This test is needed to prove external registry-backed MCP registrations share one project-scoped runtime.
            - Verify `start_all()` reports success for the agent, FAITH-owned tool, and shared `mcp-runtime`.

        :param tmp_path: Temporary pytest directory fixture.
    """

    project = tmp_path / "project"
    faith_dir = project / ".faith"
    config_dir = tmp_path / "config"
    (faith_dir / "agents" / "developer").mkdir(parents=True)
    (faith_dir / "tools").mkdir(parents=True)
    config_dir.mkdir()
    (faith_dir / "agents" / "developer" / "config.yaml").write_text(
        yaml.safe_dump(
            {"name": "Developer", "role": "implementation", "model": "gpt-5.4-mini"}
        ),
        encoding="utf-8",
    )
    (faith_dir / "tools" / "filesystem.yaml").write_text(
        yaml.safe_dump(
            {"image": "faith-tool-filesystem:latest", "env": {"MODE": "rw"}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (faith_dir / "tools" / "external-git.yaml").write_text(
        yaml.safe_dump(
            {
                "registry_ref": "registry/git",
                "package_version": "1.2.3",
                "transport": "stdio",
                "env": {"GIT_MODE": "readonly"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    runtime = InMemoryContainerRuntime()
    manager = ContainerManager(runtime, faith_dir=faith_dir, config_dir=config_dir)

    results = await manager.start_all(project)

    assert results["faith-agent-developer"] is True
    assert results["faith-tool-filesystem"] is True
    assert results["faith-mcp-runtime"] is True
    assert manager.inspect("faith-mcp-runtime").container_type == "mcp-runtime"


@pytest.mark.asyncio
async def test_reattach_running_counts_only_managed_running_containers() -> None:
    """Description:
        Verify reattach only counts running FAITH-managed containers.

        Requirements:
            - This test is needed to prove PA restart recovery ignores stopped and unmanaged runtimes.
            - Verify the reattach count includes only running containers with the FAITH managed label.
    """

    runtime = InMemoryContainerRuntime()
    runtime.create_or_update(
        ContainerSpec(
            name="faith-agent-dev",
            image="faith-agent-base:latest",
            labels={FAITH_LABEL: "true"},
            container_type="agent",
        )
    )
    runtime.create_or_update(
        ContainerSpec(
            name="unmanaged-sidecar",
            image="sidecar:latest",
            labels={},
        )
    )
    runtime.create_or_update(
        ContainerSpec(
            name="faith-tool-db",
            image="faith-tool-db:latest",
            labels={FAITH_LABEL: "true"},
            container_type="tool",
        )
    )
    runtime.stop("faith-tool-db")
    manager = ContainerManager(runtime)

    recovered = await manager.reattach_running()

    assert recovered == 1
