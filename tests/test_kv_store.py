"""
Description:
    Verify the KV store MCP package namespaces session data and honours Redis
    cleanup behaviour.

Requirements:
    - Prove values round-trip through JSON serialisation.
    - Prove session cleanup preserves persistent keys and removes transient
      keys.
    - Prove the MCP request dispatcher validates params and command names.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from faith_mcp.kv_store import handle_request
from faith_mcp.kv_store.models import (
    KVExistsParams,
    KVListParams,
    KVSetParams,
)
from faith_mcp.kv_store.server import TOOL_MANIFEST, _store_cache
from faith_mcp.kv_store.store import KVStore


class FakeRedis:
    """
    Description:
        Provide a minimal async Redis test double for the KV store suite.

    Requirements:
        - Support the Redis operations used by the store implementation.
        - Preserve keys, sets, and TTL metadata for assertions.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        existed = 0
        if key in self.values:
            existed = 1
            del self.values[key]
        self.ttls.pop(key, None)
        return existed

    async def exists(self, key: str) -> int:
        return int(key in self.values)

    async def sadd(self, key: str, member: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.add(member)
        return int(len(bucket) > before)

    async def srem(self, key: str, member: str) -> int:
        bucket = self.sets.get(key)
        if bucket is None or member not in bucket:
            return 0
        bucket.remove(member)
        return 1

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def scan_iter(self, match: str = "*", count: int = 100):  # noqa: ARG002
        from fnmatch import fnmatch

        for key in sorted(self.values):
            if fnmatch(key, match):
                yield key
        for key in sorted(self.sets):
            if fnmatch(key, match):
                yield key


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """
    Description:
        Keep the server-level KV store cache isolated between tests.

    Requirements:
        - Prevent session state from leaking across test cases.
    """
    _store_cache.clear()


@pytest.fixture
def redis() -> FakeRedis:
    """
    Description:
        Provide a fresh fake Redis instance for each test.

    Requirements:
        - Ensure each test sees isolated Redis state.

    :returns: Empty fake Redis client.
    """
    return FakeRedis()


@pytest.fixture
def store(redis: FakeRedis) -> KVStore:
    """
    Description:
        Build a KV store bound to the fake Redis client.

    Requirements:
        - Reuse the same session identifier across the test case.

    :param redis: Fake Redis client fixture.
    :returns: Session-scoped KV store.
    """
    return KVStore(redis, session_id="sess-123")


def test_manifest_exposes_all_commands() -> None:
    """
    Description:
        Verify the tool manifest advertises the five expected commands.

    Requirements:
        - This test is needed to prove the MCP-facing manifest matches the
          task brief.
        - Verify the manifest exposes the set/get/delete/list/exists commands.
    """
    assert set(TOOL_MANIFEST["commands"]) == {"set", "get", "delete", "list", "exists"}


@pytest.mark.asyncio
async def test_set_get_and_delete_round_trip(store: KVStore, redis: FakeRedis) -> None:
    """
    Description:
        Verify values are JSON-serialised, retrieved, and deleted correctly.

    Requirements:
        - This test is needed to prove the store persists values in Redis and
          deserialises them on read.
        - Verify delete returns whether the key existed.

    :param store: Session-scoped KV store fixture.
    :param redis: Fake Redis client fixture.
    """
    await store.set("decision", {"model": "gpt-5", "approved": True})
    value, found = await store.get("decision")
    assert found is True
    assert value == {"model": "gpt-5", "approved": True}

    deleted = await store.delete("decision")
    assert deleted is True
    value, found = await store.get("decision")
    assert found is False
    assert value is None
    assert "sess-sess-123:decision" not in redis.values


@pytest.mark.asyncio
async def test_ttl_and_persistent_tracking(store: KVStore, redis: FakeRedis) -> None:
    """
    Description:
        Verify TTL keys use setex and persistent keys are tracked separately.

    Requirements:
        - This test is needed to prove expiry metadata is stored when a TTL is
          supplied.
        - Verify persistent keys survive cleanup while transient keys do not.

    :param store: Session-scoped KV store fixture.
    :param redis: Fake Redis client fixture.
    """
    await store.set("ephemeral", "value", ttl=10)
    await store.set("keep", "value", persistent=True)

    assert redis.ttls["sess-sess-123:ephemeral"] == 10
    assert "keep" in redis.sets["sess-sess-123:__persistent__"]

    deleted = await store.cleanup_session()
    assert deleted == 1
    value, found = await store.get("keep")
    assert found is True
    assert value == "value"


@pytest.mark.asyncio
async def test_list_keys_sorted_and_namespaced(store: KVStore) -> None:
    """
    Description:
        Verify list_keys returns user-facing names in sorted order.

    Requirements:
        - This test is needed to prove session metadata does not leak into the
          listed keys.
        - Verify prefix filtering only returns matching keys.

    :param store: Session-scoped KV store fixture.
    """
    await store.set("b", 2)
    await store.set("a", 1)
    await store.set("cache:one", 3)

    keys = await store.list_keys()
    assert keys == ["a", "b", "cache:one"]

    prefixed = await store.list_keys(prefix="cache:")
    assert prefixed == ["cache:one"]


@pytest.mark.asyncio
async def test_session_isolation(redis: FakeRedis) -> None:
    """
    Description:
        Verify the same logical key can hold different values per session.

    Requirements:
        - This test is needed to prove keys are isolated by session namespace.
        - Verify two sessions do not see each other’s data.

    :param redis: Fake Redis client fixture.
    """
    left = KVStore(redis, session_id="left")
    right = KVStore(redis, session_id="right")

    await left.set("shared", "left")
    await right.set("shared", "right")

    left_value, left_found = await left.get("shared")
    right_value, right_found = await right.get("shared")
    assert left_found is True and right_found is True
    assert left_value == "left"
    assert right_value == "right"


@pytest.mark.asyncio
async def test_handle_request_dispatch_and_validation(redis: FakeRedis) -> None:
    """
    Description:
        Verify the request dispatcher validates input and routes commands.

    Requirements:
        - This test is needed to prove the MCP command layer enforces session
          IDs and known command names.
        - Verify list and exists requests round-trip through the dispatcher.

    :param redis: Fake Redis client fixture.
    """
    await handle_request(redis, "set", {"session_id": "sess-1", "key": "x", "value": 1})
    payload = await handle_request(redis, "get", {"session_id": "sess-1", "key": "x"})
    assert payload["found"] is True
    assert payload["value"] == 1

    listed = await handle_request(redis, "list", {"session_id": "sess-1"})
    assert listed["count"] == 1
    assert listed["keys"] == ["x"]

    exists = await handle_request(redis, "exists", {"session_id": "sess-1", "key": "x"})
    assert exists["exists"] is True

    with pytest.raises(ValueError, match="session_id"):
        await handle_request(redis, "get", {"key": "x"})

    with pytest.raises(ValueError, match="Unknown"):
        await handle_request(redis, "missing", {"session_id": "sess-1"})


def test_model_validation_rejects_bad_inputs() -> None:
    """
    Description:
        Verify the request models enforce required fields and TTL limits.

    Requirements:
        - This test is needed to prove the dispatcher can rely on model
          validation before touching Redis.
        - Verify invalid TTL and missing keys raise validation errors.
    """
    with pytest.raises(ValidationError):
        KVSetParams(value="x")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        KVSetParams(key="x", value="y", ttl=0)

    assert KVListParams().prefix is None
    assert KVExistsParams(key="x").key == "x"
