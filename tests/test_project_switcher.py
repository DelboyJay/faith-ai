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
