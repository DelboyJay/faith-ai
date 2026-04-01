"""FAITH event subscriber, stall detector, and completion batcher."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis

from faith.protocol.events import EventPublisher, EventType, FaithEvent
from faith.utils.redis_client import SYSTEM_EVENTS_CHANNEL

logger = logging.getLogger("faith.protocol.subscriber")

EventHandler = Callable[[FaithEvent], Awaitable[None]]


async def _maybe_await(result: Any) -> None:
    if asyncio.iscoroutine(result):
        await result


class CompletionBatcher:
    """Accumulate completion events until all expected tasks arrive."""

    def __init__(
        self,
        timeout_seconds: float = 600.0,
        immediate_events: set[EventType | str] | None = None,
    ):
        self._timeout_seconds = timeout_seconds
        self._immediate_events = {
            event.value if isinstance(event, EventType) else str(event)
            for event in (immediate_events or set())
        }
        self._pending: dict[str, set[str]] = {}
        self._results: dict[str, list[FaithEvent]] = {}
        self._batch_callback: Callable[[FaithEvent], Any] | None = None
        self._timeout_callback: Callable[[FaithEvent], Any] | None = None
        self._timeout_tasks: dict[str, asyncio.Task] = {}

    def on_batch_ready(self, callback: Callable[[FaithEvent], Any]) -> None:
        self._batch_callback = callback

    def on_batch_timeout(self, callback: Callable[[FaithEvent], Any]) -> None:
        self._timeout_callback = callback

    def expect(self, batch_id: str, task_ids: set[str]) -> None:
        self._pending[batch_id] = set(task_ids)
        self._results[batch_id] = []
        if batch_id in self._timeout_tasks:
            self._timeout_tasks[batch_id].cancel()
        self._timeout_tasks[batch_id] = asyncio.create_task(self._watch_timeout(batch_id))

    async def on_event(self, event: FaithEvent) -> bool:
        if event.event.value in self._immediate_events:
            return False

        task_id = (event.data or {}).get("task_id") or event.source
        for batch_id, pending in self._pending.items():
            if task_id in pending:
                pending.discard(task_id)
                self._results[batch_id].append(event)
                if not pending:
                    await self._fire_batch(batch_id)
                return True
        return False

    async def _fire_batch(self, batch_id: str) -> None:
        task = self._timeout_tasks.pop(batch_id, None)
        if task:
            task.cancel()
        results = self._results.pop(batch_id, [])
        self._pending.pop(batch_id, None)
        if self._batch_callback and results:
            batch_event = FaithEvent(
                event=EventType.BATCH_COMPLETE,
                source="completion_batcher",
                data={
                    "batch_id": batch_id,
                    "results": [event.to_dict() for event in results],
                    "count": len(results),
                },
            )
            await _maybe_await(self._batch_callback(batch_event))

    async def _watch_timeout(self, batch_id: str) -> None:
        try:
            await asyncio.sleep(self._timeout_seconds)
        except asyncio.CancelledError:
            return

        still_pending = self._pending.pop(batch_id, set())
        results = self._results.pop(batch_id, [])
        self._timeout_tasks.pop(batch_id, None)

        if self._timeout_callback:
            timeout_event = FaithEvent(
                event=EventType.BATCH_TIMEOUT,
                source="completion_batcher",
                data={
                    "batch_id": batch_id,
                    "completed_results": [event.to_dict() for event in results],
                    "completed_count": len(results),
                    "still_pending": list(still_pending),
                    "pending_count": len(still_pending),
                },
            )
            await _maybe_await(self._timeout_callback(timeout_event))
        elif self._batch_callback and results:
            batch_event = FaithEvent(
                event=EventType.BATCH_PARTIAL,
                source="completion_batcher",
                data={
                    "batch_id": batch_id,
                    "results": [event.to_dict() for event in results],
                    "count": len(results),
                    "still_pending": list(still_pending),
                },
            )
            await _maybe_await(self._batch_callback(batch_event))

    def cancel(self, batch_id: str) -> None:
        self._pending.pop(batch_id, None)
        self._results.pop(batch_id, None)
        if batch_id in self._timeout_tasks:
            self._timeout_tasks[batch_id].cancel()
            del self._timeout_tasks[batch_id]

    @property
    def active_batches(self) -> list[str]:
        return list(self._pending.keys())


class StallDetector:
    """Detect channel inactivity and heartbeat absence."""

    def __init__(
        self,
        publisher: EventPublisher,
        stall_timeout: float = 300.0,
        heartbeat_interval: float = 30.0,
        missed_heartbeat_limit: int = 3,
        tick_interval: float = 60.0,
    ):
        self.publisher = publisher
        self.stall_timeout = stall_timeout
        self.heartbeat_interval = heartbeat_interval
        self.missed_heartbeat_limit = missed_heartbeat_limit
        self.tick_interval = tick_interval
        self._channel_activity: dict[str, float] = {}
        self._agent_heartbeats: dict[str, float] = {}
        self._stalled_channels: set[str] = set()
        self._errored_agents: set[str] = set()
        self._running = False
        self._task: asyncio.Task | None = None

    def register_channel(self, channel: str) -> None:
        self._channel_activity[channel] = time.monotonic()
        self._stalled_channels.discard(channel)

    def unregister_channel(self, channel: str) -> None:
        self._channel_activity.pop(channel, None)
        self._stalled_channels.discard(channel)

    def register_agent(self, agent_name: str) -> None:
        self._agent_heartbeats[agent_name] = time.monotonic()
        self._errored_agents.discard(agent_name)

    def unregister_agent(self, agent_name: str) -> None:
        self._agent_heartbeats.pop(agent_name, None)
        self._errored_agents.discard(agent_name)

    def record_event(self, event: FaithEvent) -> None:
        now = time.monotonic()
        if event.channel and event.channel in self._channel_activity:
            self._channel_activity[event.channel] = now
            self._stalled_channels.discard(event.channel)
        if event.event == EventType.AGENT_HEARTBEAT and event.source in self._agent_heartbeats:
            self._agent_heartbeats[event.source] = now
            self._errored_agents.discard(event.source)
        if event.event in (EventType.TOOL_CALL_STARTED, EventType.TOOL_CALL_COMPLETE):
            channel = event.channel or (event.data or {}).get("channel")
            if channel and channel in self._channel_activity:
                self._channel_activity[channel] = now
                self._stalled_channels.discard(channel)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop(), name="stall-detector")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _tick_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self.tick_interval)
                if not self._running:
                    break
                await self._check_channels()
                await self._check_heartbeats()
        except asyncio.CancelledError:
            pass

    async def _check_channels(self) -> None:
        now = time.monotonic()
        for channel, last_active in list(self._channel_activity.items()):
            elapsed = now - last_active
            if elapsed >= self.stall_timeout and channel not in self._stalled_channels:
                self._stalled_channels.add(channel)
                await self.publisher.publish(
                    FaithEvent(
                        event=EventType.CHANNEL_STALLED,
                        source="pa",
                        channel=channel,
                        data={
                            "idle_seconds": round(elapsed),
                            "threshold_seconds": round(self.stall_timeout),
                        },
                    )
                )

    async def _check_heartbeats(self) -> None:
        now = time.monotonic()
        max_absence = self.heartbeat_interval * self.missed_heartbeat_limit
        for agent, last_beat in list(self._agent_heartbeats.items()):
            elapsed = now - last_beat
            if elapsed >= max_absence and agent not in self._errored_agents:
                self._errored_agents.add(agent)
                missed_count = int(elapsed // self.heartbeat_interval)
                await self.publisher.publish(
                    FaithEvent(
                        event=EventType.AGENT_ERROR,
                        source=agent,
                        data={
                            "error": "heartbeat_absence",
                            "missed_heartbeats": missed_count,
                            "elapsed_seconds": round(elapsed),
                            "threshold_seconds": round(max_absence),
                        },
                    )
                )


class EventSubscriber:
    """Listen on system-events and dispatch to registered handlers."""

    def __init__(
        self,
        redis: aioredis.Redis,
        stall_detector: StallDetector | None = None,
        completion_batcher: CompletionBatcher | None = None,
    ):
        self.redis = redis
        self.stall_detector = stall_detector
        self.completion_batcher = completion_batcher
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._wildcard_handlers: list[EventHandler] = []
        self._running = False
        self._task: asyncio.Task | None = None

    def on(self, event_type: EventType, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def on_all(self, handler: EventHandler) -> None:
        self._wildcard_handlers.append(handler)

    def remove(self, event_type: EventType, handler: EventHandler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self.stall_detector:
            await self.stall_detector.start()
        self._task = asyncio.create_task(self._listen(), name="event-subscriber")

    async def stop(self) -> None:
        self._running = False
        if self.stall_detector:
            await self.stall_detector.stop()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _listen(self) -> None:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(SYSTEM_EVENTS_CHANNEL)
        try:
            while self._running:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message or message.get("type") != "message":
                    continue
                raw = message.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    event = FaithEvent.from_json(raw)
                except Exception:
                    logger.exception("Failed to parse event payload")
                    continue
                if self.stall_detector:
                    self.stall_detector.record_event(event)
                if self.completion_batcher and await self.completion_batcher.on_event(event):
                    continue
                await self._dispatch(event)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await pubsub.unsubscribe(SYSTEM_EVENTS_CHANNEL)
            finally:
                close = getattr(pubsub, "aclose", None)
                if close is not None:
                    await close()
                else:
                    await pubsub.close()

    async def _dispatch(self, event: FaithEvent) -> None:
        handlers = list(self._handlers.get(event.event, []))
        handlers.extend(self._wildcard_handlers)
        if not handlers:
            return

        async def _safe_call(handler: EventHandler) -> None:
            try:
                await handler(event)
            except Exception:
                logger.exception("Handler failed for %s", event.event.value)

        await asyncio.gather(*[_safe_call(handler) for handler in handlers])
