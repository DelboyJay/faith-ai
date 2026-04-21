"""Description:
    Verify the Phase 7 external MCP registration and lifecycle flow.

Requirements:
    - Prove version-pinned external MCP registrations load from `.faith/tools`.
    - Prove privacy, enablement, and agent assignment gates are enforced.
    - Prove the PA starts a shared `mcp-runtime` and stdio subprocesses per session.
    - Prove session-scoped cleanup, stdio access, and config reload behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from faith_pa.pa.external_mcp import (
    ExternalMCPManager,
    build_playwright_mcp_registration,
    ensure_playwright_mcp_registration,
)
from faith_pa.pa.secret_resolver import SecretResolver
from faith_shared.config.models import ExternalMCPToolConfig, PrivacyProfile


class FakeProcess:
    """Description:
        Model one running external MCP stdio subprocess for lifecycle tests.

    Requirements:
        - Preserve stdin/stdout handles and lifecycle state for assertions.
        - Support graceful terminate, wait, and force-kill semantics.
    """

    def __init__(self, *, pid: int = 1234) -> None:
        """Description:
            Initialise the fake subprocess state.

        Requirements:
            - Start in the running state with deterministic stdio sentinels.

        :param pid: Fake process identifier.
        """

        self.pid = pid
        self.returncode: int | None = None
        self.stdin = object()
        self.stdout = object()
        self.stderr = object()
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        """Description:
            Mark the fake subprocess as terminated.

        Requirements:
            - Set a zero exit code when the process is still running.
        """

        self.terminated = True
        if self.returncode is None:
            self.returncode = 0

    def kill(self) -> None:
        """Description:
            Mark the fake subprocess as force-killed.

        Requirements:
            - Set a non-zero exit code when the process is still running.
        """

        self.killed = True
        if self.returncode is None:
            self.returncode = 137

    async def wait(self) -> int:
        """Description:
            Return the fake subprocess exit code.

        Requirements:
            - Default unfinished processes to a clean exit when waited.

        :returns: Fake subprocess exit code.
        """

        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeLauncher:
    """Description:
        Record external MCP subprocess launch requests without real process spawning.

    Requirements:
        - Preserve every command and environment block for later assertions.
        - Return a prebuilt fake subprocess for each launch.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the launch history and fake process queue.

        Requirements:
            - Start with an empty call list and a deterministic PID counter.
        """

        self.calls: list[dict[str, Any]] = []
        self._next_pid = 2000

    async def __call__(self, *cmd: str, env: dict[str, str]) -> FakeProcess:
        """Description:
            Record one launch request and return a fake subprocess.

        Requirements:
            - Preserve the command tuple and the resolved environment.

        :param cmd: Command-line segments for the launch.
        :param env: Resolved process environment.
        :returns: Fake subprocess instance.
        """

        self.calls.append({"cmd": list(cmd), "env": dict(env)})
        self._next_pid += 1
        return FakeProcess(pid=self._next_pid)


class FakeContainerManager:
    """Description:
        Record `mcp-runtime` lifecycle requests without starting real containers.

    Requirements:
        - Preserve every `start_mcp_runtime` request for later assertions.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake runtime call history.

        Requirements:
            - Start with no runtime start calls.
        """

        self.start_calls: list[dict[str, Any]] = []

    async def start_mcp_runtime(
        self,
        *,
        external_tools: dict[str, dict[str, Any]],
        workspace_path: Path,
    ) -> dict[str, Any]:
        """Description:
            Record one `mcp-runtime` startup request.

        Requirements:
            - Preserve the workspace path and external tool mapping.

        :param external_tools: External MCP tool configs keyed by tool name.
        :param workspace_path: Project workspace path.
        :returns: Lightweight runtime info payload.
        """

        self.start_calls.append(
            {
                "external_tools": external_tools,
                "workspace_path": Path(workspace_path),
            }
        )
        return {"name": "faith-mcp-runtime", "status": "running"}


@pytest.fixture
def tmp_faith_dir(tmp_path: Path) -> Path:
    """Description:
        Create a temporary project `.faith` tree for external MCP tests.

    Requirements:
        - Provide `.faith/tools` and framework `config` directories.

    :param tmp_path: Temporary pytest workspace root.
    :returns: Temporary `.faith` directory path.
    """

    faith_dir = tmp_path / ".faith"
    (faith_dir / "tools").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    return faith_dir


@pytest.fixture
def secret_resolver(tmp_faith_dir: Path) -> SecretResolver:
    """Description:
        Build a secret resolver backed by a temporary config directory.

    Requirements:
        - Expose both environment and `env_secret_refs` resolution for tests.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :returns: Configured secret resolver.
    """

    config_dir = tmp_faith_dir.parent / "config"
    (config_dir / "secrets.yaml").write_text(
        yaml.safe_dump({"credentials": {"github-token": "secret-token"}}, sort_keys=False),
        encoding="utf-8",
    )
    (config_dir / ".env").write_text("GITHUB_TOKEN=dotenv-token\n", encoding="utf-8")
    return SecretResolver(config_dir)


def write_external_tool(
    faith_dir: Path,
    name: str,
    payload: dict[str, Any],
) -> Path:
    """Description:
        Write one external MCP tool config under `.faith/tools`.

    Requirements:
        - Use the canonical `external-*.yaml` naming pattern.

    :param faith_dir: Project `.faith` directory.
    :param name: Logical external tool name.
    :param payload: YAML payload to write.
    :returns: Written config path.
    """

    path = faith_dir / "tools" / f"external-{name}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def build_external_payload(**overrides: Any) -> dict[str, Any]:
    """Description:
        Build one valid external MCP tool config payload for tests.

    Requirements:
        - Produce a version-pinned registry-backed stdio config by default.
        - Allow callers to override individual fields per scenario.

    :param overrides: Field overrides merged into the baseline payload.
    :returns: External MCP tool config payload.
    """

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "registry_ref": "@modelcontextprotocol/server-github",
        "package_version": "1.2.3",
        "transport": "stdio",
        "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
        "env_secret_refs": {"API_TOKEN": "github-token"},
        "privacy_tier": "internal",
        "agents": ["software-developer", "qa-engineer"],
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def test_external_mcp_tool_config_requires_pinned_version() -> None:
    """Description:
        Verify external MCP configs require an explicit pinned package version.

    Requirements:
        - This test is needed to prove v1 external MCP registrations cannot be unpinned.
        - Verify validation fails when `package_version` is omitted.
    """

    with pytest.raises(Exception):
        ExternalMCPToolConfig.model_validate(
            {
                "schema_version": "1.0",
                "registry_ref": "@modelcontextprotocol/server-github",
                "transport": "stdio",
            }
        )


def test_external_mcp_tool_config_rejects_non_registry_source_type() -> None:
    """Description:
        Verify external MCP configs reject unsupported source types in v1.

    Requirements:
        - This test is needed to prove v1 onboarding stays limited to registry-backed registrations.
        - Verify validation fails when `source_type` is not `registry`.
    """

    with pytest.raises(Exception):
        ExternalMCPToolConfig.model_validate(
            {
                "schema_version": "1.0",
                "source_type": "git",
                "registry_ref": "@modelcontextprotocol/server-github",
                "package_version": "1.2.3",
                "transport": "stdio",
            }
        )


def test_load_configs_discovers_external_tool_files(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify the external MCP manager discovers external tool registrations from disk.

    Requirements:
        - This test is needed to prove the PA can bootstrap external MCP state from `.faith/tools`.
        - Verify one valid `external-*.yaml` file produces one named registration.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(tmp_faith_dir, "github", build_external_payload())
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
    )

    count = manager.load_configs()

    assert count == 1
    assert manager.get_server("github") is not None


def test_get_servers_for_agent_respects_privacy_and_enablement(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify agent-visible external MCP servers respect privacy and enablement gates.

    Requirements:
        - This test is needed to prove blocked or disabled servers do not leak into agent routing.
        - Verify only allowed, enabled registrations are returned for one agent.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(tmp_faith_dir, "github", build_external_payload())
    write_external_tool(
        tmp_faith_dir,
        "jira",
        build_external_payload(
            registry_ref="@modelcontextprotocol/server-jira",
            package_version="4.5.6",
            privacy_tier="public",
        ),
    )
    write_external_tool(
        tmp_faith_dir,
        "disabled",
        build_external_payload(
            registry_ref="@modelcontextprotocol/server-disabled",
            package_version="9.9.9",
            enabled=False,
        ),
    )
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        active_privacy_profile=PrivacyProfile.INTERNAL,
    )
    manager.load_configs()

    servers = manager.get_servers_for_agent("software-developer")

    assert servers == ["github"]


def test_project_agent_receives_all_enabled_external_mcp_servers_by_default(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify the Project Agent can see all enabled external MCP servers by default.

    Requirements:
        - This test is needed because the PA orchestrates tools directly before
          choosing whether to delegate work to specialist agents.
        - Verify the PA is not limited by specialist-agent assignment lists.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(
        tmp_faith_dir,
        "playwright",
        build_external_payload(
            registry_ref="@playwright/mcp",
            package_version="0.0.36",
            agents=["qa-engineer"],
        ),
    )
    write_external_tool(
        tmp_faith_dir,
        "github",
        build_external_payload(
            registry_ref="@modelcontextprotocol/server-github",
            package_version="1.2.3",
            agents=["software-developer"],
        ),
    )
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        active_privacy_profile=PrivacyProfile.INTERNAL,
    )
    manager.load_configs()

    servers = manager.get_servers_for_agent("project-agent")

    assert servers == ["github", "playwright"]


def test_playwright_mcp_registration_uses_official_package_and_qa_defaults() -> None:
    """Description:
        Verify the built-in Playwright MCP registration template matches the
        agreed external-container approach.

    Requirements:
        - This test is needed so FAITH can create a reliable default Playwright
          MCP registration without hand-authored YAML.
        - Verify the official package, pinned version, PA-friendly agent list,
          and isolated/headless arguments are present.
    """

    registration = build_playwright_mcp_registration(package_version="0.0.36")

    assert registration["registry_ref"] == "@playwright/mcp"
    assert registration["package_version"] == "0.0.36"
    assert registration["agents"] == ["qa-engineer", "security-expert"]
    assert registration["args"] == ["--headless", "--isolated"]


def test_ensure_playwright_mcp_registration_creates_default_tool_file(
    tmp_faith_dir: Path,
) -> None:
    """Description:
        Verify FAITH can materialise the default Playwright external MCP registration.

    Requirements:
        - This test is needed so setup/wizard code can install the default
          browser automation registration without duplicating YAML knowledge.
        - Verify the generated file uses the official package and executable
          headless/isolated command arguments.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    """

    path = ensure_playwright_mcp_registration(tmp_faith_dir, package_version="0.0.36")

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert path == tmp_faith_dir / "tools" / "external-playwright.yaml"
    assert payload["registry_ref"] == "@playwright/mcp"
    assert payload["package_version"] == "0.0.36"
    assert payload["args"] == ["--headless", "--isolated"]


def test_ensure_playwright_mcp_registration_preserves_existing_tool_file(
    tmp_faith_dir: Path,
) -> None:
    """Description:
        Verify default Playwright registration creation does not overwrite user config.

    Requirements:
        - This test is needed because users may pin a different Playwright MCP
          version or disable the server.
        - Verify an existing registration file is left unchanged.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    """

    path = write_external_tool(
        tmp_faith_dir,
        "playwright",
        build_playwright_mcp_registration(package_version="0.0.12", enabled=False),
    )

    ensured = ensure_playwright_mcp_registration(tmp_faith_dir, package_version="0.0.36")

    payload = yaml.safe_load(ensured.read_text(encoding="utf-8"))
    assert ensured == path
    assert payload["package_version"] == "0.0.12"
    assert payload["enabled"] is False


@pytest.mark.asyncio
async def test_playwright_mcp_registration_launches_with_browser_safety_args(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify the default Playwright MCP registration launches with explicit browser safety args.

    Requirements:
        - This test is needed because environment-only flags would not reliably
          reach the external `@playwright/mcp` command.
        - Verify `--headless` and `--isolated` are appended to the `npx`
          command used to start the MCP subprocess.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(
        tmp_faith_dir,
        "playwright",
        build_playwright_mcp_registration(package_version="0.0.36"),
    )
    launcher = FakeLauncher()
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        launcher=launcher,
    )
    manager.load_configs()

    started = await manager.start_server("playwright", session_id="sess-playwright")

    assert started is True
    assert launcher.calls[0]["cmd"] == [
        "npx",
        "-y",
        "@playwright/mcp@0.0.36",
        "--headless",
        "--isolated",
    ]


@pytest.mark.asyncio
async def test_start_server_launches_stdio_process_with_resolved_env(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify starting one external MCP server launches a version-pinned stdio subprocess.

    Requirements:
        - This test is needed to prove the PA turns external MCP registrations into executable stdio processes.
        - Verify the launch command is version-pinned and the environment includes resolved secret-backed values.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(tmp_faith_dir, "github", build_external_payload())
    launcher = FakeLauncher()
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        launcher=launcher,
    )
    manager.load_configs()

    started = await manager.start_server("github", session_id="sess-001")

    assert started is True
    assert launcher.calls[0]["cmd"] == [
        "npx",
        "-y",
        "@modelcontextprotocol/server-github@1.2.3",
    ]
    assert launcher.calls[0]["env"]["GITHUB_TOKEN"] == "dotenv-token"
    assert launcher.calls[0]["env"]["API_TOKEN"] == "secret-token"
    assert manager.get_server("github").session_id == "sess-001"


@pytest.mark.asyncio
async def test_start_server_uses_registry_resolution_before_launch(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify external MCP startup resolves registry metadata before building the launch command.

    Requirements:
        - This test is needed to prove FAITH models registry resolution explicitly instead of treating the registry ref as an implicit package spec.
        - Verify the resolved package identifier is used in the final `npx` command.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(tmp_faith_dir, "github", build_external_payload())
    launcher = FakeLauncher()

    async def resolve_registry(registry_ref: str, package_version: str) -> dict[str, str]:
        """Description:
        Return a fake resolved registry payload for the test.

        Requirements:
            - Preserve the registry ref and package version while swapping the launchable package name.

        :param registry_ref: Registry reference declared in the config.
        :param package_version: Pinned package version declared in the config.
        :returns: Fake resolved registry metadata.
        """

        return {
            "registry_ref": registry_ref,
            "package_version": package_version,
            "package": "@custom/server-github",
        }

    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        launcher=launcher,
        registry_resolver=resolve_registry,
    )
    manager.load_configs()

    started = await manager.start_server("github", session_id="sess-001")

    assert started is True
    assert launcher.calls[0]["cmd"] == [
        "npx",
        "-y",
        "@custom/server-github@1.2.3",
    ]


@pytest.mark.asyncio
async def test_start_servers_for_session_starts_runtime_once_and_needed_servers(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify session startup starts `mcp-runtime` once and then the required external MCP servers.

    Requirements:
        - This test is needed to prove external MCP subprocesses are scoped to the project runtime for each session.
        - Verify only servers needed by the session agents are launched and the runtime is started once.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(tmp_faith_dir, "github", build_external_payload())
    write_external_tool(
        tmp_faith_dir,
        "slack",
        build_external_payload(
            registry_ref="@modelcontextprotocol/server-slack",
            package_version="2.3.4",
            agents=["support-agent"],
        ),
    )
    launcher = FakeLauncher()
    container_manager = FakeContainerManager()
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        launcher=launcher,
        container_manager=container_manager,
    )
    manager.load_configs()

    results = await manager.start_servers_for_session(
        agent_ids=["software-developer"],
        session_id="sess-002",
        workspace_path=tmp_faith_dir.parent,
    )

    assert results == {"github": True}
    assert len(container_manager.start_calls) == 1
    assert sorted(container_manager.start_calls[0]["external_tools"]) == [
        "external-github",
        "external-slack",
    ]
    assert len(launcher.calls) == 1


@pytest.mark.asyncio
async def test_stop_session_servers_only_stops_matching_session(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify session shutdown only stops external MCP servers owned by that session.

    Requirements:
        - This test is needed to prove concurrent session lifecycles do not interfere with each other.
        - Verify only the matching session server is stopped and cleared.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(tmp_faith_dir, "github", build_external_payload())
    write_external_tool(
        tmp_faith_dir,
        "slack",
        build_external_payload(
            registry_ref="@modelcontextprotocol/server-slack",
            package_version="2.3.4",
            agents=["software-developer"],
        ),
    )
    launcher = FakeLauncher()
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        launcher=launcher,
    )
    manager.load_configs()
    await manager.start_server("github", session_id="sess-003")
    await manager.start_server("slack", session_id="sess-004")

    stopped = await manager.stop_session_servers("sess-003")

    assert stopped == 1
    assert manager.get_server("github").process is None
    assert manager.get_server("slack").process is not None


@pytest.mark.asyncio
async def test_get_server_stdio_returns_handles_for_running_server(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify stdio handles are exposed only for running external MCP servers.

    Requirements:
        - This test is needed to prove the MCP adapter can obtain a live stdio transport for routing.
        - Verify a started server returns stdin/stdout handles.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(tmp_faith_dir, "github", build_external_payload())
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        launcher=FakeLauncher(),
    )
    manager.load_configs()
    await manager.start_server("github", session_id="sess-005")

    stdio = manager.get_server_stdio("github")

    assert stdio is not None
    assert len(stdio) == 2


def test_reload_configs_preserves_running_process_state(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify reloading external MCP configs preserves running process state for unchanged registrations.

    Requirements:
        - This test is needed to prove hot-reload can pick up config changes without breaking active sessions.
        - Verify the running process handle survives a reload for an unchanged server name.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(tmp_faith_dir, "github", build_external_payload())
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
    )
    manager.load_configs()
    original = manager.get_server("github")
    original.process = FakeProcess(pid=9999)
    original.session_id = "sess-006"

    reloaded = manager.reload_configs()

    assert reloaded == 1
    updated = manager.get_server("github")
    assert updated.process is original.process
    assert updated.session_id == "sess-006"
