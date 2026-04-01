"""FAITH event system models and Redis publisher."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import redis.asyncio as aioredis
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from faith.utils.redis_client import SYSTEM_EVENTS_CHANNEL

logger = logging.getLogger("faith.protocol.events")


class EventType(str, Enum):
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
        return self.event

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, exclude_none=True)

    @classmethod
    def from_json(cls, json_str: str) -> FaithEvent:
        return cls.model_validate_json(json_str)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


class EventPublisher:
    def __init__(self, redis_client: aioredis.Redis, source: str):
        self.redis_client = redis_client
        self.source = source

    async def publish(self, event: FaithEvent) -> None:
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
        data = {"reason": reason}
        if waiting_for is not None:
            data["waiting_for"] = waiting_for
        await self.publish(self._event(EventType.AGENT_TASK_BLOCKED, channel=channel, data=data))

    async def agent_needs_input(self, channel: str, question: str) -> None:
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
        await self.publish(
            self._event(
                EventType.AGENT_ERROR,
                source=agent,
                channel=channel,
                data={"error": error, "recoverable": recoverable},
            )
        )

    async def agent_heartbeat(self, agent: str | None = None) -> None:
        await self.publish(self._event(EventType.AGENT_HEARTBEAT, source=agent))

    async def agent_model_escalation(self, channel: str, reason: str, current_model: str) -> None:
        await self.publish(
            self._event(
                EventType.AGENT_MODEL_ESCALATION,
                channel=channel,
                data={"reason": reason, "current_model": current_model},
            )
        )

    async def agent_context_summary(
        self, channel: str | None = None, summary: str | None = None
    ) -> None:
        data: dict[str, Any] = {}
        if summary is not None:
            data["summary"] = summary
        await self.publish(self._event(EventType.AGENT_CONTEXT_SUMMARY, channel=channel, data=data))

    async def channel_stalled(self, channel: str, idle_seconds: int) -> None:
        await self.publish(
            self._event(
                EventType.CHANNEL_STALLED,
                channel=channel,
                data={"idle_seconds": idle_seconds},
            )
        )

    async def channel_goal_achieved(self, channel: str) -> None:
        await self.publish(self._event(EventType.CHANNEL_GOAL_ACHIEVED, channel=channel))

    async def channel_loop_detected(
        self,
        channel: str,
        description: str,
        agents_involved: list[str],
    ) -> None:
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
        await self.publish(
            self._event(
                EventType.TOOL_CALL_COMPLETE,
                source=tool,
                channel=channel,
                data={"action": action, "agent": agent, "success": success},
            )
        )

    async def tool_permission_denied(self, tool: str, action: str, agent: str, reason: str) -> None:
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
        data: dict[str, Any] = {
            "path": path,
            "sha256_before": sha256_before,
            "sha256_after": sha256_after,
        }
        if agent is not None:
            data["agent"] = agent
        await self.publish(self._event(EventType.FILE_CHANGED, data=data))

    async def file_created(self, path: str) -> None:
        await self.publish(self._event(EventType.FILE_CREATED, data={"path": path}))

    async def file_deleted(self, path: str) -> None:
        await self.publish(self._event(EventType.FILE_DELETED, data={"path": path}))

    async def approval_requested(
        self,
        request_id: str,
        agent: str,
        action: str,
        detail: str,
        channel: str | None = None,
    ) -> None:
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
        await self.publish(
            self._event(
                EventType.APPROVAL_DECISION,
                data={"request_id": request_id, "decision": decision, "agent": agent},
            )
        )

    async def resource_token_threshold(self, pct_used: float, tokens_used: int, model: str) -> None:
        await self.publish(
            self._event(
                EventType.RESOURCE_TOKEN_THRESHOLD,
                data={"pct_used": pct_used, "tokens_used": tokens_used, "model": model},
            )
        )

    async def resource_token_critical(self, pct_used: float, tokens_used: int, model: str) -> None:
        await self.publish(
            self._event(
                EventType.RESOURCE_TOKEN_CRITICAL,
                data={"pct_used": pct_used, "tokens_used": tokens_used, "model": model},
            )
        )

    async def system_config_changed(self, file: str, path: str) -> None:
        await self.publish(
            self._event(EventType.SYSTEM_CONFIG_CHANGED, data={"file": file, "path": path})
        )

    async def system_container_started(self, container_name: str, container_type: str) -> None:
        await self.publish(
            self._event(
                EventType.SYSTEM_CONTAINER_STARTED,
                data={"container_name": container_name, "container_type": container_type},
            )
        )

    async def system_container_stopped(self, container_name: str, reason: str = "normal") -> None:
        await self.publish(
            self._event(
                EventType.SYSTEM_CONTAINER_STOPPED,
                data={"container_name": container_name, "reason": reason},
            )
        )

    async def system_container_error(self, container_name: str, error: str) -> None:
        await self.publish(
            self._event(
                EventType.SYSTEM_CONTAINER_ERROR,
                data={"container_name": container_name, "error": error},
            )
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
