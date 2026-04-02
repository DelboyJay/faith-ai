"""Description:
    Verify PA session and task lifecycle behaviour.

Requirements:
    - Prove sessions and tasks are created with persisted metadata.
    - Prove task-phase activation publishes agent notifications.
    - Prove idle monitoring ends sessions automatically.
    - Prove lightweight agent-state files round-trip correctly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from faith_pa.config.models import PAConfig, PrivacyProfile, SystemConfig
from faith_pa.pa.session import AgentState, SessionManager


class FakeRedis:
    """Description:
        Provide a minimal Redis double for session-manager tests.

    Requirements:
        - Record published messages for later assertions.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake Redis state.

        Requirements:
            - Start with an empty published-message log.
        """

        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        """Description:
            Record one published Redis message.

        Requirements:
            - Preserve the channel and payload for assertions.

        :param channel: Published channel name.
        :param payload: Published payload.
        """

        self.published.append((channel, payload))


@pytest.fixture
def system_config() -> SystemConfig:
    """Description:
        Create a baseline system configuration for session-manager tests.

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
async def test_session_manager_creates_session_and_task(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify the session manager creates a session, task metadata, and phase-activation notifications.

        Requirements:
            - This test is needed to prove session and task state is persisted for active work.
            - Verify the session and task identifiers are generated, task metadata is written, and agent activation is published.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

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
    """Description:
        Verify the idle monitor ends the session after the configured timeout.

        Requirements:
            - This test is needed to prove sessions do not remain active indefinitely when idle timeout is configured.
            - Verify the active session reference is cleared after the timeout expires.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    manager = SessionManager(
        project_root=tmp_path,
        system_config=system_config,
        idle_timeout_seconds=0.05,
    )
    await manager.start_session()
    await asyncio.sleep(0.2)

    assert manager.current_session is None


def test_agent_state_roundtrip() -> None:
    """Description:
        Verify lightweight agent-state markdown round-trips back into the same object.

        Requirements:
            - This test is needed to prove project-switch and recovery flows can persist and restore lightweight agent state.
            - Verify the parsed state equals the original state object.
    """

    state = AgentState(agent_id="developer", status="idle", summary="Waiting for review")
    parsed = AgentState.from_markdown(state.to_markdown())

    assert parsed == state
