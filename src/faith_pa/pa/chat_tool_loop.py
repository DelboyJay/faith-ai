"""Description:
    Provide PA browser-chat helpers for MCP-style tool use.

Requirements:
    - Advertise a compact tool-call protocol to non-native LLMs such as llama3.
    - Parse model-emitted JSON tool calls from assistant text.
    - Execute supported FAITH-owned MCP tools and format their results for a
      follow-up model turn.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from faith_mcp.filesystem import FilesystemServer
from faith_pa.config import (
    ConfigLoadError,
    load_agent_config,
    load_tool_config,
    project_config_dir,
    project_root,
)

TOOL_CALL_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
TOOL_MANIFEST_PROMPT = """In FAITH, MCP always means Model Context Protocol.
Never interpret MCP as Microsoft Configuration Manager.

You can use FAITH MCP tools when needed.

Available MCP tools:
- mcp.list_tools args: {}
- filesystem.read args: {"mount": "project", "path": "relative/path"}
- filesystem.list args: {"mount": "project", "path": "relative/path"}
- filesystem.stat args: {"mount": "project", "path": "relative/path"}

When a tool is needed, reply with only one JSON object in this exact shape:
{"type": "tool_call", "tool": "filesystem", "action": "read", "args": {"mount": "project", "path": "README.md"}}

After FAITH returns a tool result, answer the user normally using that result."""


@dataclass(frozen=True, slots=True)
class ChatToolCall:
    """Description:
        Represent one model-requested PA chat tool call.

    Requirements:
        - Preserve the tool name, action name, and structured arguments parsed
          from assistant output.

    :param tool: Tool family requested by the model.
    :param action: Action requested within the tool family.
    :param args: Structured tool arguments.
    """

    tool: str
    action: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPToolDescriptor:
    """Description:
        Describe one PA-visible MCP tool surface.

    Requirements:
        - Preserve the server name, action name, and user-facing description
          used by deterministic inventory answers and tool manifests.

    :param server: MCP server or tool family name.
    :param action: Action exposed by the server.
    :param description: Human-readable action description.
    """

    server: str
    action: str
    description: str

    @property
    def name(self) -> str:
        """Description:
            Return the compact tool action name.

        Requirements:
            - Join server and action names using the compact manifest format.

        :returns: Compact tool action name.
        """

        return f"{self.server}.{self.action}"


AVAILABLE_CHAT_MCP_TOOLS: tuple[MCPToolDescriptor, ...] = (
    MCPToolDescriptor(
        server="mcp",
        action="list_tools",
        description="List the FAITH MCP tools exposed to the Project Agent chat loop.",
    ),
    MCPToolDescriptor(
        server="filesystem",
        action="read",
        description="Read a file from an allowed project mount.",
    ),
    MCPToolDescriptor(
        server="filesystem",
        action="list",
        description="List files and directories from an allowed project mount.",
    ),
    MCPToolDescriptor(
        server="filesystem",
        action="stat",
        description="Return metadata for a file or directory on an allowed project mount.",
    ),
)


def build_tool_manifest_prompt() -> str:
    """Description:
        Return the tool manifest appended to the Project Agent system prompt.

    Requirements:
        - Keep the manifest plain-text so non-native local models can follow it.
        - Include the exact compact JSON shape expected by the PA parser.

    :returns: Tool manifest prompt text.
    """

    return TOOL_MANIFEST_PROMPT


def is_mcp_inventory_question(user_text: str) -> bool:
    """Description:
        Return whether a user message asks for the FAITH MCP tool inventory.

    Requirements:
        - Detect common wording around available MCP servers or tools.
        - Avoid routing broad unrelated MCP questions into a fixed inventory answer.

    :param user_text: User message text to classify.
    :returns: ``True`` when the PA should answer from canonical inventory.
    """

    text = user_text.lower()
    has_mcp = "mcp" in text
    asks_availability = any(term in text for term in ("available", "what", "which", "list"))
    asks_inventory = any(term in text for term in ("server", "servers", "tool", "tools"))
    return has_mcp and asks_availability and asks_inventory


def build_mcp_inventory_answer() -> str:
    """Description:
        Build the deterministic Project Agent answer for available MCP tools.

    Requirements:
        - Define MCP as Model Context Protocol.
        - Report the canonical PA-visible tool inventory without involving the LLM.
        - Mention that only read-only filesystem actions are currently exposed
          to the interactive chat loop.

    :returns: User-facing MCP inventory answer.
    """

    tool_lines = "\n".join(
        f"- `{tool.name}`: {tool.description}" for tool in AVAILABLE_CHAT_MCP_TOOLS
    )
    return (
        "In FAITH, MCP means Model Context Protocol.\n\n"
        "The Project Agent chat loop currently exposes these MCP tools:\n"
        f"{tool_lines}\n\n"
        "The filesystem MCP server is available for read-only project inspection in chat. "
        "Mutating filesystem actions are intentionally not exposed here until the approval "
        "path is wired into the interactive chat loop."
    )


def parse_chat_tool_call(output: str) -> ChatToolCall | None:
    """Description:
        Parse one assistant output string for a compact JSON tool call.

    Requirements:
        - Return ``None`` when the assistant produced normal user-facing text.
        - Accept JSON embedded in surrounding text or a fenced block.
        - Require the FAITH ``type: tool_call`` marker before treating JSON as executable.

    :param output: Assistant output text to inspect.
    :returns: Parsed tool-call request, or ``None`` when no request exists.
    """

    match = TOOL_CALL_PATTERN.search(output.strip())
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "tool_call":
        return None
    tool = str(payload.get("tool", "")).strip()
    action = str(payload.get("action", "")).strip()
    args = payload.get("args", {})
    if not tool or not action or not isinstance(args, dict):
        return None
    return ChatToolCall(tool=tool, action=action, args=args)


def format_tool_result_for_model(request: ChatToolCall, result: dict[str, Any]) -> str:
    """Description:
        Format one tool result as the next model input.

    Requirements:
        - Include the original tool and action so the model can ground the
          follow-up answer.
        - Preserve the structured result as JSON for deterministic parsing.

    :param request: Tool call that produced the result.
    :param result: Structured tool execution result.
    :returns: User-role message content containing the tool result.
    """

    payload = {
        "tool": request.tool,
        "action": request.action,
        "args": request.args,
        "result": result,
    }
    return f"Tool result:\n{json.dumps(payload, sort_keys=True)}"


class ProjectAgentMCPToolExecutor:
    """Description:
        Execute PA browser-chat tool calls against FAITH-owned MCP servers.

    Requirements:
        - Dispatch filesystem requests through the filesystem MCP server.
        - Load project tool and agent configuration lazily so startup can
          degrade when project configuration is not ready.

    :param root: Optional project root used for tool configuration lookup.
    """

    def __init__(self, root: Path | None = None) -> None:
        """Description:
            Initialise the PA chat tool executor.

        Requirements:
            - Resolve the project root once and lazily initialise concrete tool
              servers on first use.

        :param root: Optional project root used for tool configuration lookup.
        """

        self.root = (root or project_root()).resolve()
        self._filesystem_server: FilesystemServer | None = None
        self._filesystem_agent_mounts: dict[str, str] | None = None

    async def execute(self, request: ChatToolCall) -> dict[str, Any]:
        """Description:
            Execute one parsed chat tool request.

        Requirements:
            - Return structured success or failure payloads instead of raising
              raw tool exceptions into the chat runtime.
            - Support the filesystem MCP server in the first implementation
              slice.

        :param request: Parsed tool-call request from the model.
        :returns: Structured tool result payload.
        """

        try:
            if request.tool == "mcp" and request.action == "list_tools":
                return {
                    "success": True,
                    "result": {
                        "tools": [
                            {
                                "server": tool.server,
                                "action": tool.action,
                                "name": tool.name,
                                "description": tool.description,
                            }
                            for tool in AVAILABLE_CHAT_MCP_TOOLS
                        ]
                    },
                }
            if request.tool == "filesystem":
                result = await self._execute_filesystem(request)
                return {"success": True, "result": result}
            return {"success": False, "error": f"Unsupported MCP tool '{request.tool}'."}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def _execute_filesystem(self, request: ChatToolCall) -> dict[str, Any]:
        """Description:
            Execute one filesystem MCP action for the Project Agent.

        Requirements:
            - Use the configured filesystem tool when present.
            - Fall back to a readonly project-root mount during early setup so
              the PA can still inspect the current project.

        :param request: Filesystem tool-call request.
        :returns: Filesystem MCP server result payload.
        """

        server = self._get_filesystem_server()
        agent_mounts = self._get_filesystem_agent_mounts()
        return await server.handle_tool_call(
            request.action,
            request.args,
            agent_id="project-agent",
            agent_mounts=agent_mounts,
        )

    def _get_filesystem_server(self) -> FilesystemServer:
        """Description:
            Return a lazily built filesystem MCP server.

        Requirements:
            - Reuse the server after first construction.
            - Load validated project config when available.

        :returns: Filesystem MCP server instance.
        """

        if self._filesystem_server is None:
            self._filesystem_server = FilesystemServer(
                project_config_dir(self.root),
                self._load_filesystem_config(),
            )
        return self._filesystem_server

    def _load_filesystem_config(self) -> dict[str, Any]:
        """Description:
            Load filesystem tool configuration for chat-time MCP execution.

        Requirements:
            - Prefer the validated `.faith/tools/filesystem.yaml` config.
            - Provide a readonly project-root mount when no config exists yet.

        :returns: Filesystem tool config mapping.
        """

        try:
            return load_tool_config("filesystem.yaml", root=self.root).model_dump(mode="json")
        except ConfigLoadError:
            return {
                "schema_version": "1.0",
                "mounts": {
                    "project": {
                        "host_path": str(self.root),
                        "access": "readonly",
                        "recursive": True,
                        "history": False,
                        "history_depth": 10,
                        "max_file_size_mb": 50,
                        "subfolder_overrides": {},
                    }
                },
            }

    def _get_filesystem_agent_mounts(self) -> dict[str, str]:
        """Description:
            Return filesystem mount grants for the Project Agent.

        Requirements:
            - Honour the Project Agent config when available.
            - Fall back to the configured mount access levels during bootstrap.

        :returns: Mapping of mount names to access levels.
        """

        if self._filesystem_agent_mounts is not None:
            return self._filesystem_agent_mounts
        try:
            agent_config = load_agent_config("project-agent", root=self.root)
            self._filesystem_agent_mounts = {
                name: access.value for name, access in agent_config.mounts.items()
            }
        except ConfigLoadError:
            filesystem_config = self._load_filesystem_config()
            self._filesystem_agent_mounts = {
                name: str(mount.get("access", "readonly"))
                for name, mount in filesystem_config.get("mounts", {}).items()
                if isinstance(mount, dict)
            }
        return self._filesystem_agent_mounts
