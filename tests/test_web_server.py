"""Description:
    Cover the FAITH Web UI HTTP and WebSocket surfaces.

Requirements:
    - Verify the implemented browser endpoints behave correctly on both healthy and degraded paths.
    - Verify Redis-backed WebSocket relays forward the expected payloads.
"""

from __future__ import annotations

import asyncio
import base64
import json
import warnings

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import faith_web.app as web_app
from faith_pa.utils.redis_client import SYSTEM_EVENTS_CHANNEL, USER_INPUT_CHANNEL
from faith_web.app import APPROVAL_EVENTS_CHANNEL, APPROVAL_RESPONSES_CHANNEL, create_app


class FakePubSub:
    """Description:
        Provide a minimal async Redis pub/sub stand-in for Web UI tests.

    Requirements:
        - Record subscriptions and unsubscriptions for assertion.
        - Allow tests to inject synthetic Redis messages into WebSocket bridges.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake pub/sub object.

        Requirements:
            - Start with empty subscription and message buffers.
        """

        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.messages: list[dict[str, object]] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        """Description:
            Record one subscription request.

        Requirements:
            - Preserve the subscribed channel name for later assertions.

        :param channel: Redis channel name to subscribe to.
        """

        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str | None = None) -> None:
        """Description:
            Record one unsubscribe request.

        Requirements:
            - Preserve the unsubscribed channel name when one is provided.

        :param channel: Redis channel name to unsubscribe from.
        """

        if channel is not None:
            self.unsubscribed.append(channel)

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
        """Description:
            Return one queued fake Redis message when available.

        Requirements:
            - Sleep briefly and return ``None`` when no message is queued.
            - Accept the same arguments as the real Redis pub/sub API used by the bridge.

        :param ignore_subscribe_messages: Unused compatibility argument.
        :param timeout: Unused compatibility timeout argument.
        :returns: Next queued message payload or ``None`` when no message is available.
        """

        del ignore_subscribe_messages, timeout
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0.01)
        return None

    async def close(self) -> None:
        """Description:
            Mark the fake pub/sub instance as closed.

        Requirements:
            - Support the cleanup path used by the WebSocket bridge.
        """

        self.closed = True

    def inject_message(self, channel: str, data: str) -> None:
        """Description:
            Queue one synthetic Redis message for the WebSocket bridge.

        Requirements:
            - Preserve channel and payload values so tests can simulate real Redis deliveries.

        :param channel: Redis channel name attached to the message.
        :param data: JSON or text payload delivered by the fake Redis feed.
        """

        self.messages.append(
            {
                "type": "message",
                "channel": channel,
                "data": data,
            }
        )


class FakeRedis:
    """Description:
        Provide a minimal async Redis client stand-in for Web UI tests.

    Requirements:
        - Record published messages for endpoint assertions.
        - Provide a fake pub/sub object for WebSocket relay tests.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake Redis client.

        Requirements:
            - Start with an empty published-message buffer.
            - Attach one reusable fake pub/sub instance.
        """

        self.published: list[tuple[str, str]] = []
        self.pubsub_instance = FakePubSub()

    async def publish(self, channel: str, message: str) -> None:
        """Description:
            Record one published Redis message.

        Requirements:
            - Preserve channel and payload values for later assertions.

        :param channel: Redis channel name.
        :param message: Published payload string.
        """

        self.published.append((channel, message))

    async def ping(self) -> bool:
        """Description:
            Simulate a successful Redis ping.

        Requirements:
            - Always report healthy for the default fake Redis fixture.

        :returns: ``True``.
        """

        return True

    def pubsub(self) -> FakePubSub:
        """Description:
            Return the fake pub/sub object used by WebSocket bridge tests.

        Requirements:
            - Reuse the same fake pub/sub instance for the lifetime of the fake client.

        :returns: Fake pub/sub object.
        """

        return self.pubsub_instance

    async def aclose(self) -> None:
        """Description:
            Provide the async close hook expected by the app lifespan.

        Requirements:
            - Be safely callable without side effects.
        """

        return None


@pytest.fixture
def fake_redis() -> FakeRedis:
    """Description:
        Provide a healthy fake Redis client for Web UI tests.

    Requirements:
        - Reuse the same fake client across one test so publish assertions are stable.

    :returns: Healthy fake Redis client.
    """

    return FakeRedis()


@pytest.fixture
def app(fake_redis: FakeRedis):
    """Description:
        Create the Web UI app with the shared fake Redis client installed.

    Requirements:
        - Replace the module-level Redis pool for the duration of each test.
        - Restore the original Redis pool afterwards.

    :param fake_redis: Healthy fake Redis client.
    :yields: Test-configured Web UI FastAPI application.
    """

    original = web_app.redis_pool
    web_app.redis_pool = fake_redis
    application = create_app(testing=True)
    yield application
    web_app.redis_pool = original


@pytest.fixture
def client(app):
    """Description:
        Provide a synchronous test client for the Web UI app.

    Requirements:
        - Use the test-configured application fixture.

    :param app: Test-configured Web UI application.
    :yields: FastAPI synchronous test client.
    """

    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def async_client(app):
    """Description:
        Provide an async HTTP client for the Web UI app.

    Requirements:
        - Use ASGI transport so tests exercise the application in-process.

    :param app: Test-configured Web UI application.
    :yields: Async HTTP client bound to the Web UI app.
    """

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def test_index_returns_html(client: TestClient) -> None:
    """Description:
        Verify the index route returns the main HTML page.

    Requirements:
        - This test is needed to prove the root page renders successfully on the happy path.
        - Verify the response contains the expected FAITH Web UI marker text.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/")
    assert response.status_code == 200
    assert "FAITH Web UI" in response.text


def test_index_route_uses_non_deprecated_template_signature(client: TestClient) -> None:
    """Description:
        Verify the index route renders without relying on the deprecated Starlette template response signature.

    Requirements:
        - This test is needed to prevent the root page from working locally while failing in newer container environments.
        - Verify the index route does not emit the known ``TemplateResponse`` deprecation warning during rendering.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        response = client.get("/")

    assert response.status_code == 200
    assert not any(
        isinstance(warning.message, DeprecationWarning)
        and "TemplateResponse" in str(warning.message)
        for warning in captured
    )


def test_health_returns_ok(client: TestClient) -> None:
    """Description:
        Verify the health endpoint returns a successful payload on the happy path.

    Requirements:
        - This test is needed to prove the Web UI health route does not return HTTP 500 under normal conditions.
        - Verify the payload identifies the Web UI service.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "faith-web-ui"


def test_static_assets_are_served(client: TestClient) -> None:
    """Description:
        Verify the bundled static CSS and JavaScript assets are served successfully.

    Requirements:
        - This test is needed to prove the browser can load its static UI assets without server errors.
        - Verify representative CSS and JavaScript content is present.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    css_response = client.get("/static/css/theme.css")
    js_response = client.get("/static/js/app.js")
    assert css_response.status_code == 200
    assert "--bg" in css_response.text
    assert js_response.status_code == 200
    assert "refreshStatus" in js_response.text


@pytest.mark.asyncio
async def test_submit_input_publishes_message(
    async_client: AsyncClient, fake_redis: FakeRedis
) -> None:
    """Description:
        Verify the input endpoint publishes one user message to Redis.

    Requirements:
        - This test is needed to prove browser text input is forwarded into the PA input channel.
        - Verify the published payload preserves message text and session ID.

    :param async_client: Async HTTP client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    response = await async_client.post("/input", json={"message": "hello", "session_id": "sess-1"})
    assert response.status_code == 200
    channel, payload_text = fake_redis.published[0]
    payload = json.loads(payload_text)
    assert channel == USER_INPUT_CHANNEL
    assert payload["type"] == "user_input"
    assert payload["message"] == "hello"
    assert payload["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_input_returns_503_when_redis_missing(app) -> None:
    """Description:
        Verify the input endpoint returns HTTP 503 when Redis is unavailable.

    Requirements:
        - This test is needed to prove degraded dependencies do not surface as internal server errors.
        - Verify the endpoint returns the expected service-unavailable status.

    :param app: Test-configured Web UI application.
    """

    original = web_app.redis_pool
    web_app.redis_pool = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            response = await async_client.post("/input", json={"message": "hello"})
        assert response.status_code == 503
    finally:
        web_app.redis_pool = original


@pytest.mark.asyncio
async def test_upload_file_publishes_encoded_content(
    async_client: AsyncClient, fake_redis: FakeRedis
) -> None:
    """Description:
        Verify the upload endpoint publishes accepted file content in base64 form.

    Requirements:
        - This test is needed to prove browser uploads are forwarded into the PA input channel.
        - Verify the published payload preserves the uploaded file content.

    :param async_client: Async HTTP client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    response = await async_client.post(
        "/upload",
        files={"file": ("note.txt", b"hello world", "text/plain")},
        data={"message": "review", "session_id": "sess-2"},
    )
    assert response.status_code == 200
    channel, payload_text = fake_redis.published[0]
    payload = json.loads(payload_text)
    assert channel == USER_INPUT_CHANNEL
    assert payload["type"] == "user_upload"
    assert base64.b64decode(payload["content_base64"]) == b"hello world"


@pytest.mark.asyncio
async def test_upload_rejects_invalid_type(async_client: AsyncClient) -> None:
    """Description:
        Verify the upload endpoint rejects unsupported file types.

    Requirements:
        - This test is needed to prove invalid file types return HTTP 415 instead of being accepted or crashing.
        - Verify an executable MIME type is rejected.

    :param async_client: Async HTTP client bound to the FAITH web app.
    """

    response = await async_client.post(
        "/upload",
        files={"file": ("bad.exe", b"123", "application/x-msdownload")},
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(async_client: AsyncClient) -> None:
    """Description:
        Verify the upload endpoint rejects files larger than the configured limit.

    Requirements:
        - This test is needed to prove oversized uploads return HTTP 413.
        - Verify a file larger than the maximum upload size is rejected.

    :param async_client: Async HTTP client bound to the FAITH web app.
    """

    response = await async_client.post(
        "/upload",
        files={"file": ("big.txt", b"x" * (11 * 1024 * 1024), "text/plain")},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_approval_endpoint_publishes_decision(
    async_client: AsyncClient, fake_redis: FakeRedis
) -> None:
    """Description:
        Verify the approval endpoint publishes accepted approval decisions to Redis.

    Requirements:
        - This test is needed to prove browser approval actions are forwarded to the PA.
        - Verify the published payload preserves the request ID and decision.

    :param async_client: Async HTTP client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    response = await async_client.post(
        "/approve/apr-1",
        json={"decision": "approve_session", "scope": "folder", "reason": "allowed"},
    )
    assert response.status_code == 200
    channel, payload_text = fake_redis.published[0]
    payload = json.loads(payload_text)
    assert channel == APPROVAL_RESPONSES_CHANNEL
    assert payload["decision"] == "approve_session"
    assert payload["request_id"] == "apr-1"


@pytest.mark.asyncio
async def test_approval_endpoint_validates_decision(async_client: AsyncClient) -> None:
    """Description:
        Verify the approval endpoint rejects invalid decision values.

    Requirements:
        - This test is needed to prove malformed approval payloads return HTTP 422.
        - Verify an unsupported decision value is rejected.

    :param async_client: Async HTTP client bound to the FAITH web app.
    """

    response = await async_client.post(
        "/approve/apr-2",
        json={"decision": "approve", "scope": "once"},
    )
    assert response.status_code == 422


def test_agent_websocket_relays_messages(client: TestClient, fake_redis: FakeRedis) -> None:
    """Description:
        Verify the agent WebSocket relays Redis messages to the browser.

    Requirements:
        - This test is needed to prove agent output reaches the Web UI.
        - Verify the bridge subscribes to and unsubscribes from the expected Redis channel.

    :param client: FastAPI test client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    with client.websocket_connect("/ws/agent/dev") as websocket:
        fake_redis.pubsub_instance.inject_message(
            "agent:dev:output", json.dumps({"agent": "dev", "text": "hello"})
        )
        payload = json.loads(websocket.receive_text())
        assert payload["agent"] == "dev"
    assert "agent:dev:output" in fake_redis.pubsub_instance.subscribed
    assert "agent:dev:output" in fake_redis.pubsub_instance.unsubscribed


def test_tool_websocket_relays_messages(client: TestClient, fake_redis: FakeRedis) -> None:
    """Description:
        Verify the tool WebSocket relays Redis messages to the browser.

    Requirements:
        - This test is needed to prove tool output reaches the Web UI.
        - Verify the relayed payload matches the injected tool event.

    :param client: FastAPI test client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    with client.websocket_connect("/ws/tool/filesystem") as websocket:
        fake_redis.pubsub_instance.inject_message(
            "tool:filesystem:output", json.dumps({"tool": "filesystem", "action": "read"})
        )
        payload = json.loads(websocket.receive_text())
        assert payload["tool"] == "filesystem"


def test_approval_websocket_relays_messages(client: TestClient, fake_redis: FakeRedis) -> None:
    """Description:
        Verify the approval WebSocket relays approval events to the browser.

    Requirements:
        - This test is needed to prove approval requests reach the approval panel.
        - Verify the relayed payload matches the injected approval event.

    :param client: FastAPI test client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    with client.websocket_connect("/ws/approvals") as websocket:
        fake_redis.pubsub_instance.inject_message(
            APPROVAL_EVENTS_CHANNEL, json.dumps({"request_id": "apr-3"})
        )
        payload = json.loads(websocket.receive_text())
        assert payload["request_id"] == "apr-3"


def test_status_websocket_relays_messages(client: TestClient, fake_redis: FakeRedis) -> None:
    """Description:
        Verify the status WebSocket relays system events to the browser.

    Requirements:
        - This test is needed to prove shared status events reach the status panel.
        - Verify the relayed payload matches the injected system event.

    :param client: FastAPI test client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    with client.websocket_connect("/ws/status") as websocket:
        fake_redis.pubsub_instance.inject_message(
            SYSTEM_EVENTS_CHANNEL, json.dumps({"event": "agent:heartbeat"})
        )
        payload = json.loads(websocket.receive_text())
        assert payload["event"] == "agent:heartbeat"


def test_api_status_returns_ok(client: TestClient) -> None:
    """Description:
        Verify the web status endpoint returns a successful status payload.

    Requirements:
        - This test is needed to prove the explicit ``/api/status`` route responds successfully over HTTP.
        - Verify the route does not rely on ``/health`` tests alone for coverage.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["service"] == "faith-web-ui"


def test_health_returns_503_when_redis_missing(app) -> None:
    """Description:
        Verify the web health endpoint returns HTTP 503 when Redis is unavailable.

    Requirements:
        - This test is needed to prove degraded dependencies do not surface as internal server errors.
        - Verify the health endpoint returns the expected degraded status code.

    :param app: Test-configured Web UI application.
    """

    original = web_app.redis_pool
    web_app.redis_pool = None
    try:
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 503
    finally:
        web_app.redis_pool = original


def test_api_status_returns_503_when_redis_missing(app) -> None:
    """Description:
        Verify the web status endpoint returns HTTP 503 when Redis is unavailable.

    Requirements:
        - This test is needed to prove the explicit ``/api/status`` route reports degraded dependencies correctly.
        - Verify the route returns the expected degraded status code.

    :param app: Test-configured Web UI application.
    """

    original = web_app.redis_pool
    web_app.redis_pool = None
    try:
        with TestClient(app) as client:
            response = client.get("/api/status")
        assert response.status_code == 503
    finally:
        web_app.redis_pool = original


@pytest.mark.asyncio
async def test_input_validates_required_message(async_client: AsyncClient) -> None:
    """Description:
        Verify the input endpoint returns validation errors for invalid payloads.

    Requirements:
        - This test is needed to prove invalid input payloads return HTTP 422 instead of a server error.
        - Verify an empty message is rejected.

    :param async_client: Async HTTP client bound to the FAITH web app.
    """

    response = await async_client.post("/input", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_requires_file(async_client: AsyncClient) -> None:
    """Description:
        Verify the upload endpoint validates that a file is provided.

    Requirements:
        - This test is needed to prove missing multipart file uploads return HTTP 422.
        - Verify the endpoint rejects requests that omit the required file field.

    :param async_client: Async HTTP client bound to the FAITH web app.
    """

    response = await async_client.post("/upload", data={"message": "review"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_returns_503_when_redis_missing(app) -> None:
    """Description:
        Verify the upload endpoint returns HTTP 503 when Redis is unavailable.

    Requirements:
        - This test is needed to prove upload requests fail cleanly when Redis is unavailable.
        - Verify the endpoint returns the expected service-unavailable status code.

    :param app: Test-configured Web UI application.
    """

    original = web_app.redis_pool
    web_app.redis_pool = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            response = await async_client.post(
                "/upload",
                files={"file": ("note.txt", b"hello", "text/plain")},
            )
        assert response.status_code == 503
    finally:
        web_app.redis_pool = original


@pytest.mark.asyncio
async def test_approval_endpoint_returns_503_when_redis_missing(app) -> None:
    """Description:
        Verify the approval endpoint returns HTTP 503 when Redis is unavailable.

    Requirements:
        - This test is needed to prove approval decisions fail cleanly when Redis is unavailable.
        - Verify the endpoint returns the expected service-unavailable status code.

    :param app: Test-configured Web UI application.
    """

    original = web_app.redis_pool
    web_app.redis_pool = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            response = await async_client.post(
                "/approve/apr-503",
                json={"decision": "allow_once", "scope": "file"},
            )
        assert response.status_code == 503
    finally:
        web_app.redis_pool = original


def test_api_routes_returns_manifest(client: TestClient) -> None:
    """Description:
        Verify the Web UI route-discovery endpoint returns the structured route manifest.

    Requirements:
        - This test is needed to prove the CLI can discover Web UI endpoints without hard-coding them.
        - Verify the manifest includes both HTTP and WebSocket routes with expected metadata.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/api/routes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "faith-web-ui"
    assert any(route["path"] == "/api/routes" for route in payload["routes"])
    assert any(route["path"] == "/ws/status" for route in payload["routes"])
