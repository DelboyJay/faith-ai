"""
Description:
    Provide the request dispatcher for the KV store tool.

Requirements:
    - Validate inputs through the Pydantic request models before touching Redis.
    - Keep the per-session store cache small and easy to clear on session end.
"""

from __future__ import annotations

from typing import Any

from faith_mcp.kv_store.models import (
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
from faith_mcp.kv_store.store import KVStore

TOOL_MANIFEST = {
    "name": "kv_store",
    "description": "Session-scoped key-value store for shared facts and cached decisions.",
    "commands": {
        "set": {"description": "Store a value.", "parameters": KVSetParams.model_json_schema()},
        "get": {"description": "Retrieve a value.", "parameters": KVGetParams.model_json_schema()},
        "delete": {
            "description": "Delete a value.",
            "parameters": KVDeleteParams.model_json_schema(),
        },
        "list": {"description": "List keys.", "parameters": KVListParams.model_json_schema()},
        "exists": {
            "description": "Check key existence.",
            "parameters": KVExistsParams.model_json_schema(),
        },
    },
}

_store_cache: dict[str, KVStore] = {}


def _get_store(redis_client: Any, session_id: str) -> KVStore:
    """Return a cached store for one session."""
    store = _store_cache.get(session_id)
    if store is None:
        store = KVStore(redis_client, session_id)
        _store_cache[session_id] = store
    return store


async def handle_request(redis_client: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Dispatch one KV store command.

    :param redis_client: Async Redis client or test double.
    :param method: Command name to execute.
    :param params: Request parameters, including `session_id`.
    :returns: JSON-safe response payload.
    :raises ValueError: If the session identifier or method is invalid.
    """
    data = dict(params)
    session_id = data.pop("session_id", None)
    if not session_id:
        raise ValueError("session_id is required in params")

    store = _get_store(redis_client, session_id)

    if method == "set":
        payload = KVSetParams(**data)
        await store.set(payload.key, payload.value, ttl=payload.ttl, persistent=payload.persistent)
        return KVSetResult(
            key=payload.key, ttl=payload.ttl, persistent=payload.persistent
        ).model_dump()

    if method == "get":
        payload = KVGetParams(**data)
        value, found = await store.get(payload.key)
        return KVGetResult(key=payload.key, value=value, found=found).model_dump()

    if method == "delete":
        payload = KVDeleteParams(**data)
        deleted = await store.delete(payload.key)
        return KVDeleteResult(key=payload.key, deleted=deleted).model_dump()

    if method == "list":
        payload = KVListParams(**data)
        keys = await store.list_keys(prefix=payload.prefix)
        return KVListResult(keys=keys, count=len(keys)).model_dump()

    if method == "exists":
        payload = KVExistsParams(**data)
        exists = await store.exists(payload.key)
        return KVExistsResult(key=payload.key, exists=exists).model_dump()

    raise ValueError(f"Unknown kv_store method: {method}")


async def cleanup_session(redis_client: Any, session_id: str) -> int:
    """
    Clean up one session's transient keys.

    :param redis_client: Async Redis client or test double.
    :param session_id: Session to clean up.
    :returns: Number of deleted keys.
    """
    store = _get_store(redis_client, session_id)
    deleted = await store.cleanup_session()
    _store_cache.pop(session_id, None)
    return deleted
