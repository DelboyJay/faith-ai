"""Project switching helpers for the PA."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from faith_pa.config.loader import project_config_dir, recent_projects_file
from faith_pa.pa.session import AgentState, SessionManager


@dataclass(slots=True)
class ProjectLoadResult:
    project_root: Path
    first_visit: bool
    already_active: bool = False


class ProjectSwitcher:
    """Coordinate project teardown/load and recent-project persistence."""

    def __init__(
        self,
        *,
        session_manager: SessionManager,
        stop_containers: Callable[[], Awaitable[None]] | None = None,
        start_project_runtime: Callable[[Path], Awaitable[None]] | None = None,
        reindex_project: Callable[[Path], Awaitable[None]] | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.stop_containers = stop_containers
        self.start_project_runtime = start_project_runtime
        self.reindex_project = reindex_project

    async def teardown_current_project(self) -> None:
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
        project_root = Path(project_root).resolve()
        if project_root == self.session_manager.project_root.resolve():
            return ProjectLoadResult(
                project_root=project_root, first_visit=False, already_active=True
            )
        await self.teardown_current_project()
        return await self.load_project(project_root)

    def _update_recent_projects(self, project_root: Path, *, max_entries: int = 10) -> Path:
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

