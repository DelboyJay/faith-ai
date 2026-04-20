"""Description:
    Verify PA intervention behaviour for stalled channels, blocked tasks, and agent errors.

Requirements:
    - Prove stalled channels trigger status requests and user notifications.
    - Prove blocked tasks can restart the required tool container.
    - Prove agent errors surface configured fallback models when available.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from faith_pa.config.models import PAConfig, PrivacyProfile, SystemConfig
from faith_pa.pa.intervention import InterventionHandler
from faith_pa.pa.session import SessionManager
from faith_shared.protocol.events import EventType, FaithEvent


class FakeRedis:
    """Description:
        Provide a minimal Redis double for intervention-handler tests.

    Requirements:
        - Record published messages and expose list-range lookups for channel history.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake Redis state.

        Requirements:
            - Start with empty published messages and history lists.
        """

        self.published: list[tuple[str, str]] = []
        self._lists: dict[str, list[bytes]] = {}

    async def publish(self, channel: str, payload: str) -> None:
        """Description:
            Record one published Redis message.

        Requirements:
            - Preserve the channel and payload for later assertions.

        :param channel: Published Redis channel name.
        :param payload: Published Redis payload.
        """

        self.published.append((channel, payload))

    async def lrange(self, key: str, start: int, end: int):
        """Description:
            Return a slice of the stored fake Redis list.

        Requirements:
            - Mirror the ``-1`` end-index behaviour used by the intervention handler.

        :param key: Redis list key.
        :param start: Slice start index.
        :param end: Slice end index.
        :returns: Selected fake list entries.
        """

        items = self._lists.get(key, [])
        return items[start:] if end == -1 else items[start : end + 1]


class FakeContainerManager:
    """Description:
        Provide a minimal container manager double for intervention tests.

    Requirements:
        - Record restarted container names.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake container manager state.

        Requirements:
            - Start with an empty restart log.
        """

        self.restarted: list[str] = []

    async def restart_container(self, name: str):
        """Description:
            Record a container restart request.

        Requirements:
            - Append the restarted container name to the restart log.

        :param name: Container name to restart.
        :returns: Restarted container name.
        """

        self.restarted.append(name)
        return name


@pytest.fixture
def system_config() -> SystemConfig:
    """Description:
    Create a baseline system configuration for intervention tests.

    Requirements:
        - Provide a PA model and fallback-model configuration for session-manager lookups.

    :returns: Baseline system configuration.
    """

    return SystemConfig(
        privacy_profile=PrivacyProfile.INTERNAL,
        pa=PAConfig(model="gpt-5.4", fallback_model="gpt-5.4-mini"),
        default_agent_model="gpt-5.4-mini",
    )


@pytest.mark.asyncio
async def test_handle_channel_stalled_requests_status(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
    Verify stalled channels trigger a status request and a user notification.

    Requirements:
        - This test is needed to prove the PA attempts recovery before escalating a stall.
        - Verify the last non-PA speaker receives the status request and the user channel is notified.

    :param tmp_path: Temporary pytest directory fixture.
    :param system_config: Baseline system configuration fixture.
    """

    del tmp_path, system_config
    redis = FakeRedis()
    redis._lists["channel:dev:messages"] = [
        json.dumps({"from": "developer", "summary": "Working"}).encode("utf-8"),
        json.dumps({"from": "pa", "summary": "Ack"}).encode("utf-8"),
    ]
    handler = InterventionHandler(redis_client=redis)

    result = await handler.handle_channel_stalled(
        FaithEvent(event=EventType.CHANNEL_STALLED, source="pa", channel="dev", data={})
    )

    assert result["agent"] == "developer"
    assert redis.published[0][0] == "pa-developer"
    assert redis.published[1][0] == "pa-user"


@pytest.mark.asyncio
async def test_handle_task_blocked_restarts_tool_container() -> None:
    """Description:
    Verify blocked tasks restart the referenced tool container when possible.

    Requirements:
        - This test is needed to prove recoverable tool blockers are handled automatically.
        - Verify the correct tool container restart is requested.
    """

    redis = FakeRedis()
    containers = FakeContainerManager()
    handler = InterventionHandler(redis_client=redis, container_manager=containers)

    result = await handler.handle_task_blocked(
        FaithEvent(
            event=EventType.AGENT_TASK_BLOCKED,
            source="developer",
            data={"waiting_for": "tool:filesystem"},
        )
    )

    assert result["restarted_tool"] == "filesystem"
    assert containers.restarted == ["filesystem"]


@pytest.mark.asyncio
async def test_handle_agent_error_uses_fallback_model(
    tmp_path: Path, system_config: SystemConfig
) -> None:
    """Description:
    Verify agent errors surface a configured fallback model when one is available.

    Requirements:
        - This test is needed to prove the PA can suggest a recovery path for agent model failures.
        - Verify the fallback model is included in the intervention result and the user is notified.

    :param tmp_path: Temporary pytest directory fixture.
    :param system_config: Baseline system configuration fixture.
    """

    project = tmp_path / "project"
    agent_dir = project / ".faith" / "agents" / "developer"
    agent_dir.mkdir(parents=True)
    agent_dir.joinpath("config.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "name": "Developer",
                "role": "implementation",
                "model": "gpt-5.4",
                "fallback_model": "gpt-5.4-mini",
                "trust": "standard",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    manager = SessionManager(project_root=project, system_config=system_config)
    redis = FakeRedis()
    handler = InterventionHandler(redis_client=redis, session_manager=manager)

    result = await handler.handle_agent_error(
        FaithEvent(event=EventType.AGENT_ERROR, source="developer", data={"error": "llm_failure"})
    )

    assert result["fallback_model"] == "gpt-5.4-mini"
    assert redis.published[-1][0] == "pa-user"
