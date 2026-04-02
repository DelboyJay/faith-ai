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

from faith_pa.config.loader import project_config_dir, recent_projects_file
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
        stop_containers: Callable[[], Awaitable[None]] | None = None,
        start_project_runtime: Callable[[Path], Awaitable[None]] | None = None,
        reindex_project: Callable[[Path], Awaitable[None]] | None = None,
    ) -> None:
        """Description:
            Initialise the project switcher.

        Requirements:
            - Preserve the supplied session manager and optional runtime callbacks.

        :param session_manager: Session manager tracking the active project.
        :param stop_containers: Optional callback used to stop current project containers.
        :param start_project_runtime: Optional callback used to start the new project runtime.
        :param reindex_project: Optional callback used to refresh project indexes.
        """

        self.session_manager = session_manager
        self.stop_containers = stop_containers
        self.start_project_runtime = start_project_runtime
        self.reindex_project = reindex_project

    async def teardown_current_project(self) -> None:
        """Description:
            Persist lightweight state and tear down the currently active project.

        Requirements:
            - Persist an ``idle`` state file for agents attached to the active task.
            - End the current session before stopping runtime containers.

        """

        session = self.session_manager.current_session
        if session is not None and session.active_task is not None:
            for agent_id in session.active_task.active_agents:
                agent_dir = self.session_manager.project_root / ".faith" / "agents" / agent_id
                agent_dir.mkdir(parents=True, exist_ok=True)
                state = AgentState(agent_id=agent_id, status="idle", summary="Saved before switch")
                (agent_dir / "state.md").write_text(state.to_markdown(), encoding="utf-8")
        await self.session_manager.end_session()
        if self.stop_containers is not None:
            await self.stop_containers()

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
        if self.start_project_runtime is not None:
            await self.start_project_runtime(project_root)
        if self.reindex_project is not None:
            await self.reindex_project(project_root)
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
