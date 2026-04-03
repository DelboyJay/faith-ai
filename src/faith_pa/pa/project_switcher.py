"""Description:
    Coordinate project teardown, loading, and recent-project tracking for the PA.

Requirements:
    - Persist lightweight agent state before switching away from the active project.
    - Start project runtime services and reindex the project after a successful load.
    - Keep the recent-project list updated under the framework config directory.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from faith_pa.config.loader import (
    load_all_agent_configs,
    load_all_tool_configs,
    project_config_dir,
    recent_projects_file,
)
from faith_pa.pa.session import AgentState, SessionManager


@dataclass(slots=True)
class ProjectLoadResult:
    """Description:
        Represent the result of loading or switching to a project.

    Requirements:
        - Preserve the resolved project root and whether the visit is the first one.

    :param project_root: Resolved project root path.
    :param first_visit: Whether the project had not been initialised before.
    :param already_active: Whether the requested project was already active.
    """

    project_root: Path
    first_visit: bool
    already_active: bool = False


class ProjectSwitcher:
    """Description:
        Coordinate project teardown, project load, and recent-project persistence.

    Requirements:
        - End the current session before loading a different project.
        - Stop running project containers when a stop callback is provided.
        - Start the new project runtime and optional reindex step after switching.

    :param session_manager: Session manager tracking the active project.
    :param stop_containers: Optional callback used to stop current project containers.
    :param start_project_runtime: Optional callback used to start the new project runtime.
    :param reindex_project: Optional callback used to refresh project indexes.
    """

    def __init__(
        self,
        *,
        session_manager: SessionManager,
        container_manager: object | None = None,
        stop_containers: Callable[[], Awaitable[None]] | None = None,
        start_project_runtime: Callable[[Path], Awaitable[None]] | None = None,
        reindex_project: Callable[[Path], Awaitable[None]] | None = None,
    ) -> None:
        """Description:
            Initialise the project switcher.

        Requirements:
            - Preserve the supplied session manager and optional runtime callbacks.

        :param session_manager: Session manager tracking the active project.
        :param container_manager: Optional container manager used for richer teardown/load orchestration.
        :param stop_containers: Optional callback used to stop current project containers.
        :param start_project_runtime: Optional callback used to start the new project runtime.
        :param reindex_project: Optional callback used to refresh project indexes.
        """

        self.session_manager = session_manager
        self.container_manager = container_manager
        self.stop_containers = stop_containers
        self.start_project_runtime = start_project_runtime
        self.reindex_project = reindex_project

    async def _persist_agent_state(self, agent_id: str, active_task) -> None:
        """Description:
            Persist one agent state snapshot during project teardown.

        Requirements:
            - Prefer the container-manager state snapshot when available.
            - Fall back to the active task summary when no runtime state is exposed.

        :param agent_id: Agent identifier to persist.
        :param active_task: Active task record supplying fallback state.
        """

        runtime_state: dict | None = None
        if self.container_manager is not None and hasattr(self.container_manager, "get_agent_state"):
            runtime_state = await self.container_manager.get_agent_state(agent_id)
        if runtime_state:
            state = AgentState(
                agent_id=agent_id,
                current_task=str(runtime_state.get("current_task", active_task.goal)),
                progress=str(runtime_state.get("progress", "")),
                channel_assignments=list(runtime_state.get("channel_assignments", sorted(active_task.channels))),
                file_watches=list(runtime_state.get("file_watches", [])),
                summary=str(runtime_state.get("summary", "Saved before switch")),
                status=str(runtime_state.get("status", "idle")),
            )
        else:
            state = AgentState(
                agent_id=agent_id,
                current_task=active_task.goal,
                progress=f"Phase {active_task.current_phase or '(none)'}",
                channel_assignments=sorted(active_task.channels),
                summary="Saved before switch",
                status="idle",
            )
        self.session_manager.write_agent_state(agent_id, state.to_markdown())

    async def _stop_active_agents(self, agent_ids: list[str]) -> None:
        """Description:
            Signal and stop active agent containers during project teardown.

        Requirements:
            - Send finish signals before requesting container shutdown when the container manager supports both hooks.

        :param agent_ids: Agent identifiers to stop.
        """

        if self.container_manager is None:
            return
        for agent_id in agent_ids:
            if hasattr(self.container_manager, "signal_agent_finish"):
                await self.container_manager.signal_agent_finish(agent_id)
            if hasattr(self.container_manager, "stop"):
                await self.container_manager.stop(f"faith-agent-{agent_id}", reason="project-switch")
            elif hasattr(self.container_manager, "stop_container"):
                await self.container_manager.stop_container(
                    f"faith-agent-{agent_id}",
                    reason="project-switch",
                )

    async def teardown_current_project(self) -> None:
        """Description:
            Persist lightweight state and tear down the currently active project.

        Requirements:
            - Persist an ``idle`` state file for agents attached to the active task.
            - End the current session before stopping runtime containers.

        """

        active_task = self.session_manager.get_active_task()
        if active_task is not None:
            for agent_id in sorted(active_task.agents):
                await self._persist_agent_state(agent_id, active_task)
            await self._stop_active_agents(sorted(active_task.agents))
        await self.session_manager.end_session()
        if self.stop_containers is not None:
            await self.stop_containers()

    def _load_agent_states(self, project_root: Path) -> dict[str, AgentState]:
        """Description:
            Load persisted per-agent state files for one project.

        Requirements:
            - Return only state files that exist and parse successfully.

        :param project_root: Project root to inspect.
        :returns: Parsed agent state snapshots keyed by agent identifier.
        """

        states: dict[str, AgentState] = {}
        agents_dir = project_config_dir(project_root) / "agents"
        if not agents_dir.exists():
            return states
        for state_path in sorted(agents_dir.glob("*/state.md")):
            agent_id = state_path.parent.name
            states[agent_id] = AgentState.from_markdown(state_path.read_text(encoding="utf-8"))
        return states

    async def load_project(self, project_root: Path) -> ProjectLoadResult:
        """Description:
            Load one project and start its runtime support.

        Requirements:
            - Resolve the project path before using it.
            - Detect whether this is the first visit by checking for the local project config directory.
            - Start project runtime services and indexing when callbacks are supplied.
            - Update the recent-project list after the load.

        :param project_root: Project root to load.
        :returns: Project load result payload.
        """

        project_root = Path(project_root).resolve()
        first_visit = not project_config_dir(project_root).exists()
        self.session_manager.project_root = project_root
        self.session_manager.workspace_path = project_root
        self.session_manager.faith_dir = project_config_dir(project_root)
        self.session_manager.sessions_dir = self.session_manager.faith_dir / "sessions"
        self.session_manager.sessions_dir.mkdir(parents=True, exist_ok=True)
        if self.container_manager is not None and hasattr(self.container_manager, "faith_dir"):
            self.container_manager.faith_dir = self.session_manager.faith_dir
        if self.container_manager is not None and hasattr(self.container_manager, "discover_agents"):
            for agent_id, config in self.container_manager.discover_agents().items():
                if hasattr(self.container_manager, "start_agent"):
                    await self.container_manager.start_agent(
                        agent_id=agent_id,
                        agent_config=config,
                        workspace_path=project_root,
                    )
        elif self.container_manager is not None:
            for agent_id, config in load_all_agent_configs(project_root).items():
                if hasattr(self.container_manager, "start_agent"):
                    await self.container_manager.start_agent(
                        agent_id=agent_id,
                        agent_config=config.model_dump(mode="python"),
                        workspace_path=project_root,
                    )
        if self.container_manager is not None:
            if hasattr(self.container_manager, "discover_tools"):
                tool_items = self.container_manager.discover_tools().items()
            else:
                tool_items = load_all_tool_configs(project_root).items()
            for tool_name, config in tool_items:
                if hasattr(self.container_manager, "reconfigure_tool"):
                    payload = config.model_dump(mode="python") if hasattr(config, "model_dump") else config
                    await self.container_manager.reconfigure_tool(
                        tool_name,
                        payload,
                        project_root,
                    )
        if self.start_project_runtime is not None:
            await self.start_project_runtime(project_root)
        if self.reindex_project is not None:
            await self.reindex_project(project_root)
        self._load_agent_states(project_root)
        self._update_recent_projects(project_root)
        return ProjectLoadResult(project_root=project_root, first_visit=first_visit)

    async def switch_project(self, project_root: Path) -> ProjectLoadResult:
        """Description:
            Switch from the current project to a new project root.

        Requirements:
            - Return ``already_active`` when the requested project is already current.
            - Tear down the current project before loading a different one.

        :param project_root: Project root to activate.
        :returns: Project load result payload.
        """

        project_root = Path(project_root).resolve()
        if project_root == self.session_manager.project_root.resolve():
            return ProjectLoadResult(
                project_root=project_root, first_visit=False, already_active=True
            )
        await self.teardown_current_project()
        return await self.load_project(project_root)

    def _update_recent_projects(self, project_root: Path, *, max_entries: int = 10) -> Path:
        """Description:
            Update the recent-projects file with the newly loaded project.

        Requirements:
            - Deduplicate the current project and keep it at the front of the list.
            - Persist at most the configured number of entries.

        :param project_root: Project root to insert.
        :param max_entries: Maximum number of recent projects to retain.
        :returns: Recent-projects file path.
        """

        path = recent_projects_file()
        existing: list[str] = []
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                existing = [str(item) for item in raw.get("projects", [])]
        target = str(project_root)
        deduped = [target, *[item for item in existing if item != target]]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"projects": deduped[:max_entries]}, sort_keys=False),
            encoding="utf-8",
        )
        return path
