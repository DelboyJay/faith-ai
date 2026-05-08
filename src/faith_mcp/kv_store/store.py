"""
Description:
    Provide a Redis-backed session-scoped key-value store.

Requirements:
    - Namespace all keys per session to prevent cross-session leakage.
    - Track persistent keys separately so session cleanup can preserve them.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("faith_mcp.kv_store.store")

_PERSISTENT_SUFFIX = "__persistent__"


class KVStore:
    """Wrap a Redis client with session-scoped key operations."""

    def __init__(self, redis_client: Any, session_id: str) -> None:
        """
        Create a store bound to one session.

        :param redis_client: Async Redis client or a test double.
        :param session_id: Session identifier used for namespacing.
        """
        self.redis = redis_client
        self.session_id = session_id

    def _full_key(self, key: str) -> str:
        """Return the namespaced Redis key."""
        return f"sess-{self.session_id}:{key}"

    def _persistent_key(self) -> str:
        """Return the Redis key used to track persistent session keys."""
        return f"sess-{self.session_id}:{_PERSISTENT_SUFFIX}"

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ttl: int | None = None,
        persistent: bool = False,
    ) -> None:
        """
        Store one value in Redis.

        :param key: User-facing key name.
        :param value: JSON-serialisable value to store.
        :param ttl: Optional time-to-live in seconds.
        :param persistent: Whether the key survives session cleanup.
        """
        full_key = self._full_key(key)
        payload = json.dumps(value)
        if ttl is None:
            await self.redis.set(full_key, payload)
        else:
            await self.redis.setex(full_key, ttl, payload)
        if persistent:
            await self.redis.sadd(self._persistent_key(), key)
        else:
            await self.redis.srem(self._persistent_key(), key)
        logger.debug("set key=%s session=%s persistent=%s", key, self.session_id, persistent)

    async def get(self, key: str) -> tuple[Any, bool]:
        """
        Retrieve one value from Redis.

        :param key: User-facing key name.
        :returns: Tuple of value and found flag.
        """
        raw = await self.redis.get(self._full_key(key))
        if raw is None:
            return None, False
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw), True

    async def delete(self, key: str) -> bool:
        """
        Remove one key from Redis.

        :param key: User-facing key name.
        :returns: `True` when the key existed.
        """
        deleted = bool(await self.redis.delete(self._full_key(key)))
        await self.redis.srem(self._persistent_key(), key)
        return deleted

    async def list_keys(self, prefix: str | None = None) -> list[str]:
        """
        List session keys with optional prefix filtering.

        :param prefix: Optional prefix filter.
        :returns: Sorted list of user-facing keys.
        """
        match = f"sess-{self.session_id}:{prefix or ''}*"
        keys: list[str] = []
        async for raw_key in self.redis.scan_iter(match=match, count=100):
            if isinstance(raw_key, bytes):
                raw_key = raw_key.decode("utf-8")
            user_key = raw_key.split(":", 1)[1]
            if user_key == _PERSISTENT_SUFFIX:
                continue
            keys.append(user_key)
        return sorted(keys)

    async def exists(self, key: str) -> bool:
        """
        Check whether one key exists.

        :param key: User-facing key name.
        :returns: `True` when the key exists.
        """
        return bool(await self.redis.exists(self._full_key(key)))

    async def cleanup_session(self) -> int:
        """
        Remove all non-persistent keys from the session.

        :returns: Number of deleted keys.
        """
        persistent_members = await self.redis.smembers(self._persistent_key())
        persistent_keys: set[str] = set()
        for member in persistent_members:
            if isinstance(member, bytes):
                member = member.decode("utf-8")
            persistent_keys.add(member)

        deleted = 0
        match = f"sess-{self.session_id}:*"
        async for raw_key in self.redis.scan_iter(match=match, count=100):
            if isinstance(raw_key, bytes):
                raw_key = raw_key.decode("utf-8")
            user_key = raw_key.split(":", 1)[1]
            if user_key == _PERSISTENT_SUFFIX or user_key in persistent_keys:
                continue
            deleted += int(bool(await self.redis.delete(raw_key)))
        await self.redis.delete(self._persistent_key())
        return deleted
