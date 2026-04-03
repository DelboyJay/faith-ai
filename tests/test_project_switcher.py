"""Description:
    Verify project switching and recent-project update behaviour.

Requirements:
    - Prove switching to the already-active project reports that state without teardown.
    - Prove loading a new project starts runtime support and updates recent-project tracking.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from faith_pa.config.models import PAConfig, PrivacyProfile, SystemConfig
from faith_pa.pa.project_switcher import ProjectSwitcher
from faith_pa.pa.session import SessionManager


class FakeContainerManager:
    """Description:
        Provide a minimal container-manager double for project-switcher tests.

    Requirements:
        - Expose the teardown and load hooks used by the project switcher.
        - Preserve state snapshots, finish signals, starts, stops, and tool reconfiguration calls.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake container-manager state.

            Requirements:
                - Start with empty lifecycle call logs.
        """

        self.faith_dir = None
        self.finish_calls: list[str] = []
        self.stop_calls: list[tuple[str, str]] = []
        self.start_calls: list[tuple[str, str, str]] = []
        self.tool_calls: list[tuple[str, str]] = []

    async def signal_agent_finish(self, agent_id: str) -> None:
        """Description:
            Record a finish signal for one agent.

            Requirements:
                - Preserve the agent identifier unchanged.

            :param agent_id: Agent identifier being signalled.
        """

        self.finish_calls.append(agent_id)

    async def get_agent_state(self, agent_id: str) -> dict[str, object]:
        """Description:
            Return a lightweight agent-state snapshot for teardown tests.

            Requirements:
                - Preserve the agent identifier and a recognisable summary payload.

            :param agent_id: Agent identifier to inspect.
            :returns: Lightweight state snapshot.
        """

        return {
            "agent_id": agent_id,
            "status": "busy",
            "current_task": f"{agent_id} task",
            "progress": "halfway",
            "channel_assignments": ["ch-auth"],
            "file_watches": ["src/auth.py"],
            "summary": f"{agent_id} summary",
        }

    async def stop(self, name: str, *, reason: str = "normal") -> None:
        """Description:
            Record a container stop request.

            Requirements:
                - Preserve the container name and stop reason.

            :param name: Container name to stop.
            :param reason: Stop reason string.
        """

        self.stop_calls.append((name, reason))

    def discover_agents(self) -> dict[str, dict[str, str]]:
        """Description:
            Return a small discovered-agent mapping for load tests.

            Requirements:
                - Expose at least one agent definition.

            :returns: Agent configuration mapping.
        """

        return {"developer": {"model": "gpt-5.4-mini", "role": "implementation"}}

    def discover_tools(self) -> dict[str, dict[str, object]]:
        """Description:
            Return a small discovered-tool mapping for load tests.

            Requirements:
                - Expose at least one tool definition.

            :returns: Tool configuration mapping.
        """

        return {"filesystem": {"image": "faith-tool-filesystem:latest", "env": {"MODE": "rw"}}}

    async def start_agent(
        self, *, agent_id: str, agent_config: dict[str, object], workspace_path: Path
    ) -> None:
        """Description:
            Record an agent-start request.

            Requirements:
                - Preserve the agent identifier, declared role, and workspace path.

            :param agent_id: Agent identifier to start.
            :param agent_config: Agent configuration payload.
            :param workspace_path: Project workspace path.
        """

        self.start_calls.append((agent_id, str(agent_config.get("role", "")), str(workspace_path)))

    async def reconfigure_tool(
        self, tool_name: str, tool_config: dict[str, object], workspace_path: Path
    ) -> None:
        """Description:
            Record a tool reconfiguration request.

            Requirements:
                - Preserve the tool name and workspace path.

            :param tool_name: Tool name to reconfigure.
            :param tool_config: Tool configuration payload.
            :param workspace_path: Project workspace path.
        """

        del tool_config
        self.tool_calls.append((tool_name, str(workspace_path)))


@pytest.fixture
def system_config() -> SystemConfig:
    """Description:
        Create a baseline system configuration for project-switcher tests.

        Requirements:
            - Provide a valid PA model configuration for session-manager initialisation.

        :returns: Baseline system configuration.
    """

    return SystemConfig(
        privacy_profile=PrivacyProfile.INTERNAL,
        pa=PAConfig(model="gpt-5.4"),
        default_agent_model="gpt-5.4-mini",
    )


@pytest.mark.asyncio
async def test_switch_project_marks_already_active(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify switching to the current project reports it as already active.

        Requirements:
            - This test is needed to prove redundant project switches do not trigger teardown and reload work.
            - Verify the result payload marks the project as already active.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    manager = SessionManager(project_root=tmp_path, system_config=system_config)
    switcher = ProjectSwitcher(session_manager=manager)
    result = await switcher.switch_project(tmp_path)
    assert result.already_active is True


@pytest.mark.asyncio
async def test_load_project_updates_recent_projects(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify loading a new project starts runtime support and updates recent-project state.

        Requirements:
            - This test is needed to prove project activation runs the expected runtime hooks.
            - Verify both the runtime-start and reindex callbacks are awaited.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    manager = SessionManager(project_root=tmp_path / "current", system_config=system_config)
    start_runtime = AsyncMock()
    reindex = AsyncMock()
    switcher = ProjectSwitcher(
        session_manager=manager,
        start_project_runtime=start_runtime,
        reindex_project=reindex,
    )
    target = tmp_path / "next"
    result = await switcher.load_project(target)
    assert result.project_root == target.resolve()
    start_runtime.assert_awaited_once()
    reindex.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_project_reports_first_visit(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify loading a project without a ``.faith`` directory reports the first-visit flow.

        Requirements:
            - This test is needed to prove the PA can distinguish uninitialised projects from existing FAITH workspaces.
            - Verify the load result marks the target as a first visit.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    manager = SessionManager(project_root=tmp_path / "current", system_config=system_config)
    switcher = ProjectSwitcher(session_manager=manager)
    target = tmp_path / "fresh-project"
    target.mkdir()

    result = await switcher.load_project(target)

    assert result.first_visit is True


@pytest.mark.asyncio
async def test_teardown_current_project_writes_state_for_active_agents(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify project teardown writes ``state.md`` files for active task agents before ending the session.

        Requirements:
            - This test is needed to prove project switching preserves resumable agent state.
            - Verify teardown writes one ``state.md`` file per active agent.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    project_root = tmp_path / "project"
    manager = SessionManager(project_root=project_root, system_config=system_config)
    await manager.start_session()
    task = manager.create_task("Implement auth", channels=["ch-auth"])
    task.stage_agents("build", ["developer", "qa"])
    manager.activate_phase(task.task_id, "build")
    stop_runtime = AsyncMock()
    switcher = ProjectSwitcher(session_manager=manager, stop_containers=stop_runtime)

    await switcher.teardown_current_project()

    assert (project_root / ".faith" / "agents" / "developer" / "state.md").exists()
    assert (project_root / ".faith" / "agents" / "qa" / "state.md").exists()
    stop_runtime.assert_awaited_once()


@pytest.mark.asyncio
async def test_teardown_current_project_signals_and_stops_agent_containers(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify project teardown signals active agents and stops their containers before leaving the project.

        Requirements:
            - This test is needed to prove the project-switch teardown follows the richer Phase 4 lifecycle contract.
            - Verify the switcher asks the container layer for state, signals finish, and stops the expected agent containers.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    project_root = tmp_path / "project"
    manager = SessionManager(project_root=project_root, system_config=system_config)
    await manager.start_session()
    task = manager.create_task("Implement auth", channels=["ch-auth"])
    task.stage_agents("build", ["developer", "qa"])
    manager.activate_phase(task.task_id, "build")
    container_manager = FakeContainerManager()
    switcher = ProjectSwitcher(session_manager=manager, container_manager=container_manager)

    await switcher.teardown_current_project()

    assert container_manager.finish_calls == ["developer", "qa"]
    assert container_manager.stop_calls == [
        ("faith-agent-developer", "project-switch"),
        ("faith-agent-qa", "project-switch"),
    ]
    saved = (project_root / ".faith" / "agents" / "developer" / "state.md").read_text(encoding="utf-8")
    assert "developer summary" in saved


@pytest.mark.asyncio
async def test_load_project_starts_agents_and_reconfigures_tools(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify loading a project starts discovered agents and reconfigures discovered tools.

        Requirements:
            - This test is needed to prove project activation reconstructs the team and project-scoped tool runtime.
            - Verify the container manager receives both the agent-start and tool-reconfigure requests.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    current_root = tmp_path / "current"
    target_root = tmp_path / "target"
    (target_root / ".faith").mkdir(parents=True)
    manager = SessionManager(project_root=current_root, system_config=system_config)
    container_manager = FakeContainerManager()
    switcher = ProjectSwitcher(session_manager=manager, container_manager=container_manager)

    result = await switcher.load_project(target_root)

    assert result.first_visit is False
    assert container_manager.start_calls == [
        ("developer", "implementation", str(target_root.resolve())),
    ]
    assert container_manager.tool_calls == [
        ("filesystem", str(target_root.resolve())),
    ]
