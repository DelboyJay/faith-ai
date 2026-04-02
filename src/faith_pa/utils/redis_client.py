"""Description:
    Provide Redis client helpers shared by the FAITH runtime components.

Requirements:
    - Centralise the default Redis URL and well-known channel names.
    - Expose both synchronous and asynchronous Redis client helpers.
    - Allow health checks to be performed without leaking temporary clients.
"""

from __future__ import annotations

import os

import redis as sync_redis
import redis.asyncio as aioredis
from redis.exceptions import RedisError

from faith_shared.protocol.events import SYSTEM_EVENTS_CHANNEL

DEFAULT_REDIS_URL = "redis://redis:6379/0"
USER_INPUT_CHANNEL = "pa-input"


def get_redis_url() -> str:
    """Description:
        Return the configured Redis URL for the active runtime.

    Requirements:
        - Fall back to the default internal Redis URL when no override is present.

    :returns: Configured Redis connection URL.
    """

    return os.environ.get("FAITH_REDIS_URL", DEFAULT_REDIS_URL)


def get_sync_client(
    url: str | None = None,
    *,
    decode_responses: bool = True,
) -> sync_redis.Redis:
    """Description:
        Create a synchronous Redis client for the supplied or configured URL.

    Requirements:
        - Default to the configured FAITH Redis URL when no explicit URL is provided.
        - Preserve the caller's response decoding preference.

    :param url: Optional Redis URL override.
    :param decode_responses: Whether Redis responses should be decoded to strings.
    :returns: Configured synchronous Redis client instance.
    """

    return sync_redis.from_url(
        url or get_redis_url(),
        decode_responses=decode_responses,
    )


async def get_async_client(
    url: str | None = None,
    *,
    decode_responses: bool = True,
) -> aioredis.Redis:
    """Description:
        Create an asynchronous Redis client for the supplied or configured URL.

    Requirements:
        - Default to the configured FAITH Redis URL when no explicit URL is provided.
        - Enable periodic health checks on long-lived async connections.

    :param url: Optional Redis URL override.
    :param decode_responses: Whether Redis responses should be decoded to strings.
    :returns: Configured asynchronous Redis client instance.
    """

    return aioredis.from_url(
        url or get_redis_url(),
        decode_responses=decode_responses,
        health_check_interval=30,
    )


async def check_connection(client: aioredis.Redis | None = None) -> bool:
    """Description:
        Return whether Redis responds successfully to a health-check ping.

    Requirements:
        - Reuse a caller-provided client when available.
        - Create and close a temporary client when one is not supplied.
        - Return ``False`` for connection or Redis protocol failures.

    :param client: Optional asynchronous Redis client to reuse.
    :returns: ``True`` when Redis responds to ``PING``.
    """

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
