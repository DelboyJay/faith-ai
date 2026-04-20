"""FastAPI web service for the FAITH browser UI."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from httpx import AsyncClient

from faith_pa.utils.redis_client import check_connection, get_redis_url
from faith_shared.api import RouteManifestEntry, ServiceRouteManifest
from faith_shared.config import DockerRuntimeSnapshot
from faith_web.version import __version__

logger = logging.getLogger("faith.web")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "src" / "faith_web" / "templates"
STATIC_DIR = PROJECT_ROOT / "web"

APPROVAL_EVENTS_CHANNEL = "approval-events"
APPROVAL_RESPONSES_CHANNEL = "approval-responses"
DEFAULT_PA_URL = os.getenv("FAITH_PA_URL", "http://pa:8000")

redis_pool: aioredis.Redis | Any | None = None
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_static_asset_version() -> str:
    """Description:
        Build a cache-busting version string for bundled browser assets.

    Requirements:
        - Change when local static files are rebuilt or modified.
        - Fall back to the application version when asset inspection fails.

    :returns: Version string suitable for static asset query parameters.
    """

    try:
        latest_mtime = max(
            path.stat().st_mtime_ns for path in STATIC_DIR.rglob("*") if path.is_file()
        )
    except ValueError:
        return __version__
    except OSError:
        return __version__
    return str(latest_mtime)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Description:
        Create and clean up the shared Redis client for the Web UI process.

    Requirements:
        - Skip Redis setup entirely when the app is running in testing mode.
        - Open a shared async Redis client for the lifetime of the application.
        - Close the client cleanly on shutdown.

    :param app: FastAPI application instance being started.
    :yields: Control back to FastAPI once startup has completed.
    """

    global redis_pool

    if getattr(app.state, "testing", False):
        yield
        return

    redis_url = get_redis_url()
    logger.info("Connecting web service to Redis at %s", redis_url)
    redis_pool = aioredis.from_url(
        redis_url,
        decode_responses=True,
        health_check_interval=30,
    )
    try:
        await redis_pool.ping()
        yield
    finally:
        if redis_pool is not None:
            close = getattr(redis_pool, "aclose", None)
            if callable(close):
                await close()
            else:
                close = getattr(redis_pool, "close", None)
                if callable(close):
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
        redis_pool = None


async def _redis_connected() -> bool:
    """Description:
        Return whether the shared Redis client is currently reachable.

    Requirements:
        - Return ``False`` when the shared Redis client has not been created.
        - Delegate the connectivity check to the shared Redis helper.

    :returns: ``True`` when Redis is reachable, otherwise ``False``.
    """

    return await check_connection(redis_pool) if redis_pool is not None else False


def _build_route_manifest() -> ServiceRouteManifest:
    """Description:
        Build the structured route manifest exposed by the Web UI service.

    Requirements:
        - Describe all currently supported public Web UI HTTP and WebSocket endpoints.
        - Keep the manifest machine-readable so CLI tools do not hard-code UI routes.

    :returns: Route manifest payload for the Web UI service.
    """

    return ServiceRouteManifest(
        service="faith-web-ui",
        version=__version__,
        routes=[
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="http",
                method="GET",
                path="/",
                summary="Serve the main FAITH Web UI page.",
                expected_status_codes=[200],
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="http",
                method="GET",
                path="/health",
                summary="Return Web UI liveness and Redis health.",
                expected_status_codes=[200, 503],
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="http",
                method="GET",
                path="/api/status",
                summary="Return the current Web UI status payload.",
                expected_status_codes=[200, 503],
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="http",
                method="GET",
                path="/api/docker-runtime",
                summary="Return the current Docker runtime snapshot for the Web UI panels.",
                expected_status_codes=[200, 503],
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="http",
                method="GET",
                path="/api/routes",
                summary="Return the structured Web UI route manifest for CLI discovery.",
                expected_status_codes=[200],
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="http",
                method="POST",
                path="/input",
                summary="Submit a user text message to the PA input channel.",
                expected_status_codes=[200, 422, 503],
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="http",
                method="POST",
                path="/upload",
                summary="Upload a file and publish it to the PA input channel.",
                expected_status_codes=[200, 413, 415, 422, 503],
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="http",
                method="POST",
                path="/approve/{request_id}",
                summary="Submit an approval decision back to the PA.",
                expected_status_codes=[200, 404, 422, 503],
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="http",
                method="GET",
                path="/static/{path:path}",
                summary="Serve bundled frontend assets.",
                expected_status_codes=[200],
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="websocket",
                path="/ws/agent/{agent_id}",
                summary="Stream one agent output feed.",
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="websocket",
                path="/ws/tool/{tool_id}",
                summary="Stream one tool output feed.",
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="websocket",
                path="/ws/approvals",
                summary="Stream approval requests and updates.",
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="websocket",
                path="/ws/status",
                summary="Stream shared system status events.",
            ),
            RouteManifestEntry(
                service="faith-web-ui",
                protocol="websocket",
                path="/ws/docker",
                summary="Stream Docker runtime snapshots for operational panels.",
            ),
        ],
    )


async def fetch_pa_docker_runtime() -> DockerRuntimeSnapshot:
    """Description:
        Fetch the current Docker runtime snapshot from the PA service.

    Requirements:
        - Use the configured PA base URL.
        - Raise on upstream HTTP errors so callers can report a degraded state.

    :returns: Docker runtime snapshot from the PA service.
    """

    async with AsyncClient(base_url=DEFAULT_PA_URL, timeout=5.0) as client:
        response = await client.get("/api/docker-runtime")
        response.raise_for_status()
        return DockerRuntimeSnapshot.model_validate(response.json())


async def health() -> JSONResponse:
    """Description:
        Return the FAITH Web UI health payload.

    Requirements:
        - Report a degraded state when Redis is unavailable.
        - Return HTTP 200 when healthy and HTTP 503 when degraded.

    :returns: JSON response containing service and Redis status information.
    """

    connected = await _redis_connected()
    payload = {
        "service": "faith-web-ui",
        "version": __version__,
        "status": "ok" if connected else "degraded",
        "redis": {
            "connected": connected,
            "url": get_redis_url(),
        },
    }
    return JSONResponse(payload, status_code=200 if connected else 503)


async def api_status() -> JSONResponse:
    """Description:
        Return the current Web UI status payload.

    Requirements:
        - Reuse the health payload so both status routes stay aligned.

    :returns: JSON response containing the current service status payload.
    """

    return await health()


async def api_routes() -> ServiceRouteManifest:
    """Description:
        Return the machine-readable Web UI route manifest.

    Requirements:
        - Expose a discovery contract for CLI tooling instead of requiring hard-coded UI routes.
        - Remain available without depending on Redis health.

    :returns: Structured manifest for Web UI HTTP and WebSocket routes.
    """

    return _build_route_manifest()


def create_app(*, testing: bool = False) -> FastAPI:
    """Description:
        Create the FAITH Web UI FastAPI application.

    Requirements:
        - Serve templates from ``src/faith_web/templates``.
        - Serve frontend assets from the repository-level ``web/`` directory.
        - Register the HTTP and WebSocket routers for browser interaction.

    :param testing: When ``True``, skip Redis connection setup for tests.
    :returns: Configured FastAPI application instance.
    """

    app = FastAPI(
        title="FAITH Web UI",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.testing = testing
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.state.pa_runtime_fetcher = fetch_pa_docker_runtime
    app.state.pending_approval_ids = set()
    app.state.approval_registry_active = False
    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/api/status", api_status, methods=["GET"])
    app.add_api_route(
        "/api/routes", api_routes, methods=["GET"], response_model=ServiceRouteManifest
    )

    from faith_web.routes.docker_runtime import router as docker_runtime_router
    from faith_web.routes.http import router as http_router
    from faith_web.routes.websocket import router as websocket_router

    app.include_router(docker_runtime_router)
    app.include_router(http_router)
    app.include_router(websocket_router)
    return app


app = create_app()
