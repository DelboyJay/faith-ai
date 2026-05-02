# FAITH-016 — PA Event Dispatcher & Intervention Logic

**Phase:** 4 — PA Core
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-015, FAITH-009
**FRS Reference:** Section 3.2, 3.7.5, 3.7.6, 3.7.8

---

## Objective

Wire the PA's main event loop by subscribing to `system-events` via the `EventSubscriber` (FAITH-009) and dispatching events to a registry of typed handlers. Implement event batching via `CompletionBatcher` (FAITH-009): when the PA dispatches parallel sub-tasks, it accumulates `agent:task_complete` events and only invokes its LLM once all pending tasks have reported (or the batch timeout expires), avoiding wasted output tokens on intermediate results. Urgent events (`agent:error`, `channel:stalled`, `channel:loop_detected`, `approval:requested`) always bypass batching and trigger immediate handling. Implement the four core intervention behaviours: stall recovery, task-blocked resolution, error diagnosis with container restart / fallback model, and model escalation surfacing. Interventions read only the last N messages from the relevant channel (default 10) — never full history — keeping the PA's context window lean.

---

## Architecture

```
faith/pa/
├── __init__.py
├── event_dispatcher.py   ← PAEventDispatcher class (this task)
└── intervention.py       ← InterventionHandler class (this task)

tests/
├── test_pa_event_dispatcher.py
└── test_pa_intervention.py
```

---

## Files to Create

### 1. `faith/pa/__init__.py`

```python
"""FAITH Project Agent (PA) core package."""
```

### 2. `faith/pa/event_dispatcher.py`

```python
"""PA Event Dispatcher — wires the PA's main event loop.

Subscribes to system-events via EventSubscriber, registers typed
handlers, and dispatches incoming events. This is the PA's brain
stem — all reactive behaviour flows through here.

FRS Reference: Section 3.2, 3.7.5
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

import redis.asyncio as aioredis

from faith.protocol.events import EventType, FaithEvent
from faith.protocol.subscriber import EventSubscriber, StallDetector

logger = logging.getLogger("faith.pa.event_dispatcher")

# Type alias for PA event handler functions.
# Handlers receive the FaithEvent and may return an optional dict
# of action results for testing / auditing.
PAHandler = Callable[[FaithEvent], Awaitable[Optional[dict[str, Any]]]]


class PAEventDispatcher:
    """Central event dispatcher for the Project Agent.

    Wraps EventSubscriber to register PA-specific handlers by event type.
    Handlers are called in registration order. If multiple handlers are
    registered for the same event type, they execute concurrently via
    asyncio.gather.

    The dispatcher owns the EventSubscriber lifecycle — call start()
    to begin listening and stop() to tear down.

    Attributes:
        redis: Async Redis client.
        subscriber: The underlying EventSubscriber from FAITH-009.
        intervention: Optional InterventionHandler (injected).
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        stall_timeout_seconds: int = 300,
        stall_tick_seconds: int = 60,
        missed_heartbeats_threshold: int = 3,
        heartbeat_interval_seconds: int = 30,
    ):
        """Initialise the PA event dispatcher.

        Args:
            redis_client: Connected async Redis client.
            stall_timeout_seconds: Channel inactivity timeout before
                publishing channel:stalled (default 300s / 5 min).
            stall_tick_seconds: Background tick interval for stall
                checks (default 60s).
            missed_heartbeats_threshold: Number of missed heartbeats
                before publishing agent:error (default 3).
            heartbeat_interval_seconds: Expected heartbeat interval
                from agents (default 30s).
        """
        self.redis = redis_client

        # Build the stall detector (from FAITH-009)
        self._stall_detector = StallDetector(
            redis=redis_client,
            timeout_seconds=stall_timeout_seconds,
            tick_seconds=stall_tick_seconds,
            missed_heartbeats=missed_heartbeats_threshold,
            heartbeat_interval=heartbeat_interval_seconds,
        )

        # Build the event subscriber (from FAITH-009)
        self.subscriber = EventSubscriber(
            redis=redis_client,
            stall_detector=self._stall_detector,
        )

        # PA handler registry: event type -> list of handlers
        self._handlers: dict[EventType, list[PAHandler]] = {}

        # Wildcard handlers (called for every event)
        self._wildcard_handlers: list[PAHandler] = []

        # Intervention results log (most recent N, for diagnostics)
        self._recent_interventions: list[dict[str, Any]] = []
        self._max_recent_interventions: int = 100

    def register(self, event_type: EventType, handler: PAHandler) -> None:
        """Register a handler for a specific event type.

        Multiple handlers can be registered per event type. They
        execute concurrently when the event fires.

        Args:
            event_type: The event type to handle.
            handler: Async function called with the FaithEvent.
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        logger.debug(
            f"Registered PA handler for {event_type.value}: "
            f"{handler.__name__}"
        )

    def register_wildcard(self, handler: PAHandler) -> None:
        """Register a handler that receives ALL events.

        Use sparingly — primarily for logging or audit.

        Args:
            handler: Async function called with every FaithEvent.
        """
        self._wildcard_handlers.append(handler)
        logger.debug(f"Registered PA wildcard handler: {handler.__name__}")

    def unregister(self, event_type: EventType, handler: PAHandler) -> None:
        """Remove a previously registered handler.

        Args:
            event_type: The event type the handler was registered for.
            handler: The handler function to remove.
        """
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)
            logger.debug(
                f"Removed PA handler for {event_type.value}: "
                f"{handler.__name__}"
            )

    async def _dispatch(self, event: FaithEvent) -> None:
        """Internal dispatcher called by EventSubscriber for every event.

        Looks up handlers for the event type, executes them concurrently,
        and records intervention results.

        Args:
            event: The incoming FaithEvent.
        """
        tasks: list[asyncio.Task] = []

        # Type-specific handlers
        typed_handlers = self._handlers.get(event.event_type, [])
        for handler in typed_handlers:
            tasks.append(
                asyncio.create_task(
                    self._safe_call(handler, event),
                    name=f"pa-handler-{handler.__name__}",
                )
            )

        # Wildcard handlers
        for handler in self._wildcard_handlers:
            tasks.append(
                asyncio.create_task(
                    self._safe_call(handler, event),
                    name=f"pa-wildcard-{handler.__name__}",
                )
            )

        if not tasks:
            return

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any intervention results
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"PA handler raised exception for "
                    f"{event.event_type.value}: {result}",
                    exc_info=result,
                )
            elif result is not None:
                record = {
                    "event_type": event.event_type.value,
                    "source": event.source,
                    "channel": event.channel,
                    "ts": event.ts,
                    "result": result,
                }
                self._recent_interventions.append(record)
                if len(self._recent_interventions) > self._max_recent_interventions:
                    self._recent_interventions.pop(0)

    async def _safe_call(
        self, handler: PAHandler, event: FaithEvent
    ) -> Optional[dict[str, Any]]:
        """Call a handler with exception isolation.

        Args:
            handler: The handler to call.
            event: The event to pass.

        Returns:
            Handler result or None.

        Raises:
            Exception: Re-raised after logging so gather() can collect it.
        """
        try:
            return await handler(event)
        except Exception as e:
            logger.error(
                f"Handler {handler.__name__} failed for "
                f"{event.event_type.value}: {e}",
                exc_info=True,
            )
            raise

    def register_channel(self, channel: str) -> None:
        """Register an active channel with the stall detector.

        Called by SessionManager (FAITH-015) when a new task channel
        is created.

        Args:
            channel: The channel name (e.g. "ch-auth-feature").
        """
        self._stall_detector.register_channel(channel)
        logger.info(f"Registered channel for stall detection: {channel}")

    def unregister_channel(self, channel: str) -> None:
        """Remove a channel from stall detection.

        Called when a task channel is closed.

        Args:
            channel: The channel name.
        """
        self._stall_detector.unregister_channel(channel)
        logger.info(f"Unregistered channel from stall detection: {channel}")

    def register_agent(self, agent_id: str) -> None:
        """Register an agent for heartbeat monitoring.

        Called by SessionManager (FAITH-015) when an agent container
        starts.

        Args:
            agent_id: The agent's identifier.
        """
        self._stall_detector.register_agent(agent_id)
        logger.info(f"Registered agent for heartbeat monitoring: {agent_id}")

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from heartbeat monitoring.

        Called when an agent container is stopped.

        Args:
            agent_id: The agent's identifier.
        """
        self._stall_detector.unregister_agent(agent_id)
        logger.info(f"Unregistered agent from heartbeat monitoring: {agent_id}")

    async def start(self) -> None:
        """Start the event dispatcher.

        Registers the internal _dispatch method as a wildcard handler
        on the EventSubscriber, then starts the subscriber (which
        also starts the stall detector).
        """
        # Wire our dispatch function into the EventSubscriber
        self.subscriber.on_all(self._dispatch)

        await self.subscriber.start()
        logger.info("PAEventDispatcher started")

    async def stop(self) -> None:
        """Stop the event dispatcher and underlying subscriber."""
        await self.subscriber.stop()
        logger.info("PAEventDispatcher stopped")

    @property
    def recent_interventions(self) -> list[dict[str, Any]]:
        """Return the recent intervention results (read-only copy)."""
        return list(self._recent_interventions)
```

### 3. `faith/pa/intervention.py`

```python
"""PA Intervention Handler — reactive logic for system events.

Implements the four core intervention behaviours defined in FRS 3.2
and 3.7.5:

1. channel:stalled      — query agent for status, read last N messages
2. agent:task_blocked    — determine blocker, resolve or escalate
3. agent:error           — diagnose, restart container, fallback model
4. agent:model_escalation_requested — surface to user via Web UI

All interventions read only the last N messages from the relevant
channel (configurable, default 10) — never full history.

FRS Reference: Section 3.2, 3.7.5, 3.7.6
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from faith.protocol.compact import CompactMessage, MessageType
from faith.protocol.events import EventPublisher, EventType, FaithEvent

logger = logging.getLogger("faith.pa.intervention")

# Default number of recent messages to read from a channel
# when investigating an intervention. FRS 3.7.5 specifies
# the PA reads only the most recent N messages — never full history.
DEFAULT_MESSAGE_READ_LIMIT = 10


class InterventionHandler:
    """Handles PA intervention events with concrete resolution logic.

    Each handler method corresponds to one of the PA's reactive
    intervention types. The PAEventDispatcher registers these methods
    for their respective event types.

    Attributes:
        redis: Async Redis client for channel reads and messaging.
        event_publisher: EventPublisher for emitting follow-up events.
        container_manager: ContainerManager (FAITH-014) for restart ops.
        session_manager: SessionManager (FAITH-015) for session context.
        message_read_limit: Number of recent messages to read on
            intervention (default 10).
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        event_publisher: EventPublisher,
        container_manager: Any = None,
        session_manager: Any = None,
        message_read_limit: int = DEFAULT_MESSAGE_READ_LIMIT,
    ):
        """Initialise the intervention handler.

        Args:
            redis_client: Connected async Redis client.
            event_publisher: EventPublisher for system-events.
            container_manager: ContainerManager from FAITH-014.
                Optional — if None, container restart operations
                will log a warning and skip.
            session_manager: SessionManager from FAITH-015.
                Optional — if None, session context lookups
                will be limited.
            message_read_limit: Max recent messages to read from
                a channel during intervention (default 10).
        """
        self.redis = redis_client
        self.event_publisher = event_publisher
        self.container_manager = container_manager
        self.session_manager = session_manager
        self.message_read_limit = message_read_limit

    # ──────────────────────────────────────────────────
    # Channel message reading
    # ──────────────────────────────────────────────────

    async def _read_recent_messages(
        self, channel: str, limit: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """Read the last N messages from a Redis channel's history list.

        Messages are stored in a Redis list keyed by channel name
        (written by agents as they publish). We read from the tail
        (most recent) up to `limit` entries.

        Args:
            channel: The channel name.
            limit: Number of messages to read. Defaults to
                self.message_read_limit.

        Returns:
            List of parsed message dicts, oldest first.
        """
        n = limit or self.message_read_limit
        history_key = f"channel:{channel}:messages"

        try:
            # LRANGE with negative indices: -n to -1 = last n items
            raw_messages = await self.redis.lrange(history_key, -n, -1)
        except Exception as e:
            logger.error(f"Failed to read messages from {history_key}: {e}")
            return []

        messages = []
        for raw in raw_messages:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                messages.append(json.loads(raw))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"Skipping malformed message in {channel}: {e}")

        return messages

    # ──────────────────────────────────────────────────
    # Agent status query
    # ──────────────────────────────────────────────────

    async def _query_agent_status(
        self, agent_id: str, channel: str, reason: str
    ) -> None:
        """Send a structured status_request to an agent.

        Per FRS 3.7.6, the PA sends a direct query rather than
        inferring state from conversation.

        Args:
            agent_id: The agent to query.
            channel: The channel context.
            reason: Human-readable reason for the query.
        """
        status_msg = {
            "from": "pa",
            "to": agent_id,
            "channel": channel,
            "type": "status_request",
            "summary": reason,
            "needs": "Current task, blocker if any, estimated completion",
        }
        # Publish to the agent's personal channel
        personal_channel = f"pa-{agent_id}"
        await self.redis.publish(personal_channel, json.dumps(status_msg))
        logger.info(
            f"Sent status_request to {agent_id} on {personal_channel}: "
            f"{reason}"
        )

    # ──────────────────────────────────────────────────
    # Web UI notification
    # ──────────────────────────────────────────────────

    async def _notify_user(
        self,
        title: str,
        message: str,
        severity: str = "info",
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        """Surface a notification to the user via the Web UI.

        Publishes to the `pa-user` Redis channel which the Web UI
        WebSocket endpoint subscribes to.

        Args:
            title: Short notification title.
            message: Detailed message body.
            severity: One of "info", "warning", "error".
            data: Optional structured data payload.
        """
        notification = {
            "type": "pa_notification",
            "title": title,
            "message": message,
            "severity": severity,
            "data": data or {},
        }
        await self.redis.publish("pa-user", json.dumps(notification))
        logger.info(f"User notification [{severity}]: {title}")

    # ──────────────────────────────────────────────────
    # Agent config lookup
    # ──────────────────────────────────────────────────

    async def _get_agent_config(self, agent_id: str) -> dict[str, Any]:
        """Load an agent's config from the session manager.

        Falls back to an empty dict if session_manager is not
        available or the agent is not found.

        Args:
            agent_id: The agent identifier.

        Returns:
            Agent config dict.
        """
        if self.session_manager is None:
            return {}

        try:
            return self.session_manager.get_agent_config(agent_id) or {}
        except Exception as e:
            logger.warning(f"Failed to get config for {agent_id}: {e}")
            return {}

    # ──────────────────────────────────────────────────
    # Intervention: channel:stalled
    # ──────────────────────────────────────────────────

    async def handle_channel_stalled(
        self, event: FaithEvent
    ) -> Optional[dict[str, Any]]:
        """Handle a channel:stalled event.

        Strategy:
        1. Read the last N messages from the stalled channel.
        2. Identify the last active agent on the channel.
        3. Send a status_request to that agent.
        4. Notify the user that a stall was detected.

        Per FRS 3.7.6: the PA sends a direct structured query to
        the relevant agent rather than inferring state from conversation.

        Args:
            event: The channel:stalled FaithEvent.

        Returns:
            Intervention result dict.
        """
        channel = event.channel
        if not channel:
            logger.warning("channel:stalled event missing channel field")
            return {"action": "skipped", "reason": "no_channel"}

        logger.info(f"Investigating stalled channel: {channel}")

        # Read recent messages to identify last active agent
        recent = await self._read_recent_messages(channel)

        last_agent = None
        if recent:
            # Find the most recent message with a "from" field
            # that is not the PA itself
            for msg in reversed(recent):
                sender = msg.get("from", "")
                if sender and sender != "pa":
                    last_agent = sender
                    break

        if last_agent:
            timeout = event.data.get("timeout_seconds", 300)
            await self._query_agent_status(
                agent_id=last_agent,
                channel=channel,
                reason=(
                    f"No activity detected on {channel} for "
                    f"{timeout} seconds. What is your current status?"
                ),
            )
        else:
            logger.warning(
                f"No agent identified on stalled channel {channel}"
            )

        # Notify user
        await self._notify_user(
            title="Channel Stalled",
            message=(
                f"Channel '{channel}' has been inactive. "
                f"{'Queried ' + last_agent + ' for status.' if last_agent else 'No active agent found.'}"
            ),
            severity="warning",
            data={
                "channel": channel,
                "last_agent": last_agent,
                "recent_message_count": len(recent),
            },
        )

        return {
            "action": "stall_investigated",
            "channel": channel,
            "last_agent": last_agent,
            "messages_read": len(recent),
        }

    # ──────────────────────────────────────────────────
    # Intervention: agent:task_blocked
    # ──────────────────────────────────────────────────

    async def handle_task_blocked(
        self, event: FaithEvent
    ) -> Optional[dict[str, Any]]:
        """Handle an agent:task_blocked event.

        Strategy:
        1. Extract blocker information from the event data.
        2. Read the last N messages from the channel for context.
        3. Determine blocker type (waiting on agent, tool, or user).
        4. Attempt resolution:
           - Waiting on another agent: send a nudge to that agent.
           - Waiting on a tool: check tool status, restart if needed.
           - Waiting on user input: surface to user via Web UI.
        5. If no automatic resolution, escalate to user.

        Args:
            event: The agent:task_blocked FaithEvent.

        Returns:
            Intervention result dict.
        """
        agent_id = event.source
        channel = event.channel or ""
        blocker = event.data.get("blocker", "unknown")
        blocker_type = event.data.get("blocker_type", "unknown")
        reason = event.data.get("reason", "No reason provided")

        logger.info(
            f"Agent {agent_id} blocked on {channel}: "
            f"type={blocker_type}, blocker={blocker}, reason={reason}"
        )

        # Read recent messages for context
        recent = []
        if channel:
            recent = await self._read_recent_messages(channel)

        resolution = "escalated_to_user"

        if blocker_type == "agent":
            # Another agent is the blocker — send a nudge
            if blocker and blocker != "unknown":
                await self._query_agent_status(
                    agent_id=blocker,
                    channel=channel,
                    reason=(
                        f"Agent {agent_id} is blocked waiting on your output. "
                        f"Reason: {reason}"
                    ),
                )
                resolution = "nudged_blocking_agent"
                logger.info(f"Nudged blocking agent {blocker}")

        elif blocker_type == "tool":
            # A tool is the blocker — check status and possibly restart
            if self.container_manager and blocker and blocker != "unknown":
                try:
                    tool_status = await self.container_manager.get_container_status(
                        blocker
                    )
                    if tool_status in ("exited", "dead", "not_found"):
                        logger.info(
                            f"Tool {blocker} is {tool_status} — "
                            f"attempting restart"
                        )
                        await self.container_manager.restart_container(blocker)
                        resolution = "restarted_tool_container"
                    else:
                        resolution = f"tool_status_{tool_status}"
                except Exception as e:
                    logger.error(f"Failed to check/restart tool {blocker}: {e}")
                    resolution = "tool_restart_failed"

        elif blocker_type == "user":
            # User input needed — surface immediately
            resolution = "surfaced_to_user"

        # Always notify user for blocked agents
        await self._notify_user(
            title="Agent Blocked",
            message=(
                f"Agent '{agent_id}' is blocked on channel '{channel}'. "
                f"Blocker: {blocker} ({blocker_type}). "
                f"Reason: {reason}. "
                f"Resolution: {resolution}."
            ),
            severity="warning",
            data={
                "agent_id": agent_id,
                "channel": channel,
                "blocker": blocker,
                "blocker_type": blocker_type,
                "reason": reason,
                "resolution": resolution,
            },
        )

        return {
            "action": "task_blocked_handled",
            "agent_id": agent_id,
            "channel": channel,
            "blocker": blocker,
            "blocker_type": blocker_type,
            "resolution": resolution,
            "messages_read": len(recent),
        }

    # ──────────────────────────────────────────────────
    # Intervention: agent:error
    # ──────────────────────────────────────────────────

    async def handle_agent_error(
        self, event: FaithEvent
    ) -> Optional[dict[str, Any]]:
        """Handle an agent:error event.

        Strategy:
        1. Read the last N messages from the agent's channel.
        2. Diagnose the error type from event data.
        3. Attempt recovery:
           - Container crash: restart via ContainerManager (FAITH-014).
           - LLM API error: switch to fallback model if configured
             in .faith/agents/{id}/config.yaml.
           - Heartbeat timeout: restart the agent container.
           - Unknown: notify user.
        4. If recovery fails or is not possible, escalate to user.

        Args:
            event: The agent:error FaithEvent.

        Returns:
            Intervention result dict.
        """
        agent_id = event.source
        channel = event.channel or ""
        error_type = event.data.get("error_type", "unknown")
        error_message = event.data.get("message", "No details")
        error_code = event.data.get("code", None)

        logger.error(
            f"Agent error — {agent_id}: type={error_type}, "
            f"message={error_message}, code={error_code}"
        )

        # Read recent messages for diagnostic context
        recent = []
        if channel:
            recent = await self._read_recent_messages(channel)

        resolution = "escalated_to_user"
        fallback_model = None

        # ── Container crash or heartbeat timeout ──
        if error_type in ("container_crash", "heartbeat_timeout", "container_error"):
            if self.container_manager:
                try:
                    logger.info(f"Restarting container for agent {agent_id}")
                    await self.container_manager.restart_container(agent_id)
                    resolution = "container_restarted"
                except Exception as e:
                    logger.error(
                        f"Failed to restart container for {agent_id}: {e}"
                    )
                    resolution = "container_restart_failed"
            else:
                logger.warning(
                    "ContainerManager not available — cannot restart "
                    f"agent {agent_id}"
                )
                resolution = "no_container_manager"

        # ── LLM API error ──
        elif error_type in ("llm_api_error", "model_error", "rate_limit"):
            # Check for a fallback model in agent config
            agent_config = await self._get_agent_config(agent_id)
            fallback_model = agent_config.get("fallback_model")

            if fallback_model:
                logger.info(
                    f"Switching agent {agent_id} to fallback model: "
                    f"{fallback_model}"
                )
                # Notify the agent to switch models via its personal channel
                switch_msg = {
                    "from": "pa",
                    "to": agent_id,
                    "type": "model_switch",
                    "summary": (
                        f"Primary model error ({error_message}). "
                        f"Switching to fallback model: {fallback_model}"
                    ),
                    "data": {
                        "fallback_model": fallback_model,
                        "reason": error_message,
                    },
                }
                await self.redis.publish(
                    f"pa-{agent_id}", json.dumps(switch_msg)
                )
                resolution = "switched_to_fallback_model"
            else:
                # No fallback — try container restart as last resort
                if self.container_manager:
                    try:
                        await self.container_manager.restart_container(agent_id)
                        resolution = "container_restarted_after_llm_error"
                    except Exception as e:
                        logger.error(f"Restart failed for {agent_id}: {e}")
                        resolution = "escalated_to_user"
                else:
                    resolution = "escalated_to_user"

        # ── Unknown error type ──
        else:
            logger.warning(
                f"Unknown error type '{error_type}' for agent {agent_id}"
            )

        # Determine severity for user notification
        severity = "error"
        if resolution in (
            "container_restarted",
            "switched_to_fallback_model",
            "container_restarted_after_llm_error",
        ):
            severity = "warning"

        await self._notify_user(
            title="Agent Error",
            message=(
                f"Agent '{agent_id}' encountered an error: {error_message}. "
                f"Type: {error_type}. Resolution: {resolution}."
                + (f" Fallback model: {fallback_model}." if fallback_model else "")
            ),
            severity=severity,
            data={
                "agent_id": agent_id,
                "channel": channel,
                "error_type": error_type,
                "error_message": error_message,
                "error_code": error_code,
                "resolution": resolution,
                "fallback_model": fallback_model,
            },
        )

        return {
            "action": "agent_error_handled",
            "agent_id": agent_id,
            "channel": channel,
            "error_type": error_type,
            "resolution": resolution,
            "fallback_model": fallback_model,
            "messages_read": len(recent),
        }

    # ──────────────────────────────────────────────────
    # Intervention: agent:model_escalation_requested
    # ──────────────────────────────────────────────────

    async def handle_model_escalation(
        self, event: FaithEvent
    ) -> Optional[dict[str, Any]]:
        """Handle an agent:model_escalation_requested event.

        Strategy:
        1. Read the last N messages for context.
        2. Surface the escalation request to the user via Web UI.
        3. The user decides whether to approve the model upgrade.

        The PA does not auto-approve model escalations — they always
        require user consent because they affect cost.

        Args:
            event: The agent:model_escalation_requested FaithEvent.

        Returns:
            Intervention result dict.
        """
        agent_id = event.source
        channel = event.channel or ""
        current_model = event.data.get("current_model", "unknown")
        requested_model = event.data.get("requested_model", "unknown")
        reason = event.data.get("reason", "No reason provided")

        logger.info(
            f"Model escalation requested by {agent_id}: "
            f"{current_model} -> {requested_model}. Reason: {reason}"
        )

        # Read recent messages for context to include in the
        # user notification
        recent = []
        if channel:
            recent = await self._read_recent_messages(channel)

        # Build a context summary from recent messages
        context_snippet = ""
        if recent:
            last_msgs = recent[-3:]  # last 3 messages for brief context
            snippets = []
            for msg in last_msgs:
                sender = msg.get("from", "?")
                summary = msg.get("summary", msg.get("content", ""))
                if len(summary) > 120:
                    summary = summary[:117] + "..."
                snippets.append(f"  [{sender}]: {summary}")
            context_snippet = "\n".join(snippets)

        await self._notify_user(
            title="Model Escalation Request",
            message=(
                f"Agent '{agent_id}' requests a model upgrade from "
                f"'{current_model}' to '{requested_model}'.\n"
                f"Reason: {reason}\n"
                f"Channel: {channel}\n"
                + (f"Recent context:\n{context_snippet}" if context_snippet else "")
            ),
            severity="info",
            data={
                "agent_id": agent_id,
                "channel": channel,
                "current_model": current_model,
                "requested_model": requested_model,
                "reason": reason,
                "type": "model_escalation_request",
            },
        )

        return {
            "action": "model_escalation_surfaced",
            "agent_id": agent_id,
            "channel": channel,
            "current_model": current_model,
            "requested_model": requested_model,
            "messages_read": len(recent),
        }

    # ──────────────────────────────────────────────────
    # Registration helper
    # ──────────────────────────────────────────────────

    def register_all(self, dispatcher: "PAEventDispatcher") -> None:
        """Register all intervention handlers with a PAEventDispatcher.

        Convenience method that wires up all four intervention types
        in a single call.

        Args:
            dispatcher: The PAEventDispatcher to register with.
        """
        dispatcher.register(
            EventType.CHANNEL_STALLED, self.handle_channel_stalled
        )
        dispatcher.register(
            EventType.AGENT_TASK_BLOCKED, self.handle_task_blocked
        )
        dispatcher.register(
            EventType.AGENT_ERROR, self.handle_agent_error
        )
        dispatcher.register(
            EventType.AGENT_MODEL_ESCALATION_REQUESTED,
            self.handle_model_escalation,
        )
        logger.info("All intervention handlers registered")
```

### 4. `tests/test_pa_event_dispatcher.py`

```python
"""Tests for PAEventDispatcher.

Covers handler registration, event dispatch, wildcard handlers,
exception isolation, channel/agent registration for stall detection,
and lifecycle management.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.pa.event_dispatcher import PAEventDispatcher
from faith.protocol.events import EventType, FaithEvent


# ──────────────────────────────────────────────────
# Fake Redis for testing
# ──────────────────────────────────────────────────


class FakePubSub:
    """Minimal fake async Redis PubSub."""

    def __init__(self):
        self.subscribed: list[str] = []
        self._messages: list[dict] = []
        self._closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str = None) -> None:
        pass

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self._messages:
            return self._messages.pop(0)
        return None

    async def close(self) -> None:
        self._closed = True


class FakeRedis:
    """Minimal fake async Redis client for testing."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self._pubsub = FakePubSub()

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    def pubsub(self) -> FakePubSub:
        return self._pubsub

    async def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        return []


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


def _make_event(
    event_type: EventType,
    source: str = "test-agent",
    channel: str = "ch-test",
    data: dict = None,
) -> FaithEvent:
    """Create a FaithEvent for testing."""
    return FaithEvent(
        event_type=event_type,
        source=source,
        channel=channel,
        ts="2026-03-24T10:00:00Z",
        data=data or {},
    )


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def dispatcher(fake_redis):
    """Create a PAEventDispatcher with mocked stall detector."""
    with patch("faith.pa.event_dispatcher.StallDetector") as MockSD:
        mock_sd = MagicMock()
        mock_sd.start = AsyncMock()
        mock_sd.stop = AsyncMock()
        mock_sd.register_channel = MagicMock()
        mock_sd.unregister_channel = MagicMock()
        mock_sd.register_agent = MagicMock()
        mock_sd.unregister_agent = MagicMock()
        MockSD.return_value = mock_sd

        d = PAEventDispatcher(redis_client=fake_redis)
        yield d


# ──────────────────────────────────────────────────
# Tests: Handler Registration
# ──────────────────────────────────────────────────


class TestHandlerRegistration:
    """Test handler registration and unregistration."""

    def test_register_handler(self, dispatcher):
        async def my_handler(event):
            pass

        dispatcher.register(EventType.AGENT_ERROR, my_handler)
        assert my_handler in dispatcher._handlers[EventType.AGENT_ERROR]

    def test_register_multiple_handlers_same_type(self, dispatcher):
        async def handler_a(event):
            pass

        async def handler_b(event):
            pass

        dispatcher.register(EventType.AGENT_ERROR, handler_a)
        dispatcher.register(EventType.AGENT_ERROR, handler_b)
        assert len(dispatcher._handlers[EventType.AGENT_ERROR]) == 2

    def test_unregister_handler(self, dispatcher):
        async def my_handler(event):
            pass

        dispatcher.register(EventType.AGENT_ERROR, my_handler)
        dispatcher.unregister(EventType.AGENT_ERROR, my_handler)
        assert my_handler not in dispatcher._handlers.get(
            EventType.AGENT_ERROR, []
        )

    def test_unregister_nonexistent_handler(self, dispatcher):
        async def my_handler(event):
            pass

        # Should not raise
        dispatcher.unregister(EventType.AGENT_ERROR, my_handler)

    def test_register_wildcard_handler(self, dispatcher):
        async def wildcard(event):
            pass

        dispatcher.register_wildcard(wildcard)
        assert wildcard in dispatcher._wildcard_handlers


# ──────────────────────────────────────────────────
# Tests: Event Dispatch
# ──────────────────────────────────────────────────


class TestEventDispatch:
    """Test event dispatching to handlers."""

    @pytest.mark.asyncio
    async def test_dispatch_calls_typed_handler(self, dispatcher):
        results = []

        async def handler(event):
            results.append(event.event_type)
            return {"handled": True}

        dispatcher.register(EventType.AGENT_ERROR, handler)
        event = _make_event(EventType.AGENT_ERROR)
        await dispatcher._dispatch(event)

        assert results == [EventType.AGENT_ERROR]

    @pytest.mark.asyncio
    async def test_dispatch_calls_wildcard_handler(self, dispatcher):
        results = []

        async def wildcard(event):
            results.append(("wildcard", event.event_type))

        dispatcher.register_wildcard(wildcard)
        event = _make_event(EventType.AGENT_HEARTBEAT)
        await dispatcher._dispatch(event)

        assert len(results) == 1
        assert results[0] == ("wildcard", EventType.AGENT_HEARTBEAT)

    @pytest.mark.asyncio
    async def test_dispatch_calls_both_typed_and_wildcard(self, dispatcher):
        typed_results = []
        wildcard_results = []

        async def typed(event):
            typed_results.append(True)

        async def wildcard(event):
            wildcard_results.append(True)

        dispatcher.register(EventType.CHANNEL_STALLED, typed)
        dispatcher.register_wildcard(wildcard)

        event = _make_event(EventType.CHANNEL_STALLED)
        await dispatcher._dispatch(event)

        assert len(typed_results) == 1
        assert len(wildcard_results) == 1

    @pytest.mark.asyncio
    async def test_dispatch_no_handlers_does_not_raise(self, dispatcher):
        event = _make_event(EventType.AGENT_HEARTBEAT)
        # Should not raise even with no handlers
        await dispatcher._dispatch(event)

    @pytest.mark.asyncio
    async def test_dispatch_multiple_handlers_run_concurrently(
        self, dispatcher
    ):
        call_order = []

        async def handler_a(event):
            call_order.append("a")

        async def handler_b(event):
            call_order.append("b")

        dispatcher.register(EventType.AGENT_ERROR, handler_a)
        dispatcher.register(EventType.AGENT_ERROR, handler_b)

        event = _make_event(EventType.AGENT_ERROR)
        await dispatcher._dispatch(event)

        assert set(call_order) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_dispatch_handler_exception_isolated(self, dispatcher):
        results = []

        async def failing_handler(event):
            raise ValueError("boom")

        async def good_handler(event):
            results.append("ok")

        dispatcher.register(EventType.AGENT_ERROR, failing_handler)
        dispatcher.register(EventType.AGENT_ERROR, good_handler)

        event = _make_event(EventType.AGENT_ERROR)
        # Should not raise — exceptions are gathered
        await dispatcher._dispatch(event)

        # The good handler should still have been called
        assert "ok" in results

    @pytest.mark.asyncio
    async def test_dispatch_records_intervention_results(self, dispatcher):
        async def handler(event):
            return {"action": "test_action", "detail": "ok"}

        dispatcher.register(EventType.CHANNEL_STALLED, handler)

        event = _make_event(EventType.CHANNEL_STALLED, channel="ch-test")
        await dispatcher._dispatch(event)

        assert len(dispatcher.recent_interventions) == 1
        record = dispatcher.recent_interventions[0]
        assert record["event_type"] == EventType.CHANNEL_STALLED.value
        assert record["result"]["action"] == "test_action"


# ──────────────────────────────────────────────────
# Tests: Channel and Agent Registration
# ──────────────────────────────────────────────────


class TestRegistration:
    """Test channel and agent registration for stall detection."""

    def test_register_channel(self, dispatcher):
        dispatcher.register_channel("ch-feature")
        dispatcher._stall_detector.register_channel.assert_called_once_with(
            "ch-feature"
        )

    def test_unregister_channel(self, dispatcher):
        dispatcher.unregister_channel("ch-feature")
        dispatcher._stall_detector.unregister_channel.assert_called_once_with(
            "ch-feature"
        )

    def test_register_agent(self, dispatcher):
        dispatcher.register_agent("dev-agent")
        dispatcher._stall_detector.register_agent.assert_called_once_with(
            "dev-agent"
        )

    def test_unregister_agent(self, dispatcher):
        dispatcher.unregister_agent("dev-agent")
        dispatcher._stall_detector.unregister_agent.assert_called_once_with(
            "dev-agent"
        )


# ──────────────────────────────────────────────────
# Tests: Recent Interventions Buffer
# ──────────────────────────────────────────────────


class TestInterventionBuffer:
    """Test the capped recent interventions buffer."""

    @pytest.mark.asyncio
    async def test_buffer_caps_at_max(self, dispatcher):
        dispatcher._max_recent_interventions = 3

        async def handler(event):
            return {"n": event.data.get("n")}

        dispatcher.register(EventType.AGENT_HEARTBEAT, handler)

        for i in range(5):
            event = _make_event(
                EventType.AGENT_HEARTBEAT, data={"n": i}
            )
            await dispatcher._dispatch(event)

        assert len(dispatcher.recent_interventions) == 3
        # Should keep the last 3
        assert dispatcher.recent_interventions[0]["result"]["n"] == 2
        assert dispatcher.recent_interventions[2]["result"]["n"] == 4

    def test_recent_interventions_returns_copy(self, dispatcher):
        copy = dispatcher.recent_interventions
        copy.append({"fake": True})
        assert len(dispatcher._recent_interventions) == 0
```

### 5. `tests/test_pa_intervention.py`

```python
"""Tests for InterventionHandler.

Covers all four intervention types: channel:stalled, agent:task_blocked,
agent:error, and agent:model_escalation_requested. Verifies correct
message reading, agent querying, container operations, fallback model
switching, and user notifications.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.pa.intervention import InterventionHandler, DEFAULT_MESSAGE_READ_LIMIT
from faith.protocol.events import EventPublisher, EventType, FaithEvent


# ──────────────────────────────────────────────────
# Fake Redis for testing
# ──────────────────────────────────────────────────


class FakeRedis:
    """Fake async Redis with configurable lrange responses."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self._lists: dict[str, list[bytes]] = {}

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    async def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        items = self._lists.get(key, [])
        # Simulate Redis LRANGE with negative indices
        if items:
            return items[start:] if end == -1 else items[start : end + 1]
        return []

    def seed_messages(self, channel: str, messages: list[dict]) -> None:
        """Pre-populate channel message history for testing."""
        key = f"channel:{channel}:messages"
        self._lists[key] = [
            json.dumps(m).encode("utf-8") for m in messages
        ]


def _make_event(
    event_type: EventType,
    source: str = "test-agent",
    channel: str = "ch-test",
    data: dict = None,
) -> FaithEvent:
    return FaithEvent(
        event_type=event_type,
        source=source,
        channel=channel,
        ts="2026-03-24T10:00:00Z",
        data=data or {},
    )


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def event_publisher(fake_redis):
    pub = MagicMock(spec=EventPublisher)
    pub.redis = fake_redis
    return pub


@pytest.fixture
def container_manager():
    cm = MagicMock()
    cm.restart_container = AsyncMock()
    cm.get_container_status = AsyncMock(return_value="running")
    return cm


@pytest.fixture
def session_manager():
    sm = MagicMock()
    sm.get_agent_config = MagicMock(return_value={})
    return sm


@pytest.fixture
def handler(fake_redis, event_publisher, container_manager, session_manager):
    return InterventionHandler(
        redis_client=fake_redis,
        event_publisher=event_publisher,
        container_manager=container_manager,
        session_manager=session_manager,
        message_read_limit=10,
    )


# ──────────────────────────────────────────────────
# Tests: _read_recent_messages
# ──────────────────────────────────────────────────


class TestReadRecentMessages:
    """Test reading recent messages from channel history."""

    @pytest.mark.asyncio
    async def test_reads_messages_from_redis_list(self, handler, fake_redis):
        fake_redis.seed_messages("ch-test", [
            {"from": "dev", "summary": "msg 1"},
            {"from": "qa", "summary": "msg 2"},
        ])
        messages = await handler._read_recent_messages("ch-test")
        assert len(messages) == 2
        assert messages[0]["from"] == "dev"
        assert messages[1]["from"] == "qa"

    @pytest.mark.asyncio
    async def test_returns_empty_on_missing_channel(self, handler):
        messages = await handler._read_recent_messages("ch-nonexistent")
        assert messages == []

    @pytest.mark.asyncio
    async def test_respects_message_limit(self, handler):
        assert handler.message_read_limit == 10

    @pytest.mark.asyncio
    async def test_handles_malformed_messages(self, handler, fake_redis):
        key = "channel:ch-test:messages"
        fake_redis._lists[key] = [
            b"not-valid-json",
            json.dumps({"from": "dev"}).encode("utf-8"),
        ]
        messages = await handler._read_recent_messages("ch-test")
        assert len(messages) == 1
        assert messages[0]["from"] == "dev"

    @pytest.mark.asyncio
    async def test_default_message_limit_is_10(self):
        assert DEFAULT_MESSAGE_READ_LIMIT == 10


# ──────────────────────────────────────────────────
# Tests: handle_channel_stalled
# ──────────────────────────────────────────────────


class TestHandleChannelStalled:
    """Test channel:stalled intervention."""

    @pytest.mark.asyncio
    async def test_queries_last_active_agent(self, handler, fake_redis):
        fake_redis.seed_messages("ch-test", [
            {"from": "dev", "summary": "working on auth"},
            {"from": "qa", "summary": "waiting for tests"},
        ])
        event = _make_event(EventType.CHANNEL_STALLED, channel="ch-test")
        result = await handler.handle_channel_stalled(event)

        assert result["action"] == "stall_investigated"
        assert result["last_agent"] == "qa"
        assert result["messages_read"] == 2

        # Should have published a status_request to qa
        status_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-qa"
        ]
        assert len(status_msgs) == 1
        parsed = json.loads(status_msgs[0][1])
        assert parsed["type"] == "status_request"

    @pytest.mark.asyncio
    async def test_skips_pa_messages_when_finding_last_agent(
        self, handler, fake_redis
    ):
        fake_redis.seed_messages("ch-test", [
            {"from": "dev", "summary": "done"},
            {"from": "pa", "summary": "acknowledged"},
        ])
        event = _make_event(EventType.CHANNEL_STALLED, channel="ch-test")
        result = await handler.handle_channel_stalled(event)

        assert result["last_agent"] == "dev"

    @pytest.mark.asyncio
    async def test_handles_no_channel(self, handler):
        event = _make_event(
            EventType.CHANNEL_STALLED, channel=""
        )
        # Patch channel to None to simulate missing field
        event.channel = None
        result = await handler.handle_channel_stalled(event)
        assert result["action"] == "skipped"

    @pytest.mark.asyncio
    async def test_handles_empty_channel_history(self, handler):
        event = _make_event(EventType.CHANNEL_STALLED, channel="ch-empty")
        result = await handler.handle_channel_stalled(event)

        assert result["last_agent"] is None
        assert result["messages_read"] == 0

    @pytest.mark.asyncio
    async def test_notifies_user(self, handler, fake_redis):
        fake_redis.seed_messages("ch-test", [
            {"from": "dev", "summary": "working"},
        ])
        event = _make_event(EventType.CHANNEL_STALLED, channel="ch-test")
        await handler.handle_channel_stalled(event)

        user_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-user"
        ]
        assert len(user_msgs) >= 1
        parsed = json.loads(user_msgs[0][1])
        assert parsed["severity"] == "warning"
        assert "Channel Stalled" in parsed["title"]


# ──────────────────────────────────────────────────
# Tests: handle_task_blocked
# ──────────────────────────────────────────────────


class TestHandleTaskBlocked:
    """Test agent:task_blocked intervention."""

    @pytest.mark.asyncio
    async def test_nudges_blocking_agent(self, handler, fake_redis):
        event = _make_event(
            EventType.AGENT_TASK_BLOCKED,
            source="qa",
            data={
                "blocker": "dev",
                "blocker_type": "agent",
                "reason": "Waiting for implementation",
            },
        )
        result = await handler.handle_task_blocked(event)

        assert result["resolution"] == "nudged_blocking_agent"
        assert result["blocker"] == "dev"

        # Should have published a status_request to dev
        nudge_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-dev"
        ]
        assert len(nudge_msgs) == 1

    @pytest.mark.asyncio
    async def test_restarts_dead_tool(self, handler, container_manager):
        container_manager.get_container_status = AsyncMock(
            return_value="exited"
        )
        event = _make_event(
            EventType.AGENT_TASK_BLOCKED,
            source="dev",
            data={
                "blocker": "filesystem-tool",
                "blocker_type": "tool",
                "reason": "Tool not responding",
            },
        )
        result = await handler.handle_task_blocked(event)

        assert result["resolution"] == "restarted_tool_container"
        container_manager.restart_container.assert_awaited_once_with(
            "filesystem-tool"
        )

    @pytest.mark.asyncio
    async def test_surfaces_user_blocker(self, handler, fake_redis):
        event = _make_event(
            EventType.AGENT_TASK_BLOCKED,
            source="dev",
            data={
                "blocker": "user",
                "blocker_type": "user",
                "reason": "Need DB credentials",
            },
        )
        result = await handler.handle_task_blocked(event)

        assert result["resolution"] == "surfaced_to_user"

        user_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-user"
        ]
        assert len(user_msgs) >= 1

    @pytest.mark.asyncio
    async def test_escalates_unknown_blocker(self, handler, fake_redis):
        event = _make_event(
            EventType.AGENT_TASK_BLOCKED,
            source="dev",
            data={
                "blocker": "unknown",
                "blocker_type": "unknown",
                "reason": "Something unclear",
            },
        )
        result = await handler.handle_task_blocked(event)

        assert result["resolution"] == "escalated_to_user"

    @pytest.mark.asyncio
    async def test_reads_channel_messages(self, handler, fake_redis):
        fake_redis.seed_messages("ch-test", [
            {"from": "dev", "summary": "stuck"},
        ])
        event = _make_event(
            EventType.AGENT_TASK_BLOCKED,
            source="dev",
            channel="ch-test",
            data={"blocker": "qa", "blocker_type": "agent", "reason": "x"},
        )
        result = await handler.handle_task_blocked(event)
        assert result["messages_read"] == 1

    @pytest.mark.asyncio
    async def test_tool_restart_failure_handled(
        self, handler, container_manager
    ):
        container_manager.get_container_status = AsyncMock(
            return_value="exited"
        )
        container_manager.restart_container = AsyncMock(
            side_effect=RuntimeError("Docker error")
        )
        event = _make_event(
            EventType.AGENT_TASK_BLOCKED,
            source="dev",
            data={
                "blocker": "python-tool",
                "blocker_type": "tool",
                "reason": "Tool crashed",
            },
        )
        result = await handler.handle_task_blocked(event)
        assert result["resolution"] == "tool_restart_failed"


# ──────────────────────────────────────────────────
# Tests: handle_agent_error
# ──────────────────────────────────────────────────


class TestHandleAgentError:
    """Test agent:error intervention."""

    @pytest.mark.asyncio
    async def test_restarts_container_on_crash(
        self, handler, container_manager
    ):
        event = _make_event(
            EventType.AGENT_ERROR,
            source="dev",
            data={
                "error_type": "container_crash",
                "message": "Container exited unexpectedly",
            },
        )
        result = await handler.handle_agent_error(event)

        assert result["resolution"] == "container_restarted"
        container_manager.restart_container.assert_awaited_once_with("dev")

    @pytest.mark.asyncio
    async def test_restarts_container_on_heartbeat_timeout(
        self, handler, container_manager
    ):
        event = _make_event(
            EventType.AGENT_ERROR,
            source="qa",
            data={
                "error_type": "heartbeat_timeout",
                "message": "3 missed heartbeats",
            },
        )
        result = await handler.handle_agent_error(event)

        assert result["resolution"] == "container_restarted"
        container_manager.restart_container.assert_awaited_once_with("qa")

    @pytest.mark.asyncio
    async def test_switches_to_fallback_model(
        self, handler, session_manager, fake_redis
    ):
        session_manager.get_agent_config = MagicMock(
            return_value={"fallback_model": "gpt-4o-mini"}
        )
        event = _make_event(
            EventType.AGENT_ERROR,
            source="dev",
            data={
                "error_type": "llm_api_error",
                "message": "Rate limited",
                "code": 429,
            },
        )
        result = await handler.handle_agent_error(event)

        assert result["resolution"] == "switched_to_fallback_model"
        assert result["fallback_model"] == "gpt-4o-mini"

        # Should have published a model_switch message to the agent
        switch_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-dev"
        ]
        assert len(switch_msgs) == 1
        parsed = json.loads(switch_msgs[0][1])
        assert parsed["type"] == "model_switch"
        assert parsed["data"]["fallback_model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_no_fallback_restarts_container(
        self, handler, container_manager, session_manager
    ):
        session_manager.get_agent_config = MagicMock(return_value={})
        event = _make_event(
            EventType.AGENT_ERROR,
            source="dev",
            data={
                "error_type": "llm_api_error",
                "message": "Service unavailable",
                "code": 503,
            },
        )
        result = await handler.handle_agent_error(event)

        assert result["resolution"] == "container_restarted_after_llm_error"

    @pytest.mark.asyncio
    async def test_unknown_error_escalates_to_user(self, handler, fake_redis):
        event = _make_event(
            EventType.AGENT_ERROR,
            source="dev",
            data={
                "error_type": "mysterious_failure",
                "message": "Something went wrong",
            },
        )
        result = await handler.handle_agent_error(event)

        assert result["resolution"] == "escalated_to_user"

        user_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-user"
        ]
        assert len(user_msgs) >= 1
        parsed = json.loads(user_msgs[0][1])
        assert parsed["severity"] == "error"

    @pytest.mark.asyncio
    async def test_container_restart_failure(
        self, handler, container_manager
    ):
        container_manager.restart_container = AsyncMock(
            side_effect=RuntimeError("Docker daemon unreachable")
        )
        event = _make_event(
            EventType.AGENT_ERROR,
            source="dev",
            data={
                "error_type": "container_crash",
                "message": "Container died",
            },
        )
        result = await handler.handle_agent_error(event)

        assert result["resolution"] == "container_restart_failed"

    @pytest.mark.asyncio
    async def test_no_container_manager_warns(self, fake_redis, event_publisher):
        handler = InterventionHandler(
            redis_client=fake_redis,
            event_publisher=event_publisher,
            container_manager=None,
        )
        event = _make_event(
            EventType.AGENT_ERROR,
            source="dev",
            data={"error_type": "container_crash", "message": "crashed"},
        )
        result = await handler.handle_agent_error(event)

        assert result["resolution"] == "no_container_manager"

    @pytest.mark.asyncio
    async def test_reads_channel_messages_for_diagnosis(
        self, handler, fake_redis
    ):
        fake_redis.seed_messages("ch-test", [
            {"from": "dev", "summary": "about to call API"},
            {"from": "dev", "summary": "API returned 500"},
        ])
        event = _make_event(
            EventType.AGENT_ERROR,
            source="dev",
            channel="ch-test",
            data={"error_type": "llm_api_error", "message": "500 error"},
        )
        result = await handler.handle_agent_error(event)
        assert result["messages_read"] == 2

    @pytest.mark.asyncio
    async def test_notification_severity_warning_on_recovery(
        self, handler, fake_redis, container_manager
    ):
        event = _make_event(
            EventType.AGENT_ERROR,
            source="dev",
            data={"error_type": "container_crash", "message": "died"},
        )
        await handler.handle_agent_error(event)

        user_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-user"
        ]
        parsed = json.loads(user_msgs[0][1])
        assert parsed["severity"] == "warning"  # recovered, so warning not error


# ──────────────────────────────────────────────────
# Tests: handle_model_escalation
# ──────────────────────────────────────────────────


class TestHandleModelEscalation:
    """Test agent:model_escalation_requested intervention."""

    @pytest.mark.asyncio
    async def test_surfaces_to_user(self, handler, fake_redis):
        event = _make_event(
            EventType.AGENT_MODEL_ESCALATION_REQUESTED,
            source="dev",
            data={
                "current_model": "gpt-4o-mini",
                "requested_model": "claude-4-opus",
                "reason": "Complex architectural decision needed",
            },
        )
        result = await handler.handle_model_escalation(event)

        assert result["action"] == "model_escalation_surfaced"
        assert result["current_model"] == "gpt-4o-mini"
        assert result["requested_model"] == "claude-4-opus"

        user_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-user"
        ]
        assert len(user_msgs) == 1
        parsed = json.loads(user_msgs[0][1])
        assert parsed["severity"] == "info"
        assert "Model Escalation" in parsed["title"]
        assert parsed["data"]["type"] == "model_escalation_request"

    @pytest.mark.asyncio
    async def test_includes_context_snippet(self, handler, fake_redis):
        fake_redis.seed_messages("ch-test", [
            {"from": "dev", "summary": "Analyzing complex codebase"},
            {"from": "dev", "summary": "Need deeper reasoning for arch"},
        ])
        event = _make_event(
            EventType.AGENT_MODEL_ESCALATION_REQUESTED,
            source="dev",
            channel="ch-test",
            data={
                "current_model": "gpt-4o-mini",
                "requested_model": "claude-4-opus",
                "reason": "Complex task",
            },
        )
        result = await handler.handle_model_escalation(event)

        assert result["messages_read"] == 2

        user_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-user"
        ]
        parsed = json.loads(user_msgs[0][1])
        assert "Analyzing complex codebase" in parsed["message"]

    @pytest.mark.asyncio
    async def test_handles_empty_channel(self, handler, fake_redis):
        event = _make_event(
            EventType.AGENT_MODEL_ESCALATION_REQUESTED,
            source="dev",
            channel="",
            data={
                "current_model": "gpt-4o-mini",
                "requested_model": "claude-4-opus",
                "reason": "Need upgrade",
            },
        )
        result = await handler.handle_model_escalation(event)

        assert result["messages_read"] == 0
        assert result["action"] == "model_escalation_surfaced"

    @pytest.mark.asyncio
    async def test_truncates_long_context_snippets(self, handler, fake_redis):
        long_summary = "A" * 200
        fake_redis.seed_messages("ch-test", [
            {"from": "dev", "summary": long_summary},
        ])
        event = _make_event(
            EventType.AGENT_MODEL_ESCALATION_REQUESTED,
            source="dev",
            channel="ch-test",
            data={
                "current_model": "x",
                "requested_model": "y",
                "reason": "z",
            },
        )
        await handler.handle_model_escalation(event)

        user_msgs = [
            (ch, msg) for ch, msg in fake_redis.published
            if ch == "pa-user"
        ]
        parsed = json.loads(user_msgs[0][1])
        # Summary should be truncated to 120 chars
        assert "..." in parsed["message"]


# ──────────────────────────────────────────────────
# Tests: register_all
# ──────────────────────────────────────────────────


class TestRegisterAll:
    """Test the convenience registration method."""

    def test_registers_all_four_handlers(self, handler):
        mock_dispatcher = MagicMock()
        handler.register_all(mock_dispatcher)

        assert mock_dispatcher.register.call_count == 4

        registered_types = [
            call.args[0] for call in mock_dispatcher.register.call_args_list
        ]
        assert EventType.CHANNEL_STALLED in registered_types
        assert EventType.AGENT_TASK_BLOCKED in registered_types
        assert EventType.AGENT_ERROR in registered_types
        assert EventType.AGENT_MODEL_ESCALATION_REQUESTED in registered_types
```

---

## Integration Points

### FAITH-009 — EventSubscriber

The `PAEventDispatcher` wraps `EventSubscriber` and `StallDetector` from FAITH-009. It registers a single wildcard handler on the subscriber that routes events through the PA's own typed handler registry.

```python
# Startup wiring in the PA main entry point:
from faith.pa.event_dispatcher import PAEventDispatcher
from faith.pa.intervention import InterventionHandler

dispatcher = PAEventDispatcher(redis_client=redis)
intervention = InterventionHandler(
    redis_client=redis,
    event_publisher=event_publisher,
    container_manager=container_mgr,       # FAITH-014
    session_manager=session_mgr,           # FAITH-015
)
intervention.register_all(dispatcher)
await dispatcher.start()
```

### FAITH-015 — SessionManager

The `SessionManager` provides agent config lookups (for fallback model resolution) and notifies the dispatcher when channels/agents are created or destroyed:

```python
# When SessionManager creates a task channel:
dispatcher.register_channel("ch-auth-feature")
dispatcher.register_agent("software-developer")

# When a task is complete and channel closes:
dispatcher.unregister_channel("ch-auth-feature")
```

### FAITH-014 — ContainerManager

The `InterventionHandler` calls `ContainerManager.restart_container()` and `ContainerManager.get_container_status()` when handling `agent:error` and `agent:task_blocked` (tool blocker) events. If `ContainerManager` is not injected, restart operations log a warning and skip.

---

## Acceptance Criteria

1. `PAEventDispatcher.__init__` creates an `EventSubscriber` and `StallDetector` with configurable timeout parameters.
2. `register()` and `unregister()` correctly manage the typed handler registry; multiple handlers per event type are supported.
3. `register_wildcard()` registers handlers called for every event.
4. `_dispatch()` calls all typed and wildcard handlers concurrently via `asyncio.gather`. Handler exceptions are isolated — one failing handler does not prevent others from executing.
5. `_dispatch()` records non-None handler return values in the capped `recent_interventions` buffer.
6. `register_channel()` / `unregister_channel()` and `register_agent()` / `unregister_agent()` delegate to the `StallDetector`.
7. `start()` wires `_dispatch` into the `EventSubscriber` as a wildcard handler and starts the subscriber.
8. `InterventionHandler.handle_channel_stalled()` reads the last N messages (default 10), identifies the last non-PA agent, sends a `status_request` to that agent, and notifies the user.
9. `InterventionHandler.handle_task_blocked()` identifies blocker type (agent / tool / user), nudges blocking agents, restarts dead tool containers, or escalates to user.
10. `InterventionHandler.handle_agent_error()` restarts containers on crash / heartbeat timeout, switches to fallback model on LLM errors (if configured in `.faith/agents/{id}/config.yaml`), and escalates unknown errors to user.
11. `InterventionHandler.handle_model_escalation()` always surfaces the request to the user — never auto-approves (cost implications).
12. All interventions read only the last N messages from the channel (default 10), never full history.
13. `InterventionHandler.register_all()` registers all four handlers with a single call.
14. `PAEventDispatcher` integrates `CompletionBatcher` (FAITH-009): when the PA dispatches parallel sub-tasks, `agent:task_complete` events are routed through the batcher and only trigger an LLM call once all expected completions arrive.
15. Urgent events (`agent:error`, `channel:stalled`, `channel:loop_detected`, `approval:requested`) always bypass the batcher and dispatch immediately.
16. Batch timeout fires with partial results and triggers stall detection for still-pending tasks.
17. All tests in `tests/test_pa_event_dispatcher.py` and `tests/test_pa_intervention.py` pass, covering handler registration, dispatch, exception isolation, event batching, all four intervention types, container operations, fallback model switching, user notification, and edge cases (missing channel, no container manager, empty history, malformed messages).

---

## Notes for Implementer

- **Message history storage**: Interventions read channel history from Redis lists keyed as `channel:{channel_name}:messages`. Agents are expected to `RPUSH` each message to this list when they publish to a channel. This convention must be documented and enforced in the `BaseAgent` (FAITH-010) message send path. If that is not yet implemented, add a note in the `BaseAgent` for future alignment.
- **Last N messages only**: The FRS is explicit (Section 3.7.5) that the PA reads only the most recent N messages when joining a channel to investigate. The default of 10 is configurable via `message_read_limit`. This is a hard design constraint — resist the temptation to read more for "better context". The PA should query the agent directly for a structured status update rather than inferring state from conversation.
- **No agents.yaml**: Agent configs live at `.faith/agents/{id}/config.yaml`. The `InterventionHandler` resolves agent config via the `SessionManager`, which reads from these per-agent config files. There is no monolithic `agents.yaml`.
- **Fallback model**: The `fallback_model` field in `.faith/agents/{id}/config.yaml` is optional. If not present, the PA attempts a container restart as a last-resort recovery for LLM errors. If that also fails, it escalates to the user.
- **ContainerManager optional**: The `InterventionHandler` accepts `container_manager=None`. This supports testing and scenarios where the PA is running without Docker access (e.g. unit tests, development mode). All container operations are guarded with None checks.
- **SessionManager optional**: Similarly, `session_manager=None` is accepted. Agent config lookups return empty dicts when the session manager is unavailable.
- **User notifications**: The `_notify_user` method publishes to the `pa-user` Redis channel. The Web UI (FAITH-036) subscribes to this channel via WebSocket and renders notifications. The notification format (`type`, `title`, `message`, `severity`, `data`) should be treated as a contract — coordinate with the Web UI task.
- **Event batching**: The `CompletionBatcher` from FAITH-009 is the mechanism for this. The PA calls `batcher.expect(batch_id, task_ids)` when dispatching parallel work, then routes `agent:task_complete` events through `batcher.on_event()`. The batch callback receives a synthetic `batch:complete` event containing all results, which is then passed to the LLM in a single call. This saves significant output tokens — 4 parallel tasks produce 1 LLM call instead of 4. See FRS Section 3.7.8.
- **EventType enum**: The `EventType` enum is defined in FAITH-008. This task depends on the following values existing: `CHANNEL_STALLED`, `AGENT_TASK_BLOCKED`, `AGENT_ERROR`, `AGENT_MODEL_ESCALATION_REQUESTED`, `AGENT_HEARTBEAT`. If the FAITH-008 enum uses different names, adjust the imports accordingly.
- **FakeRedis in tests**: Tests use a custom `FakeRedis` rather than `fakeredis` to keep dependencies minimal and match the pattern established in FAITH-009 and FAITH-010 test suites.
- **Async tests**: All test methods that call async handler methods require the `@pytest.mark.asyncio` decorator and `pytest-asyncio` as a test dependency.
