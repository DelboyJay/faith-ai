"""Minimal Project Agent HTTP service for the FAITH POC."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from faith_pa import __version__
from faith_pa.config import ConfigSummary, RedisStatus, ServiceStatus, build_config_summary
from faith_pa.utils import SYSTEM_EVENTS_CHANNEL, check_connection, get_async_client, get_redis_url
from faith_shared.api import RouteManifestEntry, ServiceRouteManifest


async def _build_status(app: FastAPI) -> ServiceStatus:
    """Build the current runtime status snapshot."""

    redis_client = getattr(app.state, "redis", None)
    redis_connected = await check_connection(redis_client)
    status = "ok" if redis_connected else "degraded"
    return ServiceStatus(
        service="faith-project-agent",
        version=__version__,
        status=status,
        redis=RedisStatus(url=get_redis_url(), connected=redis_connected),
        config=build_config_summary(),
    )


def _build_route_manifest() -> ServiceRouteManifest:
    """Description:
        Build the structured route manifest exposed by the PA service.

    Requirements:
        - Describe all currently supported public PA endpoints.
        - Keep the manifest machine-readable so CLI clients do not hard-code PA routes.

    :returns: Route manifest payload for the PA service.
    """

    return ServiceRouteManifest(
        service="faith-project-agent",
        version=__version__,
        routes=[
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/health",
                summary="Return PA liveness and dependency health.",
                expected_status_codes=[200, 503],
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/status",
                summary="Return the current PA runtime status snapshot.",
                expected_status_codes=[200],
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/config",
                summary="Return the redacted PA config summary.",
                expected_status_codes=[200],
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="POST",
                path="/api/events/test",
                summary="Publish a test event into the PA system-events channel.",
                expected_status_codes=[200, 503],
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/routes",
                summary="Return the structured PA route manifest for CLI discovery.",
                expected_status_codes=[200],
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="websocket",
                path="/ws/status",
                summary="Stream PA status snapshots over WebSocket.",
            ),
        ],
    )


def _require_redis(app: FastAPI):
    """Return the shared Redis client or raise a service-unavailable error.

    :param app: FastAPI application holding shared runtime state.
    :raises HTTPException: If Redis is not available.
    :returns: Shared Redis client.
    """
    redis_client = getattr(app.state, "redis", None)
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis not available")
    return redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create shared resources for the API lifespan."""

    app.state.redis = await get_async_client()
    yield
    redis_client = getattr(app.state, "redis", None)
    if redis_client is not None:
        await redis_client.aclose()


app = FastAPI(
    title="FAITH Project Agent",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness and dependency status."""

    status = await _build_status(app)
    code = 200 if status.status == "ok" else 503
    return JSONResponse(status.model_dump(mode="json"), status_code=code)


@app.get("/api/status", response_model=ServiceStatus)
async def api_status() -> ServiceStatus:
    """Return current PA runtime status."""

    return await _build_status(app)


@app.get("/api/config", response_model=ConfigSummary)
async def api_config() -> ConfigSummary:
    """Return the redacted config summary."""

    return build_config_summary()


@app.post("/api/events/test")
async def publish_test_event() -> dict[str, str]:
    """Publish a simple test event to Redis for the POC."""

    payload = {
        "event": "poc:test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    redis_client = _require_redis(app)
    await redis_client.publish(SYSTEM_EVENTS_CHANNEL, str(payload))
    return payload


@app.get("/api/routes", response_model=ServiceRouteManifest)
async def api_routes() -> ServiceRouteManifest:
    """Description:
        Return the machine-readable PA route manifest.

    Requirements:
        - Expose a discovery contract for CLI tooling instead of requiring hard-coded route knowledge.
        - Remain available without depending on Redis health.

    :returns: Structured manifest for PA HTTP and WebSocket routes.
    """

    return _build_route_manifest()


@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket) -> None:
    """Push a status snapshot to connected clients."""

    await websocket.accept()
    try:
        while True:
            status = await _build_status(app)
            await websocket.send_json(status.model_dump(mode="json"))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
