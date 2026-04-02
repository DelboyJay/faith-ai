"""Description:
    Route tool calls through either native MCP execution or prompt-based fallback execution.

Requirements:
    - Publish tool-start, tool-complete, and tool-error events when an event publisher is available.
    - Delegate MCP-native agents to direct MCP execution.
    - Delegate non-MCP-native agents to prompt-mediated execution.
"""

from __future__ import annotations

import asyncio
from typing import Any

from faith_pa.pa.mcp_adapter import MCPAdapter
from faith_shared.protocol.compact import CompactMessage
from faith_shared.protocol.events import EventPublisher


class ToolRouter:
    """Description:
        Route compact tool-call messages through the appropriate execution path.

    Requirements:
        - Use the MCP adapter to decide whether an agent is MCP-native.
        - Publish tool lifecycle events when an event publisher is configured.

    :param adapter: MCP adapter used for translation and capability checks.
    :param event_publisher: Optional event publisher for tool lifecycle events.
    """

    def __init__(self, adapter: MCPAdapter, event_publisher: EventPublisher | None = None) -> None:
        """Description:
            Initialise the tool router.

        Requirements:
            - Preserve the supplied adapter and optional event publisher.

        :param adapter: MCP adapter used for translation and capability checks.
        :param event_publisher: Optional event publisher for tool lifecycle events.
        """

        self.adapter = adapter
        self.event_publisher = event_publisher

    async def route_tool_call(
        self,
        agent_id: str,
        message: CompactMessage,
        execute_mcp: Any,
        execute_prompt: Any,
    ) -> CompactMessage:
        """Description:
            Execute one tool call through either MCP or prompt fallback routing.

        Requirements:
            - Publish a tool-start event before execution when possible.
            - Route MCP-native agents through direct MCP execution.
            - Route non-MCP-native agents through prompt execution.
            - Publish tool-complete or tool-error events when possible.

        :param agent_id: Agent requesting the tool execution.
        :param message: Compact tool-call message.
        :param execute_mcp: Callable used for direct MCP execution.
        :param execute_prompt: Callable used for prompt fallback execution.
        :returns: Compact tool-result message.
        :raises Exception: Re-raises execution failures after publishing a tool-error event.
        """

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
    """Description:
        Await coroutine results while leaving plain values unchanged.

    Requirements:
        - Await the value only when it is a coroutine object.

    :param value: Value or coroutine to normalise.
    :returns: Awaited or original value.
    """

    if asyncio.iscoroutine(value):
        return await value
    return value
