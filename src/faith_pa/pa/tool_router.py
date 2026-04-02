"""Minimal PA tool routing for MCP-native vs prompt-mediated flows."""

from __future__ import annotations

import asyncio
from typing import Any

from faith_pa.pa.mcp_adapter import MCPAdapter
from faith_shared.protocol.compact import CompactMessage
from faith_shared.protocol.events import EventPublisher


class ToolRouter:
    def __init__(self, adapter: MCPAdapter, event_publisher: EventPublisher | None = None) -> None:
        self.adapter = adapter
        self.event_publisher = event_publisher

    async def route_tool_call(
        self,
        agent_id: str,
        message: CompactMessage,
        execute_mcp: Any,
        execute_prompt: Any,
    ) -> CompactMessage:
        tool = message.data.get("tool", "unknown")
        action = message.data.get("action", "")
        if self.event_publisher is not None:
            await self.event_publisher.tool_call_started(
                tool=tool, action=action, agent=agent_id, channel=message.channel
            )

        try:
            if self.adapter.is_mcp_native(agent_id):
                request = self.adapter.translate_to_mcp(message)
                response = await _maybe_await(execute_mcp(request))
                result = self.adapter.translate_from_mcp(response, message)
            else:
                prompt = self.adapter.translate_to_prompt(message)
                response_text = await _maybe_await(execute_prompt(prompt))
                result = self.adapter.parse_prompt_result(response_text, message)
        except Exception as exc:
            if self.event_publisher is not None:
                await self.event_publisher.tool_error(tool=tool, error=str(exc), agent=agent_id)
            raise

        if self.event_publisher is not None:
            await self.event_publisher.tool_call_complete(
                tool=tool,
                action=action,
                agent=agent_id,
                success=result.data.get("success", False),
                channel=message.channel,
            )
        return result


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value

