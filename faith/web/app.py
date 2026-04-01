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

from faith import __version__
from faith.utils.redis_client import check_connection, get_redis_url

logger = logging.getLogger("faith.web")

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

APPROVAL_EVENTS_CHANNEL = "approval-events"
APPROVAL_RESPONSES_CHANNEL = "approval-responses"

redis_pool: aioredis.Redis | Any | None = None
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create and clean up the shared Redis client."""

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
    return await check_connection(redis_pool) if redis_pool is not None else False


async def health() -> JSONResponse:
    """Basic web service health endpoint."""

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
    """Return the current web service status payload."""

    return await health()


def create_app(*, testing: bool = False) -> FastAPI:
    """Create the FAITH web application."""

    app = FastAPI(
        title="FAITH Web UI",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.testing = testing
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/api/status", api_status, methods=["GET"])

    from faith.web.routes.http import router as http_router
    from faith.web.routes.websocket import router as websocket_router

    app.include_router(http_router)
    app.include_router(websocket_router)
    return app


app = create_app()
