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
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self._lists: dict[str, list[bytes]] = {}

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))

    async def lrange(self, key: str, start: int, end: int):
        items = self._lists.get(key, [])
        return items[start:] if end == -1 else items[start : end + 1]


class FakeContainerManager:
    def __init__(self) -> None:
        self.restarted: list[str] = []

    async def restart_container(self, name: str):
        self.restarted.append(name)
        return name


@pytest.fixture
def system_config() -> SystemConfig:
    return SystemConfig(
        privacy_profile=PrivacyProfile.INTERNAL,
        pa=PAConfig(model="gpt-5.4", fallback_model="gpt-5.4-mini"),
        default_agent_model="gpt-5.4-mini",
    )


@pytest.mark.asyncio
async def test_handle_channel_stalled_requests_status(
    tmp_path: Path, system_config: SystemConfig
) -> None:
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

