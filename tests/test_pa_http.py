"""Description:
High-level HTTP tests for the FAITH Project Agent API.

Requirements:
- Verify the currently implemented PA HTTP endpoints return stable
  request/response behaviour under normal conditions.
- Verify expected service-unavailable responses are returned when Redis
  is unavailable instead of surfacing internal server errors.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import faith_pa.pa.app as pa_app_module


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

    monkeypatch.setattr(pa_app_module, "get_async_client", _get_async_client)
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

