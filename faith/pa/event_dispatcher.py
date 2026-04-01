"""Main PA event dispatch integration."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from faith.protocol.events import EventPublisher, EventType, FaithEvent
from faith.protocol.subscriber import CompletionBatcher, EventSubscriber, StallDetector

PAHandler = Callable[[FaithEvent], Awaitable[dict[str, Any] | None]]


class PAEventDispatcher:
    """PA-owned wrapper around the lower-level event subscriber."""

    def __init__(
        self,
        redis_client: Any,
        *,
        event_publisher: EventPublisher | None = None,
        stall_timeout: float = 300.0,
        heartbeat_interval: float = 30.0,
        missed_heartbeat_limit: int = 3,
        batch_timeout_seconds: float = 600.0,
        recent_interventions_limit: int = 50,
    ) -> None:
        self.event_publisher = event_publisher or EventPublisher(redis_client, source="pa")
        self.stall_detector = StallDetector(
            publisher=self.event_publisher,
            stall_timeout=stall_timeout,
            heartbeat_interval=heartbeat_interval,
            missed_heartbeat_limit=missed_heartbeat_limit,
            tick_interval=min(1.0, stall_timeout),
        )
        self.completion_batcher = CompletionBatcher(
            timeout_seconds=batch_timeout_seconds,
            immediate_events={
                EventType.AGENT_ERROR,
                EventType.CHANNEL_STALLED,
                EventType.CHANNEL_LOOP_DETECTED,
                EventType.APPROVAL_REQUESTED,
            },
        )
        self.subscriber = EventSubscriber(
            redis_client,
            stall_detector=self.stall_detector,
            completion_batcher=self.completion_batcher,
        )
        self._handlers: dict[EventType, list[PAHandler]] = defaultdict(list)
        self._wildcard_handlers: list[PAHandler] = []
        self.recent_interventions: deque[dict[str, Any]] = deque(maxlen=recent_interventions_limit)
        self.completion_batcher.on_batch_ready(self._dispatch)
        self.completion_batcher.on_batch_timeout(self._dispatch)

    def register(self, event_type: EventType, handler: PAHandler) -> None:
        self._handlers[event_type].append(handler)

    def unregister(self, event_type: EventType, handler: PAHandler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def register_wildcard(self, handler: PAHandler) -> None:
        self._wildcard_handlers.append(handler)

    def unregister_wildcard(self, handler: PAHandler) -> None:
        if handler in self._wildcard_handlers:
            self._wildcard_handlers.remove(handler)

    def register_channel(self, channel: str) -> None:
        self.stall_detector.register_channel(channel)

    def unregister_channel(self, channel: str) -> None:
        self.stall_detector.unregister_channel(channel)

    def register_agent(self, agent_id: str) -> None:
        self.stall_detector.register_agent(agent_id)

    def unregister_agent(self, agent_id: str) -> None:
        self.stall_detector.unregister_agent(agent_id)

    def expect_completion_batch(self, batch_id: str, task_ids: set[str]) -> None:
        self.completion_batcher.expect(batch_id, task_ids)

    async def start(self) -> None:
        self.subscriber.on_all(self._dispatch)
        await self.subscriber.start()

    async def stop(self) -> None:
        await self.subscriber.stop()

    async def _dispatch(self, event: FaithEvent) -> None:
        handlers = list(self._handlers.get(event.event, []))
        handlers.extend(self._wildcard_handlers)
        if not handlers:
            return

        async def _safe_call(handler: PAHandler) -> dict[str, Any] | None:
            try:
                return await handler(event)
            except Exception as exc:  # pragma: no cover - defensive
                return {"error": str(exc)}

        results = await asyncio.gather(*[_safe_call(handler) for handler in handlers])
        for result in results:
            if result is None:
                continue
            payload = {
                "event": event.event.value,
                "source": event.source,
                "channel": event.channel,
                "result": result,
            }
            if isinstance(result, dict):
                payload.update(result)
            self.recent_interventions.append(payload)
