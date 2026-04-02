"""Shared Redis client helpers for the FAITH POC runtime."""

from __future__ import annotations

import os

import redis as sync_redis
import redis.asyncio as aioredis
from redis.exceptions import RedisError

DEFAULT_REDIS_URL = "redis://redis:6379/0"
SYSTEM_EVENTS_CHANNEL = "system-events"
USER_INPUT_CHANNEL = "pa-input"


def get_redis_url() -> str:
    """Return the configured Redis URL."""

    return os.environ.get("FAITH_REDIS_URL", DEFAULT_REDIS_URL)


def get_sync_client(
    url: str | None = None,
    *,
    decode_responses: bool = True,
) -> sync_redis.Redis:
    """Create a synchronous Redis client."""

    return sync_redis.from_url(
        url or get_redis_url(),
        decode_responses=decode_responses,
    )


async def get_async_client(
    url: str | None = None,
    *,
    decode_responses: bool = True,
) -> aioredis.Redis:
    """Create an asynchronous Redis client."""

    return aioredis.from_url(
        url or get_redis_url(),
        decode_responses=decode_responses,
        health_check_interval=30,
    )


async def check_connection(client: aioredis.Redis | None = None) -> bool:
    """Return whether Redis responds to `PING`."""

    owns_client = client is None
    redis_client = client
    try:
        if redis_client is None:
            redis_client = await get_async_client()
        return bool(await redis_client.ping())
    except (OSError, RedisError):
        return False
    finally:
        if owns_client and redis_client is not None:
            await redis_client.aclose()
