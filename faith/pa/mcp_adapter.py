"""FAITH MCP Adapter — transparent protocol translation layer."""

from __future__ import annotations

import json
import re
from typing import Any

from faith.protocol.compact import CompactMessage, MessageStatus, MessageType


class MCPAdapter:
    def __init__(self, agent_configs: dict[str, dict[str, Any]] | None = None) -> None:
        self.agent_configs = agent_configs or {}

    def is_mcp_native(self, agent_id: str) -> bool:
        return bool(self.agent_configs.get(agent_id, {}).get("mcp_native", False))

    def translate_to_mcp(self, compact_msg: CompactMessage) -> dict[str, Any]:
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
        if compact_msg.type != MessageType.TOOL_CALL:
            raise ValueError(f"Expected tool_call message, got {compact_msg.type.value}")


__all__ = ["MCPAdapter"]
