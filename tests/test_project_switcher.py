from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from faith.config.models import PAConfig, PrivacyProfile, SystemConfig
from faith.pa.project_switcher import ProjectSwitcher
from faith.pa.session import SessionManager


@pytest.fixture
def system_config() -> SystemConfig:
    return SystemConfig(
        privacy_profile=PrivacyProfile.INTERNAL,
        pa=PAConfig(model="gpt-5.4"),
        default_agent_model="gpt-5.4-mini",
    )


@pytest.mark.asyncio
async def test_switch_project_marks_already_active(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    manager = SessionManager(project_root=tmp_path, system_config=system_config)
    switcher = ProjectSwitcher(session_manager=manager)
    result = await switcher.switch_project(tmp_path)
    assert result.already_active is True


@pytest.mark.asyncio
async def test_load_project_updates_recent_projects(
    tmp_path: Path, system_config: SystemConfig
) -> None:
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
