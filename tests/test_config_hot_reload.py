"""Description:
    Verify the configuration hot-reload watcher validates and publishes change events.

Requirements:
    - Prove valid configuration edits produce change notifications.
    - Prove invalid configuration edits produce error notifications and preserve the prior snapshot.
    - Prove prompt-file changes can be published without YAML validation.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from faith_pa.config.hot_reload import ConfigWatcher


class FakeRedis:
    """Description:
        Provide a minimal async Redis publisher for watcher tests.

    Requirements:
        - Record published channel and payload pairs for later assertions.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake Redis publisher state.

        Requirements:
            - Start with an empty message log.
        """

        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        """Description:
            Record a published Redis message.

        Requirements:
            - Preserve both the channel name and payload text for assertions.

        :param channel: Redis channel name.
        :param payload: Published payload text.
        """

        self.messages.append((channel, payload))


def write_file(path: Path, contents: str) -> None:
    """Description:
        Write one test configuration file with normalised indentation.

    Requirements:
        - Create parent directories when needed.
        - Ensure the resulting file ends with a trailing newline.

    :param path: Target file path.
    :param contents: Text content to write after dedenting.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).strip() + "\n", encoding="utf-8")


@pytest.fixture
def watcher_env(tmp_path, monkeypatch):
    """Description:
        Create a minimal framework and project configuration tree for watcher tests.

    Requirements:
        - Provide valid baseline secrets, system, and security configuration files.
        - Point the FAITH configuration environment variables at the temporary tree.

    :param tmp_path: Temporary pytest directory fixture.
    :param monkeypatch: Pytest environment mutation fixture.
    :returns: Tuple of project root and project ``.faith`` directory.
    """

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
    """Description:
    Verify a valid system configuration edit publishes a config-changed event.

    Requirements:
        - This test is needed to prove valid project-system edits are accepted and announced.
        - Verify the registered handler is invoked and the published event type is ``system:config_changed``.

    :param watcher_env: Temporary watcher environment fixture.
    """

    project_root, faith_dir = watcher_env
    redis = FakeRedis()
    handled: list[str] = []

    async def handler(path: Path) -> None:
        """Description:
            Record the handled file name for the watcher test.

        Requirements:
            - Preserve the handled path name for later assertions.

        :param path: Changed file path.
        """

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
    """Description:
    Verify an invalid YAML edit publishes an error event and preserves the prior snapshot.

    Requirements:
        - This test is needed to prove invalid config changes are rejected safely.
        - Verify the watcher does not advance the stored hash when validation fails.

    :param watcher_env: Temporary watcher environment fixture.
    """

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
    """Description:
    Verify prompt file changes bypass YAML validation and still publish a change event.

    Requirements:
        - This test is needed to prove non-YAML prompt edits can trigger reload flows.
        - Verify the prompt handler runs and the change event is published.

    :param watcher_env: Temporary watcher environment fixture.
    """

    project_root, faith_dir = watcher_env
    redis = FakeRedis()
    handled: list[str] = []
    prompt_path = faith_dir / "agents" / "dev" / "prompt.md"
    write_file(prompt_path, "hello")

    async def handler(path: Path) -> None:
        """Description:
            Record the handled prompt filename for the watcher test.

        Requirements:
            - Preserve the handled path name for later assertions.

        :param path: Changed prompt file path.
        """

        handled.append(path.name)

    watcher = ConfigWatcher(
        project_root=project_root, redis_client=redis, handlers={"agent.prompt": handler}
    )
    watcher.refresh_snapshot()
    write_file(prompt_path, "updated")

    events = await watcher.poll_once()
    assert events == [{"path": str(prompt_path), "kind": "agent.prompt"}]
    assert handled == ["prompt.md"]
