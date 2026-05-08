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
    build_external_mcp_registration,
    build_git_mcp_registration,
    build_playwright_mcp_registration,
    build_postgresql_mcp_registration,
    build_tavily_mcp_registration,
    ensure_git_mcp_registration,
    ensure_playwright_mcp_registration,
    ensure_postgresql_mcp_registration,
    ensure_tavily_mcp_registration,
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
        yaml.safe_dump(
            {
                "credentials": {
                    "github-token": "secret-token",
                    "tavily-api-key": "tavily-secret",
                }
            },
            sort_keys=False,
        ),
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


def build_tavily_payload(**overrides: Any) -> dict[str, Any]:
    """Description:
        Build one valid Tavily external MCP tool config payload for tests.

    Requirements:
        - Produce a version-pinned Tavily registration with secret-backed API key resolution by default.
        - Allow callers to override individual fields per scenario.

    :param overrides: Field overrides merged into the baseline payload.
    :returns: Tavily external MCP tool config payload.
    """

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "registry_ref": "@tavily/mcp",
        "package_version": "0.2.9",
        "transport": "stdio",
        "env": {},
        "env_secret_refs": {"TAVILY_API_KEY": "tavily-api-key"},
        "privacy_tier": "internal",
        "agents": ["qa-engineer", "security-expert"],
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


def test_load_configs_skips_invalid_files_and_keeps_valid_registrations(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify invalid external MCP config files do not block valid registrations.

    Requirements:
        - This test is needed because one bad `.faith/tools/external-*.yaml` file should not take down all external MCP loading.
        - Verify the manager skips the invalid file and still registers the valid one.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(tmp_faith_dir, "github", build_external_payload())
    write_external_tool(
        tmp_faith_dir,
        "broken",
        {"schema_version": "1.0", "transport": "stdio"},
    )
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
    )

    count = manager.load_configs()

    assert count == 1
    assert manager.get_server("github") is not None
    assert manager.get_server("broken") is None


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


def test_generic_external_mcp_registration_supports_non_browser_servers() -> None:
    """Description:
        Verify FAITH can generate a generic external MCP registration template.

    Requirements:
        - This test is needed because optional external-first tools like RAG and pricing should not require FAITH-owned server code.
        - Verify the helper preserves registry/package metadata, agent targeting, and secret wiring for a non-browser server.
    """

    registration = build_external_mcp_registration(
        registry_ref="@modelcontextprotocol/server-rag",
        package_version="2.4.6",
        privacy_tier=PrivacyProfile.CONFIDENTIAL,
        agents=["project-agent", "researcher"],
        args=["--project-scope", "--source-aware"],
        env={"RAG_CACHE_DIR": "/workspace/.faith/rag"},
        env_secret_refs={"RAG_API_KEY": "rag-api-key"},
    )

    assert registration["registry_ref"] == "@modelcontextprotocol/server-rag"
    assert registration["package_version"] == "2.4.6"
    assert registration["privacy_tier"] == PrivacyProfile.CONFIDENTIAL.value
    assert registration["agents"] == ["project-agent", "researcher"]
    assert registration["args"] == ["--project-scope", "--source-aware"]
    assert registration["env"]["RAG_CACHE_DIR"] == "/workspace/.faith/rag"
    assert registration["env_secret_refs"] == {"RAG_API_KEY": "rag-api-key"}


def test_postgresql_mcp_registration_uses_secret_wiring_and_db_defaults() -> None:
    """Description:
        Verify FAITH can generate an external PostgreSQL registration with secret-backed credentials.

    Requirements:
        - This test is needed because database credentials must stay out of project tool files.
        - Verify the helper pins the package, assigns database-focused agents, and stores the password as a secret reference.
    """

    registration = build_postgresql_mcp_registration(
        package_version="0.1.3",
        host="db.internal",
        port=5432,
        database="myapp",
        user="agent_readonly",
        password_secret_ref="prod-db-password",
    )

    assert registration["registry_ref"] == "mcp-postgres-server"
    assert registration["package_version"] == "0.1.3"
    assert registration["privacy_tier"] == PrivacyProfile.INTERNAL.value
    assert registration["agents"] == ["software-developer", "fds-architect"]
    assert registration["env"] == {
        "PG_HOST": "db.internal",
        "PG_PORT": "5432",
        "PG_DATABASE": "myapp",
        "PG_USER": "agent_readonly",
    }
    assert registration["env_secret_refs"] == {"PG_PASSWORD": "prod-db-password"}


def test_git_mcp_registration_targets_local_repository_workspace() -> None:
    """Description:
        Verify FAITH can generate an external local Git registration that binds to the active workspace.

    Requirements:
        - This test is needed because local Git servers must operate on the checked-out repository, not a remote API.
        - Verify the helper pins the package and exposes the workspace binding placeholder for runtime resolution.
    """

    registration = build_git_mcp_registration(package_version="2.3.3")

    assert registration["registry_ref"] == "@cyanheads/git-mcp-server"
    assert registration["package_version"] == "2.3.3"
    assert registration["privacy_tier"] == PrivacyProfile.INTERNAL.value
    assert registration["agents"] == ["software-developer", "code-reviewer"]
    assert registration["env"]["GIT_BASE_DIR"] == "${FAITH_WORKSPACE_PATH}"
    assert registration["env"]["GIT_SIGN_COMMITS"] == "false"


def test_ensure_postgresql_mcp_registration_creates_default_tool_file(
    tmp_faith_dir: Path,
) -> None:
    """Description:
        Verify FAITH can materialise the default PostgreSQL external MCP registration.

    Requirements:
        - This test is needed so setup and wizard flows can add a compliant database server without duplicating YAML knowledge.
        - Verify the generated file uses the external registry package and secret-backed password wiring.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    """

    path = ensure_postgresql_mcp_registration(
        tmp_faith_dir,
        package_version="0.1.3",
    )

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert path == tmp_faith_dir / "tools" / "external-postgres.yaml"
    assert payload["registry_ref"] == "mcp-postgres-server"
    assert payload["package_version"] == "0.1.3"
    assert payload["env_secret_refs"] == {"PG_PASSWORD": "postgres-password"}


def test_ensure_git_mcp_registration_creates_default_tool_file(
    tmp_faith_dir: Path,
) -> None:
    """Description:
        Verify FAITH can materialise the default external Git registration.

    Requirements:
        - This test is needed so workspace-bound Git access can be installed without a hand-authored config file.
        - Verify the generated file uses the external Git package and workspace placeholder wiring.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    """

    path = ensure_git_mcp_registration(tmp_faith_dir, package_version="2.3.3")

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert path == tmp_faith_dir / "tools" / "external-git.yaml"
    assert payload["registry_ref"] == "@cyanheads/git-mcp-server"
    assert payload["package_version"] == "2.3.3"
    assert payload["env"]["GIT_BASE_DIR"] == "${FAITH_WORKSPACE_PATH}"


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

    ensured = ensure_playwright_mcp_registration(
        tmp_faith_dir,
        package_version="0.0.36",
    )

    payload = yaml.safe_load(ensured.read_text(encoding="utf-8"))
    assert ensured == path
    assert payload["package_version"] == "0.0.12"
    assert payload["enabled"] is False


def test_tavily_mcp_registration_uses_official_package_and_secret_backed_api_key() -> None:
    """Description:
        Verify the built-in Tavily MCP registration template matches the agreed external-search approach.

    Requirements:
        - This test is needed so FAITH can create a reliable default Tavily MCP registration without hand-authored YAML.
        - Verify the official package, pinned version, secret-backed API key mapping, and privacy tier are present.
    """

    registration = build_tavily_mcp_registration(package_version="0.2.9")

    assert registration["registry_ref"] == "@tavily/mcp"
    assert registration["package_version"] == "0.2.9"
    assert registration["env_secret_refs"] == {"TAVILY_API_KEY": "tavily-api-key"}
    assert registration["privacy_tier"] == "internal"


def test_ensure_tavily_mcp_registration_creates_default_tool_file(
    tmp_faith_dir: Path,
) -> None:
    """Description:
        Verify FAITH can materialise the default Tavily external MCP registration.

    Requirements:
        - This test is needed so setup/wizard code can install the default web-search registration without duplicating YAML knowledge.
        - Verify the generated file uses the official package and secret-backed API key configuration.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    """

    path = ensure_tavily_mcp_registration(tmp_faith_dir, package_version="0.2.9")

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert path == tmp_faith_dir / "tools" / "external-tavily.yaml"
    assert payload["registry_ref"] == "@tavily/mcp"
    assert payload["package_version"] == "0.2.9"
    assert payload["env_secret_refs"] == {"TAVILY_API_KEY": "tavily-api-key"}


def test_ensure_tavily_mcp_registration_preserves_existing_tool_file(
    tmp_faith_dir: Path,
) -> None:
    """Description:
        Verify default Tavily registration creation does not overwrite user config.

    Requirements:
        - This test is needed because users may pin a different Tavily MCP version or disable the server.
        - Verify an existing registration file is left unchanged.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    """

    path = write_external_tool(
        tmp_faith_dir,
        "tavily",
        build_tavily_payload(package_version="0.2.3", enabled=False),
    )

    ensured = ensure_tavily_mcp_registration(tmp_faith_dir, package_version="0.2.9")

    payload = yaml.safe_load(ensured.read_text(encoding="utf-8"))
    assert ensured == path
    assert payload["package_version"] == "0.2.3"
    assert payload["enabled"] is False


@pytest.mark.asyncio
async def test_tavily_mcp_registration_launches_with_resolved_api_key(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify the default Tavily MCP registration launches with a resolved API key.

    Requirements:
        - This test is needed because the Tavily API key must come from secrets/environment rather than hard-coded YAML.
        - Verify the launch command is version-pinned and the environment includes the resolved API key value.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(
        tmp_faith_dir,
        "tavily",
        build_tavily_payload(),
    )
    launcher = FakeLauncher()
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        launcher=launcher,
    )
    manager.load_configs()

    started = await manager.start_server("tavily", session_id="sess-tavily")

    assert started is True
    assert launcher.calls[0]["cmd"] == [
        "npx",
        "-y",
        "@tavily/mcp@0.2.9",
    ]
    assert launcher.calls[0]["env"]["TAVILY_API_KEY"] == "tavily-secret"
    assert manager.get_server("tavily").session_id == "sess-tavily"


@pytest.mark.asyncio
async def test_tavily_mcp_registration_blocks_confidential_privacy_before_secret_resolution(
    tmp_faith_dir: Path,
) -> None:
    """Description:
        Verify Tavily access is blocked for confidential projects before any outbound launch attempt.

    Requirements:
        - This test is needed to prove privacy gating happens before secret resolution or network activity.
        - Verify the manager returns `False` without consulting the secret resolver or launcher.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    """

    class FailingSecretResolver:
        """Description:
            Raise if the confidential privacy gate does not short-circuit first.

        Requirements:
            - Fail loudly if environment resolution is attempted.
        """

        def resolve_environment(
            self,
            *,
            env: dict[str, str] | None = None,
            env_secret_refs: dict[str, str] | None = None,
        ) -> dict[str, str]:
            """Description:
                Reject any secret resolution attempt in the confidential privacy case.

            Requirements:
                - Preserve the privacy regression test's short-circuit guarantee.

            :param env: Plain environment values.
            :param env_secret_refs: Secret-reference mapping.
            :raises AssertionError: Always raised if called.
            """

            raise AssertionError("secret resolution should not run for confidential privacy")

    write_external_tool(tmp_faith_dir, "tavily", build_tavily_payload())
    launcher = FakeLauncher()
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=FailingSecretResolver(),
        active_privacy_profile=PrivacyProfile.CONFIDENTIAL,
        launcher=launcher,
    )
    manager.load_configs()

    started = await manager.start_server("tavily", session_id="sess-private")

    assert started is False
    assert launcher.calls == []


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
async def test_start_server_launches_postgresql_process_with_resolved_env(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify starting the external PostgreSQL server resolves secret-backed environment values.

    Requirements:
        - This test is needed because database credentials must be resolved before launch without being written into the tool config.
        - Verify the launch command is version-pinned and the PostgreSQL password comes from the secret resolver.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(
        tmp_faith_dir,
        "postgres",
        build_postgresql_mcp_registration(
            package_version="0.1.3",
            password_secret_ref="github-token",
        ),
    )
    launcher = FakeLauncher()
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        launcher=launcher,
    )
    manager.load_configs()

    started = await manager.start_server("postgres", session_id="sess-db")

    assert started is True
    assert launcher.calls[0]["cmd"] == [
        "npx",
        "-y",
        "mcp-postgres-server@0.1.3",
    ]
    assert launcher.calls[0]["env"]["PG_PASSWORD"] == "secret-token"
    assert launcher.calls[0]["env"]["PG_HOST"] == "localhost"
    assert launcher.calls[0]["env"]["PG_DATABASE"] == "postgres"


@pytest.mark.asyncio
async def test_start_server_launches_git_process_with_workspace_path(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify starting the external Git server resolves the workspace-bound repository path.

    Requirements:
        - This test is needed because local Git operations must target the checked-out repository on disk.
        - Verify the workspace placeholder is expanded before the subprocess is launched.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(
        tmp_faith_dir,
        "git",
        build_git_mcp_registration(package_version="2.3.3"),
    )
    launcher = FakeLauncher()
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
        launcher=launcher,
    )
    manager.load_configs()

    started = await manager.start_server(
        "git",
        session_id="sess-git",
        workspace_path=tmp_faith_dir.parent,
    )

    assert started is True
    assert launcher.calls[0]["cmd"] == [
        "npx",
        "-y",
        "@cyanheads/git-mcp-server@2.3.3",
    ]
    assert launcher.calls[0]["env"]["GIT_BASE_DIR"] == str(tmp_faith_dir.parent.resolve())
    assert manager.get_server("git").session_id == "sess-git"


@pytest.mark.asyncio
async def test_start_server_returns_false_when_launch_fails(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify external MCP startup degrades cleanly when subprocess launch fails.

    Requirements:
        - This test is needed so one failed external tool does not crash the session flow.
        - Verify the manager returns `False` when the launcher raises an exception.

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

    async def failing_launcher(*cmd: str, env: dict[str, str]) -> FakeProcess:
        """Description:
            Raise a launch failure for the regression test.

        Requirements:
            - Simulate the subprocess launcher rejecting the request.

        :param cmd: Command-line segments for the launch attempt.
        :param env: Resolved process environment.
        :raises RuntimeError: Always raised to simulate a launch failure.
        """

        raise RuntimeError("launch failed")

    manager.launcher = failing_launcher

    started = await manager.start_server("github", session_id="sess-007")

    assert started is False
    assert manager.get_server("github").process is None


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


def test_list_servers_exposes_health_and_transport_metadata(
    tmp_faith_dir: Path,
    secret_resolver: SecretResolver,
) -> None:
    """Description:
        Verify external MCP summaries expose the metadata needed for the UI configuration page.

    Requirements:
        - This test is needed because optional external-first services still need health and transport details without FAITH-owned server logic.
        - Verify a registered server reports its transport, health, and rollback metadata fields.

    :param tmp_faith_dir: Temporary `.faith` directory fixture.
    :param secret_resolver: Temporary secret resolver fixture.
    """

    write_external_tool(
        tmp_faith_dir,
        "pricing",
        build_external_payload(
            registry_ref="@modelcontextprotocol/server-pricing",
            package_version="3.2.1",
        ),
    )
    manager = ExternalMCPManager(
        faith_dir=tmp_faith_dir,
        secret_resolver=secret_resolver,
    )
    manager.load_configs()

    listing = manager.list_servers()

    assert listing == [
        {
            "name": "pricing",
            "registry_ref": "@modelcontextprotocol/server-pricing",
            "package_version": "3.2.1",
            "previous_package_version": None,
            "enabled": True,
            "privacy_tier": PrivacyProfile.INTERNAL.value,
            "privacy_allowed": True,
            "transport": "stdio",
            "health": "stopped",
            "agents": ["software-developer", "qa-engineer"],
            "env_keys": ["API_TOKEN", "GITHUB_TOKEN"],
            "running": False,
            "session_id": None,
            "install_status": "registered",
            "available_update_version": None,
        }
    ]


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
