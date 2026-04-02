"""Description:
    Subscribe to system events, detect stalled channels, and batch completion notifications.

Requirements:
    - Support wildcard and typed event handlers.
    - Detect channel stalls and missing agent heartbeats.
    - Batch completion events until all expected task results arrive or timeout fires.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis

from faith_pa.utils.redis_client import SYSTEM_EVENTS_CHANNEL
from faith_shared.protocol.events import EventPublisher, EventType, FaithEvent

logger = logging.getLogger("faith.protocol.subscriber")

EventHandler = Callable[[FaithEvent], Awaitable[None]]


async def _maybe_await(result: Any) -> None:
    """Description:
        Await coroutine results while allowing synchronous callbacks.

    Requirements:
        - Await the supplied value only when it is a coroutine object.

    :param result: Value or coroutine to normalise.
    """

    if asyncio.iscoroutine(result):
        await result


class CompletionBatcher:
    """Description:
        Accumulate completion events until all expected task results arrive.

    Requirements:
        - Track pending task identifiers per batch.
        - Emit either a complete or partial batch event when the batch resolves.
        - Allow selected event types to bypass batching immediately.

    :param timeout_seconds: Maximum time to wait before timing out a batch.
    :param immediate_events: Event types that should bypass batching.
    """

    def __init__(
        self,
        timeout_seconds: float = 600.0,
        immediate_events: set[EventType | str] | None = None,
    ):
        """Description:
            Initialise the completion batcher.

        Requirements:
            - Normalise immediate event types to their string values.
            - Start with empty batch state and callback registrations.

        :param timeout_seconds: Maximum time to wait before timing out a batch.
        :param immediate_events: Event types that should bypass batching.
        """

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
        """Description:
            Register the callback used when a batch completes successfully.

        Requirements:
            - Replace any previously registered batch-ready callback.

        :param callback: Callback invoked with the batch-complete event.
        """

        self._batch_callback = callback

    def on_batch_timeout(self, callback: Callable[[FaithEvent], Any]) -> None:
        """Description:
            Register the callback used when a batch times out.

        Requirements:
            - Replace any previously registered batch-timeout callback.

        :param callback: Callback invoked with the batch-timeout event.
        """

        self._timeout_callback = callback

    def expect(self, batch_id: str, task_ids: set[str]) -> None:
        """Description:
            Register the expected task identifiers for one batch.

        Requirements:
            - Replace any existing timeout task for the batch.
            - Start a fresh timeout watcher for the batch.

        :param batch_id: Batch identifier.
        :param task_ids: Task identifiers expected in the batch.
        """

        self._pending[batch_id] = set(task_ids)
        self._results[batch_id] = []
        if batch_id in self._timeout_tasks:
            self._timeout_tasks[batch_id].cancel()
        self._timeout_tasks[batch_id] = asyncio.create_task(self._watch_timeout(batch_id))

    async def on_event(self, event: FaithEvent) -> bool:
        """Description:
            Offer one event to the batcher and return whether it was consumed.

        Requirements:
            - Ignore events configured as immediate.
            - Remove matching task identifiers from the pending batch set.
            - Fire the batch-ready callback once a batch becomes complete.

        :param event: Event to process.
        :returns: ``True`` when the event was consumed by a batch.
        """

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
        """Description:
            Emit the completed batch event for one batch identifier.

        Requirements:
            - Cancel and remove the timeout task for the batch.
            - Clear the pending and result state after emission.

        :param batch_id: Batch identifier to emit.
        """

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
        """Description:
            Wait for one batch timeout and emit the appropriate timeout or partial-batch event.

        Requirements:
            - Exit quietly when the timeout task is cancelled.
            - Prefer the timeout callback when one is registered.
            - Fall back to the batch callback with a partial-batch event otherwise.

        :param batch_id: Batch identifier being watched.
        """

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
        """Description:
            Cancel one active batch and discard its pending state.

        Requirements:
            - Cancel the timeout task when one exists.

        :param batch_id: Batch identifier to cancel.
        """

        self._pending.pop(batch_id, None)
        self._results.pop(batch_id, None)
        if batch_id in self._timeout_tasks:
            self._timeout_tasks[batch_id].cancel()
            del self._timeout_tasks[batch_id]

    @property
    def active_batches(self) -> list[str]:
        """Description:
            Return the currently active batch identifiers.

        Requirements:
            - Reflect the keys of the pending-batch mapping.

        :returns: Active batch identifiers.
        """

        return list(self._pending.keys())


class StallDetector:
    """Description:
        Detect stalled channels and missing agent heartbeats.

    Requirements:
        - Track recent activity timestamps for channels and agents.
        - Publish stall and heartbeat-absence events only once per incident.

    :param publisher: Event publisher used for detector events.
    :param stall_timeout: Channel stall timeout in seconds.
    :param heartbeat_interval: Expected heartbeat interval in seconds.
    :param missed_heartbeat_limit: Number of missed heartbeats tolerated.
    :param tick_interval: Poll interval for detector checks.
    """

    def __init__(
        self,
        publisher: EventPublisher,
        stall_timeout: float = 300.0,
        heartbeat_interval: float = 30.0,
        missed_heartbeat_limit: int = 3,
        tick_interval: float = 60.0,
    ):
        """Description:
            Initialise the stall detector.

        Requirements:
            - Start with empty channel and agent tracking state.

        :param publisher: Event publisher used for detector events.
        :param stall_timeout: Channel stall timeout in seconds.
        :param heartbeat_interval: Expected heartbeat interval in seconds.
        :param missed_heartbeat_limit: Number of missed heartbeats tolerated.
        :param tick_interval: Poll interval for detector checks.
        """

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
        """Description:
            Start tracking activity for one channel.

        Requirements:
            - Reset the stalled marker for the registered channel.

        :param channel: Channel name to track.
        """

        self._channel_activity[channel] = time.monotonic()
        self._stalled_channels.discard(channel)

    def unregister_channel(self, channel: str) -> None:
        """Description:
            Stop tracking activity for one channel.

        Requirements:
            - Remove the channel from both activity and stalled sets.

        :param channel: Channel name to stop tracking.
        """

        self._channel_activity.pop(channel, None)
        self._stalled_channels.discard(channel)

    def register_agent(self, agent_name: str) -> None:
        """Description:
            Start tracking heartbeats for one agent.

        Requirements:
            - Reset the errored marker for the agent.

        :param agent_name: Agent identifier to track.
        """

        self._agent_heartbeats[agent_name] = time.monotonic()
        self._errored_agents.discard(agent_name)

    def unregister_agent(self, agent_name: str) -> None:
        """Description:
            Stop tracking heartbeats for one agent.

        Requirements:
            - Remove the agent from both heartbeat and errored sets.

        :param agent_name: Agent identifier to stop tracking.
        """

        self._agent_heartbeats.pop(agent_name, None)
        self._errored_agents.discard(agent_name)

    def record_event(self, event: FaithEvent) -> None:
        """Description:
            Update channel and agent activity from one observed event.

        Requirements:
            - Refresh channel activity for channel events and tool lifecycle events.
            - Refresh heartbeat timestamps for explicit agent-heartbeat events.

        :param event: Observed event payload.
        """

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
        """Description:
            Start the periodic detector loop.

        Requirements:
            - Avoid creating duplicate detector tasks.
        """

        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop(), name="stall-detector")

    async def stop(self) -> None:
        """Description:
            Stop the periodic detector loop.

        Requirements:
            - Cancel and await the active detector task when present.
        """

        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _tick_loop(self) -> None:
        """Description:
            Run the periodic channel-stall and heartbeat checks.

        Requirements:
            - Sleep between checks using the configured tick interval.
            - Exit quietly on cancellation.
        """

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
        """Description:
            Publish stall events for channels that have exceeded the inactivity threshold.

        Requirements:
            - Publish each stall incident only once until activity resumes.
        """

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
        """Description:
            Publish agent-error events for agents missing too many heartbeats.

        Requirements:
            - Publish each heartbeat-absence incident only once until a heartbeat resumes.
        """

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
    """Description:
        Listen to the system-events channel and dispatch parsed events to registered handlers.

    Requirements:
        - Support typed handlers and wildcard handlers.
        - Update the stall detector with each parsed event.
        - Allow the completion batcher to consume events before normal dispatch.

    :param redis: Async Redis client used for pubsub.
    :param stall_detector: Optional stall detector.
    :param completion_batcher: Optional completion batcher.
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        stall_detector: StallDetector | None = None,
        completion_batcher: CompletionBatcher | None = None,
    ):
        """Description:
            Initialise the event subscriber.

        Requirements:
            - Start with no registered handlers and no active listener task.

        :param redis: Async Redis client used for pubsub.
        :param stall_detector: Optional stall detector.
        :param completion_batcher: Optional completion batcher.
        """

        self.redis = redis
        self.stall_detector = stall_detector
        self.completion_batcher = completion_batcher
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._wildcard_handlers: list[EventHandler] = []
        self._running = False
        self._task: asyncio.Task | None = None

    def on(self, event_type: EventType, handler: EventHandler) -> None:
        """Description:
            Register one typed event handler.

        Requirements:
            - Preserve registration order for the event type.

        :param event_type: Event type to subscribe to.
        :param handler: Async handler to invoke for matching events.
        """

        self._handlers[event_type].append(handler)

    def on_all(self, handler: EventHandler) -> None:
        """Description:
            Register one wildcard handler.

        Requirements:
            - Invoke the handler for every dispatched event.

        :param handler: Async handler to invoke for all events.
        """

        self._wildcard_handlers.append(handler)

    def remove(self, event_type: EventType, handler: EventHandler) -> None:
        """Description:
            Remove one typed event handler.

        Requirements:
            - Remove the handler only when it is currently registered.

        :param event_type: Event type to unsubscribe from.
        :param handler: Handler to remove.
        """

        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def start(self) -> None:
        """Description:
            Start listening on the system-events channel.

        Requirements:
            - Start the stall detector first when one is configured.
            - Avoid creating duplicate listener tasks.
        """

        if self._running:
            return
        self._running = True
        if self.stall_detector:
            await self.stall_detector.start()
        self._task = asyncio.create_task(self._listen(), name="event-subscriber")

    async def stop(self) -> None:
        """Description:
            Stop listening on the system-events channel.

        Requirements:
            - Stop the stall detector when one is configured.
            - Cancel and await the listener task when present.
        """

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
        """Description:
            Consume Redis pubsub messages, parse them into events, and dispatch them.

        Requirements:
            - Subscribe to the shared system-events channel.
            - Skip non-message pubsub frames.
            - Log and continue when event parsing fails.
            - Close the pubsub object cleanly on shutdown.
        """

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
        """Description:
            Dispatch one parsed event to the registered handlers.

        Requirements:
            - Invoke typed handlers before wildcard handlers.
            - Catch handler failures and continue dispatching the remaining handlers.

        :param event: Parsed event payload.
        """

        handlers = list(self._handlers.get(event.event, []))
        handlers.extend(self._wildcard_handlers)
        if not handlers:
            return

        async def _safe_call(handler: EventHandler) -> None:
            """Description:
                Invoke one event handler safely.

            Requirements:
                - Log failures without aborting dispatch of other handlers.

            :param handler: Handler to invoke.
            """

            try:
                await handler(event)
            except Exception:
                logger.exception("Handler failed for %s", event.event.value)

        await asyncio.gather(*[_safe_call(handler) for handler in handlers])
