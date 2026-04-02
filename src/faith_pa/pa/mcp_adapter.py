"""Description:
    Translate between FAITH compact tool-call messages and MCP request/response payloads.

Requirements:
    - Convert compact tool-call messages into standard MCP ``tools/call`` payloads.
    - Convert MCP responses back into compact tool-result messages.
    - Provide a prompt-based fallback representation for non-MCP-native agents.
"""

from __future__ import annotations

import json
import re
from typing import Any

from faith_shared.protocol.compact import CompactMessage, MessageStatus, MessageType


class MCPAdapter:
    """Description:
        Translate tool-call traffic between the FAITH compact protocol and MCP-style payloads.

    Requirements:
        - Detect whether an agent is configured as MCP-native.
        - Support both direct MCP translation and prompt-based fallback translation.

    :param agent_configs: Optional per-agent configuration payloads.
    """

    def __init__(self, agent_configs: dict[str, dict[str, Any]] | None = None) -> None:
        """Description:
            Initialise the MCP adapter.

        Requirements:
            - Default to an empty agent-configuration map when none is supplied.

        :param agent_configs: Optional per-agent configuration payloads.
        """

        self.agent_configs = agent_configs or {}

    def is_mcp_native(self, agent_id: str) -> bool:
        """Description:
            Return whether the named agent is configured for native MCP tool calls.

        Requirements:
            - Treat missing configuration as non-MCP-native.

        :param agent_id: Agent identifier to inspect.
        :returns: ``True`` when the agent is MCP-native.
        """

        return bool(self.agent_configs.get(agent_id, {}).get("mcp_native", False))

    def translate_to_mcp(self, compact_msg: CompactMessage) -> dict[str, Any]:
        """Description:
            Translate one FAITH compact tool-call message into an MCP request payload.

        Requirements:
            - Accept only ``tool_call`` compact messages.
            - Derive the MCP tool name from the tool and action fields.

        :param compact_msg: Compact tool-call message to translate.
        :returns: MCP ``tools/call`` request payload.
        :raises ValueError: If the supplied message is not a tool call.
        """

        self._require_tool_call(compact_msg)
        tool = compact_msg.data.get("tool", "unknown")
        action = compact_msg.data.get("action", "")
        arguments = compact_msg.data.get("args", {})
        name = f"{tool}_{action}" if action else tool
        return {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
            "id": f"msg-{compact_msg.msg_id}",
        }

    def translate_from_mcp(
        self, mcp_response: dict[str, Any], original_msg: CompactMessage
    ) -> CompactMessage:
        """Description:
            Translate one MCP response back into a FAITH compact result message.

        Requirements:
            - Preserve the original tool-call metadata.
            - Mark failures when the MCP payload contains an ``error`` object.

        :param mcp_response: MCP response payload.
        :param original_msg: Original compact tool-call message.
        :returns: Compact tool-result message.
        """

        data = dict(original_msg.data)
        if "error" in mcp_response:
            error_info = mcp_response["error"]
            data["success"] = False
            data["error"] = (
                error_info.get("message", str(error_info))
                if isinstance(error_info, dict)
                else str(error_info)
            )
        else:
            data["success"] = True
            data["result"] = mcp_response.get("result", {})
        return self._build_result_message(original_msg, data)

    def translate_to_prompt(self, compact_msg: CompactMessage) -> str:
        """Description:
            Render one compact tool-call message into a prompt-friendly instruction block.

        Requirements:
            - Accept only ``tool_call`` compact messages.
            - Include the tool, action, and structured arguments in the prompt.

        :param compact_msg: Compact tool-call message to translate.
        :returns: Prompt text for non-MCP-native execution.
        :raises ValueError: If the supplied message is not a tool call.
        """

        self._require_tool_call(compact_msg)
        tool = compact_msg.data.get("tool", "unknown")
        action = compact_msg.data.get("action", "")
        arguments = compact_msg.data.get("args", {})
        args_block = (
            "\n".join(f"  - {key}: {json.dumps(value)}" for key, value in arguments.items())
            or "  (none)"
        )
        return (
            "Execute the requested tool action mechanically. Return only JSON with keys: "
            "success (boolean), result (object/string if success), error (string if failure).\n\n"
            f"Tool: {tool}\n"
            f"Action: {action}\n"
            f"Arguments:\n{args_block}"
        )

    def parse_prompt_result(self, output: str, original_msg: CompactMessage) -> CompactMessage:
        """Description:
            Parse a prompt-based tool result back into a compact tool-result message.

        Requirements:
            - Extract the first JSON object from the output when one exists.
            - Fall back to a failed result when parsing is not possible.

        :param output: Raw prompt execution output.
        :param original_msg: Original compact tool-call message.
        :returns: Compact tool-result message.
        """

        match = re.search(r"\{.*\}", output, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = {"success": False, "error": output.strip()}
        else:
            parsed = {"success": False, "error": output.strip()}

        data = dict(original_msg.data)
        data.update(parsed)
        return self._build_result_message(original_msg, data)

    def _build_result_message(
        self, original_msg: CompactMessage, data: dict[str, Any]
    ) -> CompactMessage:
        """Description:
            Build a compact tool-result message from translated tool output.

        Requirements:
            - Mark successful calls as ``complete`` and failures as ``blocked``.
            - Preserve the original channel and tags while incrementing the message identifier.

        :param original_msg: Original compact tool-call message.
        :param data: Result payload to attach.
        :returns: Compact tool-result message.
        """

        status = MessageStatus.COMPLETE if data.get("success") else MessageStatus.BLOCKED
        summary = (
            f"Tool call completed for {data.get('tool', 'tool')}"
            if data.get("success")
            else f"Tool call failed: {data.get('error', 'Unknown failure')}"
        )
        return CompactMessage(
            **{
                "from": "pa",
                "to": original_msg.from_agent,
                "channel": original_msg.channel,
                "msg_id": original_msg.msg_id + 1,
                "type": MessageType.TOOL_RESULT,
                "tags": list(original_msg.tags),
                "summary": summary,
                "status": status,
                "context_ref": f"{original_msg.channel}/msg-{original_msg.msg_id}",
                "data": data,
            }
        )

    @staticmethod
    def _require_tool_call(compact_msg: CompactMessage) -> None:
        """Description:
            Validate that the supplied compact message is a tool call.

        Requirements:
            - Raise ``ValueError`` for any non-tool-call message type.

        :param compact_msg: Compact message to validate.
        :raises ValueError: If the message is not a tool call.
        """

        if compact_msg.type != MessageType.TOOL_CALL:
            raise ValueError(f"Expected tool_call message, got {compact_msg.type.value}")


__all__ = ["MCPAdapter"]
