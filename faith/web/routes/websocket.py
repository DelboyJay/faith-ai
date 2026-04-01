"""WebSocket routes for Redis-backed browser updates."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from faith.utils.redis_client import SYSTEM_EVENTS_CHANNEL
from faith.web.app import APPROVAL_EVENTS_CHANNEL

router = APIRouter()


def _get_redis_pool():
    import faith.web.app as web_app_module

    return web_app_module.redis_pool


async def _receive_until_disconnect(websocket: WebSocket) -> None:
    while True:
        await websocket.receive_text()


async def _forward_pubsub_messages(websocket: WebSocket, pubsub: Any) -> None:
    while True:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message is None:
            await asyncio.sleep(0.01)
            continue
        if message.get("type") != "message":
            continue

        raw = message.get("data")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        elif not isinstance(raw, str):
            raw = json.dumps(raw)
        await websocket.send_text(raw)


async def _redis_to_ws_bridge(websocket: WebSocket, redis_channel: str) -> None:
    redis = _get_redis_pool()
    await websocket.accept()
    if redis is None:
        await websocket.close(code=1011, reason="Redis not available")
        return

    pubsub = redis.pubsub()
    await pubsub.subscribe(redis_channel)
    forwarder = asyncio.create_task(_forward_pubsub_messages(websocket, pubsub))
    receiver = asyncio.create_task(_receive_until_disconnect(websocket))

    try:
        done, pending = await asyncio.wait(
            {forwarder, receiver},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            with suppress(WebSocketDisconnect, asyncio.CancelledError):
                await task
        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task
    finally:
        with suppress(Exception):
            await pubsub.unsubscribe(redis_channel)
        close = getattr(pubsub, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                with suppress(Exception):
                    await result


@router.websocket("/ws/agent/{agent_id}")
async def agent_output(websocket: WebSocket, agent_id: str) -> None:
    await _redis_to_ws_bridge(websocket, f"agent:{agent_id}:output")


@router.websocket("/ws/tool/{tool_id}")
async def tool_output(websocket: WebSocket, tool_id: str) -> None:
    await _redis_to_ws_bridge(websocket, f"tool:{tool_id}:output")


@router.websocket("/ws/approvals")
async def approvals(websocket: WebSocket) -> None:
    await _redis_to_ws_bridge(websocket, APPROVAL_EVENTS_CHANNEL)


@router.websocket("/ws/status")
async def status(websocket: WebSocket) -> None:
    await _redis_to_ws_bridge(websocket, SYSTEM_EVENTS_CHANNEL)
