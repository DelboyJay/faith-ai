from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from faith.config.models import PAConfig, PrivacyProfile, SystemConfig
from faith.pa.session import AgentState, SessionManager


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


@pytest.fixture
def system_config() -> SystemConfig:
    return SystemConfig(
        privacy_profile=PrivacyProfile.INTERNAL,
        pa=PAConfig(model="gpt-5.4"),
        default_agent_model="gpt-5.4-mini",
    )


@pytest.mark.asyncio
async def test_session_manager_creates_session_and_task(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    redis = FakeRedis()
    manager = SessionManager(project_root=tmp_path, system_config=system_config, redis_client=redis)
    session = await manager.start_session(trigger="cli")
    task = manager.create_task("Implement phase 4")
    task.stage_agents("design", ["architect"])

    activated = await manager.activate_task_phase(task, "design")

    assert session.session_id.startswith("sess-0001-")
    assert task.task_id.startswith("task-1-")
    assert activated == ["architect"]
    assert (task.path / "task.meta.json").exists()
    assert redis.published[0][0] == "pa-architect"


@pytest.mark.asyncio
async def test_idle_monitor_ends_session(tmp_path: Path, system_config: SystemConfig) -> None:
    manager = SessionManager(
        project_root=tmp_path,
        system_config=system_config,
        idle_timeout_seconds=0.05,
    )
    await manager.start_session()
    await asyncio.sleep(0.2)

    assert manager.current_session is None


def test_agent_state_roundtrip() -> None:
    state = AgentState(agent_id="developer", status="idle", summary="Waiting for review")
    parsed = AgentState.from_markdown(state.to_markdown())

    assert parsed == state
