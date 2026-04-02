"""Description:
    Provide Redis-backed WebSocket routes for browser updates.

Requirements:
    - Bridge Redis pub/sub feeds into WebSocket clients.
    - Keep browser status streams isolated by channel and endpoint purpose.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from faith_pa.utils.redis_client import SYSTEM_EVENTS_CHANNEL
from faith_web.app import APPROVAL_EVENTS_CHANNEL

router = APIRouter()


def _get_redis_pool() -> Any:
    """Description:
        Return the shared Redis client used by the Web UI process.

    Requirements:
        - Resolve the Redis pool lazily so tests can replace it safely.

    :returns: Shared Redis client or ``None`` when not configured.
    """

    import faith_web.app as web_app_module

    return web_app_module.redis_pool


async def _receive_until_disconnect(websocket: WebSocket) -> None:
    """Description:
        Consume browser messages until the WebSocket disconnects.

    Requirements:
        - Keep the receive task alive so the bridge can detect disconnects promptly.

    :param websocket: Connected browser WebSocket.
    """

    while True:
        await websocket.receive_text()


async def _forward_pubsub_messages(websocket: WebSocket, pubsub: Any) -> None:
    """Description:
        Forward Redis pub/sub messages into one connected WebSocket.

    Requirements:
        - Ignore subscribe bookkeeping messages.
        - Normalise non-string payloads into JSON text before forwarding.

    :param websocket: Connected browser WebSocket.
    :param pubsub: Redis pub/sub object subscribed to one channel.
    """

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
    """Description:
        Bridge one Redis channel into one WebSocket connection.

    Requirements:
        - Close the WebSocket with an error code when Redis is unavailable.
        - Unsubscribe and close pubsub resources during teardown.
        - Stop cleanly when either the receive task or forward task completes first.

    :param websocket: Connected browser WebSocket.
    :param redis_channel: Redis channel name to subscribe to.
    """

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
    """Description:
        Stream one agent output feed to the browser.

    Requirements:
        - Subscribe to the agent-specific Redis output channel.

    :param websocket: Connected browser WebSocket.
    :param agent_id: Agent identifier whose output should be streamed.
    """

    await _redis_to_ws_bridge(websocket, f"agent:{agent_id}:output")


@router.websocket("/ws/tool/{tool_id}")
async def tool_output(websocket: WebSocket, tool_id: str) -> None:
    """Description:
        Stream one tool output feed to the browser.

    Requirements:
        - Subscribe to the tool-specific Redis output channel.

    :param websocket: Connected browser WebSocket.
    :param tool_id: Tool identifier whose output should be streamed.
    """

    await _redis_to_ws_bridge(websocket, f"tool:{tool_id}:output")


@router.websocket("/ws/approvals")
async def approvals(websocket: WebSocket) -> None:
    """Description:
        Stream approval events to the browser.

    Requirements:
        - Subscribe to the shared approval-events Redis channel.

    :param websocket: Connected browser WebSocket.
    """

    await _redis_to_ws_bridge(websocket, APPROVAL_EVENTS_CHANNEL)


@router.websocket("/ws/status")
async def status(websocket: WebSocket) -> None:
    """Description:
        Stream shared system-status events to the browser.

    Requirements:
        - Subscribe to the shared system-events Redis channel.

    :param websocket: Connected browser WebSocket.
    """

    await _redis_to_ws_bridge(websocket, SYSTEM_EVENTS_CHANNEL)
