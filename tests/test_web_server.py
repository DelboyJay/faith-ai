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
from pathlib import Path

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


def test_index_bootstraps_goldenlayout_shell(client: TestClient) -> None:
    """Description:
        Verify the main HTML shell includes the GoldenLayout mount points and asset bootstrapping.

    Requirements:
        - This test is needed to prove the browser receives the panel-framework shell required by FAITH-037.
        - Verify the index page includes the toolbar, layout container, and GoldenLayout/layout asset references.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/")
    assert response.status_code == 200
    assert 'id="faith-toolbar"' in response.text
    assert 'id="faith-layout"' in response.text
    assert "goldenlayout" in response.text.lower()
    assert "vue" in response.text.lower()
    assert "xterm" in response.text.lower()
    assert "/static/js/layout.js" in response.text


def test_index_cache_busts_local_static_assets(client: TestClient) -> None:
    """Description:
        Verify the browser shell cache-busts local static assets.

    Requirements:
        - This test is needed to prove frontend fixes are loaded after container rebuilds.
        - Verify local CSS and JavaScript asset URLs include the generated asset version query string.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/")

    assert response.status_code == 200
    assert "/static/css/theme.css?v=" in response.text
    assert "/static/js/panels/agent-panel.js?v=" in response.text
    assert "/static/js/layout.js?v=" in response.text
    assert "/static/js/app.js?v=" in response.text


def test_layout_asset_is_served(client: TestClient) -> None:
    """Description:
        Verify the dedicated GoldenLayout asset is served as a static file.

    Requirements:
        - This test is needed to prove the browser can load the panel framework JavaScript without a server error.
        - Verify the script exposes the expected FAITH layout API surface.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/static/js/layout.js")
    assert response.status_code == 200
    assert "faithLayout" in response.text
    assert "faith_layout_v1" in response.text


def test_layout_asset_uses_minimal_first_load_defaults(client: TestClient) -> None:
    """Description:
        Verify the GoldenLayout asset defines the minimal first-load workspace.

    Requirements:
        - This test is needed to prove fresh browser loads do not assume a software-team workflow.
        - Verify the default layout includes Project Agent, Input, Approvals, and System Status.
        - Verify the default layout does not pre-create Software Developer or QA Engineer panels.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/static/js/layout.js")
    assert response.status_code == 200
    assert "Project Agent" in response.text
    assert 'title: "Input"' in response.text
    assert 'title: "Approvals"' in response.text
    assert 'title: "System Status"' in response.text
    assert "Software Developer" not in response.text
    assert "QA Engineer" not in response.text


def test_layout_asset_keeps_saved_layout_and_dynamic_agent_helpers(client: TestClient) -> None:
    """Description:
        Verify the layout asset still supports saved layouts and later agent panel creation.

    Requirements:
        - This test is needed to prove FAITH-060 does not break FAITH-037 persistence semantics.
        - Verify the canonical localStorage key remains in use.
        - Verify the layout API still exposes the dynamic agent-panel helper for later specialist agents.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/static/js/layout.js")
    assert response.status_code == 200
    assert "faith_layout_v1" in response.text
    assert "layout.loadLayout(loadSavedLayout() || buildDefaultLayoutConfig());" in response.text
    assert "addAgentPanel" in response.text


def test_layout_asset_exposes_panel_lifecycle_helpers(client: TestClient) -> None:
    """Description:
        Verify the layout asset exposes the helper surface needed for panel lifecycle handling.

    Requirements:
        - This test is needed to prove close/reopen and dedupe behaviour is implemented in the shared layout runtime.
        - Verify the asset exposes helpers for duplicate detection, existing-panel focus, and panel removal by identity.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/static/js/layout.js")
    assert response.status_code == 200
    assert "hasExistingPanel" in response.text
    assert "focusExistingPanel" in response.text
    assert "removePanelByIdentity" in response.text


def test_layout_asset_uses_title_bar_as_primary_panel_label(client: TestClient) -> None:
    """Description:
        Verify the panel body no longer duplicates the panel title already shown by the title bar.

    Requirements:
        - This test is needed to prove FAITH-064 removes wasted duplicated panel-name chrome.
        - Verify the placeholder panel body does not render a second heading label inside the panel content.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/static/js/layout.js")
    assert response.status_code == 200
    assert "faith-panel__title" not in response.text
    assert "heading.textContent = title" not in response.text


def test_vendor_goldenlayout_asset_is_served(client: TestClient) -> None:
    """Description:
        Verify the vendored GoldenLayout fallback asset is served successfully.

    Requirements:
        - This test is needed to prove the browser can load the local fallback when CDN access is unavailable.
        - Verify the fallback script exposes the expected GoldenLayout global.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/static/js/vendor/goldenlayout.umd.js")
    assert response.status_code == 200
    assert "goldenLayout" in response.text
    assert "GoldenLayout" in response.text


def test_vendor_goldenlayout_asset_supports_panel_close_action(client: TestClient) -> None:
    """Description:
        Verify the vendored GoldenLayout fallback includes the panel close action hook.

    Requirements:
        - This test is needed to prove the local fallback can remove panels through the UI instead of leaving close/reopen unimplemented.
        - Verify the fallback script references the FAITH panel removal helper and close button styling hook.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/static/js/vendor/goldenlayout.umd.js")
    assert response.status_code == 200
    assert "removePanelByIdentity" in response.text
    assert "faith-panel__close" in response.text


def test_title_bar_close_affordance_is_styled(client: TestClient) -> None:
    """Description:
        Verify the shared stylesheet includes the title-bar close affordance styles.

    Requirements:
        - This test is needed to prove the close action remains a visible, intentional part of the panel title bar.
        - Verify the stylesheet includes the close-control selectors used by the panel chrome.

    :param client: FastAPI test client bound to the FAITH web app.
    """

    response = client.get("/static/css/theme.css")
    assert response.status_code == 200
    assert ".faith-panel__close" in response.text
    assert ".faith-panel__fallback-header" in response.text


def test_layout_support_files_exist() -> None:
    """Description:
        Verify the FAITH-037 support files exist in the repository.

    Requirements:
        - This test is needed to prove the offline-vendor guidance and manual layout harness ship with the task.
        - Verify the vendored fallback README and the manual layout test harness are present.
    """

    project_root = Path(__file__).resolve().parents[1]
    vendor_readme = project_root / "web" / "js" / "vendor" / "README.md"
    vendor_script = project_root / "web" / "js" / "vendor" / "goldenlayout.umd.js"
    vendor_base_css = project_root / "web" / "js" / "vendor" / "goldenlayout-base.css"
    vendor_theme_css = project_root / "web" / "js" / "vendor" / "goldenlayout-dark-theme.css"
    layout_harness = project_root / "tests" / "test_layout.html"
    assert vendor_readme.exists()
    assert vendor_script.exists()
    assert vendor_base_css.exists()
    assert vendor_theme_css.exists()
    assert layout_harness.exists()


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


def test_agent_websocket_relays_parseable_message_frames(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    """Description:
        Verify the agent WebSocket relays parseable JSON frames to the browser.

    Requirements:
        - This test is needed to prove the agent panel can rely on the backend WebSocket feed for structured messages.
        - Verify output, protocol, status, and error payloads survive the relay unchanged.

    :param client: FastAPI test client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    messages = [
        {"type": "output", "text": "Hello from agent"},
        {"type": "protocol", "text": "compact:task:update"},
        {"type": "status", "status": "running", "model": "ollama/llama3:8b"},
        {"type": "error", "message": "connection lost"},
    ]

    with client.websocket_connect("/ws/agent/project-agent") as websocket:
        for message in messages:
            fake_redis.pubsub_instance.inject_message(
                "agent:project-agent:output",
                json.dumps(message),
            )
            payload = json.loads(websocket.receive_text())
            assert payload == message


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


def test_docker_runtime_websocket_streams_snapshot(app) -> None:
    """Description:
        Verify the Web UI Docker runtime WebSocket streams runtime snapshots.

    Requirements:
        - This test is needed to prove the dedicated Docker runtime panel can consume the event-driven feed.
        - Verify one streamed payload includes the expected bootstrap container role.

    :param app: Test-configured Web UI application.
    """

    async def _fake_runtime_fetcher():
        """Description:
            Return one deterministic runtime snapshot for the Docker WebSocket test.

        Requirements:
            - Provide one bootstrap container record suitable for browser assertions.
        """

        return {
            "docker_available": True,
            "status": "ok",
            "images": ["faith-web-ui:latest"],
            "containers": [
                {
                    "name": "faith-web-ui",
                    "category": "bootstrap",
                    "role": "Web UI",
                    "state": "running",
                    "image": "faith-web-ui:latest",
                    "health": "healthy",
                    "restart_count": 0,
                    "url": "http://localhost:8080",
                    "ownership": {},
                }
            ],
        }

    app.state.pa_runtime_fetcher = _fake_runtime_fetcher

    with TestClient(app) as client:
        with client.websocket_connect("/ws/docker") as websocket:
            payload = websocket.receive_json()

    assert payload["docker_available"] is True
    assert payload["images"] == ["faith-web-ui:latest"]
    assert payload["containers"][0]["role"] == "Web UI"


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


def test_api_docker_runtime_returns_pa_snapshot(app) -> None:
    """Description:
        Verify the Web UI Docker runtime endpoint returns the PA runtime snapshot payload.

    Requirements:
        - This test is needed to prove the Web UI exposes the dedicated Docker runtime feed over HTTP.
        - Verify the route returns the configured runtime snapshot rather than raw JSON text.

    :param app: Test-configured Web UI application.
    """

    async def _fake_runtime_fetcher():
        """Description:
            Return one deterministic Docker runtime snapshot for the request-style test.

        Requirements:
            - Provide a bootstrap container entry suitable for Web UI assertions.
        """

        return {
            "docker_available": True,
            "status": "ok",
            "images": ["faith-pa:latest"],
            "containers": [
                {
                    "name": "faith-pa",
                    "category": "bootstrap",
                    "role": "Project Agent",
                    "state": "running",
                    "image": "faith-pa:latest",
                    "health": "healthy",
                    "restart_count": 0,
                    "url": "http://localhost:8000",
                    "ownership": {},
                }
            ],
        }

    app.state.pa_runtime_fetcher = _fake_runtime_fetcher

    with TestClient(app) as client:
        response = client.get("/api/docker-runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["docker_available"] is True
    assert payload["images"] == ["faith-pa:latest"]
    assert payload["containers"][0]["role"] == "Project Agent"


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
    assert any(route["path"] == "/api/docker-runtime" for route in payload["routes"])
    assert any(route["path"] == "/ws/docker" for route in payload["routes"])
