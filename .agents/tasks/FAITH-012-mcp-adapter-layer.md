# FAITH-012 — MCP Adapter Layer

**Phase:** 3 — Base Agent Runtime
**Complexity:** L
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-010
**FRS Reference:** Section 4.1

---

## Objective

Implement a transparent MCP (Model Context Protocol) translation layer that lives in the PA. Agents always make tool requests using compact protocol format. The PA checks whether the agent's model supports MCP natively and either forwards the request directly to the MCP server or translates it into a structured natural language prompt for non-MCP models. This ensures agents never need to know which path was taken, and swapping an agent's model requires no changes to agent prompts.

---

## Architecture

```
faith/pa/
├── __init__.py
├── mcp_adapter.py   ← MCPAdapter class (this task)
└── tool_router.py   ← ToolRouter class (this task)
```

The adapter is **stateless and mechanical** — it performs format translation only, no reasoning. It does not consume meaningful PA context. As Ollama models gain native MCP support, the adapter becomes unused automatically. Works transparently with external MCP servers (FAITH-035).

---

## Files to Create

### 1. `faith/pa/mcp_adapter.py`

```python
"""FAITH MCP Adapter — transparent protocol translation layer.

Translates between the compact protocol tool_call format used by agents
and the JSON-RPC 2.0 MCP format used by tool servers. For non-MCP models,
converts tool calls into structured natural language prompts and parses
the LLM's response back into compact protocol format.

The adapter is stateless and mechanical — format translation only.

FRS Reference: Section 4.1
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from faith.protocol.compact import CompactMessage, MessageType

logger = logging.getLogger("faith.pa.mcp_adapter")


class MCPAdapter:
    """Translates between compact protocol and MCP/prompt formats.

    Agents always issue tool requests in compact protocol format. The PA
    uses this adapter to determine the correct path:
    - MCP-native models: translate to JSON-RPC 2.0 MCP request, forward
      directly, translate response back.
    - Non-MCP models: convert to a structured natural language prompt,
      inject into the agent's next message, parse response back.

    Attributes:
        agent_configs: Dict mapping agent_id to its loaded config dict.
            Each config is expected to have an 'mcp_native' boolean flag
            sourced from .faith/agents/{id}/config.yaml.
    """

    def __init__(self, agent_configs: dict[str, dict[str, Any]]):
        """Initialise with loaded agent configurations.

        Args:
            agent_configs: Dict of agent_id -> config dict. Each config
                must contain at minimum an 'mcp_native' key (bool).
        """
        self.agent_configs = agent_configs

    def is_mcp_native(self, agent_id: str) -> bool:
        """Check whether an agent's model supports MCP natively.

        Reads the 'mcp_native' flag from the agent's config.yaml.
        Defaults to False if the agent is not found or the flag is absent.

        Args:
            agent_id: The agent's short identifier.

        Returns:
            True if the agent's model has native MCP support.
        """
        config = self.agent_configs.get(agent_id, {})
        return bool(config.get("mcp_native", False))

    def translate_to_mcp(self, compact_msg: CompactMessage) -> dict:
        """Convert a compact protocol tool_call to MCP JSON-RPC 2.0 format.

        Takes a compact protocol message of type 'tool_call' and produces
        a JSON-RPC 2.0 request dict suitable for sending to an MCP server.

        The MCP tool name is constructed as '{tool}_{action}' from the
        compact message fields. Arguments are passed through directly.

        Args:
            compact_msg: A CompactMessage with type=tool_call. Expected
                to have 'tool', 'action', and 'args' in its data dict.

        Returns:
            A JSON-RPC 2.0 MCP request dict with keys: jsonrpc, method,
            params (name, arguments), and id.

        Raises:
            ValueError: If the message is not a tool_call type.
        """
        if compact_msg.type != MessageType.TOOL_CALL:
            raise ValueError(
                f"Expected tool_call message, got {compact_msg.type.value}"
            )

        data = compact_msg.data
        tool_name = data.get("tool", "unknown")
        action = data.get("action", "")
        args = data.get("args", {})

        # Construct MCP tool name: '{tool}_{action}'
        mcp_tool_name = f"{tool_name}_{action}" if action else tool_name

        return {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": mcp_tool_name,
                "arguments": args,
            },
            "id": f"msg-{compact_msg.msg_id}",
        }

    def translate_from_mcp(
        self,
        mcp_response: dict,
        original_msg: CompactMessage,
    ) -> CompactMessage:
        """Convert an MCP JSON-RPC 2.0 response back to compact protocol.

        Takes the raw MCP response dict and the original tool_call message,
        and produces a tool_result CompactMessage.

        Args:
            mcp_response: The MCP server's JSON-RPC 2.0 response dict.
                Expected to have either 'result' or 'error' key.
            original_msg: The original tool_call CompactMessage that
                triggered this MCP request.

        Returns:
            A CompactMessage of type tool_result with the response data.
        """
        data = original_msg.data.copy()

        if "error" in mcp_response:
            error_info = mcp_response["error"]
            data["success"] = False
            data["error"] = (
                error_info.get("message", "Unknown MCP error")
                if isinstance(error_info, dict)
                else str(error_info)
            )
        else:
            result = mcp_response.get("result", {})
            data["success"] = True
            data["result"] = result

        return CompactMessage(
            **{
                "from": "pa",
                "to": original_msg.from_agent,
                "channel": original_msg.channel,
                "msg_id": original_msg.msg_id + 1,
                "type": MessageType.TOOL_RESULT,
                "tags": original_msg.tags,
                "summary": _build_result_summary(data),
                "data": data,
            }
        )

    def translate_to_prompt(self, compact_msg: CompactMessage) -> str:
        """Convert a tool_call to a structured natural language instruction.

        For non-MCP models that cannot make native tool calls, this method
        produces a clear, structured prompt that tells the LLM exactly what
        tool operation to perform and what response format to use.

        Args:
            compact_msg: A CompactMessage with type=tool_call.

        Returns:
            A structured natural language instruction string.

        Raises:
            ValueError: If the message is not a tool_call type.
        """
        if compact_msg.type != MessageType.TOOL_CALL:
            raise ValueError(
                f"Expected tool_call message, got {compact_msg.type.value}"
            )

        data = compact_msg.data
        tool = data.get("tool", "unknown")
        action = data.get("action", "")
        args = data.get("args", {})

        args_block = "\n".join(
            f"  - {k}: {json.dumps(v)}" for k, v in args.items()
        ) if args else "  (none)"

        return (
            f"[TOOL REQUEST]\n"
            f"Tool: {tool}\n"
            f"Action: {action}\n"
            f"Arguments:\n{args_block}\n\n"
            f"Execute this tool operation and respond with EXACTLY this format:\n"
            f"[TOOL RESULT]\n"
            f"Success: true/false\n"
            f"Result: <the result data as JSON>\n"
            f"Error: <error message if failed, omit if successful>"
        )

    def translate_from_prompt(
        self,
        llm_response: str,
        original_msg: CompactMessage,
    ) -> CompactMessage:
        """Parse an LLM's natural language tool response back to compact protocol.

        Extracts structured data from the LLM's response to a tool prompt
        instruction. Handles both well-formatted responses (matching the
        requested format) and free-form responses (treated as the result).

        Args:
            llm_response: The raw text response from the LLM.
            original_msg: The original tool_call CompactMessage.

        Returns:
            A CompactMessage of type tool_result with parsed response data.
        """
        data = original_msg.data.copy()
        parsed = _parse_tool_result_block(llm_response)

        if parsed is not None:
            data["success"] = parsed["success"]
            if parsed["success"]:
                data["result"] = parsed["result"]
            else:
                data["error"] = parsed.get("error", "Unknown error")
        else:
            # Free-form response — treat the entire response as the result
            data["success"] = True
            data["result"] = llm_response.strip()

        return CompactMessage(
            **{
                "from": "pa",
                "to": original_msg.from_agent,
                "channel": original_msg.channel,
                "msg_id": original_msg.msg_id + 1,
                "type": MessageType.TOOL_RESULT,
                "tags": original_msg.tags,
                "summary": _build_result_summary(data),
                "data": data,
            }
        )


def _build_result_summary(data: dict) -> str:
    """Build a concise summary string for a tool result message."""
    tool = data.get("tool", "unknown")
    action = data.get("action", "")
    success = data.get("success", False)
    status = "ok" if success else "failed"
    return f"{tool}.{action} → {status}" if action else f"{tool} → {status}"


def _parse_tool_result_block(text: str) -> Optional[dict]:
    """Parse a [TOOL RESULT] block from LLM response text.

    Expected format:
        [TOOL RESULT]
        Success: true/false
        Result: <json or text>
        Error: <message>  (optional, only on failure)

    Args:
        text: The raw LLM response string.

    Returns:
        Parsed dict with 'success', 'result', and optionally 'error' keys,
        or None if the response doesn't contain a parseable block.
    """
    # Find the [TOOL RESULT] marker
    marker_match = re.search(r"\[TOOL RESULT\]", text)
    if not marker_match:
        return None

    block = text[marker_match.end():]

    # Extract Success line
    success_match = re.search(
        r"Success:\s*(true|false)", block, re.IGNORECASE
    )
    if not success_match:
        return None

    success = success_match.group(1).lower() == "true"

    # Extract Result line (everything after "Result:" until next field or end)
    result_match = re.search(
        r"Result:\s*(.+?)(?=\nError:|\Z)", block, re.DOTALL
    )
    result_value = result_match.group(1).strip() if result_match else ""

    # Try to parse result as JSON
    try:
        result_value = json.loads(result_value)
    except (json.JSONDecodeError, TypeError):
        pass  # Keep as string

    parsed: dict[str, Any] = {"success": success, "result": result_value}

    # Extract Error line if present
    error_match = re.search(r"Error:\s*(.+)", block)
    if error_match and not success:
        parsed["error"] = error_match.group(1).strip()

    return parsed
```

### 2. `faith/pa/tool_router.py`

```python
"""FAITH Tool Router — routes tool calls through the correct path.

The ToolRouter is the PA's single entry point for all agent tool requests.
It uses the MCPAdapter to determine whether the agent's model supports
MCP natively, then either forwards the request as a JSON-RPC 2.0 MCP
call or converts it to a structured prompt for non-MCP models.

All tool calls emit events (tool:call_started, tool:call_complete,
tool:error) via the FAITH event system.

FRS Reference: Section 4.1
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from faith.pa.mcp_adapter import MCPAdapter
from faith.protocol.compact import CompactMessage, MessageType
from faith.protocol.events import EventPublisher

logger = logging.getLogger("faith.pa.tool_router")

# Redis channel prefix for MCP tool servers
MCP_TOOL_CHANNEL_PREFIX = "mcp-tool:"

# Timeout for waiting on MCP tool responses (seconds)
MCP_RESPONSE_TIMEOUT = 30


class ToolRouter:
    """Routes agent tool calls through MCP-native or prompt-translated paths.

    The ToolRouter is stateless with respect to tool execution — it does
    not cache results or maintain session state. It relies on the
    MCPAdapter for format translation and Redis pub/sub for MCP server
    communication.

    Attributes:
        adapter: The MCPAdapter instance for format translation.
        redis_client: Async Redis client for pub/sub with MCP servers.
        event_publisher: EventPublisher for emitting tool lifecycle events.
    """

    def __init__(
        self,
        adapter: MCPAdapter,
        redis_client: aioredis.Redis,
        event_publisher: Optional[EventPublisher] = None,
    ):
        """Initialise the ToolRouter.

        Args:
            adapter: MCPAdapter instance for protocol translation.
            redis_client: Async Redis client for MCP server communication.
            event_publisher: Optional EventPublisher for tool lifecycle
                events. If None, events are silently skipped.
        """
        self.adapter = adapter
        self.redis_client = redis_client
        self.event_publisher = event_publisher

    async def route_tool_call(
        self,
        msg: CompactMessage,
        agent_id: str,
    ) -> CompactMessage:
        """Route a tool call through the appropriate path.

        Main entry point for all agent tool requests. Determines whether
        the agent's model supports MCP natively, then:
        - MCP-native: translate to MCP format, forward to tool server,
          translate response back to compact protocol.
        - Non-MCP: translate to structured prompt, return prompt
          instruction as a CompactMessage for the PA to inject into
          the agent's next message.

        Emits tool:call_started before execution and tool:call_complete
        or tool:error after.

        Args:
            msg: The tool_call CompactMessage from the agent.
            agent_id: The agent's short identifier.

        Returns:
            A tool_result CompactMessage with the result or error.
        """
        data = msg.data
        tool_name = data.get("tool", "unknown")
        action = data.get("action", "")

        # Publish tool:call_started event
        if self.event_publisher:
            await self.event_publisher.tool_call_started(
                tool=tool_name,
                action=action,
                agent=agent_id,
                channel=msg.channel,
            )

        try:
            if self.adapter.is_mcp_native(agent_id):
                result = await self._route_mcp_native(msg)
            else:
                result = await self._route_via_prompt(msg, agent_id)

            # Publish tool:call_complete event
            success = result.data.get("success", False)
            if self.event_publisher:
                await self.event_publisher.tool_call_complete(
                    tool=tool_name,
                    action=action,
                    agent=agent_id,
                    success=success,
                    channel=msg.channel,
                )

            return result

        except Exception as e:
            logger.exception(
                f"Tool call failed: {tool_name}.{action} for {agent_id}"
            )

            # Publish tool:error event
            if self.event_publisher:
                await self.event_publisher.tool_error(
                    tool=tool_name,
                    error=str(e),
                    agent=agent_id,
                )

            # Return error as a tool_result message
            error_data = msg.data.copy()
            error_data["success"] = False
            error_data["error"] = str(e)

            return CompactMessage(
                **{
                    "from": "pa",
                    "to": msg.from_agent,
                    "channel": msg.channel,
                    "msg_id": msg.msg_id + 1,
                    "type": MessageType.TOOL_RESULT,
                    "tags": msg.tags,
                    "summary": f"{tool_name}.{action} → error: {e}",
                    "data": error_data,
                }
            )

    async def _route_mcp_native(self, msg: CompactMessage) -> CompactMessage:
        """Route via the MCP-native path.

        Translates to MCP format, forwards to the tool's MCP server
        via Redis, awaits the response, and translates back.

        Args:
            msg: The tool_call CompactMessage.

        Returns:
            A tool_result CompactMessage.
        """
        mcp_request = self.adapter.translate_to_mcp(msg)
        tool_name = msg.data.get("tool", "unknown")
        mcp_response = await self._forward_mcp(tool_name, mcp_request)
        return self.adapter.translate_from_mcp(mcp_response, msg)

    async def _route_via_prompt(
        self,
        msg: CompactMessage,
        agent_id: str,
    ) -> CompactMessage:
        """Route via the prompt-translated path for non-MCP models.

        Converts the tool call to a structured natural language prompt.
        The PA will inject this into the agent's next message context.
        For now, this returns the prompt instruction wrapped in a
        tool_result message — the actual LLM execution happens in the
        PA's agent loop.

        Args:
            msg: The tool_call CompactMessage.
            agent_id: The agent's short identifier.

        Returns:
            A tool_result CompactMessage containing the prompt instruction
            for the PA to inject into the agent's next turn.
        """
        prompt_instruction = self.adapter.translate_to_prompt(msg)
        llm_response = await self._execute_via_prompt(
            tool_name=msg.data.get("tool", "unknown"),
            prompt_instruction=prompt_instruction,
            agent_id=agent_id,
        )
        return self.adapter.translate_from_prompt(llm_response, msg)

    async def _forward_mcp(
        self,
        tool_name: str,
        mcp_request: dict,
    ) -> dict:
        """Send an MCP request to the tool's Redis channel and await response.

        Publishes the MCP JSON-RPC 2.0 request to the tool's dedicated
        Redis channel and waits for the response on a reply channel.

        Args:
            tool_name: The tool identifier (used to derive the channel).
            mcp_request: The JSON-RPC 2.0 MCP request dict.

        Returns:
            The MCP server's JSON-RPC 2.0 response dict.

        Raises:
            TimeoutError: If no response is received within the timeout.
            RuntimeError: If the response cannot be parsed.
        """
        request_id = mcp_request.get("id", "unknown")
        tool_channel = f"{MCP_TOOL_CHANNEL_PREFIX}{tool_name}"
        reply_channel = f"{tool_channel}:reply:{request_id}"

        # Subscribe to reply channel before sending request
        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe(reply_channel)

        try:
            # Publish request to tool's MCP channel
            await self.redis_client.publish(
                tool_channel, json.dumps(mcp_request)
            )

            # Wait for response with timeout
            import asyncio
            async def _wait_for_reply():
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        return json.loads(message["data"])
                return None

            response = await asyncio.wait_for(
                _wait_for_reply(),
                timeout=MCP_RESPONSE_TIMEOUT,
            )

            if response is None:
                raise RuntimeError(
                    f"Empty response from MCP server for {tool_name}"
                )

            return response

        except asyncio.TimeoutError:
            raise TimeoutError(
                f"MCP server for '{tool_name}' did not respond "
                f"within {MCP_RESPONSE_TIMEOUT}s"
            )
        finally:
            await pubsub.unsubscribe(reply_channel)
            await pubsub.close()

    async def _execute_via_prompt(
        self,
        tool_name: str,
        prompt_instruction: str,
        agent_id: str,
    ) -> str:
        """Execute a tool call via structured prompt injection.

        For non-MCP models, this is NOT an actual tool call. Instead,
        the prompt instruction is added to the agent's next message
        context, and the agent's LLM response is returned.

        In this base implementation, the prompt instruction is stored
        for the PA's agent loop to pick up. The actual LLM call happens
        outside this method.

        Args:
            tool_name: The tool identifier.
            prompt_instruction: The structured natural language instruction
                generated by MCPAdapter.translate_to_prompt().
            agent_id: The agent's short identifier.

        Returns:
            The prompt instruction string (to be injected by the PA).
            In the full runtime, this will be replaced by the LLM's
            actual response after the PA injects the prompt.
        """
        # Store the prompt instruction in Redis for the PA agent loop
        # to pick up on the agent's next turn.
        prompt_key = f"faith:prompt_injection:{agent_id}"
        await self.redis_client.set(
            prompt_key,
            prompt_instruction,
            ex=300,  # 5 minute TTL
        )

        logger.info(
            f"Stored prompt injection for {agent_id}: {tool_name} "
            f"(key: {prompt_key})"
        )

        # Return the prompt instruction — the PA agent loop will:
        # 1. Read this from Redis before the agent's next turn
        # 2. Inject it into the agent's context
        # 3. Parse the agent's response via translate_from_prompt()
        return prompt_instruction
```

### 3. `faith/pa/__init__.py`

```python
"""FAITH PA (Personal Assistant) — orchestration and tool routing."""

from faith.pa.mcp_adapter import MCPAdapter
from faith.pa.tool_router import ToolRouter

__all__ = [
    "MCPAdapter",
    "ToolRouter",
]
```

### 4. `tests/test_mcp_adapter.py`

```python
"""Tests for the FAITH MCP Adapter Layer.

Covers MCPAdapter format translation (both MCP-native and prompt paths),
ToolRouter routing logic, and event publishing on tool calls.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from faith.pa.mcp_adapter import MCPAdapter, _parse_tool_result_block
from faith.pa.tool_router import ToolRouter, MCP_TOOL_CHANNEL_PREFIX
from faith.protocol.compact import CompactMessage, MessageType
from faith.protocol.events import EventPublisher


# --- Fixtures ---

@pytest.fixture
def agent_configs():
    """Agent configs with one MCP-native and one non-MCP agent."""
    return {
        "software-developer": {
            "mcp_native": True,
            "model": "claude-sonnet-4-20250514",
        },
        "qa-tester": {
            "mcp_native": False,
            "model": "ollama/codellama",
        },
        "architect": {
            # mcp_native flag missing — should default to False
            "model": "ollama/mistral",
        },
    }


@pytest.fixture
def adapter(agent_configs):
    return MCPAdapter(agent_configs)


@pytest.fixture
def tool_call_msg():
    """A compact protocol tool_call message."""
    return CompactMessage(
        **{
            "from": "software-developer",
            "to": "pa",
            "channel": "ch-auth-feature",
            "msg_id": 10,
            "type": MessageType.TOOL_CALL,
            "tags": ["code", "filesystem"],
            "summary": "read main.py",
            "data": {
                "tool": "filesystem",
                "action": "read",
                "args": {"path": "/workspace/src/main.py"},
            },
        }
    )


class FakeRedis:
    """Minimal fake async Redis for testing."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self.stored: dict[str, str] = {}

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    async def set(self, key: str, value: str, ex: int = None) -> None:
        self.stored[key] = value

    def pubsub(self):
        return FakePubSub()


class FakePubSub:
    """Minimal fake Redis PubSub."""

    def __init__(self):
        self.subscribed: list[str] = []

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        pass

    async def close(self) -> None:
        pass

    async def listen(self):
        yield {
            "type": "message",
            "data": json.dumps({
                "jsonrpc": "2.0",
                "result": {"content": "file contents here"},
                "id": "msg-10",
            }),
        }


class FakeEventRedis:
    """Fake Redis that tracks event publishes separately."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def fake_event_redis():
    return FakeEventRedis()


# --- MCPAdapter: MCP native detection ---

def test_is_mcp_native_true(adapter):
    """Agent with mcp_native=True should be detected as native."""
    assert adapter.is_mcp_native("software-developer") is True


def test_is_mcp_native_false(adapter):
    """Agent with mcp_native=False should not be detected as native."""
    assert adapter.is_mcp_native("qa-tester") is False


def test_is_mcp_native_missing_flag(adapter):
    """Agent with missing mcp_native flag should default to False."""
    assert adapter.is_mcp_native("architect") is False


def test_is_mcp_native_unknown_agent(adapter):
    """Unknown agent should default to False."""
    assert adapter.is_mcp_native("nonexistent-agent") is False


# --- MCPAdapter: Compact → MCP translation ---

def test_translate_to_mcp_format(adapter, tool_call_msg):
    """Compact tool_call should translate to valid JSON-RPC 2.0 MCP request."""
    mcp_req = adapter.translate_to_mcp(tool_call_msg)

    assert mcp_req["jsonrpc"] == "2.0"
    assert mcp_req["method"] == "tools/call"
    assert mcp_req["params"]["name"] == "filesystem_read"
    assert mcp_req["params"]["arguments"] == {
        "path": "/workspace/src/main.py"
    }
    assert mcp_req["id"] == "msg-10"


def test_translate_to_mcp_rejects_non_tool_call(adapter):
    """Non-tool_call messages should raise ValueError."""
    msg = CompactMessage(
        **{
            "from": "dev",
            "to": "qa",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.STATUS_UPDATE,
            "tags": ["code"],
            "summary": "done",
        }
    )
    with pytest.raises(ValueError, match="Expected tool_call"):
        adapter.translate_to_mcp(msg)


# --- MCPAdapter: MCP → Compact translation ---

def test_translate_from_mcp_success(adapter, tool_call_msg):
    """Successful MCP response should translate to tool_result with success=True."""
    mcp_response = {
        "jsonrpc": "2.0",
        "result": {"content": "print('hello')"},
        "id": "msg-10",
    }

    result = adapter.translate_from_mcp(mcp_response, tool_call_msg)

    assert result.type == MessageType.TOOL_RESULT
    assert result.from_agent == "pa"
    assert result.to_agent == "software-developer"
    assert result.data["success"] is True
    assert result.data["result"] == {"content": "print('hello')"}


def test_translate_from_mcp_error(adapter, tool_call_msg):
    """MCP error response should translate to tool_result with success=False."""
    mcp_response = {
        "jsonrpc": "2.0",
        "error": {"code": -32600, "message": "File not found"},
        "id": "msg-10",
    }

    result = adapter.translate_from_mcp(mcp_response, tool_call_msg)

    assert result.type == MessageType.TOOL_RESULT
    assert result.data["success"] is False
    assert result.data["error"] == "File not found"


# --- MCPAdapter: Prompt translation for non-MCP models ---

def test_translate_to_prompt_format(adapter, tool_call_msg):
    """Prompt translation should produce structured natural language."""
    prompt = adapter.translate_to_prompt(tool_call_msg)

    assert "[TOOL REQUEST]" in prompt
    assert "Tool: filesystem" in prompt
    assert "Action: read" in prompt
    assert "path" in prompt
    assert "/workspace/src/main.py" in prompt
    assert "[TOOL RESULT]" in prompt
    assert "Success: true/false" in prompt


def test_translate_from_prompt_structured(adapter, tool_call_msg):
    """Well-formatted LLM response should be parsed correctly."""
    llm_response = (
        "I've read the file. Here are the results:\n\n"
        "[TOOL RESULT]\n"
        "Success: true\n"
        'Result: {"content": "import os\\nprint(os.getcwd())"}\n'
    )

    result = adapter.translate_from_prompt(llm_response, tool_call_msg)

    assert result.type == MessageType.TOOL_RESULT
    assert result.data["success"] is True
    assert result.data["result"]["content"] == "import os\nprint(os.getcwd())"


def test_translate_from_prompt_freeform(adapter, tool_call_msg):
    """Free-form LLM response (no [TOOL RESULT] block) should be treated as result."""
    llm_response = "The file contains a Python script that prints hello world."

    result = adapter.translate_from_prompt(llm_response, tool_call_msg)

    assert result.type == MessageType.TOOL_RESULT
    assert result.data["success"] is True
    assert result.data["result"] == llm_response.strip()


def test_translate_from_prompt_error_response(adapter, tool_call_msg):
    """LLM response reporting failure should be parsed as error."""
    llm_response = (
        "[TOOL RESULT]\n"
        "Success: false\n"
        "Result: \n"
        "Error: Permission denied — file is outside workspace"
    )

    result = adapter.translate_from_prompt(llm_response, tool_call_msg)

    assert result.data["success"] is False
    assert "Permission denied" in result.data["error"]


# --- ToolRouter: routing with both paths ---

@pytest.mark.asyncio
async def test_router_mcp_native_path(adapter, tool_call_msg, fake_redis):
    """MCP-native agent should go through MCP translation path."""
    router = ToolRouter(adapter, fake_redis)

    result = await router.route_tool_call(tool_call_msg, "software-developer")

    assert result.type == MessageType.TOOL_RESULT
    assert result.data["success"] is True
    # Verify MCP request was published to Redis
    assert len(fake_redis.published) == 1
    channel, raw = fake_redis.published[0]
    assert channel == f"{MCP_TOOL_CHANNEL_PREFIX}filesystem"
    mcp_req = json.loads(raw)
    assert mcp_req["method"] == "tools/call"


@pytest.mark.asyncio
async def test_router_prompt_path(adapter, tool_call_msg, fake_redis):
    """Non-MCP agent should go through prompt translation path."""
    router = ToolRouter(adapter, fake_redis)

    result = await router.route_tool_call(tool_call_msg, "qa-tester")

    assert result.type == MessageType.TOOL_RESULT
    # Prompt injection should be stored in Redis
    assert "faith:prompt_injection:qa-tester" in fake_redis.stored


# --- ToolRouter: event publishing ---

@pytest.mark.asyncio
async def test_router_publishes_events(adapter, tool_call_msg, fake_redis):
    """ToolRouter should publish tool:call_started and tool:call_complete events."""
    event_redis = FakeEventRedis()
    event_pub = EventPublisher(event_redis, source="pa")
    router = ToolRouter(adapter, fake_redis, event_publisher=event_pub)

    await router.route_tool_call(tool_call_msg, "software-developer")

    assert len(event_redis.published) == 2
    started = json.loads(event_redis.published[0][1])
    complete = json.loads(event_redis.published[1][1])
    assert started["event"] == "tool:call_started"
    assert started["data"]["agent"] == "software-developer"
    assert complete["event"] == "tool:call_complete"
    assert complete["data"]["success"] is True


@pytest.mark.asyncio
async def test_router_publishes_error_event(adapter, fake_redis):
    """ToolRouter should publish tool:error event on failure."""
    event_redis = FakeEventRedis()
    event_pub = EventPublisher(event_redis, source="pa")

    # Create a message with invalid type to force an error in _route_mcp_native
    bad_msg = CompactMessage(
        **{
            "from": "software-developer",
            "to": "pa",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.TOOL_CALL,
            "tags": ["code"],
            "summary": "bad call",
            "data": {"tool": "broken", "action": "fail"},
        }
    )

    # Make _forward_mcp raise an error
    router = ToolRouter(adapter, fake_redis, event_publisher=event_pub)
    router._forward_mcp = AsyncMock(
        side_effect=RuntimeError("MCP server crashed")
    )

    result = await router.route_tool_call(bad_msg, "software-developer")

    assert result.data["success"] is False
    assert "MCP server crashed" in result.data["error"]

    # Should have: call_started + error
    events = [json.loads(msg) for _, msg in event_redis.published]
    event_types = [e["event"] for e in events]
    assert "tool:call_started" in event_types
    assert "tool:error" in event_types


# --- Internal helper: _parse_tool_result_block ---

def test_parse_tool_result_block_valid():
    """Valid [TOOL RESULT] block should be parsed correctly."""
    text = (
        "Here is the result:\n"
        "[TOOL RESULT]\n"
        "Success: true\n"
        'Result: {"data": [1, 2, 3]}\n'
    )
    parsed = _parse_tool_result_block(text)
    assert parsed is not None
    assert parsed["success"] is True
    assert parsed["result"] == {"data": [1, 2, 3]}


def test_parse_tool_result_block_missing():
    """Text without [TOOL RESULT] marker should return None."""
    assert _parse_tool_result_block("Just some text") is None
```

---

## Integration Points

- **FAITH-007 (Compact Protocol):** Uses `CompactMessage` and `MessageType` for all message handling. Tool calls arrive as `type: tool_call` and results are returned as `type: tool_result`.
- **FAITH-008 (Event System):** Uses `EventPublisher` to emit `tool:call_started`, `tool:call_complete`, and `tool:error` events on every tool call lifecycle.
- **FAITH-010 (Base Agent Runtime):** The agent runtime sends tool_call messages to the PA, which delegates to the ToolRouter. The ToolRouter returns tool_result messages back to the agent.
- **FAITH-035 (External MCP Servers):** The `_forward_mcp` method communicates with MCP servers via Redis pub/sub channels. External MCP server registration and discovery are handled by FAITH-035; this module only needs the tool name to derive the channel.
- **Agent config files** (`.faith/agents/{id}/config.yaml`): The `mcp_native` flag determines which translation path is used. Changing this flag is all that is required when a model gains or loses MCP support.

---

## Acceptance Criteria

1. `MCPAdapter.is_mcp_native()` correctly reads the `mcp_native` flag from agent config and defaults to `False` when the flag or agent is missing.
2. `MCPAdapter.translate_to_mcp()` produces valid JSON-RPC 2.0 requests with correct `method`, `params.name` (constructed as `{tool}_{action}`), `params.arguments`, and `id` fields.
3. `MCPAdapter.translate_from_mcp()` converts both successful MCP responses and MCP error responses back to correctly typed `tool_result` CompactMessages.
4. `MCPAdapter.translate_to_prompt()` produces structured natural language instructions that include the tool name, action, arguments, and expected response format.
5. `MCPAdapter.translate_from_prompt()` correctly parses both well-formatted `[TOOL RESULT]` blocks and free-form LLM responses into `tool_result` CompactMessages.
6. `ToolRouter.route_tool_call()` routes MCP-native agents through the MCP translation path and non-MCP agents through the prompt injection path, transparently.
7. `ToolRouter` publishes `tool:call_started` before execution and `tool:call_complete` or `tool:error` after execution via the EventPublisher.
8. All 18 tests pass.

---

## Notes for Implementer

- The adapter is **stateless and mechanical**. It performs format translation only — no reasoning, no caching, no session state. This is by design: it should not consume meaningful PA context window tokens.
- The `mcp_native` flag lives in `.faith/agents/{id}/config.yaml`. When an Ollama model gains native MCP support, flip this flag to `True` and the adapter automatically routes through the direct MCP path. No agent prompt changes required.
- The `_forward_mcp` method uses Redis pub/sub with a request/reply pattern: publish to `mcp-tool:{tool_name}`, subscribe to `mcp-tool:{tool_name}:reply:{request_id}`. The reply subscription is created **before** publishing the request to avoid race conditions.
- The `_execute_via_prompt` method stores the prompt instruction in Redis with a 5-minute TTL. The PA agent loop (FAITH-010) is responsible for reading this, injecting it into the agent's context, and calling `translate_from_prompt()` with the LLM's actual response.
- The `translate_to_prompt` / `translate_from_prompt` pair uses a simple `[TOOL RESULT]` block format. If the LLM doesn't follow the format (free-form response), the entire response is treated as a successful result. This graceful degradation is intentional.
- The `_build_result_summary` helper produces concise summary strings like `"filesystem.read → ok"` for log readability.
- For testing, the fake Redis classes simulate both pub/sub and key-value operations. The `FakePubSub.listen()` yields a single successful response immediately — real MCP server communication will have actual latency.
