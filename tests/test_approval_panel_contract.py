"""Description:
    Verify the FAITH approval panel browser and backend contract.

Requirements:
    - Prove the browser shell loads the dedicated approval panel asset.
    - Prove approval WebSocket and HTTP decisions use the canonical FRS vocabulary.
    - Prove the host-side approval panel runtime handles queue, history, and rule preview behaviour.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import faith_web.app as web_app
from faith_web.app import APPROVAL_EVENTS_CHANNEL, APPROVAL_RESPONSES_CHANNEL, create_app
from tests.test_web_server import FakeRedis

CANONICAL_APPROVAL_DECISIONS = (
    "allow_once",
    "approve_session",
    "always_allow",
    "always_ask",
    "deny_once",
    "deny_permanently",
)


@pytest.fixture
def fake_redis() -> FakeRedis:
    """Description:
        Provide a healthy fake Redis client for approval panel contract tests.

    Requirements:
        - Reuse the same fake client across one test so publish assertions remain stable.

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
    :yields: Test-configured Web UI application.
    """

    original = web_app.redis_pool
    web_app.redis_pool = fake_redis
    application = create_app(testing=True)
    yield application
    web_app.redis_pool = original


@pytest.fixture
def client(app):
    """Description:
        Provide a synchronous test client for approval panel contract tests.

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
        Provide an async HTTP client for approval panel contract tests.

    Requirements:
        - Use ASGI transport so tests exercise the application in-process.

    :param app: Test-configured Web UI application.
    :yields: Async HTTP client bound to the Web UI app.
    """

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


def test_index_includes_approval_panel_asset() -> None:
    """Description:
        Verify the main Web UI page includes the approval panel JavaScript asset.

    Requirements:
        - This test is needed to prove the browser shell can load the dedicated approval panel implementation.
        - Verify the root page references the expected asset path.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/")

    assert response.status_code == 200
    assert "/static/js/panels/approval-panel.js" in response.text


def test_approval_panel_asset_targets_expected_routes() -> None:
    """Description:
        Verify the approval panel asset targets the expected backend routes.

    Requirements:
        - This test is needed to prove the panel subscribes to approval events and posts decisions correctly.
        - Verify the asset references `/ws/approvals`, `/approve/`, and all six canonical decisions.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/static/js/panels/approval-panel.js")

    assert response.status_code == 200
    assert '"/ws/approvals"' in response.text
    assert '"/approve/"' in response.text
    for decision in CANONICAL_APPROVAL_DECISIONS:
        assert decision in response.text
    assert "auto_approved" not in response.text


def test_layout_mounts_dedicated_approval_panel_asset() -> None:
    """Description:
        Verify the layout runtime mounts the dedicated approval panel implementation.

    Requirements:
        - This test is needed to prove the GoldenLayout registration no longer renders only a placeholder.
        - Verify the layout delegates to `window.faithApprovalPanel.mountPanel` when available.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/static/js/layout.js")

    assert response.status_code == 200
    assert "faithApprovalPanel" in response.text
    assert "faithApprovalPanel.mountPanel" in response.text


def test_approval_panel_runtime_behaviour() -> None:
    """Description:
        Verify the host-side approval panel runtime checks pass.

    Requirements:
        - This test is needed to prove queue rendering, duplicate handling, rule preview, and history work together.
        - Verify the Node.js harness exits successfully.
    """

    project_root = Path(__file__).resolve().parents[1]
    runtime_test = project_root / "tests" / "test_approval_panel_runtime.js"
    result = subprocess.run(
        ["node", str(runtime_test)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.asyncio
async def test_approval_endpoint_accepts_all_canonical_decisions(
    async_client: AsyncClient,
    fake_redis: FakeRedis,
) -> None:
    """Description:
        Verify the approval endpoint accepts every canonical FRS decision.

    Requirements:
        - This test is needed to prove the browser can submit all six approval actions.
        - Verify every accepted decision is published to the approval response channel.

    :param async_client: Async HTTP client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    for index, decision in enumerate(CANONICAL_APPROVAL_DECISIONS):
        response = await async_client.post(
            f"/approve/apr-{index}",
            json={"decision": decision, "scope": "pattern", "pattern_override": "tool:action:*"},
        )
        assert response.status_code == 200

    assert len(fake_redis.published) == len(CANONICAL_APPROVAL_DECISIONS)
    published_decisions = [
        json.loads(payload_text)["decision"] for _, payload_text in fake_redis.published
    ]
    assert published_decisions == list(CANONICAL_APPROVAL_DECISIONS)
    assert {channel for channel, _ in fake_redis.published} == {APPROVAL_RESPONSES_CHANNEL}


def test_approval_websocket_relays_required_payload_fields(
    client: TestClient,
    fake_redis: FakeRedis,
) -> None:
    """Description:
        Verify the approval WebSocket relays the fields consumed by the panel.

    Requirements:
        - This test is needed to prove approval requests can render complete queue cards.
        - Verify required request, agent, tool, action, detail, timestamp, and context fields survive the relay.

    :param client: FastAPI test client bound to the FAITH web app.
    :param fake_redis: Fake Redis client used by the Web UI app.
    """

    approval_request = {
        "type": "approval_required",
        "request_id": "apr-required",
        "agent": "project-agent",
        "tool": "filesystem",
        "action": "write_file",
        "detail": "Write README.md",
        "target": "README.md",
        "timestamp": "2026-04-19T10:00:00Z",
        "context_summary": "The PA wants to update project notes.",
    }

    with client.websocket_connect("/ws/approvals") as websocket:
        fake_redis.pubsub_instance.inject_message(
            APPROVAL_EVENTS_CHANNEL, json.dumps(approval_request)
        )
        payload = json.loads(websocket.receive_text())

    assert payload == approval_request


@pytest.mark.asyncio
async def test_approval_endpoint_rejects_unknown_ids_after_queue_seen(
    app,
    async_client: AsyncClient,
) -> None:
    """Description:
        Verify unknown approval IDs are rejected once the Web UI has observed a queue.

    Requirements:
        - This test is needed to prove stale or fabricated approval decisions are not accepted after queue state exists.
        - Verify the endpoint returns HTTP 404 for a request ID absent from the observed pending queue.

    :param app: Test-configured Web UI application.
    :param async_client: Async HTTP client bound to the FAITH web app.
    """

    app.state.approval_registry_active = True
    app.state.pending_approval_ids = {"apr-known"}

    response = await async_client.post("/approve/apr-missing", json={"decision": "allow_once"})

    assert response.status_code == 404
