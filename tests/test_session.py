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
import json
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


@pytest.mark.asyncio
async def test_session_manager_persists_task_phase_and_session_summary(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify task metadata and session summary capture staged agents, active agents, and sandbox identity.

        Requirements:
            - This test is needed to prove FAITH persists enough task/session detail to reconstruct work after restart or project switch.
            - Verify task metadata includes staged phases and sandbox assignment, and session metadata reflects the task summary.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    manager = SessionManager(project_root=tmp_path, system_config=system_config)
    await manager.start_session(trigger="web-ui")
    task = manager.create_task(
        "Review auth flow",
        channels=["ch-auth-review"],
        staged_agents={"review": ["security", "qa"]},
        sandbox_id="sbx-0001",
    )

    activated = manager.activate_phase(task.task_id, "review")

    task_meta = (task.path / "task.meta.json").read_text(encoding="utf-8")
    session_meta = (manager.session_path() / "session.meta.json").read_text(encoding="utf-8")

    assert activated == ["security", "qa"]
    assert "\"sandbox_id\": \"sbx-0001\"" in task_meta
    assert "\"review\": [" in task_meta
    assert "\"channel\": \"ch-auth-review\"" in session_meta


def test_agent_state_roundtrip() -> None:
    """Description:
        Verify lightweight agent-state markdown round-trips back into the same object.

        Requirements:
            - This test is needed to prove project-switch and recovery flows can persist and restore lightweight agent state.
            - Verify the parsed state equals the original state object.
    """

    state = AgentState(
        agent_id="developer",
        current_task="Review login flow",
        progress="2 of 4 checks complete",
        channel_assignments=["ch-auth", "ch-review"],
        file_watches=["src/auth.py"],
        summary="Waiting for review",
    )
    parsed = AgentState.from_markdown(state.to_markdown())

    assert parsed == state


@pytest.mark.asyncio
async def test_session_manager_persists_task_and_session_metadata(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify the session manager writes structured metadata for sessions and tasks.

        Requirements:
            - This test is needed to prove Phase 4 session/task state survives beyond in-memory runtime state.
            - Verify both the session and task metadata files contain the expected identifiers and status fields.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    manager = SessionManager(project_root=tmp_path, system_config=system_config)
    session = await manager.start_session(trigger="web-ui")
    task = manager.create_task("Implement phase 4", channels=["ch-phase4"], sandbox_id="sbx-0001")

    session_meta = json.loads((session.path / "session.meta.json").read_text(encoding="utf-8"))
    task_meta = json.loads((task.path / "task.meta.json").read_text(encoding="utf-8"))

    assert session_meta["session_id"] == session.session_id
    assert session_meta["status"] == "active"
    assert task_meta["task_id"] == task.task_id
    assert task_meta["sandbox_id"] == "sbx-0001"
    assert "ch-phase4" in task_meta["channels"]


@pytest.mark.asyncio
async def test_session_manager_validates_agent_cag_at_session_start(
    tmp_path: Path,
    system_config: SystemConfig,
) -> None:
    """Description:
        Verify session start records per-agent CAG validation results in session metadata.

    Requirements:
        - This test is needed to prove the PA performs Phase 7 CAG validation when a session starts.
        - Verify missing CAG documents are recorded in the session metadata report for the affected agent.

    :param tmp_path: Temporary pytest directory fixture.
    :param system_config: Baseline system configuration fixture.
    """

    agent_dir = tmp_path / ".faith" / "agents" / "developer"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "config.yaml").write_text(
        """
name: Developer
role: implementation
model: gpt-5.4-mini
cag_documents:
  - docs/missing.md
""".strip(),
        encoding="utf-8",
    )
    manager = SessionManager(project_root=tmp_path, system_config=system_config)

    session = await manager.start_session(trigger="web-ui")

    session_meta = json.loads((session.path / "session.meta.json").read_text(encoding="utf-8"))
    assert "cag_validation" in session_meta
    assert "developer" in session_meta["cag_validation"]["agents"]
    assert session_meta["cag_validation"]["agents"]["developer"]["success"] is False
    assert session_meta["cag_validation"]["agents"]["developer"]["errors"]
    assert "developer" in session_meta["cag_validation"]["report"]


@pytest.mark.asyncio
async def test_session_manager_end_session_completes_active_tasks(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
        Verify ending a session completes active tasks and clears the active-session pointer.

        Requirements:
            - This test is needed to prove session teardown leaves no active tasks behind.
            - Verify the task status becomes complete and the active-session pointer is cleared.

        :param tmp_path: Temporary pytest directory fixture.
        :param system_config: Baseline system configuration fixture.
    """

    manager = SessionManager(project_root=tmp_path, system_config=system_config)
    await manager.start_session()
    task = manager.create_task("Finish me")

    await manager.end_session()

    task_meta = json.loads((task.path / "task.meta.json").read_text(encoding="utf-8"))
    assert task_meta["status"] == "complete"
    assert manager.current_session is None
