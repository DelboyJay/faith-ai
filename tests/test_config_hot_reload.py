"""Tests for config hot reload watcher."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from faith.config.hot_reload import ConfigWatcher


class FakeRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.messages.append((channel, payload))


def write_file(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).strip() + "\n", encoding="utf-8")


@pytest.fixture
def watcher_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    project_root = tmp_path / "project"
    faith_dir = project_root / ".faith"
    monkeypatch.setenv("FAITH_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))

    write_file(config_dir / "secrets.yaml", 'schema_version: "1.0"\nsecrets: {}\n')
    write_file(
        faith_dir / "system.yaml",
        """
        schema_version: "1.0"
        privacy_profile: internal
        pa:
          model: claude
        default_agent_model: claude
        """,
    )
    write_file(faith_dir / "security.yaml", 'schema_version: "1.0"\napproval_rules: {}\n')

    return project_root, faith_dir


@pytest.mark.asyncio
async def test_valid_system_change_publishes_changed_event(watcher_env):
    project_root, faith_dir = watcher_env
    redis = FakeRedis()
    handled: list[str] = []

    async def handler(path: Path) -> None:
        handled.append(path.name)

    watcher = ConfigWatcher(
        project_root=project_root, redis_client=redis, handlers={"project.system": handler}
    )
    watcher.refresh_snapshot()

    write_file(
        faith_dir / "system.yaml",
        """
        schema_version: "1.0"
        privacy_profile: confidential
        pa:
          model: claude
        default_agent_model: claude
        """,
    )

    events = await watcher.poll_once()
    assert events == [{"path": str(faith_dir / "system.yaml"), "kind": "project.system"}]
    assert handled == ["system.yaml"]
    payload = json.loads(redis.messages[-1][1])
    assert payload["event"] == "system:config_changed"


@pytest.mark.asyncio
async def test_invalid_yaml_publishes_error_and_keeps_hash(watcher_env):
    project_root, faith_dir = watcher_env
    redis = FakeRedis()
    watcher = ConfigWatcher(project_root=project_root, redis_client=redis)
    watcher.refresh_snapshot()
    original_hash = watcher._hashes[faith_dir / "system.yaml"]

    (faith_dir / "system.yaml").write_text("schema_version: [", encoding="utf-8")

    events = await watcher.poll_once()
    assert events == []
    assert watcher._hashes[faith_dir / "system.yaml"] == original_hash
    payload = json.loads(redis.messages[-1][1])
    assert payload["event"] == "system:config_error"


@pytest.mark.asyncio
async def test_prompt_change_skips_validation_and_publishes(watcher_env):
    project_root, faith_dir = watcher_env
    redis = FakeRedis()
    handled: list[str] = []
    prompt_path = faith_dir / "agents" / "dev" / "prompt.md"
    write_file(prompt_path, "hello")

    async def handler(path: Path) -> None:
        handled.append(path.name)

    watcher = ConfigWatcher(
        project_root=project_root, redis_client=redis, handlers={"agent.prompt": handler}
    )
    watcher.refresh_snapshot()
    write_file(prompt_path, "updated")

    events = await watcher.poll_once()
    assert events == [{"path": str(prompt_path), "kind": "agent.prompt"}]
    assert handled == ["prompt.md"]
