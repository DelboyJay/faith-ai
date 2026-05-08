# FAITH-033 — Key-Value Store MCP Server

**Phase:** 6 — Tool Servers
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** DONE
**Dependencies:** FAITH-002
**FRS Reference:** Section 4.13

---

## Objective

Implement a Redis-backed key-value store exposed as an MCP tool server. This gives agents a shared, session-scoped store for fast lookups of established facts, cached decisions, and inter-agent shared state — without consuming context window space. All keys are namespaced per session (`sess-{id}:{key}`) to prevent cross-session contamination. Keys expire when the session closes unless explicitly marked persistent.

---

## Architecture

```
faith/tools/
├── __init__.py
└── kv_store/
    ├── __init__.py
    ├── server.py        ← MCP server entry point (this task)
    ├── store.py         ← KVStore class wrapping Redis operations (this task)
    └── models.py        ← Pydantic request/response models (this task)

tests/
└── test_kv_store.py     ← Unit tests (this task)
```

The KV store runs as a lightweight MCP server process, receiving JSON-RPC 2.0 requests from the PA's MCP adapter (FAITH-012) and executing them against the Redis instance set up by FAITH-002. Each session gets its own key namespace, and a cleanup routine purges non-persistent keys when a session ends.

---

## Files to Create

### 1. `faith/tools/kv_store/models.py`

```python
"""Pydantic models for the Key-Value Store MCP server.

Defines request parameters and response shapes for all five
KV store commands: set, get, delete, list, exists.

FRS Reference: Section 4.13.3
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class KVSetParams(BaseModel):
    """Parameters for the set command."""

    key: str = Field(..., description="The key to store. Must not be empty.")
    value: Any = Field(..., description="The value to store. Serialised as JSON.")
    ttl: Optional[int] = Field(
        None,
        description="Time-to-live in seconds. None means no expiry "
        "(key lives until session close or explicit delete).",
        ge=1,
    )
    persistent: bool = Field(
        False,
        description="If True, key survives session close. "
        "Use sparingly — persistent keys require manual cleanup.",
    )


class KVGetParams(BaseModel):
    """Parameters for the get command."""

    key: str = Field(..., description="The key to retrieve.")


class KVDeleteParams(BaseModel):
    """Parameters for the delete command."""

    key: str = Field(..., description="The key to delete.")


class KVListParams(BaseModel):
    """Parameters for the list command."""

    prefix: Optional[str] = Field(
        None,
        description="Optional prefix filter. Only keys starting with "
        "this prefix are returned. Omit to list all keys in the session.",
    )


class KVExistsParams(BaseModel):
    """Parameters for the exists command."""

    key: str = Field(..., description="The key to check.")


class KVSetResult(BaseModel):
    """Response from the set command."""

    ok: bool = True
    key: str
    ttl: Optional[int] = None
    persistent: bool = False


class KVGetResult(BaseModel):
    """Response from the get command."""

    key: str
    value: Optional[Any] = None
    found: bool


class KVDeleteResult(BaseModel):
    """Response from the delete command."""

    key: str
    deleted: bool


class KVListResult(BaseModel):
    """Response from the list command."""

    keys: list[str]
    count: int


class KVExistsResult(BaseModel):
    """Response from the exists command."""

    key: str
    exists: bool
```

### 2. `faith/tools/kv_store/store.py`

```python
"""Redis-backed session-scoped key-value store.

All keys are namespaced under `sess-{session_id}:{key}` to isolate
sessions from each other. Values are JSON-serialised before storage.

Persistent keys are tracked in a Redis set `sess-{session_id}:__persistent__`
so that the cleanup routine can skip them when a session ends.

FRS Reference: Section 4.13.2
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger("faith.tools.kv_store")

# Prefix used for the persistent key tracking set
_PERSISTENT_SET_SUFFIX = "__persistent__"


class KVStore:
    """Session-scoped key-value store backed by Redis.

    Attributes:
        redis: Async Redis client.
        session_id: The current session identifier.
    """

    def __init__(self, redis_client: aioredis.Redis, session_id: str):
        self.redis = redis_client
        self.session_id = session_id

    def _full_key(self, key: str) -> str:
        """Build the namespaced Redis key.

        Args:
            key: The user-facing key name.

        Returns:
            The full Redis key: `sess-{session_id}:{key}`.
        """
        return f"sess-{self.session_id}:{key}"

    def _persistent_set_key(self) -> str:
        """Return the Redis key for the persistent tracking set."""
        return f"sess-{self.session_id}:{_PERSISTENT_SET_SUFFIX}"

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        persistent: bool = False,
    ) -> None:
        """Store a key-value pair.

        Args:
            key: The key name.
            value: The value to store (will be JSON-serialised).
            ttl: Optional time-to-live in seconds.
            persistent: If True, key survives session cleanup.
        """
        full_key = self._full_key(key)
        serialised = json.dumps(value)

        if ttl is not None:
            await self.redis.setex(full_key, ttl, serialised)
        else:
            await self.redis.set(full_key, serialised)

        if persistent:
            await self.redis.sadd(self._persistent_set_key(), key)
        else:
            # Ensure key is not in persistent set if previously marked
            await self.redis.srem(self._persistent_set_key(), key)

        logger.debug(
            f"SET {key} (ttl={ttl}, persistent={persistent}) "
            f"in session {self.session_id}"
        )

    async def get(self, key: str) -> tuple[Any, bool]:
        """Retrieve a value by key.

        Args:
            key: The key name.

        Returns:
            Tuple of (value, found). Value is None if not found.
        """
        full_key = self._full_key(key)
        raw = await self.redis.get(full_key)

        if raw is None:
            return None, False

        value = json.loads(raw)
        return value, True

    async def delete(self, key: str) -> bool:
        """Delete a key.

        Args:
            key: The key name.

        Returns:
            True if the key existed and was deleted.
        """
        full_key = self._full_key(key)
        count = await self.redis.delete(full_key)
        # Also remove from persistent set
        await self.redis.srem(self._persistent_set_key(), key)
        deleted = count > 0
        if deleted:
            logger.debug(f"DELETE {key} in session {self.session_id}")
        return deleted

    async def list_keys(self, prefix: Optional[str] = None) -> list[str]:
        """List keys in the session, optionally filtered by prefix.

        Args:
            prefix: Optional prefix filter (applied after the session
                namespace is stripped).

        Returns:
            List of user-facing key names (without the session prefix).
        """
        session_prefix = f"sess-{self.session_id}:"
        pattern = f"{session_prefix}{prefix or ''}*"

        keys: list[str] = []
        async for raw_key in self.redis.scan_iter(match=pattern, count=100):
            if isinstance(raw_key, bytes):
                raw_key = raw_key.decode("utf-8")

            # Strip session prefix to return user-facing key name
            user_key = raw_key[len(session_prefix):]

            # Skip internal tracking keys
            if user_key == _PERSISTENT_SET_SUFFIX:
                continue

            keys.append(user_key)

        keys.sort()
        return keys

    async def exists(self, key: str) -> bool:
        """Check whether a key exists.

        Args:
            key: The key name.

        Returns:
            True if the key exists in Redis.
        """
        full_key = self._full_key(key)
        return bool(await self.redis.exists(full_key))

    async def cleanup_session(self) -> int:
        """Delete all non-persistent keys for this session.

        Called when a session ends. Persistent keys are preserved.

        Returns:
            Number of keys deleted.
        """
        session_prefix = f"sess-{self.session_id}:"

        # Get the set of persistent keys
        persistent_keys = await self.redis.smembers(self._persistent_set_key())
        persistent_set = set()
        for pk in persistent_keys:
            if isinstance(pk, bytes):
                pk = pk.decode("utf-8")
            persistent_set.add(pk)

        deleted_count = 0
        async for raw_key in self.redis.scan_iter(
            match=f"{session_prefix}*", count=100
        ):
            if isinstance(raw_key, bytes):
                raw_key = raw_key.decode("utf-8")

            user_key = raw_key[len(session_prefix):]

            # Skip persistent keys and the persistent tracking set itself
            if user_key in persistent_set or user_key == _PERSISTENT_SET_SUFFIX:
                continue

            await self.redis.delete(raw_key)
            deleted_count += 1

        logger.info(
            f"Session {self.session_id} cleanup: deleted {deleted_count} keys, "
            f"preserved {len(persistent_set)} persistent keys"
        )
        return deleted_count
```

### 3. `faith/tools/kv_store/server.py`

```python
"""MCP server for the Key-Value Store tool.

Exposes five commands over JSON-RPC 2.0: set, get, delete, list, exists.
Each request must include a `session_id` in the params (injected by the
PA before forwarding to this server).

FRS Reference: Section 4.13
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from faith.tools.kv_store.models import (
    KVDeleteParams,
    KVDeleteResult,
    KVExistsParams,
    KVExistsResult,
    KVGetParams,
    KVGetResult,
    KVListParams,
    KVListResult,
    KVSetParams,
    KVSetResult,
)
from faith.tools.kv_store.store import KVStore

logger = logging.getLogger("faith.tools.kv_store.server")

# Tool manifest advertised to the MCP adapter
TOOL_MANIFEST = {
    "name": "kv_store",
    "description": (
        "Session-scoped key-value store for caching decisions, facts, "
        "and shared state between agents. Redis-backed, fast lookups."
    ),
    "commands": {
        "set": {
            "description": "Store a value (optional TTL in seconds).",
            "parameters": KVSetParams.model_json_schema(),
        },
        "get": {
            "description": "Retrieve a value by key.",
            "parameters": KVGetParams.model_json_schema(),
        },
        "delete": {
            "description": "Remove a key.",
            "parameters": KVDeleteParams.model_json_schema(),
        },
        "list": {
            "description": "List all keys matching an optional prefix.",
            "parameters": KVListParams.model_json_schema(),
        },
        "exists": {
            "description": "Check if a key exists.",
            "parameters": KVExistsParams.model_json_schema(),
        },
    },
}

# In-memory store cache keyed by session_id to avoid re-creating per call
_store_cache: dict[str, KVStore] = {}


def _get_store(redis_client: aioredis.Redis, session_id: str) -> KVStore:
    """Get or create a KVStore for the given session.

    Args:
        redis_client: Async Redis client.
        session_id: The session identifier.

    Returns:
        A KVStore instance for the session.
    """
    if session_id not in _store_cache:
        _store_cache[session_id] = KVStore(redis_client, session_id)
    return _store_cache[session_id]


async def handle_request(
    redis_client: aioredis.Redis,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Handle an incoming MCP JSON-RPC request.

    Args:
        redis_client: Async Redis client.
        method: The command name (set, get, delete, list, exists).
        params: The request parameters. Must include `session_id`.

    Returns:
        Result dict suitable for JSON-RPC response.

    Raises:
        ValueError: If session_id is missing or method is unknown.
    """
    session_id = params.pop("session_id", None)
    if not session_id:
        raise ValueError("session_id is required in params")

    store = _get_store(redis_client, session_id)

    if method == "set":
        p = KVSetParams(**params)
        await store.set(p.key, p.value, ttl=p.ttl, persistent=p.persistent)
        result = KVSetResult(key=p.key, ttl=p.ttl, persistent=p.persistent)

    elif method == "get":
        p = KVGetParams(**params)
        value, found = await store.get(p.key)
        result = KVGetResult(key=p.key, value=value, found=found)

    elif method == "delete":
        p = KVDeleteParams(**params)
        deleted = await store.delete(p.key)
        result = KVDeleteResult(key=p.key, deleted=deleted)

    elif method == "list":
        p = KVListParams(**params)
        keys = await store.list_keys(prefix=p.prefix)
        result = KVListResult(keys=keys, count=len(keys))

    elif method == "exists":
        p = KVExistsParams(**params)
        exists = await store.exists(p.key)
        result = KVExistsResult(key=p.key, exists=exists)

    else:
        raise ValueError(f"Unknown kv_store method: {method}")

    logger.debug(f"kv_store.{method} session={session_id} -> {result}")
    return result.model_dump()


async def cleanup_session(
    redis_client: aioredis.Redis, session_id: str
) -> int:
    """Clean up all non-persistent keys for a closed session.

    Called by the PA when a session ends.

    Args:
        redis_client: Async Redis client.
        session_id: The session to clean up.

    Returns:
        Number of keys deleted.
    """
    store = _get_store(redis_client, session_id)
    count = await store.cleanup_session()
    # Remove from cache
    _store_cache.pop(session_id, None)
    return count
```

### 4. `faith/tools/kv_store/__init__.py`

```python
"""FAITH Key-Value Store MCP Tool Server."""

from faith.tools.kv_store.server import TOOL_MANIFEST, handle_request, cleanup_session
from faith.tools.kv_store.store import KVStore

__all__ = [
    "TOOL_MANIFEST",
    "handle_request",
    "cleanup_session",
    "KVStore",
]
```

### 5. `tests/test_kv_store.py`

```python
"""Tests for the FAITH Key-Value Store MCP server.

Covers all five commands (set, get, delete, list, exists), session
namespacing, TTL behaviour, persistent key handling, session cleanup,
and error cases.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.tools.kv_store.models import (
    KVDeleteParams,
    KVExistsParams,
    KVGetParams,
    KVListParams,
    KVSetParams,
)
from faith.tools.kv_store.store import KVStore
from faith.tools.kv_store.server import handle_request, cleanup_session, _store_cache


# ──────────────────────────────────────────────────
# Fake Redis for testing
# ──────────────────────────────────────────────────


class FakeRedis:
    """Minimal async Redis fake for KV store testing.

    Supports: set, setex, get, delete, exists, sadd, srem,
    smembers, scan_iter.
    """

    def __init__(self):
        self._data: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}
        self._ttls: dict[str, int] = {}

    async def set(self, key: str, value: str) -> None:
        self._data[key] = value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = value
        self._ttls[key] = ttl

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def delete(self, key: str) -> int:
        if key in self._data:
            del self._data[key]
            self._ttls.pop(key, None)
            return 1
        return 0

    async def exists(self, key: str) -> int:
        return 1 if key in self._data else 0

    async def sadd(self, key: str, member: str) -> int:
        if key not in self._sets:
            self._sets[key] = set()
        self._sets[key].add(member)
        return 1

    async def srem(self, key: str, member: str) -> int:
        if key in self._sets and member in self._sets[key]:
            self._sets[key].discard(member)
            return 1
        return 0

    async def smembers(self, key: str) -> set[str]:
        return self._sets.get(key, set())

    async def scan_iter(self, match: str = "*", count: int = 100):
        """Yield keys matching the given glob pattern."""
        import fnmatch

        for key in list(self._data.keys()):
            if fnmatch.fnmatch(key, match):
                yield key
        # Also yield set keys that match (for persistent set)
        for key in list(self._sets.keys()):
            if fnmatch.fnmatch(key, match) and key not in self._data:
                yield key


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def store(fake_redis):
    return KVStore(fake_redis, session_id="test-session-01")


@pytest.fixture(autouse=True)
def clear_store_cache():
    """Clear the server's store cache between tests."""
    _store_cache.clear()
    yield
    _store_cache.clear()


# ──────────────────────────────────────────────────
# KVStore unit tests — set / get
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_and_get(store, fake_redis):
    """Setting a key and getting it returns the same value."""
    await store.set("auth_method", "JWT_HS256")
    value, found = await store.get("auth_method")
    assert found is True
    assert value == "JWT_HS256"


@pytest.mark.asyncio
async def test_set_complex_value(store):
    """Complex JSON values (dicts, lists) round-trip correctly."""
    data = {"tokens": 1500, "files": ["main.py", "auth.py"]}
    await store.set("build_meta", data)
    value, found = await store.get("build_meta")
    assert found is True
    assert value == data


@pytest.mark.asyncio
async def test_get_missing_key(store):
    """Getting a non-existent key returns (None, False)."""
    value, found = await store.get("does_not_exist")
    assert found is False
    assert value is None


@pytest.mark.asyncio
async def test_set_with_ttl(store, fake_redis):
    """Setting a key with TTL calls setex on Redis."""
    await store.set("temp_token", "abc123", ttl=300)
    full_key = "sess-test-session-01:temp_token"
    assert full_key in fake_redis._data
    assert fake_redis._ttls[full_key] == 300


@pytest.mark.asyncio
async def test_set_overwrites_existing(store):
    """Setting the same key twice overwrites the value."""
    await store.set("counter", 1)
    await store.set("counter", 2)
    value, found = await store.get("counter")
    assert value == 2


# ──────────────────────────────────────────────────
# KVStore unit tests — delete
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_existing_key(store):
    """Deleting an existing key returns True."""
    await store.set("temp", "data")
    deleted = await store.delete("temp")
    assert deleted is True
    value, found = await store.get("temp")
    assert found is False


@pytest.mark.asyncio
async def test_delete_missing_key(store):
    """Deleting a non-existent key returns False."""
    deleted = await store.delete("ghost")
    assert deleted is False


# ──────────────────────────────────────────────────
# KVStore unit tests — list
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_all_keys(store):
    """list_keys with no prefix returns all session keys."""
    await store.set("alpha", 1)
    await store.set("beta", 2)
    await store.set("gamma", 3)
    keys = await store.list_keys()
    assert sorted(keys) == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_list_keys_with_prefix(store):
    """list_keys with a prefix filters keys."""
    await store.set("cache:hash_main", "abc")
    await store.set("cache:hash_auth", "def")
    await store.set("decision:auth_method", "JWT")
    keys = await store.list_keys(prefix="cache:")
    assert sorted(keys) == ["cache:hash_auth", "cache:hash_main"]


@pytest.mark.asyncio
async def test_list_keys_empty_session(store):
    """list_keys on an empty session returns an empty list."""
    keys = await store.list_keys()
    assert keys == []


@pytest.mark.asyncio
async def test_list_keys_excludes_internal_keys(store):
    """The __persistent__ tracking set is not included in list results."""
    await store.set("visible", "yes", persistent=True)
    keys = await store.list_keys()
    assert "__persistent__" not in keys
    assert "visible" in keys


# ──────────────────────────────────────────────────
# KVStore unit tests — exists
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exists_true(store):
    """exists returns True for a key that is set."""
    await store.set("flag", True)
    assert await store.exists("flag") is True


@pytest.mark.asyncio
async def test_exists_false(store):
    """exists returns False for a key that is not set."""
    assert await store.exists("missing") is False


# ──────────────────────────────────────────────────
# Session namespace isolation
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_are_isolated(fake_redis):
    """Keys in one session are not visible in another."""
    store_a = KVStore(fake_redis, session_id="session-a")
    store_b = KVStore(fake_redis, session_id="session-b")

    await store_a.set("shared_name", "value_a")
    await store_b.set("shared_name", "value_b")

    val_a, _ = await store_a.get("shared_name")
    val_b, _ = await store_b.get("shared_name")
    assert val_a == "value_a"
    assert val_b == "value_b"


@pytest.mark.asyncio
async def test_list_only_shows_own_session(fake_redis):
    """list_keys only returns keys from the requesting session."""
    store_a = KVStore(fake_redis, session_id="session-a")
    store_b = KVStore(fake_redis, session_id="session-b")

    await store_a.set("key_a", 1)
    await store_b.set("key_b", 2)

    keys_a = await store_a.list_keys()
    keys_b = await store_b.list_keys()
    assert keys_a == ["key_a"]
    assert keys_b == ["key_b"]


# ──────────────────────────────────────────────────
# Persistent keys and session cleanup
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persistent_key_survives_cleanup(store, fake_redis):
    """Persistent keys are preserved during session cleanup."""
    await store.set("keep_me", "important", persistent=True)
    await store.set("temp_data", "disposable")

    deleted = await store.cleanup_session()
    assert deleted == 1

    # Persistent key still exists
    value, found = await store.get("keep_me")
    assert found is True
    assert value == "important"

    # Temp key was deleted
    _, found = await store.get("temp_data")
    assert found is False


@pytest.mark.asyncio
async def test_cleanup_returns_deleted_count(store):
    """cleanup_session returns the number of deleted keys."""
    await store.set("a", 1)
    await store.set("b", 2)
    await store.set("c", 3, persistent=True)

    deleted = await store.cleanup_session()
    assert deleted == 2


@pytest.mark.asyncio
async def test_persistent_flag_can_be_removed(store, fake_redis):
    """Re-setting a key with persistent=False removes it from the persistent set."""
    await store.set("flip", "v1", persistent=True)
    await store.set("flip", "v2", persistent=False)

    # Should be deleted during cleanup
    deleted = await store.cleanup_session()
    assert deleted == 1
    _, found = await store.get("flip")
    assert found is False


# ──────────────────────────────────────────────────
# MCP server handle_request tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_server_set_command(fake_redis):
    """MCP set command stores a value and returns ok."""
    result = await handle_request(
        fake_redis, "set",
        {"session_id": "s1", "key": "auth", "value": "JWT"},
    )
    assert result["ok"] is True
    assert result["key"] == "auth"


@pytest.mark.asyncio
async def test_server_get_command(fake_redis):
    """MCP get command retrieves a stored value."""
    await handle_request(
        fake_redis, "set",
        {"session_id": "s1", "key": "x", "value": 42},
    )
    result = await handle_request(
        fake_redis, "get",
        {"session_id": "s1", "key": "x"},
    )
    assert result["found"] is True
    assert result["value"] == 42


@pytest.mark.asyncio
async def test_server_delete_command(fake_redis):
    """MCP delete command removes a key."""
    await handle_request(
        fake_redis, "set",
        {"session_id": "s1", "key": "rm_me", "value": "bye"},
    )
    result = await handle_request(
        fake_redis, "delete",
        {"session_id": "s1", "key": "rm_me"},
    )
    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_server_list_command(fake_redis):
    """MCP list command returns matching keys."""
    await handle_request(
        fake_redis, "set",
        {"session_id": "s1", "key": "a", "value": 1},
    )
    await handle_request(
        fake_redis, "set",
        {"session_id": "s1", "key": "b", "value": 2},
    )
    result = await handle_request(
        fake_redis, "list",
        {"session_id": "s1"},
    )
    assert result["count"] == 2
    assert sorted(result["keys"]) == ["a", "b"]


@pytest.mark.asyncio
async def test_server_exists_command(fake_redis):
    """MCP exists command checks key existence."""
    await handle_request(
        fake_redis, "set",
        {"session_id": "s1", "key": "present", "value": True},
    )
    result = await handle_request(
        fake_redis, "exists",
        {"session_id": "s1", "key": "present"},
    )
    assert result["exists"] is True

    result = await handle_request(
        fake_redis, "exists",
        {"session_id": "s1", "key": "absent"},
    )
    assert result["exists"] is False


@pytest.mark.asyncio
async def test_server_missing_session_id(fake_redis):
    """Request without session_id raises ValueError."""
    with pytest.raises(ValueError, match="session_id"):
        await handle_request(fake_redis, "get", {"key": "x"})


@pytest.mark.asyncio
async def test_server_unknown_method(fake_redis):
    """Unknown method raises ValueError."""
    with pytest.raises(ValueError, match="Unknown"):
        await handle_request(
            fake_redis, "drop_table",
            {"session_id": "s1"},
        )


@pytest.mark.asyncio
async def test_server_cleanup_session(fake_redis):
    """cleanup_session deletes non-persistent keys and clears cache."""
    await handle_request(
        fake_redis, "set",
        {"session_id": "s1", "key": "tmp", "value": "gone"},
    )
    await handle_request(
        fake_redis, "set",
        {"session_id": "s1", "key": "keep", "value": "stay", "persistent": True},
    )

    deleted = await cleanup_session(fake_redis, "s1")
    assert deleted == 1


# ──────────────────────────────────────────────────
# Pydantic model validation tests
# ──────────────────────────────────────────────────


def test_set_params_requires_key():
    """KVSetParams requires a non-empty key."""
    with pytest.raises(Exception):
        KVSetParams(value="x")  # type: ignore — missing key


def test_set_params_ttl_must_be_positive():
    """KVSetParams rejects TTL < 1."""
    with pytest.raises(Exception):
        KVSetParams(key="k", value="v", ttl=0)


def test_list_params_prefix_optional():
    """KVListParams works with and without prefix."""
    p1 = KVListParams()
    assert p1.prefix is None
    p2 = KVListParams(prefix="cache:")
    assert p2.prefix == "cache:"
```

---

## Integration Points

```python
# Agent stores a decision via tool call (routed through PA/MCP adapter):
# The PA injects session_id before forwarding to the KV store server.

# Example: Software Developer agent caches auth decision
tool_call = {
    "tool": "kv_store",
    "command": "set",
    "params": {
        "key": "auth_method",
        "value": "JWT_HS256",
    },
}
# PA adds session_id and forwards to kv_store server:
# handle_request(redis, "set", {"session_id": "sess-abc123", "key": "auth_method", "value": "JWT_HS256"})

# Later, QA agent retrieves the decision without asking the PA:
tool_call = {
    "tool": "kv_store",
    "command": "get",
    "params": {"key": "auth_method"},
}
# Returns: {"key": "auth_method", "value": "JWT_HS256", "found": true}
```

```python
# Session cleanup — called by PA when session ends (FAITH-014):
from faith.tools.kv_store import cleanup_session

deleted = await cleanup_session(redis_client, session_id="sess-abc123")
# Deletes all non-persistent keys, preserves keys marked persistent=True
```

---

## Acceptance Criteria

1. `KVStore.__init__` accepts an async Redis client and session ID; all key operations use the `sess-{id}:{key}` namespace.
2. `set(key, value, ttl?, persistent?)` stores JSON-serialised values in Redis; TTL-enabled keys use `SETEX`; persistent keys are tracked in a Redis set.
3. `get(key)` returns `(value, True)` for existing keys and `(None, False)` for missing keys, correctly deserialising JSON.
4. `delete(key)` removes the key from Redis and the persistent tracking set, returning whether the key existed.
5. `list_keys(prefix?)` returns user-facing key names (session prefix stripped), filters by prefix when provided, excludes internal `__persistent__` tracking keys, and returns results sorted alphabetically.
6. `exists(key)` returns a boolean indicating whether the key exists in Redis.
7. `cleanup_session()` deletes all non-persistent keys for the session, preserves persistent keys, and returns the count of deleted keys.
8. Session namespacing prevents cross-session key contamination — two sessions with the same key name hold independent values.
9. `handle_request()` dispatches to the correct command based on `method`, validates params via Pydantic models, and raises `ValueError` for missing `session_id` or unknown methods.
10. `TOOL_MANIFEST` correctly describes all five commands with their parameter schemas.
11. All 30 tests in `tests/test_kv_store.py` pass, covering set/get/delete/list/exists, TTL, persistent flags, session isolation, cleanup, MCP server dispatch, validation, and error handling.

---

## Notes for Implementer

- **Redis dependency**: This server requires the Redis container from FAITH-002 to be running. The async Redis client (`redis.asyncio`) is passed in — the server does not manage its own connection. Use the same connection pool as other FAITH components.
- **JSON serialisation**: All values are `json.dumps()`/`json.loads()` round-tripped. This means values must be JSON-serialisable (strings, numbers, booleans, lists, dicts, None). Binary data is not supported — agents should base64-encode if needed.
- **scan_iter over KEYS**: The `list_keys` method uses `SCAN` (via `scan_iter`) rather than `KEYS` to avoid blocking Redis on large datasets. The `count=100` hint keeps each scan batch small.
- **Persistent key tracking**: Persistent keys are tracked in a Redis set (`sess-{id}:__persistent__`) rather than a separate database or metadata field. This keeps the implementation simple and atomic. The `__persistent__` key itself is excluded from `list_keys` results.
- **Store cache**: The server module maintains a lightweight `_store_cache` dict mapping session IDs to `KVStore` instances. This avoids recreating the object on every request. The cache entry is removed when `cleanup_session` is called.
- **No authentication**: The KV store trusts the PA to inject the correct `session_id`. There is no per-agent access control — any agent in the session can read/write any key. This is intentional per the FRS (Section 4.13: "shared state between agents").
- **FakeRedis in tests**: Tests use a custom `FakeRedis` class rather than the `fakeredis` package, consistent with the pattern established in FAITH-010 and other task test suites. This keeps test dependencies minimal.
