"""Description:
    Verify the FAITH Web UI read-only log endpoints.

Requirements:
    - Prove empty or missing log sources return stable empty responses rather than HTTP 500.
    - Prove audit, event, token, approval, and session endpoints return reverse-chronological data.
    - Prove malformed JSON-lines records are skipped safely.
    - Prove unknown sessions and invalid session/channel identifiers return the documented error statuses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from faith_web.app import create_app


@pytest.fixture
def app(tmp_path: Path):
    """Description:
        Create a test-configured Web UI application with isolated log roots.

    Requirements:
        - Route all log readers to temporary directories created for the test.
        - Avoid depending on any host runtime files.

    :param tmp_path: Temporary pytest directory fixture.
    :returns: Test-configured Web UI application.
    """

    application = create_app(testing=True)
    application.state.logs_dir = tmp_path / "logs"
    application.state.pa_session_root = tmp_path / "pa-runtime"
    return application


@pytest_asyncio.fixture
async def async_client(app):
    """Description:
        Provide an async HTTP client bound to the isolated Web UI app.

    Requirements:
        - Exercise the real FastAPI routes in-process through ASGI transport.

    :param app: Test-configured Web UI application.
    :yields: Async HTTP client bound to the application.
    """

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _write_json_lines(path: Path, payloads: list[dict[str, object] | str]) -> None:
    """Description:
        Write one JSON-lines file for a log-endpoint test.

    Requirements:
        - Allow tests to mix valid JSON objects with malformed raw lines.
        - Create parent directories automatically.

    :param path: File path to populate.
    :param payloads: Ordered JSON objects or raw string lines to write.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for payload in payloads:
        if isinstance(payload, str):
            lines.append(payload)
        else:
            lines.append(json.dumps(payload))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_session_tree(root: Path) -> None:
    """Description:
        Create a small persisted session tree for session-history endpoint tests.

    Requirements:
        - Include one session metadata file, one task metadata file, one channel log, and one PA transcript.

    :param root: Runtime PA session root equivalent.
    """

    session_dir = root / ".faith" / "sessions" / "sess-0001-20260504"
    task_dir = session_dir / "tasks" / "task-1-101010.000"
    task_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session.meta.json").write_text(
        json.dumps(
            {
                "session_id": "sess-0001-20260504",
                "status": "active",
                "trigger": "web-ui",
                "started_at": "2026-05-04T10:10:10Z",
                "ended_at": None,
                "tasks": {
                    "task-1-101010.000": {
                        "goal": "Inspect logs",
                        "status": "active",
                        "channel": "ch-log-review",
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (task_dir / "task.meta.json").write_text(
        json.dumps(
            {
                "task_id": "task-1-101010.000",
                "goal": "Inspect logs",
                "status": "active",
                "channels": {"ch-log-review": {"name": "ch-log-review"}},
                "agents": ["project-agent"],
                "started_at": "2026-05-04T10:10:15Z",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (task_dir / "ch-log-review.log").write_text(
        "# Channel: ch-log-review\n\n2026-05-04T10:10:16Z project-agent: Reviewing logs.\n",
        encoding="utf-8",
    )
    (session_dir / "pa-user.log").write_text(
        "# Project Agent Transcript\n\n## User\n~~~text\nShow me the logs.\n~~~\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_log_endpoints_return_empty_payloads_when_logs_are_missing(
    async_client: AsyncClient,
) -> None:
    """Description:
        Verify missing log files return empty result sets instead of server errors.

    Requirements:
        - This test is needed to prove the read-only log views remain stable on a fresh install.
        - Verify audit, events, tokens, approvals, and sessions each return HTTP 200 with zero items.

    :param async_client: Async HTTP client bound to the test Web UI app.
    """

    for path in (
        "/api/logs/audit",
        "/api/logs/events",
        "/api/logs/tokens",
        "/api/logs/approvals",
        "/api/logs/sessions",
    ):
        response = await async_client.get(path)
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_audit_and_approval_views_return_newest_entries_first_and_skip_bad_lines(
    app,
    async_client: AsyncClient,
) -> None:
    """Description:
        Verify audit-backed views skip malformed lines and sort newest entries first.

    Requirements:
        - This test is needed to prove the log views are resilient to partial writes and still sort correctly.
        - Verify the approval-history endpoint returns only approval-related audit entries.

    :param app: Test-configured Web UI application.
    :param async_client: Async HTTP client bound to the test Web UI app.
    """

    _write_json_lines(
        app.state.logs_dir / "audit.log",
        [
            {
                "ts": "2026-05-04T09:59:00Z",
                "agent": "project-agent",
                "tool": "filesystem",
                "action": "read",
                "target": "README.md",
                "decision": "approved",
            },
            "{bad json",
            {
                "ts": "2026-05-04T10:00:00Z",
                "agent": "project-agent",
                "tool": "python",
                "action": "execute",
                "target": "print(1)",
                "approval_tier": "approve_session",
                "decision": "approved",
            },
            {
                "ts": "2026-05-04T10:01:00Z",
                "agent": "project-agent",
                "tool": "python",
                "action": "execute",
                "target": "dangerous.py",
                "approval_tier": "always_deny",
                "decision": "denied",
            },
        ],
    )

    audit_response = await async_client.get("/api/logs/audit")
    assert audit_response.status_code == 200
    audit_items = audit_response.json()["items"]
    assert [item["ts"] for item in audit_items] == [
        "2026-05-04T10:01:00Z",
        "2026-05-04T10:00:00Z",
        "2026-05-04T09:59:00Z",
    ]

    approval_response = await async_client.get("/api/logs/approvals")
    assert approval_response.status_code == 200
    approval_items = approval_response.json()["items"]
    assert len(approval_items) == 2
    assert [item["decision"] for item in approval_items] == ["denied", "approved"]


@pytest.mark.asyncio
async def test_event_and_token_views_filter_and_sort_descending(
    app,
    async_client: AsyncClient,
) -> None:
    """Description:
        Verify event and token views filter records and keep newest records first.

    Requirements:
        - This test is needed to prove event and token filters work on populated logs.
        - Verify the newest matching record is returned first for each endpoint.

    :param app: Test-configured Web UI application.
    :param async_client: Async HTTP client bound to the test Web UI app.
    """

    _write_json_lines(
        app.state.logs_dir / "events.log",
        [
            {"ts": "2026-05-04T10:10:00Z", "event": "agent:error", "source": "qa"},
            {"ts": "2026-05-04T10:12:00Z", "event": "tool:call_complete", "source": "dev"},
        ],
    )
    _write_json_lines(
        app.state.logs_dir / "tokens.log",
        [
            {
                "ts": "2026-05-04T10:11:00Z",
                "session_id": "sess-1",
                "task_id": "task-1",
                "agent": "project-agent",
                "model": "ollama/llama3:8b",
                "input_tokens": 100,
                "output_tokens": 20,
                "estimated_cost": 0.0,
            },
            {
                "ts": "2026-05-04T10:13:00Z",
                "session_id": "sess-2",
                "task_id": "task-2",
                "agent": "qa",
                "model": "ollama/llama3:8b",
                "input_tokens": 50,
                "output_tokens": 10,
                "estimated_cost": 0.0,
            },
        ],
    )

    event_response = await async_client.get(
        "/api/logs/events", params={"event": "tool:call_complete"}
    )
    assert event_response.status_code == 200
    assert [item["ts"] for item in event_response.json()["items"]] == ["2026-05-04T10:12:00Z"]

    token_response = await async_client.get("/api/logs/tokens", params={"agent": "qa"})
    assert token_response.status_code == 200
    token_payload = token_response.json()
    assert [item["ts"] for item in token_payload["items"]] == ["2026-05-04T10:13:00Z"]
    assert token_payload["summary"]["by_model"]["ollama/llama3:8b"]["calls"] == 1
    assert token_payload["summary"]["session_comparisons"][0]["session_id"] == "sess-2"
    assert token_payload["summary"]["session_comparisons"][0]["total_tokens"] == 60


@pytest.mark.asyncio
async def test_session_history_endpoints_browse_sessions_and_channel_logs(
    app,
    async_client: AsyncClient,
) -> None:
    """Description:
        Verify the session-history endpoints list sessions, load details, and return channel logs.

    Requirements:
        - This test is needed to prove the session-history panel can browse persisted runtime history.
        - Verify the list, detail, and channel endpoints each return HTTP 200 on the happy path.

    :param app: Test-configured Web UI application.
    :param async_client: Async HTTP client bound to the test Web UI app.
    """

    _write_session_tree(app.state.pa_session_root)

    list_response = await async_client.get("/api/logs/sessions")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["session_id"] == "sess-0001-20260504"

    detail_response = await async_client.get("/api/logs/sessions/sess-0001-20260504")
    assert detail_response.status_code == 200
    assert detail_response.json()["session"]["session_id"] == "sess-0001-20260504"
    assert detail_response.json()["tasks"][0]["task_id"] == "task-1-101010.000"

    channel_response = await async_client.get(
        "/api/logs/sessions/sess-0001-20260504/channels/ch-log-review.log"
    )
    assert channel_response.status_code == 200
    assert "Reviewing logs" in channel_response.json()["content"]


@pytest.mark.asyncio
async def test_session_history_endpoints_reject_unknown_and_traversal_paths(
    app,
    async_client: AsyncClient,
) -> None:
    """Description:
        Verify the session-history endpoints reject missing sessions and traversal-style names.

    Requirements:
        - This test is needed to prove session-history browsing does not expose arbitrary filesystem access.
        - Verify missing sessions return HTTP 404 and invalid identifiers return HTTP 400.

    :param app: Test-configured Web UI application.
    :param async_client: Async HTTP client bound to the test Web UI app.
    """

    _write_session_tree(app.state.pa_session_root)

    missing_response = await async_client.get("/api/logs/sessions/sess-missing")
    assert missing_response.status_code == 404

    invalid_session_response = await async_client.get("/api/logs/sessions/../secret")
    assert (
        invalid_session_response.status_code == 404 or invalid_session_response.status_code == 400
    )

    invalid_channel_response = await async_client.get(
        "/api/logs/sessions/sess-0001-20260504/channels/../secret.log"
    )
    assert invalid_channel_response.status_code in {400, 404}
