import asyncio

from faith_pa.pa.mcp_adapter import MCPAdapter
from faith_pa.pa.tool_router import ToolRouter
from faith_shared.protocol.compact import CompactMessage, MessageStatus, MessageType


def build_tool_call() -> CompactMessage:
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
    adapter = MCPAdapter({"dev": {"mcp_native": True}})
    request = adapter.translate_to_mcp(build_tool_call())
    assert request["method"] == "tools/call"
    assert request["params"]["name"] == "filesystem_read"

    response = adapter.translate_from_mcp({"result": {"content": "ok"}}, build_tool_call())
    assert response.type == MessageType.TOOL_RESULT
    assert response.status == MessageStatus.COMPLETE
    assert response.data["result"]["content"] == "ok"


def test_translate_prompt_and_parse_result():
    adapter = MCPAdapter({"dev": {"mcp_native": False}})
    prompt = adapter.translate_to_prompt(build_tool_call())
    assert "filesystem" in prompt
    result = adapter.parse_prompt_result(
        '{"success": true, "result": {"content": "ok"}}', build_tool_call()
    )
    assert result.status == MessageStatus.COMPLETE
    assert result.data["success"] is True


def test_tool_router_routes_by_agent_capability():
    native_router = ToolRouter(MCPAdapter({"dev": {"mcp_native": True}}))
    prompt_router = ToolRouter(MCPAdapter({"dev": {"mcp_native": False}}))

    async def execute_mcp(request):
        return {"result": {"echo": request["params"]["name"]}}

    async def execute_prompt(prompt):
        return '{"success": true, "result": {"via": "prompt"}}'

    native_result = asyncio.run(
        native_router.route_tool_call("dev", build_tool_call(), execute_mcp, execute_prompt)
    )
    prompt_result = asyncio.run(
        prompt_router.route_tool_call("dev", build_tool_call(), execute_mcp, execute_prompt)
    )

    assert native_result.data["result"]["echo"] == "filesystem_read"
    assert prompt_result.data["result"]["via"] == "prompt"

