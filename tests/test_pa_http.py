"""Description:
    High-level HTTP and runtime tests for the FAITH Project Agent API.

Requirements:
    - Verify the currently implemented PA HTTP endpoints return stable
      request/response behaviour under normal conditions.
    - Verify expected service-unavailable responses are returned when Redis
      is unavailable instead of surfacing internal server errors.
    - Verify the browser-chat relay publishes streamed project-agent output.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import faith_pa.pa.app as pa_app_module
from faith_pa.pa.chat_tool_loop import ChatToolCall, list_available_chat_mcp_tools
from faith_pa.runtime_time_context import RuntimeTimeContextProvider
from faith_pa.utils.redis_client import USER_INPUT_CHANNEL


class FakePubSub:
    """Description:
    Provide a minimal async Redis pub/sub stand-in for PA runtime tests.

    Requirements:
    - Record subscribed and unsubscribed channels for lifecycle assertions.
    - Replay queued pub/sub messages in deterministic order.
    """

    def __init__(self) -> None:
        """Description:
        Initialise the fake pub/sub object.

        Requirements:
        - Start with empty subscription and queued-message state.
        """

        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.messages: list[dict[str, str]] = []
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        """Description:
        Record one or more subscription requests.

        Requirements:
        - Preserve subscription order for later assertions.

        :param channels: Redis channel names to subscribe.
        """

        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels: str) -> None:
        """Description:
        Record one or more unsubscribe requests.

        Requirements:
        - Preserve unsubscribe order for later assertions.

        :param channels: Redis channel names to unsubscribe.
        """

        self.unsubscribed.extend(channels)

    async def get_message(
        self,
        ignore_subscribe_messages: bool = True,
        timeout: float = 1.0,
    ) -> dict[str, str] | None:
        """Description:
        Return one queued pub/sub message when available.

        Requirements:
        - Consume queued messages in order.
        - Yield briefly and return ``None`` when no message is queued.

        :param ignore_subscribe_messages: Compatibility argument matching the Redis API.
        :param timeout: Requested polling timeout.
        :returns: Next queued pub/sub message, or ``None`` when empty.
        """

        del ignore_subscribe_messages, timeout
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0.01)
        return None

    async def aclose(self) -> None:
        """Description:
        Mark the fake pub/sub object as closed.

        Requirements:
        - Support shutdown assertions for background runtime tasks.
        """

        self.closed = True

    def inject_message(self, channel: str, payload: str) -> None:
        """Description:
        Queue one synthetic Redis message for the background chat bridge.

        Requirements:
        - Preserve the channel and payload exactly as provided by the test.

        :param channel: Redis channel name attached to the message.
        :param payload: Published payload string.
        """

        self.messages.append({"type": "message", "channel": channel, "data": payload})


class FakeRedis:
    """Description:
    Minimal async Redis stand-in for PA HTTP endpoint tests.

    Requirements:
    - Capture published messages so tests can assert that event endpoints
      emit the expected payload.
    - Support the subset of async methods used by the PA lifespan and
      health checks.
    """

    def __init__(self, *, ping_value: bool = True) -> None:
        """Description:
        Initialise the fake Redis client.

        Requirements:
        - Allow tests to simulate both healthy and unhealthy Redis states.
        - Start with an empty published-message buffer.

        :param ping_value: Value returned from `ping()`.
        """
        self.ping_value = ping_value
        self.published: list[tuple[str, str]] = []
        self.pubsub_instance = FakePubSub()

    async def ping(self) -> bool:
        """Description:
        Simulate a Redis `PING`.

        Requirements:
        - Return the configured health value so tests can control service
          status.

        :returns: Configured ping response value.
        """
        return self.ping_value

    async def publish(self, channel: str, payload: str) -> None:
        """Description:
        Record a published message.

        Requirements:
        - Preserve channel and payload order for later assertions.

        :param channel: Redis channel name.
        :param payload: Published payload string.
        """
        self.published.append((channel, payload))

    async def aclose(self) -> None:
        """Description:
        Provide the async close hook expected by the PA lifespan.

        Requirements:
        - Be safely callable without side effects.
        """

    def pubsub(self) -> FakePubSub:
        """Description:
        Return the fake pub/sub instance used by PA runtime tests.

        Requirements:
        - Return the same fake pub/sub object across the full test lifespan.

        :returns: Reusable fake pub/sub object.
        """

        return self.pubsub_instance


class FakeLLMResponse:
    """Description:
    Provide one deterministic LLM response payload for PA chat-bridge tests.

    Requirements:
    - Preserve content and token counts expected by the PA runtime.
    """

    def __init__(self, content: str) -> None:
        """Description:
        Initialise the fake LLM response.

        Requirements:
        - Preserve the supplied content unchanged.

        :param content: Response content returned to the caller.
        """

        self.content = content
        self.input_tokens = 12
        self.output_tokens = 8


class FakeLLMClient:
    """Description:
    Provide a deterministic LLM client stand-in for PA chat-bridge tests.

    Requirements:
    - Capture chat payloads for later assertions.
    - Return a fixed response without network access.
    """

    def __init__(self, content: str = "Streaming reply from the PA.") -> None:
        """Description:
        Initialise the fake LLM client.

        Requirements:
        - Start with an empty captured call list.

        :param content: Response content returned by ``chat()``.
        """

        self.content = content
        self.calls: list[dict[str, object]] = []

    async def chat(
        self,
        messages: list[dict[str, object]],
        *,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> FakeLLMResponse:
        """Description:
        Record one chat request and return a deterministic response.

        Requirements:
        - Preserve the supplied message payload and model metadata for later assertions.

        :param messages: Chat message payload supplied by the PA.
        :param model: Optional model override.
        :param fallback_model: Optional fallback-model override.
        :param temperature: Optional temperature override.
        :param max_tokens: Optional output-token cap.
        :returns: Deterministic fake LLM response.
        """

        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "fallback_model": fallback_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return FakeLLMResponse(self.content)


class SequencedFakeLLMClient:
    """Description:
    Provide deterministic multi-turn LLM responses for PA tool-loop tests.

    Requirements:
    - Return one configured response per chat call.
    - Preserve all chat payloads so tests can prove tool manifests and tool
      results are sent back to the model.
    """

    def __init__(self, responses: list[str]) -> None:
        """Description:
        Initialise the sequenced fake client.

        Requirements:
        - Copy the response list so tests do not mutate internal state by
          accident.

        :param responses: Ordered response contents returned by ``chat()``.
        """

        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def chat(
        self,
        messages: list[dict[str, object]],
        *,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> FakeLLMResponse:
        """Description:
        Return the next configured fake LLM response.

        Requirements:
        - Capture the full call metadata for assertions.
        - Return an empty response when the configured response list is
          exhausted so failures remain visible to the caller.

        :param messages: Chat messages supplied by the PA runtime.
        :param model: Optional model override.
        :param fallback_model: Optional fallback model override.
        :param temperature: Optional temperature override.
        :param max_tokens: Optional output-token cap.
        :returns: Deterministic fake LLM response.
        """

        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "fallback_model": fallback_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        content = self.responses.pop(0) if self.responses else ""
        return FakeLLMResponse(content)


class FakeToolExecutor:
    """Description:
    Provide a deterministic PA MCP tool executor for chat-loop tests.

    Requirements:
    - Capture tool requests without launching real MCP containers.
    - Return a stable payload that can be fed back to the model.
    """

    def __init__(self) -> None:
        """Description:
        Initialise the fake tool executor.

        Requirements:
        - Start with no captured calls.
        """

        self.calls: list[object] = []

    async def execute(self, request: object) -> dict[str, object]:
        """Description:
        Record one tool request and return a deterministic result.

        Requirements:
        - Preserve the original request object for assertions.
        - Return a structured success payload matching the PA executor contract.

        :param request: Parsed tool-call request from the PA chat loop.
        :returns: Deterministic tool execution result.
        """

        self.calls.append(request)
        return {"success": True, "result": {"content": "README says FAITH is local-first."}}

    def list_available_tools(self) -> tuple[object, ...]:
        """Description:
        Return the canonical chat-visible MCP inventory for tests.

        Requirements:
        - Reuse the production inventory helper so test expectations stay in
          sync with the PA chat runtime.

        :returns: Canonical tuple of chat-visible MCP tool descriptors.
        """

        return list_available_chat_mcp_tools()


class SequencedClock:
    """Description:
    Provide deterministic UTC datetimes for PA runtime time-context tests.

    Requirements:
    - Return configured datetimes in order.
    - Reuse the last configured datetime once the sequence is exhausted.

    :param values: Ordered UTC datetimes returned by the callable clock.
    """

    def __init__(self, *values: datetime) -> None:
        """Description:
        Initialise the deterministic PA clock sequence.

        Requirements:
        - Preserve the supplied datetime order for later calls.

        :param values: Ordered UTC datetimes returned by the callable clock.
        """

        self.values = list(values)
        self.last = values[-1]

    def __call__(self) -> datetime:
        """Description:
        Return the next configured UTC datetime.

        Requirements:
        - Consume values in order.
        - Reuse the final value once the configured sequence is exhausted.

        :returns: Deterministic UTC datetime.
        """

        if self.values:
            self.last = self.values.pop(0)
        return self.last


@pytest.fixture
def fake_redis() -> FakeRedis:
    """Description:
    Provide a healthy fake Redis client for PA HTTP tests.

    Requirements:
    - Default to a healthy client so happy-path endpoint tests use the
      same fixture.

    :returns: Healthy fake Redis client.
    """
    return FakeRedis()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> TestClient:
    """Description:
    Create a TestClient bound to the PA application with fake Redis.

    Requirements:
    - Replace the PA lifespan Redis client factory so tests do not depend
      on an external Redis server.
    - Restore normal application behaviour after the test finishes.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param fake_redis: Healthy fake Redis client.
    :returns: Test client for the PA application.
    """

    async def _get_async_client() -> FakeRedis:
        """Description:
        Return the fake Redis client for lifespan startup.

        Requirements:
        - Match the async contract of the production factory.

        :returns: Fake Redis client.
        """
        return fake_redis

    fake_llm_client = FakeLLMClient()

    monkeypatch.setattr(pa_app_module, "get_async_client", _get_async_client)
    monkeypatch.setattr(
        pa_app_module,
        "LLMClient",
        lambda **kwargs: fake_llm_client,
        raising=False,
    )
    with TestClient(pa_app_module.app) as test_client:
        yield test_client


def test_pa_health_returns_ok(client: TestClient) -> None:
    """Description:
    Verify the PA health endpoint returns a healthy status payload.

    Requirements:
    - Prove the health endpoint responds successfully under normal
      conditions.
    - Verify the endpoint does not surface a 500 error for the happy path.

    :param client: Test client for the PA application.
    """
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "faith-project-agent"
    assert payload["status"] == "ok"


def test_project_agent_default_model_is_local_ollama() -> None:
    """Description:
    Verify the Project Agent falls back to the local Ollama model by default.

    Requirements:
    - This test is needed to keep the runtime PA default aligned with the 6GB GPU baseline.
    - Verify the imported default model selects the managed Ollama service model.
    """

    assert pa_app_module.DEFAULT_PROJECT_AGENT_MODEL == "ollama/llama3:8b"


def test_pa_status_returns_snapshot(client: TestClient) -> None:
    """Description:
    Verify the PA status endpoint returns the runtime snapshot payload.

    Requirements:
    - Prove the status endpoint is reachable via a normal HTTP request.
    - Verify the endpoint returns the expected top-level service fields.

    :param client: Test client for the PA application.
    """
    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "faith-project-agent"
    assert "redis" in payload
    assert "config" in payload


def test_pa_status_includes_runtime_snapshot(client: TestClient) -> None:
    """Description:
    Verify the PA status endpoint includes Docker runtime snapshot data.

    Requirements:
    - This test is needed to prove the shared PA status payload exposes runtime container visibility.
    - Verify the endpoint includes a runtime block with a container list.

    :param client: Test client for the PA application.
    """

    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert "runtime" in payload
    assert "containers" in payload["runtime"]


def test_pa_docker_runtime_endpoint_returns_snapshot(client: TestClient) -> None:
    """Description:
    Verify the dedicated PA Docker runtime endpoint returns a runtime snapshot payload.

    Requirements:
    - This test is needed to prove the PA exposes a dedicated runtime feed for the Web UI Docker panel.
    - Verify the endpoint responds successfully with the expected top-level runtime fields.

    :param client: Test client for the PA application.
    """

    response = client.get("/api/docker-runtime")

    assert response.status_code == 200
    payload = response.json()
    assert "docker_available" in payload
    assert "containers" in payload
    assert "images" in payload


def test_pa_docker_runtime_websocket_streams_snapshot(client: TestClient) -> None:
    """Description:
    Verify the PA Docker runtime WebSocket streams snapshot payloads.

    Requirements:
    - This test is needed to prove the PA exposes the event-driven runtime feed used by the Web UI.
    - Verify one streamed payload includes the expected runtime snapshot fields.

    :param client: Test client for the PA application.
    """

    pa_app_module.app.state.runtime_snapshot_builder = lambda: {
        "docker_available": True,
        "status": "ok",
        "containers": [],
    }
    try:
        with client.websocket_connect("/ws/docker") as websocket:
            payload = websocket.receive_json()
        assert payload["docker_available"] is True
        assert payload["status"] == "ok"
    finally:
        if hasattr(pa_app_module.app.state, "runtime_snapshot_builder"):
            delattr(pa_app_module.app.state, "runtime_snapshot_builder")


def test_pa_config_returns_redacted_summary(client: TestClient) -> None:
    """Description:
    Verify the PA config endpoint returns a config summary payload.

    Requirements:
    - Prove the config endpoint responds successfully over HTTP.
    - Verify the response shape is suitable for a high-level API check.

    :param client: Test client for the PA application.
    """
    response = client.get("/api/config")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    assert "config_dir" in payload


def test_user_settings_endpoint_returns_project_settings(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Description:
    Verify the PA user-settings endpoint returns persisted project user settings.

    Requirements:
    - This test is needed to prove the settings panel can preload saved values instead of showing an empty form every time.
    - Verify the endpoint returns display name, preferred locale, timezone, and config metadata over HTTP.

    :param client: Test client for the PA application.
    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary project root used to hold the test system config.
    """

    project_root = tmp_path / "workspace"
    faith_dir = project_root / ".faith"
    faith_dir.mkdir(parents=True)
    (faith_dir / "system.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {"model": "ollama/llama3:8b"},
                "default_agent_model": "ollama/llama3:8b",
                "country_code": "GB",
                "timezone": "Europe/London",
                "display_name": "Del",
                "preferred_locale": "en-GB",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))

    response = client.get("/api/user-settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_name"] == "Del"
    assert payload["country_code"] == "GB"
    assert payload["preferred_locale"] == "en-GB"
    assert payload["timezone"] == "Europe/London"
    assert payload["locale_options"]
    assert payload["locale_options_by_country"]["GB"][0]["value"] == "en-GB"
    assert payload["country_options"]
    assert payload["timezone_options"]
    assert payload["timezone_options"][0]["value"] == "Europe/London"
    assert payload["path"].endswith(".faith/system.yaml")


def test_user_settings_endpoint_normalises_locale_to_selected_country(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Description:
    Verify the PA user-settings endpoint normalises stale locale values to the selected country.

    Requirements:
    - This test is needed to prove saved country, locale, and timezone values cannot drift into a confusing mismatch.
    - Verify the endpoint falls back to the first supported locale for the resolved country when the saved locale belongs elsewhere.

    :param client: Test client for the PA application.
    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary project root used to hold the persisted user-settings overlay.
    """

    session_root = tmp_path / "pa-runtime"
    overlay_path = session_root / "user-settings" / "system.yaml"
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(
        json.dumps(
            {
                "display_name": "Del",
                "country_code": "GB",
                "preferred_locale": "en-CA",
                "timezone": "Europe/London",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAITH_PA_SESSION_ROOT", str(session_root))
    monkeypatch.delenv("FAITH_DATA_DIR", raising=False)
    monkeypatch.delenv("FAITH_PROJECT_ROOT", raising=False)
    pa_app_module.app.state.user_settings_store = pa_app_module.UserSettingsStore(
        project_root=tmp_path / "workspace"
    )

    response = client.get("/api/user-settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["country_code"] == "GB"
    assert payload["preferred_locale"] == "en-GB"
    assert payload["locale_options"] == [{"value": "en-GB", "label": "English (United Kingdom)"}]


def test_user_settings_update_persists_timezone_and_refreshes_runtime(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Description:
    Verify updating user settings persists the new timezone and refreshes the live PA runtime.

    Requirements:
    - This test is needed to prove accepted settings changes affect future agent turns without a restart.
    - Verify the project system config is rewritten and the existing PA chat runtime starts using the new timezone immediately.

    :param client: Test client for the PA application.
    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary project root used to hold the test system config.
    """

    project_root = tmp_path / "workspace"
    faith_dir = project_root / ".faith"
    faith_dir.mkdir(parents=True)
    system_path = faith_dir / "system.yaml"
    system_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {"model": "ollama/llama3:8b"},
                "default_agent_model": "ollama/llama3:8b",
                "timezone": "UTC",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))

    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=FakeRedis(),
        llm_client=FakeLLMClient(),
        model_name="ollama/llama3:8b",
        prompt_store=pa_app_module.ProjectAgentPromptStore(project_root=project_root),
        session_manager=pa_app_module.SessionManager(project_root=project_root),
        time_context_provider=RuntimeTimeContextProvider(
            configured_timezone="UTC",
            now_provider=SequencedClock(datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc)),
        ),
    )
    pa_app_module.app.state.project_agent_chat_runtime = runtime

    response = client.put(
        "/api/user-settings",
        json={
            "display_name": "Del",
            "country_code": "GB",
            "preferred_locale": "en-GB",
            "timezone": "Europe/London",
        },
    )

    assert response.status_code == 200
    updated = json.loads(system_path.read_text(encoding="utf-8"))
    assert updated["display_name"] == "Del"
    assert updated["country_code"] == "GB"
    assert updated["preferred_locale"] == "en-GB"
    assert updated["timezone"] == "Europe/London"
    assert runtime.time_context_provider.configured_timezone == "Europe/London"


def test_user_settings_update_rejects_timezone_outside_selected_country(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Description:
    Verify the PA user-settings update endpoint rejects timezone choices that do not belong to the selected country.

    Requirements:
    - This test is needed to prove country-filtered timezone selection remains correct even if a crafted request bypasses the browser UI.
    - Verify the endpoint returns HTTP 400 when the timezone is not valid for the submitted country.

    :param client: Test client for the PA application.
    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary project root used to hold the test system config.
    """

    project_root = tmp_path / "workspace"
    faith_dir = project_root / ".faith"
    faith_dir.mkdir(parents=True)
    (faith_dir / "system.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {"model": "ollama/llama3:8b"},
                "default_agent_model": "ollama/llama3:8b",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))

    response = client.put(
        "/api/user-settings",
        json={
            "display_name": "Del",
            "country_code": "GB",
            "preferred_locale": "en-GB",
            "timezone": "America/New_York",
        },
    )

    assert response.status_code == 400
    assert "country" in response.json()["detail"].lower()


def test_user_settings_update_rejects_locale_outside_selected_country(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Description:
    Verify the PA user-settings update endpoint rejects locale choices that do not belong to the selected country.

    Requirements:
    - This test is needed to prove crafted requests cannot persist a mismatched country and locale pair.
    - Verify the endpoint returns HTTP 400 when the locale is not valid for the submitted country.

    :param client: Test client for the PA application.
    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary project root used to hold the test system config.
    """

    project_root = tmp_path / "workspace"
    faith_dir = project_root / ".faith"
    faith_dir.mkdir(parents=True)
    (faith_dir / "system.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {"model": "ollama/llama3:8b"},
                "default_agent_model": "ollama/llama3:8b",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))

    response = client.put(
        "/api/user-settings",
        json={
            "display_name": "Del",
            "country_code": "GB",
            "preferred_locale": "en-CA",
            "timezone": "Europe/London",
        },
    )

    assert response.status_code == 400
    assert "preferred locale must belong" in response.json()["detail"].lower()


def test_user_settings_update_rejects_invalid_timezone(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Description:
    Verify the PA user-settings update endpoint rejects invalid timezone identifiers.

    Requirements:
    - This test is needed to prove invalid timezone values fail with HTTP 400 instead of corrupting the config.
    - Verify the endpoint returns a plain-English validation message.

    :param client: Test client for the PA application.
    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary project root used to hold the test system config.
    """

    project_root = tmp_path / "workspace"
    faith_dir = project_root / ".faith"
    faith_dir.mkdir(parents=True)
    (faith_dir / "system.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {"model": "ollama/llama3:8b"},
                "default_agent_model": "ollama/llama3:8b",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))

    response = client.put(
        "/api/user-settings",
        json={"display_name": "Del", "preferred_locale": "en-GB", "timezone": "Mars/Olympus"},
    )

    assert response.status_code == 400
    assert "timezone" in response.json()["detail"].lower()


def test_pa_system_prompt_store_uses_host_backed_runtime_volume_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Description:
    Verify the Project Agent prompt store persists edited prompts under the host-backed runtime volume.

    Requirements:
    - This test is needed to prove PA prompt edits survive container rebuilds when FAITH runs with a mounted data volume.
    - Verify the prompt path resolves under `FAITH_DATA_DIR/pa-runtime` and accepted updates are written there.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary directory used to simulate the host-backed data volume.
    """

    data_root = tmp_path / "data"
    monkeypatch.setenv("FAITH_DATA_DIR", str(data_root))
    store = pa_app_module.ProjectAgentPromptStore(project_root=tmp_path / "workspace")

    payload = store.update("Persist this custom PA prompt on the host-backed volume.")

    assert store.prompt_path == data_root / "pa-runtime" / "agents" / "project-agent" / "prompt.md"
    assert store.prompt_path.read_text(encoding="utf-8") == payload["prompt"]
    assert payload["path"].endswith("pa-runtime/agents/project-agent/prompt.md")


def test_user_settings_store_uses_host_backed_runtime_volume_when_configured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Description:
    Verify user-settings updates persist under the host-backed runtime volume when configured.

    Requirements:
    - This test is needed to prove browser-saved user settings survive container rebuilds.
    - Verify the API reads defaults from project config but writes persisted overrides to `FAITH_DATA_DIR/pa-runtime`.

    :param client: Test client for the PA application.
    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary directory used for project config and host-backed runtime data.
    """

    project_root = tmp_path / "workspace"
    faith_dir = project_root / ".faith"
    faith_dir.mkdir(parents=True)
    (faith_dir / "system.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {"model": "ollama/llama3:8b"},
                "default_agent_model": "ollama/llama3:8b",
                "timezone": "UTC",
            }
        ),
        encoding="utf-8",
    )
    data_root = tmp_path / "data"
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("FAITH_DATA_DIR", str(data_root))

    response = client.put(
        "/api/user-settings",
        json={
            "display_name": "Del",
            "preferred_locale": "en-GB",
            "timezone": "Europe/London",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    overlay_path = data_root / "pa-runtime" / "user-settings" / "system.yaml"
    assert overlay_path.exists()
    stored = json.loads(overlay_path.read_text(encoding="utf-8"))
    assert stored["display_name"] == "Del"
    assert stored["preferred_locale"] == "en-GB"
    assert stored["timezone"] == "Europe/London"
    assert payload["path"].endswith("pa-runtime/user-settings/system.yaml")
    assert json.loads((faith_dir / "system.yaml").read_text(encoding="utf-8"))["timezone"] == "UTC"


def test_publish_test_event_returns_payload_and_publishes(
    client: TestClient, fake_redis: FakeRedis
) -> None:
    """Description:
    Verify the PA test-event endpoint publishes to Redis and returns a payload.

    Requirements:
    - Prove the event test endpoint works through a normal HTTP request.
    - Verify the endpoint does not return a server error on the happy path.

    :param client: Test client for the PA application.
    :param fake_redis: Fake Redis client used by the application lifespan.
    """
    response = client.post("/api/events/test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["event"] == "poc:test"
    assert len(fake_redis.published) == 1


def test_publish_test_event_returns_503_when_redis_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Description:
    Verify the PA test-event endpoint returns 503 when Redis is unavailable.

    Requirements:
    - This test is needed to prevent Redis outages from surfacing as 500
      internal server errors.
    - Verify the endpoint returns the expected service-unavailable error
      code under that condition.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    async def _get_async_client() -> None:
        """Description:
        Simulate a missing Redis client during application startup.

        Requirements:
        - Return `None` so the route exercises its unavailable-dependency
          path.
        """
        return None

    monkeypatch.setattr(pa_app_module, "get_async_client", _get_async_client)

    with TestClient(pa_app_module.app) as client:
        response = client.post("/api/events/test")

    assert response.status_code == 503
    assert response.json()["detail"] == "Redis not available"


def test_pa_health_returns_503_when_redis_unhealthy(
    client: TestClient, fake_redis: FakeRedis
) -> None:
    """Description:
    Verify the PA health endpoint returns 503 when Redis is unhealthy.

    Requirements:
    - This test is needed to prove the PA reports dependency degradation
      without surfacing an internal server error.
    - Verify the health endpoint returns HTTP 503 when Redis health checks fail.

    :param client: Test client for the PA application.
    :param fake_redis: Fake Redis client used by the application lifespan.
    """
    fake_redis.ping_value = False

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_pa_routes_returns_manifest(client: TestClient) -> None:
    """Description:
    Verify the PA route-discovery endpoint returns the structured route manifest.

    Requirements:
    - This test is needed to prove the CLI can discover PA endpoints without hard-coding them.
    - Verify the manifest includes both HTTP and WebSocket routes with expected metadata.

    :param client: Test client for the PA application.
    """
    response = client.get("/api/routes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "faith-project-agent"
    assert any(route["path"] == "/api/routes" for route in payload["routes"])
    assert any(route["path"] == "/ws/status" for route in payload["routes"])
    assert any(route["path"] == "/api/pa/system-prompt" for route in payload["routes"])


def test_pa_system_prompt_endpoint_returns_default_prompt(
    client: TestClient,
    tmp_path,
) -> None:
    """Description:
    Verify the PA system-prompt endpoint returns the built-in prompt when no override exists.

    Requirements:
    - This test is needed to prove the prompt editor can load the active prompt.
    - Verify response metadata identifies the default source and configured file path.

    :param client: Test client for the PA application.
    :param tmp_path: Temporary project root used by the prompt store.
    """

    pa_app_module.app.state.project_agent_prompt_store = pa_app_module.ProjectAgentPromptStore(
        project_root=tmp_path
    )
    response = client.get("/api/pa/system-prompt")

    assert response.status_code == 200
    payload = response.json()
    assert payload["prompt"] == pa_app_module.DEFAULT_PROJECT_AGENT_SYSTEM_PROMPT
    assert payload["source"] == "default"
    assert payload["default_available"] is True
    assert payload["differs_from_default"] is False
    assert payload["path"].endswith(".faith/agents/project-agent/prompt.md")


def test_pa_system_prompt_update_rejects_blank_prompt(
    client: TestClient,
    tmp_path,
) -> None:
    """Description:
    Verify blank PA system-prompt updates are rejected without changing the active prompt.

    Requirements:
    - This test is needed to prove invalid edits fail with a plain-English error.
    - Verify the default prompt remains active after the rejected update.

    :param client: Test client for the PA application.
    :param tmp_path: Temporary project root used by the prompt store.
    """

    pa_app_module.app.state.project_agent_prompt_store = pa_app_module.ProjectAgentPromptStore(
        project_root=tmp_path
    )
    response = client.put("/api/pa/system-prompt", json={"prompt": "   "})

    assert response.status_code == 400
    assert "Prompt cannot be empty" in response.json()["detail"]
    current = client.get("/api/pa/system-prompt").json()
    assert current["prompt"] == pa_app_module.DEFAULT_PROJECT_AGENT_SYSTEM_PROMPT
    assert current["source"] == "default"


def test_pa_system_prompt_update_persists_and_future_messages_use_it(
    client: TestClient,
    tmp_path,
) -> None:
    """Description:
    Verify accepted PA prompt updates persist and are used for future chat payloads.

    Requirements:
    - This test is needed to prove the editor affects future PA inference context.
    - Verify the prompt file is written under the approved Project Agent path.

    :param client: Test client for the PA application.
    :param tmp_path: Temporary project root used by the prompt store.
    """

    prompt_store = pa_app_module.ProjectAgentPromptStore(project_root=tmp_path)
    pa_app_module.app.state.project_agent_prompt_store = prompt_store
    custom_prompt = "You are the FAITH Project Agent. Prefer short, verified answers."

    response = client.put("/api/pa/system-prompt", json={"prompt": custom_prompt})

    assert response.status_code == 200
    payload = response.json()
    assert payload["prompt"] == custom_prompt
    assert payload["source"] == "custom"
    assert payload["differs_from_default"] is True
    assert prompt_store.prompt_path.read_text(encoding="utf-8") == custom_prompt

    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=FakeRedis(),
        llm_client=FakeLLMClient(),
        model_name="ollama/llama3:8b",
        prompt_store=prompt_store,
        session_manager=pa_app_module.SessionManager(project_root=tmp_path),
    )
    system_message = runtime._build_chat_messages("hello")[0]["content"]
    assert custom_prompt in system_message
    assert pa_app_module.DEFAULT_PROJECT_AGENT_SYSTEM_PROMPT not in system_message


def test_pa_system_prompt_reset_restores_default_prompt(
    client: TestClient,
    tmp_path,
) -> None:
    """Description:
    Verify the reset endpoint removes the custom PA prompt and restores the default.

    Requirements:
    - This test is needed to prove users can safely recover from edited prompts.
    - Verify the persisted custom prompt file is removed after reset.

    :param client: Test client for the PA application.
    :param tmp_path: Temporary project root used by the prompt store.
    """

    prompt_store = pa_app_module.ProjectAgentPromptStore(project_root=tmp_path)
    pa_app_module.app.state.project_agent_prompt_store = prompt_store
    client.put("/api/pa/system-prompt", json={"prompt": "Custom prompt."})

    response = client.post("/api/pa/system-prompt/reset")

    assert response.status_code == 200
    payload = response.json()
    assert payload["prompt"] == pa_app_module.DEFAULT_PROJECT_AGENT_SYSTEM_PROMPT
    assert payload["source"] == "default"
    assert not prompt_store.prompt_path.exists()


def test_pa_system_prompt_update_does_not_rewrite_history(tmp_path) -> None:
    """Description:
    Verify prompt changes do not mutate already stored PA transcript history.

    Requirements:
    - This test is needed to prove historical transcript entries remain untouched.
    - Verify future chat payloads include the new prompt while old history content remains unchanged.

    :param tmp_path: Temporary project root used by the prompt store.
    """

    prompt_store = pa_app_module.ProjectAgentPromptStore(project_root=tmp_path)
    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=FakeRedis(),
        llm_client=FakeLLMClient(),
        model_name="ollama/llama3:8b",
        prompt_store=prompt_store,
        session_manager=pa_app_module.SessionManager(project_root=tmp_path),
    )
    runtime._append_history("user", "Earlier user message")
    runtime._append_history("assistant", "Earlier assistant message")

    prompt_store.update("You are the FAITH Project Agent. Use the edited prompt.")
    messages = runtime._build_chat_messages("New user message")

    assert runtime.history == [
        {"role": "user", "content": "Earlier user message"},
        {"role": "assistant", "content": "Earlier assistant message"},
    ]
    assert "Use the edited prompt" in messages[0]["content"]
    assert messages[1:3] == runtime.history


def test_pa_chat_messages_include_runtime_time_context_and_refresh_each_turn(tmp_path) -> None:
    """Description:
    Verify the PA system message includes runtime-managed time context on every turn.

    Requirements:
    - This test is needed to prove the Project Agent gets explicit local date,
      local time, and timezone context without rewriting the persisted prompt.
    - Verify the time block refreshes between turns when the clock advances.

    :param tmp_path: Temporary project root used by the prompt store.
    """

    prompt_store = pa_app_module.ProjectAgentPromptStore(project_root=tmp_path)
    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=FakeRedis(),
        llm_client=FakeLLMClient(),
        model_name="ollama/llama3:8b",
        prompt_store=prompt_store,
        time_context_provider=RuntimeTimeContextProvider(
            configured_timezone="Europe/London",
            now_provider=SequencedClock(
                datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc),
                datetime(2026, 1, 15, 10, 45, tzinfo=timezone.utc),
            ),
        ),
    )

    first_system_message = runtime._build_chat_messages("hello")[0]["content"]
    second_system_message = runtime._build_chat_messages("hello again")[0]["content"]

    assert "[Runtime Time Context]" in first_system_message
    assert "Current local date: 2026-01-15" in first_system_message
    assert "Current local time: 10:30:00" in first_system_message
    assert "User timezone: Europe/London" in first_system_message
    assert "Current local time: 10:45:00" in second_system_message
    assert first_system_message != second_system_message


def test_pa_chat_messages_include_saved_user_settings_context(tmp_path: Path) -> None:
    """Description:
    Verify the Project Agent system message includes persisted user-profile context.

    Requirements:
    - This test is needed to prove the saved nickname and other user settings reach the LLM prompt on every turn.
    - Verify the system message tells the model what to call the user and includes the saved country and locale.

    :param tmp_path: Temporary project root used by the prompt and settings stores.
    """

    faith_dir = tmp_path / ".faith"
    faith_dir.mkdir(parents=True, exist_ok=True)
    (faith_dir / "system.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {"model": "ollama/llama3:8b"},
                "default_agent_model": "ollama/llama3:8b",
                "timezone": "Europe/London",
            }
        ),
        encoding="utf-8",
    )
    prompt_store = pa_app_module.ProjectAgentPromptStore(project_root=tmp_path)
    settings_store = pa_app_module.UserSettingsStore(project_root=tmp_path)
    settings_store.update(
        pa_app_module.UserSettingsUpdate(
            display_name="Delboy",
            country_code="GB",
            preferred_locale="en-GB",
            timezone="Europe/London",
        )
    )
    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=FakeRedis(),
        llm_client=FakeLLMClient(),
        model_name="ollama/llama3:8b",
        prompt_store=prompt_store,
        time_context_provider=RuntimeTimeContextProvider(
            configured_timezone="Europe/London",
            now_provider=SequencedClock(datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)),
        ),
        user_settings_store=settings_store,
    )

    system_message = runtime._build_chat_messages("hello")[0]["content"]

    assert "[Runtime User Context]" in system_message
    assert "Address the user as: Delboy" in system_message
    assert "User country: GB" in system_message
    assert "Preferred locale: en-GB" in system_message


def test_user_settings_update_refreshes_project_agent_user_prompt_context(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Description:
    Verify saving user settings refreshes the live Project Agent user-context prompt block.

    Requirements:
    - This test is needed to prove changing the saved nickname updates future Project Agent turns immediately.
    - Verify the active runtime prompt starts addressing the user by the saved display name after a settings update.

    :param client: Test client for the PA application.
    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary project root used to hold the test system config.
    """

    project_root = tmp_path / "workspace"
    faith_dir = project_root / ".faith"
    faith_dir.mkdir(parents=True)
    (faith_dir / "system.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "privacy_profile": "internal",
                "pa": {"model": "ollama/llama3:8b"},
                "default_agent_model": "ollama/llama3:8b",
                "timezone": "UTC",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))

    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=FakeRedis(),
        llm_client=FakeLLMClient(),
        model_name="ollama/llama3:8b",
        prompt_store=pa_app_module.ProjectAgentPromptStore(project_root=project_root),
        session_manager=pa_app_module.SessionManager(project_root=project_root),
        time_context_provider=RuntimeTimeContextProvider(
            configured_timezone="UTC",
            now_provider=SequencedClock(datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc)),
        ),
    )
    pa_app_module.app.state.project_agent_chat_runtime = runtime

    response = client.put(
        "/api/user-settings",
        json={
            "display_name": "Delboy",
            "country_code": "GB",
            "preferred_locale": "en-GB",
            "timezone": "Europe/London",
        },
    )

    assert response.status_code == 200
    system_message = runtime._build_chat_messages("hello")[0]["content"]
    assert "Address the user as: Delboy" in system_message
    assert "User country: GB" in system_message
    assert "Preferred locale: en-GB" in system_message


def test_pa_chat_bridge_streams_project_agent_frames(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    """Description:
    Verify the PA background chat bridge converts browser input into streamed
    project-agent frames.

    Requirements:
    - This test is needed to prove the browser chat shell can receive live
      project-agent output rather than only publishing input into Redis.
    - Verify one user-input message produces status and output frames on the
      canonical project-agent output channel.

    :param client: Test client for the PA application.
    :param fake_redis: Fake Redis client used by the application lifespan.
    """

    del client
    fake_redis.pubsub_instance.inject_message(
        USER_INPUT_CHANNEL,
        json.dumps(
            {
                "type": "user_input",
                "message_id": "msg-001",
                "message": "Please explain the architecture.",
            }
        ),
    )

    async def _wait_for_stream_frames() -> None:
        """Description:
        Wait until the fake Redis publish log contains streamed project-agent frames.

        Requirements:
        - Fail when the chat bridge does not publish within a short timeout.
        """

        for _ in range(40):
            published = [
                json.loads(payload)
                for channel, payload in fake_redis.published
                if channel == "agent:project-agent:output"
            ]
            if any(item.get("type") == "output" for item in published):
                return
            await asyncio.sleep(0.02)
        pytest.fail("Timed out waiting for streamed project-agent output.")

    asyncio.run(_wait_for_stream_frames())

    published = [
        json.loads(payload)
        for channel, payload in fake_redis.published
        if channel == "agent:project-agent:output"
    ]
    assert any(
        item.get("type") == "status" and item.get("status") == "active" for item in published
    )
    assert any(item.get("type") == "output" for item in published)
    assert any(item.get("type") == "status" and item.get("status") == "idle" for item in published)


def test_pa_chat_bridge_calls_llm_with_user_message(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    """Description:
    Verify the PA chat bridge sends the browser message through the configured LLM client.

    Requirements:
    - This test is needed to prove the chat runtime is not faking replies locally.
    - Verify the captured LLM payload includes the original user message text.

    :param client: Test client for the PA application.
    :param fake_redis: Fake Redis client used by the application lifespan.
    """

    del client
    llm_client = getattr(pa_app_module.app.state, "chat_llm_client", None)
    assert llm_client is not None

    fake_redis.pubsub_instance.inject_message(
        USER_INPUT_CHANNEL,
        json.dumps(
            {
                "type": "user_input",
                "message_id": "msg-002",
                "message": "Summarise the current task status.",
            }
        ),
    )

    async def _wait_for_llm_call() -> None:
        """Description:
        Wait until the fake LLM client has received one browser-chat request.

        Requirements:
        - Fail when the background bridge does not call the LLM within a short timeout.
        """

        for _ in range(40):
            if llm_client.calls:
                return
            await asyncio.sleep(0.02)
        pytest.fail("Timed out waiting for the PA chat bridge to call the LLM.")

    asyncio.run(_wait_for_llm_call())

    assert llm_client.calls
    assert llm_client.calls[0]["messages"][-1]["content"] == "Summarise the current task status."


def test_pa_chat_bridge_accepts_second_message_after_prompt_update(tmp_path) -> None:
    """Description:
    Verify the PA chat runtime still processes a later browser message after the prompt is edited.

    Requirements:
    - This test is needed to prove prompt updates do not dead-end the ongoing PA chat flow.
    - Verify the second LLM call uses the updated prompt and still streams output for the later message.

    :param tmp_path: Temporary project root used by the prompt store.
    """

    llm_client = SequencedFakeLLMClient(
        [
            "First reply before the prompt change.",
            "Second reply after the prompt change.",
        ]
    )
    fake_redis = FakeRedis()
    prompt_store = pa_app_module.ProjectAgentPromptStore(project_root=tmp_path)
    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=fake_redis,
        llm_client=llm_client,
        model_name="ollama/llama3:8b",
        prompt_store=prompt_store,
    )

    asyncio.run(
        runtime._handle_payload(
            {
                "type": "user_input",
                "message_id": "msg-before-prompt-update",
                "message": "Please answer the first message.",
            }
        )
    )
    prompt_store.update("You are the FAITH Project Agent. Confirm that the new prompt is active.")
    asyncio.run(
        runtime._handle_payload(
            {
                "type": "user_input",
                "message_id": "msg-after-prompt-update",
                "message": "Please answer the second message after the prompt update.",
            }
        )
    )

    assert len(llm_client.calls) == 2
    assert (
        pa_app_module.DEFAULT_PROJECT_AGENT_SYSTEM_PROMPT
        in llm_client.calls[0]["messages"][0]["content"]
    )
    assert "Confirm that the new prompt is active." in llm_client.calls[1]["messages"][0]["content"]
    output_text = "".join(
        json.loads(payload).get("text", "")
        for channel, payload in fake_redis.published
        if channel == "agent:project-agent:output"
    )
    assert "Please answer the second message after the prompt update." in output_text
    assert "Second reply after the prompt change." in output_text


def test_pa_chat_runtime_restores_latest_saved_transcript_on_startup(tmp_path: Path) -> None:
    """Description:
    Verify the PA chat runtime reloads the latest saved browser transcript during startup.

    Requirements:
        - This test is needed to prove restart-time browser chat rehydration uses the persisted session log rather than starting blank.
        - Verify the runtime restores full transcript messages for the UI snapshot and bounded recent history for future LLM turns.

    :param tmp_path: Temporary project root used to hold the persisted session log.
    """

    session_manager = pa_app_module.SessionManager(project_root=tmp_path)
    asyncio.run(session_manager.start_session(trigger="web-ui"))
    session_manager.append_project_agent_message("user", "Recovered user message.")
    session_manager.append_project_agent_message("assistant", "Recovered assistant reply.")

    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=FakeRedis(),
        llm_client=FakeLLMClient(),
        model_name="ollama/llama3:8b",
        session_manager=pa_app_module.SessionManager(project_root=tmp_path),
    )

    assert runtime.export_transcript_messages() == [
        {"role": "user", "content": "Recovered user message."},
        {"role": "assistant", "content": "Recovered assistant reply."},
    ]
    assert runtime.history == [
        {"role": "user", "content": "Recovered user message."},
        {"role": "assistant", "content": "Recovered assistant reply."},
    ]


def test_project_agent_session_manager_uses_explicit_session_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Description:
        Verify the PA runtime resolves transcript/session persistence from the dedicated session-root setting.

    Requirements:
        - This test is needed to prove restart-time Project Agent transcript rehydration survives PA container rebuilds.
        - Verify the default PA session manager uses `FAITH_PA_SESSION_ROOT` instead of the container-local repository path.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    session_root = tmp_path / "persistent-pa-runtime"
    monkeypatch.setenv("FAITH_PA_SESSION_ROOT", str(session_root))

    test_app = FastAPI()
    manager = pa_app_module._get_project_agent_session_manager(test_app)

    assert manager.project_root == session_root.resolve()
    assert manager.faith_dir == session_root.resolve() / ".faith"


def test_pa_chat_bridge_advertises_mcp_tools_to_llama() -> None:
    """Description:
    Verify the browser-chat LLM prompt includes the PA MCP tool manifest.

    Requirements:
    - This test is needed because standalone MCP servers are useless to
      non-native models unless the PA advertises the callable tool protocol.
    - Verify the system prompt tells the model about filesystem tool calls.
    """

    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=FakeRedis(),
        llm_client=SequencedFakeLLMClient(["No tool needed."]),
        model_name="ollama/llama3:8b",
        tool_executor=FakeToolExecutor(),
    )

    messages = runtime._build_chat_messages("Can you read README.md?")
    system_text = messages[0]["content"]

    assert "Available MCP tools" in system_text
    assert '"type": "tool_call"' in system_text
    assert "filesystem" in system_text
    assert "read" in system_text


def test_pa_chat_bridge_manifest_defines_faith_mcp_inventory() -> None:
    """Description:
    Verify the Project Agent tool manifest grounds the meaning of FAITH MCP.

    Requirements:
    - This test is needed to prevent local models from treating MCP as
      Microsoft Configuration Manager.
    - Verify the prompt exposes a canonical tool-inventory action and the
      Model Context Protocol definition.
    """

    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=FakeRedis(),
        llm_client=SequencedFakeLLMClient(["No tool needed."]),
        model_name="ollama/llama3:8b",
        tool_executor=FakeToolExecutor(),
    )

    system_text = runtime._build_chat_messages("What MCP servers are available?")[0]["content"]

    assert "Model Context Protocol" in system_text
    assert "Microsoft Configuration Manager" in system_text
    assert "mcp.list_tools" in system_text
    assert "filesystem.read" in system_text
    assert "python.execute_python" in system_text


def test_pa_chat_bridge_answers_mcp_inventory_questions_from_canonical_inventory() -> None:
    """Description:
    Verify MCP inventory questions are answered from the canonical inventory.

    Requirements:
    - This test is needed to prove available-tool answers do not depend on LLM improvisation.
    - Verify the PA answers directly from the canonical inventory and includes
      the Python MCP actions when they are available.
    """

    llm_client = SequencedFakeLLMClient(
        [
            "This response should never be used.",
        ]
    )
    fake_redis = FakeRedis()
    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=fake_redis,
        llm_client=llm_client,
        model_name="ollama/llama3:8b",
        tool_executor=FakeToolExecutor(),
    )

    asyncio.run(
        runtime._handle_payload(
            {
                "type": "user_input",
                "message_id": "msg-inventory-001",
                "message": "what MCP servers are available to FAITH?",
            }
        )
    )

    published = [
        json.loads(payload)
        for channel, payload in fake_redis.published
        if channel == "agent:project-agent:output"
    ]
    output_text = "".join(
        item.get("text", "") for item in published if item.get("type") == "output"
    )

    assert len(llm_client.calls) == 0
    assert "Model Context Protocol" in output_text
    assert "Microsoft Configuration Manager" not in output_text
    assert "MCP Server 1" not in output_text
    assert "mcp.list_tools" in output_text
    assert "filesystem.read" in output_text
    assert "python.execute_python" in output_text


def test_pa_chat_executor_lists_python_mcp_actions(tmp_path: Path) -> None:
    """Description:
    Verify the PA chat executor includes Python actions in the canonical inventory.

    Requirements:
    - This test is needed to prove `mcp.list_tools` reflects all currently
      chat-available MCP tools rather than only the filesystem subset.
    - Verify the returned tool list includes Python execution actions.

    :param tmp_path: Temporary project root used to build the executor safely.
    """

    executor = pa_app_module.ProjectAgentMCPToolExecutor(root=tmp_path)

    result = asyncio.run(executor.execute(ChatToolCall(tool="mcp", action="list_tools", args={})))

    assert result["success"] is True
    tools = result["result"]["tools"]
    tool_names = {tool["name"] for tool in tools}
    assert "mcp.list_tools" in tool_names
    assert "filesystem.read" in tool_names
    assert "python.execute_python" in tool_names
    assert "python.pip_install" in tool_names


def test_pa_chat_bridge_executes_mcp_tool_call_and_returns_final_answer() -> None:
    """Description:
    Verify the browser-chat bridge runs a model-requested MCP tool call.

    Requirements:
    - This test is needed to prove llama-style text output can drive the
      filesystem MCP server through the PA instead of only returning raw JSON.
    - Verify the PA executes the parsed tool request, sends the result back to
      the model, and streams the final answer to the Project Agent panel.
    """

    first_response = (
        '{"type": "tool_call", "tool": "filesystem", "action": "read", '
        '"args": {"mount": "project", "path": "README.md"}}'
    )
    llm_client = SequencedFakeLLMClient(
        [
            first_response,
            "The README says FAITH is local-first.",
        ]
    )
    tool_executor = FakeToolExecutor()
    fake_redis = FakeRedis()
    runtime = pa_app_module.ProjectAgentChatRuntime(
        redis_client=fake_redis,
        llm_client=llm_client,
        model_name="ollama/llama3:8b",
        tool_executor=tool_executor,
    )

    asyncio.run(
        runtime._handle_payload(
            {
                "type": "user_input",
                "message_id": "msg-tool-001",
                "message": "Please read README.md and summarise it.",
            }
        )
    )

    published = [
        json.loads(payload)
        for channel, payload in fake_redis.published
        if channel == "agent:project-agent:output"
    ]
    assert len(llm_client.calls) == 2
    assert len(tool_executor.calls) == 1
    assert tool_executor.calls[0].tool == "filesystem"
    assert tool_executor.calls[0].action == "read"
    assert tool_executor.calls[0].args == {"mount": "project", "path": "README.md"}
    second_call_messages = llm_client.calls[1]["messages"]
    assert any("Tool result" in message["content"] for message in second_call_messages)
    output_text = "".join(
        item.get("text", "") for item in published if item.get("type") == "output"
    )
    assert "The README says FAITH is local-first." in output_text
