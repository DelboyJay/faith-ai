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

import pytest
from fastapi.testclient import TestClient

import faith_pa.pa.app as pa_app_module
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
