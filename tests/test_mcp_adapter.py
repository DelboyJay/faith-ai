"""Description:
    Verify MCP translation and prompt-fallback routing behaviour.

Requirements:
    - Prove compact tool calls can be translated to and from MCP payloads.
    - Prove prompt fallback parsing produces the expected compact result messages.
    - Prove the tool router chooses the correct execution path based on agent capability.
"""

from __future__ import annotations

import asyncio

from faith_pa.pa.mcp_adapter import MCPAdapter
from faith_pa.pa.tool_router import ToolRouter
from faith_shared.protocol.compact import CompactMessage, MessageStatus, MessageType


def build_tool_call() -> CompactMessage:
    """Description:
        Build a baseline compact tool-call message for adapter tests.

    Requirements:
        - Provide a filesystem read tool call with deterministic message metadata.

    :returns: Baseline compact tool-call message.
    """

    return CompactMessage(
        **{
            "from": "dev",
            "to": "filesystem",
            "channel": "ch-1",
            "msg_id": 1,
            "type": MessageType.TOOL_CALL,
            "summary": "Read a file",
            "data": {"tool": "filesystem", "action": "read", "args": {"path": "README.md"}},
        }
    )


def test_translate_to_and_from_mcp():
    """Description:
    Verify compact tool calls translate cleanly to MCP and back into compact results.

    Requirements:
        - This test is needed to prove MCP-native tool execution preserves the expected tool identity and result payload.
        - Verify the MCP request method and translated result content are correct.
    """

    adapter = MCPAdapter({"dev": {"mcp_native": True}})
    request = adapter.translate_to_mcp(build_tool_call())
    assert request["method"] == "tools/call"
    assert request["params"]["name"] == "filesystem_read"

    response = adapter.translate_from_mcp({"result": {"content": "ok"}}, build_tool_call())
    assert response.type == MessageType.TOOL_RESULT
    assert response.status == MessageStatus.COMPLETE
    assert response.data["result"]["content"] == "ok"


def test_translate_prompt_and_parse_result():
    """Description:
    Verify prompt fallback rendering and parsing produce a successful compact result.

    Requirements:
        - This test is needed to prove non-MCP-native agents can still execute tool calls through prompt translation.
        - Verify the prompt contains the tool name and the parsed result is marked successful.
    """

    adapter = MCPAdapter({"dev": {"mcp_native": False}})
    prompt = adapter.translate_to_prompt(build_tool_call())
    assert "filesystem" in prompt
    result = adapter.parse_prompt_result(
        '{"success": true, "result": {"content": "ok"}}', build_tool_call()
    )
    assert result.status == MessageStatus.COMPLETE
    assert result.data["success"] is True


def test_tool_router_routes_by_agent_capability():
    """Description:
    Verify the tool router chooses MCP or prompt execution based on agent capability.

    Requirements:
        - This test is needed to prove native and fallback execution paths both remain reachable.
        - Verify the MCP-native path returns the translated MCP result and the prompt path returns the prompt result.
    """

    native_router = ToolRouter(MCPAdapter({"dev": {"mcp_native": True}}))
    prompt_router = ToolRouter(MCPAdapter({"dev": {"mcp_native": False}}))

    async def execute_mcp(request):
        """Description:
            Return a deterministic MCP execution result.

        Requirements:
            - Echo the translated MCP tool name for later assertions.

        :param request: MCP request payload.
        :returns: Deterministic MCP result payload.
        """

        return {"result": {"echo": request["params"]["name"]}}

    async def execute_prompt(prompt):
        """Description:
            Return a deterministic prompt-execution result.

        Requirements:
            - Provide a JSON payload that the adapter can parse successfully.

        :param prompt: Prompt text produced by the adapter.
        :returns: Deterministic prompt result payload.
        """

        del prompt
        return '{"success": true, "result": {"via": "prompt"}}'

    native_result = asyncio.run(
        native_router.route_tool_call("dev", build_tool_call(), execute_mcp, execute_prompt)
    )
    prompt_result = asyncio.run(
        prompt_router.route_tool_call("dev", build_tool_call(), execute_mcp, execute_prompt)
    )

    assert native_result.data["result"]["echo"] == "filesystem_read"
    assert prompt_result.data["result"]["via"] == "prompt"
