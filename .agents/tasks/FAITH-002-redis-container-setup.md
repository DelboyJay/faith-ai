# FAITH-002 — Redis Container Setup

**Phase:** 1 — Foundation
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-001
**FRS Reference:** Section 2.2.4, 4.6.4, 3.7.2

---

## Objective

Configure the Redis container with AOF persistence, verify it starts correctly, is reachable from the `maf-network`, and is ready to serve as both the message bus (pub/sub) and key-value store for FAITH.

---

## Context

Redis serves three roles in FAITH:

1. **Message bus** — pub/sub channels for agent-to-agent communication and the `system-events` channel.
2. **Key-value store** — session-scoped agent shared state (FAITH-033).
3. **State persistence** — AOF ensures message bus state survives PA crashes.

The Redis container is defined in `docker-compose.yml` (created in FAITH-001). This task validates and extends that configuration, and creates a health-check utility.

---

## Files to Create / Modify

### 1. `containers/redis/redis.conf`

A custom Redis config to fine-tune persistence and memory settings.

```conf
# FAITH Redis Configuration

# Persistence — AOF mode
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# Memory management
maxmemory 256mb
maxmemory-policy allkeys-lru

# Networking
bind 0.0.0.0
protected-mode no
tcp-keepalive 60

# Logging
loglevel notice
logfile ""

# Pub/Sub — no limits on subscribers
# (default is fine for FAITH's scale)
```

### 2. Update `docker-compose.yml` — Redis service

Modify the Redis service in `docker-compose.yml` to mount the custom config:

```yaml
  redis:
    image: redis:7-alpine
    container_name: faith-redis
    command: redis-server /usr/local/etc/redis/redis.conf
    volumes:
      - redis-data:/data
      - ./containers/redis/redis.conf:/usr/local/etc/redis/redis.conf:ro
    networks:
      - maf-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3
```

**Developer context:** This path (`./containers/redis/redis.conf`) is relative to the monorepo root. When `faith-cli` is released, the bundled `docker-compose.yml` in `~/.faith/` will be pre-configured with the correct path, and end users will not need to modify this.

### 3. `faith-shared` Redis client module

Source ownership note: this helper belongs in the shared contract/runtime layer (for example `src/faith_shared/redis_client.py`), not in the `faith_cli` package.

A shared Redis client factory used by all FAITH components (PA, agents, web-ui, tools).

```python
"""Shared Redis client factory for all FAITH components."""

from __future__ import annotations

import os
from typing import Optional

import redis.asyncio as aioredis
import redis as sync_redis


# Default Redis URL — overridden by FAITH_REDIS_URL environment variable
DEFAULT_REDIS_URL = "redis://redis:6379/0"

# Well-known channel names
SYSTEM_EVENTS_CHANNEL = "system-events"
USER_INPUT_CHANNEL = "pa-input"


def get_redis_url() -> str:
    """Get the Redis connection URL from environment or default."""
    return os.environ.get("FAITH_REDIS_URL", DEFAULT_REDIS_URL)


async def get_async_client(
    url: Optional[str] = None,
    decode_responses: bool = True,
) -> aioredis.Redis:
    """Create an async Redis client.

    Args:
        url: Redis connection URL. Defaults to FAITH_REDIS_URL env var.
        decode_responses: Whether to decode byte responses to strings.

    Returns:
        An async Redis client instance.
    """
    return aioredis.from_url(
        url or get_redis_url(),
        decode_responses=decode_responses,
    )


def get_sync_client(
    url: Optional[str] = None,
    decode_responses: bool = True,
) -> sync_redis.Redis:
    """Create a synchronous Redis client.

    Args:
        url: Redis connection URL. Defaults to FAITH_REDIS_URL env var.
        decode_responses: Whether to decode byte responses to strings.

    Returns:
        A synchronous Redis client instance.
    """
    return sync_redis.from_url(
        url or get_redis_url(),
        decode_responses=decode_responses,
    )


async def check_connection(client: Optional[aioredis.Redis] = None) -> bool:
    """Check if Redis is reachable.

    Args:
        client: Optional existing client. Creates a temporary one if not provided.

    Returns:
        True if Redis responds to PING, False otherwise.
    """
    own_client = False
    try:
        if client is None:
            client = await get_async_client()
            own_client = True
        result = await client.ping()
        return result is True
    except (ConnectionError, OSError, aioredis.ConnectionError):
        return False
    finally:
        if own_client and client is not None:
            await client.aclose()
```

### 4. `tests/test_redis_connection.py`

A simple integration test to validate Redis is running and reachable.

```python
"""Integration test — validates Redis container is running and reachable."""

import asyncio
import pytest
from faith.utils.redis_client import (
    get_async_client,
    get_sync_client,
    check_connection,
    SYSTEM_EVENTS_CHANNEL,
)


@pytest.fixture
async def redis_client():
    client = await get_async_client()
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_redis_ping(redis_client):
    """Redis should respond to PING."""
    result = await redis_client.ping()
    assert result is True


@pytest.mark.asyncio
async def test_check_connection():
    """check_connection() should return True when Redis is running."""
    assert await check_connection() is True


@pytest.mark.asyncio
async def test_pubsub_round_trip(redis_client):
    """A message published to a channel should be received by a subscriber."""
    received = []

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(SYSTEM_EVENTS_CHANNEL)

    # Publish a test message
    await redis_client.publish(SYSTEM_EVENTS_CHANNEL, '{"event": "test"}')

    # Read messages (first is the subscribe confirmation)
    for _ in range(10):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message and message["type"] == "message":
            received.append(message["data"])
            break
        await asyncio.sleep(0.1)

    await pubsub.unsubscribe(SYSTEM_EVENTS_CHANNEL)
    await pubsub.aclose()

    assert len(received) == 1
    assert '"test"' in received[0]


@pytest.mark.asyncio
async def test_key_value_round_trip(redis_client):
    """A key set in Redis should be retrievable."""
    await redis_client.set("faith:test:key", "hello")
    value = await redis_client.get("faith:test:key")
    assert value == "hello"
    await redis_client.delete("faith:test:key")


@pytest.mark.asyncio
async def test_aof_persistence_enabled(redis_client):
    """AOF persistence should be enabled."""
    info = await redis_client.info("persistence")
    assert info["aof_enabled"] == 1
```

---

## Environment Variables

All FAITH containers use the same environment variable to find Redis:

| Variable | Default | Used by |
|---|---|---|
| `FAITH_REDIS_URL` | `redis://redis:6379/0` | PA, agents, web-ui, tools |

This is already set in the `docker-compose.yml` for the web-ui service (FAITH-001). Add it to the PA service environment as well:

```yaml
  pa:
    environment:
      - FAITH_CONFIG_DIR=/config
      - FAITH_LOG_DIR=/logs
      - FAITH_REDIS_URL=redis://redis:6379/0
      # Note: In end-user installations via faith-cli, FAITH_CONFIG_DIR=/config and
      # FAITH_LOG_DIR=/logs are mounted from ~/.faith/config/ and ~/.faith/logs/.
      # FAITH_WORKSPACE_DIR is not set here — the PA mounts project workspaces
      # dynamically via Docker SDK when a project is opened. The project's .faith/
      # directory is inside the mounted workspace.
```

---

## Acceptance Criteria

1. `docker compose up redis` starts the Redis container and it reaches `healthy` status within 30 seconds.
2. `docker compose exec redis redis-cli ping` returns `PONG`.
3. `docker compose exec redis redis-cli config get appendonly` returns `yes`.
4. The `faith.utils.redis_client` module is importable and `get_async_client()` returns a working client when Redis is running.
5. All five tests in `tests/test_redis_connection.py` pass.
6. After writing a key, stopping Redis (`docker compose stop redis`), and restarting it, the key is still present (AOF persistence verified).

---

## Notes for Implementer

- Use `redis:7-alpine` specifically — not `latest`. Pin the major version for reproducibility.
- The `maxmemory 256mb` with `allkeys-lru` eviction is a sensible default for local development. It can be increased in `docker-compose.yml` overrides for larger deployments.
- `protected-mode no` is safe because Redis is only accessible within the Docker network (`maf-network`) — it is not exposed to the host.
- The async client (`redis.asyncio`) is used by the PA and web-ui (both async). The sync client is available for tools that may run synchronously.
- Do not install Redis on the host — it runs exclusively in the Docker container.
