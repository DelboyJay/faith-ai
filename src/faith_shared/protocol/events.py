"""
Description:
    Define the shared FAITH event models and Redis-backed publisher used across
    the PA, Web UI, and supporting runtime components.

Requirements:
    - Preserve the canonical event vocabulary used by the protocol.
    - Provide serialisation helpers and publishing helpers for common runtime
      events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import redis.asyncio as aioredis
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

logger = logging.getLogger("faith.protocol.events")
SYSTEM_EVENTS_CHANNEL = "system-events"


class EventType(str, Enum):
    """
    Description:
        Define the canonical event names published on the FAITH system event bus.

    Requirements:
        - Preserve the event names used by the PA, Web UI, approvals, tools, and
          runtime supervision layers.
    """

    AGENT_TASK_COMPLETE = "agent:task_complete"
    AGENT_TASK_BLOCKED = "agent:task_blocked"
    AGENT_NEEDS_INPUT = "agent:needs_input"
    AGENT_ERROR = "agent:error"
    AGENT_HEARTBEAT = "agent:heartbeat"
    AGENT_MODEL_ESCALATION = "agent:model_escalation_requested"
    AGENT_CONTEXT_SUMMARY = "agent:context_summary_triggered"

    CHANNEL_STALLED = "channel:stalled"
    CHANNEL_GOAL_ACHIEVED = "channel:goal_achieved"
    CHANNEL_LOOP_DETECTED = "channel:loop_detected"

    TOOL_CALL_STARTED = "tool:call_started"
    TOOL_CALL_COMPLETE = "tool:call_complete"
    TOOL_PERMISSION_DENIED = "tool:permission_denied"
    TOOL_ERROR = "tool:error"

    FILE_CHANGED = "file:changed"
    FILE_CREATED = "file:created"
    FILE_DELETED = "file:deleted"

    APPROVAL_REQUESTED = "approval:requested"
    APPROVAL_DECISION = "approval:decision"

    RESOURCE_TOKEN_THRESHOLD = "resource:token_threshold"
    RESOURCE_TOKEN_CRITICAL = "resource:token_critical"

    SYSTEM_CONFIG_CHANGED = "system:config_changed"
    SYSTEM_CONFIG_ERROR = "system:config_error"
    SYSTEM_CONTAINER_STARTED = "system:container_started"
    SYSTEM_CONTAINER_STOPPED = "system:container_stopped"
    SYSTEM_CONTAINER_ERROR = "system:container_error"

    BATCH_COMPLETE = "batch:complete"
    BATCH_TIMEOUT = "batch:timeout"
    BATCH_PARTIAL = "batch:partial"


class FaithEvent(BaseModel):
    """
    Description:
        Represent one event-bus payload on the FAITH system event channel.

    Requirements:
        - Accept either `event` or `event_type` when validating payloads.
        - Preserve source, optional channel, timestamp, and structured data.
    """

    event: EventType = Field(
        validation_alias=AliasChoices("event", "event_type"),
        serialization_alias="event",
    )
    source: str
    channel: str | None = None
    ts: str = Field(default_factory=lambda: _now_iso())
    data: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    @property
    def event_type(self) -> EventType:
        """
        Description:
            Return the canonical event type using the alternate accessor name.

        Requirements:
            - Preserve backward compatibility for call sites using `event_type`.

        :returns: Canonical event type value.
        """
        return self.event

    def to_json(self) -> str:
        """
        Description:
            Serialise the event to JSON for transport on Redis.

        Requirements:
            - Use alias names and omit `None` fields from the payload.

        :returns: JSON-encoded event payload.
        """
        return self.model_dump_json(by_alias=True, exclude_none=True)

    @classmethod
    def from_json(cls, json_str: str) -> FaithEvent:
        """
        Description:
            Rebuild an event from a JSON payload.

        Requirements:
            - Validate the JSON payload against the event model.

        :param json_str: JSON-encoded event payload.
        :returns: Reconstructed event object.
        """
        return cls.model_validate_json(json_str)

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the event to a JSON-safe dictionary.

        Requirements:
            - Use alias names and omit `None` fields from the output.

        :returns: JSON-safe event payload dictionary.
        """
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


class EventPublisher:
    """
    Description:
        Publish FAITH runtime events onto the shared Redis system-events channel.

    Requirements:
        - Provide helper methods for the common event shapes used by the PA and
          surrounding services.
        - Fail softly and log publication errors rather than crashing callers.

    :param redis_client: Redis client used to publish event payloads.
    :param source: Default source name recorded on emitted events.
    """

    def __init__(self, redis_client: aioredis.Redis, source: str):
        """
        Description:
            Store the Redis client and default event source.

        Requirements:
            - Preserve the client and source unchanged for later publish calls.

        :param redis_client: Redis client used to publish event payloads.
        :param source: Default source name recorded on emitted events.
        """
        self.redis_client = redis_client
        self.source = source

    async def publish(self, event: FaithEvent) -> None:
        """
        Description:
            Publish one event payload to the shared system event channel.

        Requirements:
            - Publish to the canonical `SYSTEM_EVENTS_CHANNEL`.
            - Log publication failures instead of raising them to the caller.

        :param event: Event payload to publish.
        """
        try:
            await self.redis_client.publish(SYSTEM_EVENTS_CHANNEL, event.to_json())
        except Exception:
            logger.exception("Failed to publish event: %s", event.event.value)

    def _event(
        self,
        event_type: EventType,
        *,
        data: dict[str, Any] | None = None,
        channel: str | None = None,
        source: str | None = None,
    ) -> FaithEvent:
        """
        Description:
            Build one event object using the publisher defaults.

        Requirements:
            - Fall back to the publisher's default source when the caller does
              not supply one.

        :param event_type: Event type to publish.
        :param data: Optional structured event payload.
        :param channel: Optional channel associated with the event.
        :param source: Optional explicit source override.
        :returns: Event object ready for publication.
        """
        return FaithEvent(
            event=event_type,
            source=source or self.source,
            channel=channel,
            data=data or {},
        )

    async def agent_task_complete(
        self,
        channel: str,
        task: str,
        msg_id: int | None = None,
        files_written: int = 0,
    ) -> None:
        """
        Description:
            Publish an agent-task-complete event.

        Requirements:
            - Include the task name and files-written count.
            - Include `msg_id` only when the caller supplies it.

        :param channel: Channel whose task completed.
        :param task: Task name associated with the completion.
        :param msg_id: Optional message identifier.
        :param files_written: Number of files written during the task.
        """
        await self.publish(
            self._event(
                EventType.AGENT_TASK_COMPLETE,
                channel=channel,
                data={
                    "task": task,
                    **({"msg_id": msg_id} if msg_id is not None else {}),
                    "files_written": files_written,
                },
            )
        )

    async def agent_task_blocked(
        self,
        channel: str,
        reason: str,
        waiting_for: str | None = None,
    ) -> None:
        """
        Description:
            Publish an agent-task-blocked event.

        Requirements:
            - Always include the blocking reason.
            - Include `waiting_for` only when supplied.

        :param channel: Channel whose task is blocked.
        :param reason: Reason the task is blocked.
        :param waiting_for: Optional dependency or input being awaited.
        """
        data = {"reason": reason}
        if waiting_for is not None:
            data["waiting_for"] = waiting_for
        await self.publish(self._event(EventType.AGENT_TASK_BLOCKED, channel=channel, data=data))

    async def agent_needs_input(self, channel: str, question: str) -> None:
        """
        Description:
            Publish an agent-needs-input event.

        Requirements:
            - Include the user-facing question that should be surfaced.

        :param channel: Channel requesting input.
        :param question: User-facing question to display.
        """
        await self.publish(
            self._event(EventType.AGENT_NEEDS_INPUT, channel=channel, data={"question": question})
        )

    async def agent_error(
        self,
        error: str,
        channel: str | None = None,
        recoverable: bool = False,
        agent: str | None = None,
    ) -> None:
        """
        Description:
            Publish an agent-error event.

        Requirements:
            - Include the error text and recoverable flag.
            - Allow an explicit agent source override.

        :param error: Error text to publish.
        :param channel: Optional related channel.
        :param recoverable: Whether the error is recoverable.
        :param agent: Optional explicit agent source name.
        """
        await self.publish(
            self._event(
                EventType.AGENT_ERROR,
                source=agent,
                channel=channel,
                data={"error": error, "recoverable": recoverable},
            )
        )

    async def agent_heartbeat(self, agent: str | None = None) -> None:
        """
        Description:
            Publish an agent-heartbeat event.

        Requirements:
            - Allow an explicit agent source override.

        :param agent: Optional explicit agent source name.
        """
        await self.publish(self._event(EventType.AGENT_HEARTBEAT, source=agent))

    async def agent_model_escalation(self, channel: str, reason: str, current_model: str) -> None:
        """
        Description:
            Publish an agent-model-escalation event.

        Requirements:
            - Include the escalation reason and current model name.

        :param channel: Channel requesting escalation.
        :param reason: Reason escalation is needed.
        :param current_model: Model currently in use.
        """
        await self.publish(
            self._event(
                EventType.AGENT_MODEL_ESCALATION,
                channel=channel,
                data={"reason": reason, "current_model": current_model},
            )
        )

    async def agent_context_summary(
        self,
        channel: str | None = None,
        summary: str | None = None,
    ) -> None:
        """
        Description:
            Publish an agent-context-summary event.

        Requirements:
            - Include the summary text only when supplied.

        :param channel: Optional related channel.
        :param summary: Optional generated context summary.
        """
        data: dict[str, Any] = {}
        if summary is not None:
            data["summary"] = summary
        await self.publish(self._event(EventType.AGENT_CONTEXT_SUMMARY, channel=channel, data=data))

    async def channel_stalled(self, channel: str, idle_seconds: int) -> None:
        """
        Description:
            Publish a channel-stalled event.

        Requirements:
            - Include the measured idle duration.

        :param channel: Stalled channel name.
        :param idle_seconds: Idle duration in seconds.
        """
        await self.publish(
            self._event(
                EventType.CHANNEL_STALLED,
                channel=channel,
                data={"idle_seconds": idle_seconds},
            )
        )

    async def channel_goal_achieved(self, channel: str) -> None:
        """
        Description:
            Publish a channel-goal-achieved event.

        Requirements:
            - Include the completed channel name.

        :param channel: Channel whose goal was achieved.
        """
        await self.publish(self._event(EventType.CHANNEL_GOAL_ACHIEVED, channel=channel))

    async def channel_loop_detected(
        self,
        channel: str,
        description: str,
        agents_involved: list[str],
    ) -> None:
        """
        Description:
            Publish a channel-loop-detected event.

        Requirements:
            - Include the descriptive text and the agents involved in the loop.

        :param channel: Channel where the loop was detected.
        :param description: Human-readable loop description.
        :param agents_involved: Agents involved in the detected loop.
        """
        await self.publish(
            self._event(
                EventType.CHANNEL_LOOP_DETECTED,
                channel=channel,
                data={"description": description, "agents_involved": agents_involved},
            )
        )

    async def tool_call_started(
        self,
        tool: str,
        action: str,
        agent: str,
        channel: str | None = None,
    ) -> None:
        """
        Description:
            Publish a tool-call-started event.

        Requirements:
            - Use the tool name as the event source.
            - Include the tool action and agent name.

        :param tool: Tool emitting the event.
        :param action: Action being started.
        :param agent: Agent invoking the tool.
        :param channel: Optional related channel.
        """
        await self.publish(
            self._event(
                EventType.TOOL_CALL_STARTED,
                source=tool,
                channel=channel,
                data={"action": action, "agent": agent},
            )
        )

    async def tool_call_complete(
        self,
        tool: str,
        action: str,
        agent: str,
        success: bool = True,
        channel: str | None = None,
    ) -> None:
        """
        Description:
            Publish a tool-call-complete event.

        Requirements:
            - Use the tool name as the event source.
            - Include the tool action, agent name, and success flag.

        :param tool: Tool emitting the event.
        :param action: Action that completed.
        :param agent: Agent invoking the tool.
        :param success: Whether the tool call succeeded.
        :param channel: Optional related channel.
        """
        await self.publish(
            self._event(
                EventType.TOOL_CALL_COMPLETE,
                source=tool,
                channel=channel,
                data={"action": action, "agent": agent, "success": success},
            )
        )

    async def tool_permission_denied(self, tool: str, action: str, agent: str, reason: str) -> None:
        """
        Description:
            Publish a tool-permission-denied event.

        Requirements:
            - Include the tool action, agent name, and denial reason.

        :param tool: Tool emitting the event.
        :param action: Action that was denied.
        :param agent: Agent invoking the tool.
        :param reason: Human-readable denial reason.
        """
        await self.publish(
            self._event(
                EventType.TOOL_PERMISSION_DENIED,
                source=tool,
                data={"action": action, "agent": agent, "reason": reason},
            )
        )

    async def tool_error(
        self,
        tool: str,
        error: str,
        agent: str | None = None,
        raw_content_available: bool = False,
    ) -> None:
        """
        Description:
            Publish a tool-error event.

        Requirements:
            - Include the error text and raw-content flag.
            - Include the agent name only when supplied.

        :param tool: Tool emitting the event.
        :param error: Error text to publish.
        :param agent: Optional agent invoking the tool.
        :param raw_content_available: Whether raw tool content was preserved.
        """
        data: dict[str, Any] = {"error": error, "raw_content_available": raw_content_available}
        if agent is not None:
            data["agent"] = agent
        await self.publish(self._event(EventType.TOOL_ERROR, source=tool, data=data))

    async def file_changed(
        self,
        path: str,
        sha256_before: str,
        sha256_after: str,
        agent: str | None = None,
    ) -> None:
        """
        Description:
            Publish a file-changed event.

        Requirements:
            - Include the path and before/after digests.
            - Include the agent name only when supplied.

        :param path: Changed path.
        :param sha256_before: Previous file digest.
        :param sha256_after: New file digest.
        :param agent: Optional agent responsible for the change.
        """
        data: dict[str, Any] = {
            "path": path,
            "sha256_before": sha256_before,
            "sha256_after": sha256_after,
        }
        if agent is not None:
            data["agent"] = agent
        await self.publish(self._event(EventType.FILE_CHANGED, data=data))

    async def file_created(self, path: str) -> None:
        """
        Description:
            Publish a file-created event.

        Requirements:
            - Include the created path.

        :param path: Created path.
        """
        await self.publish(self._event(EventType.FILE_CREATED, data={"path": path}))

    async def file_deleted(self, path: str) -> None:
        """
        Description:
            Publish a file-deleted event.

        Requirements:
            - Include the deleted path.

        :param path: Deleted path.
        """
        await self.publish(self._event(EventType.FILE_DELETED, data={"path": path}))

    async def approval_requested(
        self,
        request_id: str,
        agent: str,
        action: str,
        detail: str,
        channel: str | None = None,
    ) -> None:
        """
        Description:
            Publish an approval-requested event.

        Requirements:
            - Include the request id, agent, action, and detail text.

        :param request_id: Approval request identifier.
        :param agent: Agent requesting approval.
        :param action: Action requiring approval.
        :param detail: Human-readable request detail.
        :param channel: Optional related channel.
        """
        await self.publish(
            self._event(
                EventType.APPROVAL_REQUESTED,
                channel=channel,
                data={
                    "request_id": request_id,
                    "agent": agent,
                    "action": action,
                    "detail": detail,
                },
            )
        )

    async def approval_decision(self, request_id: str, decision: str, agent: str) -> None:
        """
        Description:
            Publish an approval-decision event.

        Requirements:
            - Include the request id, decision, and acting agent.

        :param request_id: Approval request identifier.
        :param decision: Approval decision taken.
        :param agent: Agent or actor associated with the decision.
        """
        await self.publish(
            self._event(
                EventType.APPROVAL_DECISION,
                data={"request_id": request_id, "decision": decision, "agent": agent},
            )
        )

    async def resource_token_threshold(self, pct_used: float, tokens_used: int, model: str) -> None:
        """
        Description:
            Publish a resource-token-threshold warning event.

        Requirements:
            - Include the usage percentage, token count, and model name.

        :param pct_used: Percentage of the token budget used.
        :param tokens_used: Number of tokens used.
        :param model: Model name associated with the budget.
        """
        await self.publish(
            self._event(
                EventType.RESOURCE_TOKEN_THRESHOLD,
                data={"pct_used": pct_used, "tokens_used": tokens_used, "model": model},
            )
        )

    async def resource_token_critical(self, pct_used: float, tokens_used: int, model: str) -> None:
        """
        Description:
            Publish a resource-token-critical warning event.

        Requirements:
            - Include the usage percentage, token count, and model name.

        :param pct_used: Percentage of the token budget used.
        :param tokens_used: Number of tokens used.
        :param model: Model name associated with the budget.
        """
        await self.publish(
            self._event(
                EventType.RESOURCE_TOKEN_CRITICAL,
                data={"pct_used": pct_used, "tokens_used": tokens_used, "model": model},
            )
        )

    async def system_config_changed(self, file: str, path: str) -> None:
        """
        Description:
            Publish a system-config-changed event.

        Requirements:
            - Include the config file name and path.

        :param file: Config file name.
        :param path: Config file path.
        """
        await self.publish(
            self._event(EventType.SYSTEM_CONFIG_CHANGED, data={"file": file, "path": path})
        )

    async def system_config_error(
        self,
        file: str,
        error: str,
        path: str | None = None,
    ) -> None:
        """
        Description:
            Publish a system-config-error event.

        Requirements:
            - Include the config file name and validation error text.
            - Include the config path only when supplied.

        :param file: Config file name that failed validation.
        :param error: Validation or reload error text.
        :param path: Optional config file path.
        """
        data: dict[str, Any] = {"file": file, "error": error}
        if path is not None:
            data["path"] = path
        await self.publish(self._event(EventType.SYSTEM_CONFIG_ERROR, data=data))

    async def system_container_started(self, container_name: str, container_type: str) -> None:
        """
        Description:
            Publish a system-container-started event.

        Requirements:
            - Include the container name and container type.

        :param container_name: Name of the started container.
        :param container_type: Logical type of the started container.
        """
        await self.publish(
            self._event(
                EventType.SYSTEM_CONTAINER_STARTED,
                data={"container_name": container_name, "container_type": container_type},
            )
        )

    async def system_container_stopped(self, container_name: str, reason: str = "normal") -> None:
        """
        Description:
            Publish a system-container-stopped event.

        Requirements:
            - Include the container name and stop reason.

        :param container_name: Name of the stopped container.
        :param reason: Reason the container stopped.
        """
        await self.publish(
            self._event(
                EventType.SYSTEM_CONTAINER_STOPPED,
                data={"container_name": container_name, "reason": reason},
            )
        )

    async def system_container_error(self, container_name: str, error: str) -> None:
        """
        Description:
            Publish a system-container-error event.

        Requirements:
            - Include the container name and error text.

        :param container_name: Name of the container that errored.
        :param error: Error text to publish.
        """
        await self.publish(
            self._event(
                EventType.SYSTEM_CONTAINER_ERROR,
                data={"container_name": container_name, "error": error},
            )
        )

    async def batch_complete(
        self,
        batch_id: str,
        results: list[dict[str, Any]],
        count: int | None = None,
    ) -> None:
        """
        Description:
            Publish a batch-complete event.

        Requirements:
            - Include the batch identifier and buffered result payloads.
            - Default the count to the number of supplied results.

        :param batch_id: Completion batch identifier.
        :param results: Buffered completion result payloads.
        :param count: Optional explicit result count override.
        """
        await self.publish(
            self._event(
                EventType.BATCH_COMPLETE,
                data={
                    "batch_id": batch_id,
                    "results": results,
                    "count": len(results) if count is None else count,
                },
            )
        )

    async def batch_timeout(
        self,
        batch_id: str,
        completed_results: list[dict[str, Any]],
        still_pending: list[str],
    ) -> None:
        """
        Description:
            Publish a batch-timeout event.

        Requirements:
            - Include both the completed results and the task identifiers still pending.
            - Record completed and pending counts explicitly.

        :param batch_id: Completion batch identifier.
        :param completed_results: Buffered results received before timeout.
        :param still_pending: Task identifiers still outstanding when the timeout fired.
        """
        await self.publish(
            self._event(
                EventType.BATCH_TIMEOUT,
                data={
                    "batch_id": batch_id,
                    "completed_results": completed_results,
                    "completed_count": len(completed_results),
                    "still_pending": still_pending,
                    "pending_count": len(still_pending),
                },
            )
        )

    async def batch_partial(
        self,
        batch_id: str,
        results: list[dict[str, Any]],
        still_pending: list[str],
    ) -> None:
        """
        Description:
            Publish a batch-partial event.

        Requirements:
            - Include the buffered results and the remaining pending task identifiers.
            - Record the number of completed results explicitly.

        :param batch_id: Completion batch identifier.
        :param results: Buffered results received so far.
        :param still_pending: Task identifiers still outstanding.
        """
        await self.publish(
            self._event(
                EventType.BATCH_PARTIAL,
                data={
                    "batch_id": batch_id,
                    "results": results,
                    "count": len(results),
                    "still_pending": still_pending,
                },
            )
        )


def _now_iso() -> str:
    """
    Description:
        Return the current UTC time in the canonical FAITH event timestamp format.

    Requirements:
        - Emit timestamps in UTC with a trailing `Z` designator.

    :returns: Current UTC timestamp string.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
