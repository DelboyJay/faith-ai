from __future__ import annotations

import asyncio
import base64
import json
import warnings

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import faith.web.app as web_app
from faith.utils.redis_client import SYSTEM_EVENTS_CHANNEL, USER_INPUT_CHANNEL
from faith.web.app import APPROVAL_EVENTS_CHANNEL, APPROVAL_RESPONSES_CHANNEL, create_app


class FakePubSub:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.messages: list[dict[str, object]] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str | None = None) -> None:
        if channel is not None:
            self.unsubscribed.append(channel)

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0.01)
        return None

    async def close(self) -> None:
        self.closed = True

    def inject_message(self, channel: str, data: str) -> None:
        self.messages.append(
            {
                "type": "message",
                "channel": channel,
                "data": data,
            }
        )


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.pubsub_instance = FakePubSub()

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    async def ping(self) -> bool:
        return True

    def pubsub(self) -> FakePubSub:
        return self.pubsub_instance

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(fake_redis: FakeRedis):
    original = web_app.redis_pool
    web_app.redis_pool = fake_redis
    application = create_app(testing=True)
    yield application
    web_app.redis_pool = original


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def async_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def test_index_returns_html(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "FAITH Web UI" in response.text


def test_index_route_uses_non_deprecated_template_signature(client: TestClient) -> None:
    """Description:
    Verify the index route renders without relying on the deprecated
    Starlette template response call signature.

    Requirements:
    - This test is needed to prevent the root page from working locally
      while failing in newer container environments.
    - Verify the index route does not emit the known TemplateResponse
      deprecation warning during rendering.

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
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "faith-web-ui"


def test_static_assets_are_served(client: TestClient) -> None:
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
    response = await async_client.post(
        "/upload",
        files={"file": ("bad.exe", b"123", "application/x-msdownload")},
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(async_client: AsyncClient) -> None:
    response = await async_client.post(
        "/upload",
        files={"file": ("big.txt", b"x" * (11 * 1024 * 1024), "text/plain")},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_approval_endpoint_publishes_decision(
    async_client: AsyncClient, fake_redis: FakeRedis
) -> None:
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
    response = await async_client.post(
        "/approve/apr-2",
        json={"decision": "approve", "scope": "once"},
    )
    assert response.status_code == 422


def test_agent_websocket_relays_messages(client: TestClient, fake_redis: FakeRedis) -> None:
    with client.websocket_connect("/ws/agent/dev") as websocket:
        fake_redis.pubsub_instance.inject_message(
            "agent:dev:output", json.dumps({"agent": "dev", "text": "hello"})
        )
        payload = json.loads(websocket.receive_text())
        assert payload["agent"] == "dev"
    assert "agent:dev:output" in fake_redis.pubsub_instance.subscribed
    assert "agent:dev:output" in fake_redis.pubsub_instance.unsubscribed


def test_tool_websocket_relays_messages(client: TestClient, fake_redis: FakeRedis) -> None:
    with client.websocket_connect("/ws/tool/filesystem") as websocket:
        fake_redis.pubsub_instance.inject_message(
            "tool:filesystem:output", json.dumps({"tool": "filesystem", "action": "read"})
        )
        payload = json.loads(websocket.receive_text())
        assert payload["tool"] == "filesystem"


def test_approval_websocket_relays_messages(client: TestClient, fake_redis: FakeRedis) -> None:
    with client.websocket_connect("/ws/approvals") as websocket:
        fake_redis.pubsub_instance.inject_message(
            APPROVAL_EVENTS_CHANNEL, json.dumps({"request_id": "apr-3"})
        )
        payload = json.loads(websocket.receive_text())
        assert payload["request_id"] == "apr-3"


def test_status_websocket_relays_messages(client: TestClient, fake_redis: FakeRedis) -> None:
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
    - Prove the explicit `/api/status` route responds successfully over HTTP.
    - Verify the route does not rely on `/health` tests alone for coverage.

    :param client: FastAPI test client bound to the FAITH web app.
    """
    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["service"] == "faith-web-ui"


def test_health_returns_503_when_redis_missing(app) -> None:
    """Description:
    Verify the web health endpoint returns 503 when Redis is unavailable.

    Requirements:
    - This test is needed to prove degraded dependencies do not surface as
      internal server errors.
    - Verify the health endpoint returns HTTP 503 under that condition.

    :param app: FastAPI application under test.
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
    Verify the web status endpoint returns 503 when Redis is unavailable.

    Requirements:
    - This test is needed to prove the explicit `/api/status` route reports
      degraded dependencies correctly.
    - Verify the route returns HTTP 503 under that condition.

    :param app: FastAPI application under test.
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
    - This test is needed to prove invalid input payloads return the expected
      HTTP 422 response instead of a server error.
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
    - This test is needed to prove missing multipart file uploads return the
      expected HTTP 422 response.
    - Verify the endpoint rejects requests that omit the required file field.

    :param async_client: Async HTTP client bound to the FAITH web app.
    """
    response = await async_client.post("/upload", data={"message": "review"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_upload_returns_503_when_redis_missing(app) -> None:
    """Description:
    Verify the upload endpoint returns 503 when Redis is unavailable.

    Requirements:
    - This test is needed to prove upload requests fail cleanly when Redis is
      unavailable.
    - Verify the endpoint returns the expected service-unavailable status code.

    :param app: FastAPI application under test.
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
    Verify the approval endpoint returns 503 when Redis is unavailable.

    Requirements:
    - This test is needed to prove approval decisions fail cleanly when Redis
      is unavailable.
    - Verify the endpoint returns the expected service-unavailable status code.

    :param app: FastAPI application under test.
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
