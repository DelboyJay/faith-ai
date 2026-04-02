"""FastAPI web service for the FAITH browser UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from faith_pa import __version__
from faith_pa.utils.redis_client import check_connection, get_redis_url

logger = logging.getLogger("faith.web")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "src" / "faith_web" / "templates"
STATIC_DIR = PROJECT_ROOT / "web"

APPROVAL_EVENTS_CHANNEL = "approval-events"
APPROVAL_RESPONSES_CHANNEL = "approval-responses"

redis_pool: aioredis.Redis | Any | None = None
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/api/status", api_status, methods=["GET"])

    from faith_web.routes.http import router as http_router
    from faith_web.routes.websocket import router as websocket_router

    app.include_router(http_router)
    app.include_router(websocket_router)
    return app


app = create_app()


