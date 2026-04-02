"""Description:
    Coordinate PA event subscription, batching, stall detection, and intervention recording.

Requirements:
    - Wrap the lower-level event subscriber with PA-specific registration helpers.
    - Track recent intervention results for diagnostics and UI display.
    - Support typed and wildcard event handlers.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from faith_shared.protocol.events import EventPublisher, EventType, FaithEvent
from faith_shared.protocol.subscriber import CompletionBatcher, EventSubscriber, StallDetector

PAHandler = Callable[[FaithEvent], Awaitable[dict[str, Any] | None]]


class PAEventDispatcher:
    """Description:
        Own the PA event-subscriber stack and dispatch runtime events to registered handlers.

    Requirements:
        - Configure stall detection and completion batching for the PA runtime.
        - Support typed handlers and wildcard handlers.
        - Record structured intervention results for later inspection.

    :param redis_client: Redis client used by the underlying subscriber.
    :param event_publisher: Optional PA-scoped event publisher.
    :param stall_timeout: Timeout in seconds before a channel is considered stalled.
    :param heartbeat_interval: Expected agent heartbeat interval in seconds.
    :param missed_heartbeat_limit: Number of missed heartbeats tolerated before escalation.
    :param batch_timeout_seconds: Timeout used by the completion batcher.
    :param recent_interventions_limit: Maximum number of intervention records to retain.
    """

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
        """Description:
            Initialise the dispatcher and its lower-level event components.

        Requirements:
            - Create a PA-scoped publisher when one is not supplied.
            - Wire batch-ready and batch-timeout hooks into the dispatcher.

        :param redis_client: Redis client used by the underlying subscriber.
        :param event_publisher: Optional PA-scoped event publisher.
        :param stall_timeout: Timeout in seconds before a channel is considered stalled.
        :param heartbeat_interval: Expected agent heartbeat interval in seconds.
        :param missed_heartbeat_limit: Number of missed heartbeats tolerated before escalation.
        :param batch_timeout_seconds: Timeout used by the completion batcher.
        :param recent_interventions_limit: Maximum number of intervention records to retain.
        """

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
        """Description:
            Register one handler for a specific event type.

        Requirements:
            - Preserve handler registration order for the event type.

        :param event_type: Event type to subscribe to.
        :param handler: Async handler to invoke for matching events.
        """

        self._handlers[event_type].append(handler)

    def unregister(self, event_type: EventType, handler: PAHandler) -> None:
        """Description:
            Unregister one handler for a specific event type.

        Requirements:
            - Remove the handler only when it is currently registered.

        :param event_type: Event type to unsubscribe from.
        :param handler: Handler to remove.
        """

        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def register_wildcard(self, handler: PAHandler) -> None:
        """Description:
            Register one wildcard handler for every event type.

        Requirements:
            - Preserve wildcard registration order.

        :param handler: Async handler to invoke for all dispatched events.
        """

        self._wildcard_handlers.append(handler)

    def unregister_wildcard(self, handler: PAHandler) -> None:
        """Description:
            Remove one wildcard handler.

        Requirements:
            - Remove the handler only when it is currently registered.

        :param handler: Wildcard handler to remove.
        """

        if handler in self._wildcard_handlers:
            self._wildcard_handlers.remove(handler)

    def register_channel(self, channel: str) -> None:
        """Description:
            Register a channel with the stall detector.

        Requirements:
            - Forward the channel registration to the underlying stall detector.

        :param channel: Channel name to register.
        """

        self.stall_detector.register_channel(channel)

    def unregister_channel(self, channel: str) -> None:
        """Description:
            Unregister a channel from the stall detector.

        Requirements:
            - Forward the channel removal to the underlying stall detector.

        :param channel: Channel name to unregister.
        """

        self.stall_detector.unregister_channel(channel)

    def register_agent(self, agent_id: str) -> None:
        """Description:
            Register an agent with the heartbeat stall detector.

        Requirements:
            - Forward the agent registration to the underlying stall detector.

        :param agent_id: Agent identifier to register.
        """

        self.stall_detector.register_agent(agent_id)

    def unregister_agent(self, agent_id: str) -> None:
        """Description:
            Unregister an agent from the heartbeat stall detector.

        Requirements:
            - Forward the agent removal to the underlying stall detector.

        :param agent_id: Agent identifier to unregister.
        """

        self.stall_detector.unregister_agent(agent_id)

    def expect_completion_batch(self, batch_id: str, task_ids: set[str]) -> None:
        """Description:
            Register an expected completion batch with the batcher.

        Requirements:
            - Forward the expected task identifiers to the underlying completion batcher.

        :param batch_id: Completion batch identifier.
        :param task_ids: Task identifiers expected in the batch.
        """

        self.completion_batcher.expect(batch_id, task_ids)

    async def start(self) -> None:
        """Description:
            Start the PA event subscriber.

        Requirements:
            - Register the dispatcher callback with the subscriber before starting it.
        """

        self.subscriber.on_all(self._dispatch)
        await self.subscriber.start()

    async def stop(self) -> None:
        """Description:
            Stop the PA event subscriber.

        Requirements:
            - Delegate shutdown to the underlying subscriber.
        """

        await self.subscriber.stop()

    async def _dispatch(self, event: FaithEvent) -> None:
        """Description:
            Dispatch one runtime event to the registered handlers.

        Requirements:
            - Run typed handlers before wildcard handlers.
            - Catch handler failures and record them as intervention payloads.
            - Append non-empty handler results to the recent intervention log.

        :param event: Event payload to dispatch.
        """

        handlers = list(self._handlers.get(event.event, []))
        handlers.extend(self._wildcard_handlers)
        if not handlers:
            return

        async def _safe_call(handler: PAHandler) -> dict[str, Any] | None:
            """Description:
                Invoke one event handler and convert unexpected failures into result payloads.

            Requirements:
                - Return a structured error payload instead of raising from dispatch.

            :param handler: Registered event handler to invoke.
            :returns: Handler result payload or structured error payload.
            """

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
