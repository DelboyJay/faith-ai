"""Reactive intervention logic for PA event handling."""

from __future__ import annotations

import json
from typing import Any

from faith.protocol.compact import CompactMessage, MessageType
from faith.protocol.events import EventPublisher, EventType, FaithEvent

DEFAULT_MESSAGE_READ_LIMIT = 10


class InterventionHandler:
    """Respond to stalled channels, blocked tasks, and agent errors."""

    def __init__(
        self,
        *,
        redis_client: Any,
        event_publisher: Any | None = None,
        container_manager: Any | None = None,
        session_manager: Any | None = None,
        message_read_limit: int = DEFAULT_MESSAGE_READ_LIMIT,
    ) -> None:
        self.redis_client = redis_client
        self.event_publisher = event_publisher or EventPublisher(redis_client, source="pa")
        self.container_manager = container_manager
        self.session_manager = session_manager
        self.message_read_limit = message_read_limit

    def register_all(self, dispatcher: Any) -> None:
        dispatcher.register(EventType.CHANNEL_STALLED, self.handle_channel_stalled)
        dispatcher.register(EventType.AGENT_TASK_BLOCKED, self.handle_task_blocked)
        dispatcher.register(EventType.AGENT_ERROR, self.handle_agent_error)
        dispatcher.register(EventType.AGENT_MODEL_ESCALATION, self.handle_model_escalation)

    async def read_recent_messages(self, channel: str) -> list[dict[str, Any]]:
        raw_items = await self.redis_client.lrange(
            f"channel:{channel}:messages",
            -self.message_read_limit,
            -1,
        )
        messages: list[dict[str, Any]] = []
        for item in raw_items[-self.message_read_limit :]:
            text = item.decode("utf-8") if isinstance(item, bytes) else str(item)
            try:
                messages.append(json.loads(text))
            except json.JSONDecodeError:
                continue
        return messages

    async def handle_channel_stalled(self, event: FaithEvent) -> dict[str, Any]:
        channel = event.channel or event.data.get("channel")
        history = await self.read_recent_messages(channel) if channel else []
        last_agent = "unknown"
        for message in reversed(history):
            candidate = message.get("from") or message.get("source")
            if candidate and candidate != "pa":
                last_agent = str(candidate)
                break
        if channel and last_agent != "unknown":
            status_request = CompactMessage(
                from_agent="pa",
                to_agent=last_agent,
                channel=f"pa-{last_agent}",
                msg_id=1,
                type=MessageType.STATUS_REQUEST,
                summary=f"Status request for stalled channel {channel}",
                data={"action": "status_request", "channel": channel},
            )
            await self.redis_client.publish(f"pa-{last_agent}", status_request.to_json())
        await self._notify_user(
            title="Channel stalled",
            message=f"Channel {channel} has stalled; requested status from {last_agent}.",
            severity="warning",
            data={"channel": channel, "agent": last_agent},
        )
        return {
            "action": "status_request",
            "channel": channel,
            "agent": last_agent,
            "messages_read": len(history),
        }

    async def handle_task_blocked(self, event: FaithEvent) -> dict[str, Any]:
        blocker = str(
            event.data.get("waiting_for")
            or event.data.get("blocker")
            or event.data.get("reason", "unknown")
        )
        result = {"action": "blocker_analysis", "blocker": blocker, "messages_read": 0}
        if self.container_manager is not None and blocker.startswith("tool:"):
            tool_name = blocker.split(":", 1)[1]
            await self.container_manager.restart_container(tool_name)
            result.update(
                {
                    "restarted_tool": tool_name,
                    "resolution": "tool_restarted",
                }
            )
        else:
            await self._notify_user(
                title="Task blocked",
                message=f"Task is blocked on {blocker}.",
                severity="warning",
                data=event.data,
            )
            result["resolution"] = "escalated_to_user"
        return result

    async def handle_agent_error(self, event: FaithEvent) -> dict[str, Any]:
        agent_id = event.source
        error = str(event.data.get("error_type") or event.data.get("error", "unknown"))
        result = {"action": "error_handled", "agent": agent_id, "error": error, "messages_read": 0}
        if self.container_manager is not None and error in {
            "heartbeat_absence",
            "heartbeat_timeout",
            "container_crash",
        }:
            target = str(event.data.get("container_name") or agent_id)
            await self.container_manager.restart_container(target)
            result.update({"restarted": True, "resolution": "container_restarted"})
            return result

        fallback_model = None
        if self.session_manager is not None:
            if hasattr(self.session_manager, "load_agent_configs"):
                configs = self.session_manager.load_agent_configs()
                config = configs.get(agent_id)
                fallback_model = (
                    config.get("fallback_model")
                    if isinstance(config, dict)
                    else getattr(config, "fallback_model", None)
                )
            elif hasattr(self.session_manager, "get_agent_config"):
                config = self.session_manager.get_agent_config(agent_id) or {}
                fallback_model = (
                    config.get("fallback_model")
                    if isinstance(config, dict)
                    else getattr(config, "fallback_model", None)
                )
        if fallback_model:
            result["fallback_model"] = fallback_model
            result["resolution"] = "switched_to_fallback_model"
        else:
            result["resolution"] = "escalated_to_user"
        await self._notify_user(
            title="Agent error",
            message=f"Agent {agent_id} reported {error}.",
            severity="error",
            data=result,
        )
        return result

    async def handle_model_escalation(self, event: FaithEvent) -> dict[str, Any]:
        await self._notify_user(
            title="Model escalation requested",
            message=f"{event.source} requested a model escalation.",
            severity="info",
            data=event.data,
        )
        return {"action": "user_notified", "agent": event.source, "messages_read": 0}

    async def _notify_user(
        self,
        *,
        title: str,
        message: str,
        severity: str,
        data: dict[str, Any],
    ) -> None:
        payload = {
            "type": "notification",
            "title": title,
            "message": message,
            "severity": severity,
            "data": data,
        }
        await self.redis_client.publish("pa-user", json.dumps(payload))
