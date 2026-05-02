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
from faith_mcp.python_exec.server import load_server_from_faith_dir
from faith_pa.config import (
    ConfigLoadError,
    load_agent_config,
    load_tool_config,
    project_config_dir,
    project_root,
)

TOOL_CALL_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
INVENTORY_QUESTION_PATTERN = re.compile(
    r"\b(mcp|tool|tools|server|servers)\b.*\b(available|enabled|have|using|use)\b"
    r"|\bwhat\b.*\b(mcp|tool|tools|server|servers)\b",
    re.IGNORECASE,
)


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
    :param args_example: Example JSON args payload shown in the manifest.
    """

    server: str
    action: str
    description: str
    args_example: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Description:
            Return the compact tool action name.

        Requirements:
            - Join server and action names using the compact manifest format.

        :returns: Compact tool action name.
        """

        return f"{self.server}.{self.action}"


DEFAULT_CHAT_MCP_TOOLS: tuple[MCPToolDescriptor, ...] = (
    MCPToolDescriptor(
        server="mcp",
        action="list_tools",
        description="List the FAITH MCP tools exposed to the Project Agent chat loop.",
        args_example={},
    ),
    MCPToolDescriptor(
        server="filesystem",
        action="read",
        description="Read a file from an allowed project mount.",
        args_example={"mount": "project", "path": "relative/path"},
    ),
    MCPToolDescriptor(
        server="filesystem",
        action="list",
        description="List files and directories from an allowed project mount.",
        args_example={"mount": "project", "path": "relative/path"},
    ),
    MCPToolDescriptor(
        server="filesystem",
        action="stat",
        description="Return metadata for a file or directory on an allowed project mount.",
        args_example={"mount": "project", "path": "relative/path"},
    ),
    MCPToolDescriptor(
        server="python",
        action="execute_python",
        description="Execute Python code inside the allowed FAITH workspace sandbox.",
        args_example={"code": "from datetime import datetime\nprint(datetime.now())"},
    ),
    MCPToolDescriptor(
        server="python",
        action="pip_install",
        description="Install Python packages for the active FAITH Python environment.",
        args_example={"packages": ["requests"]},
    ),
    MCPToolDescriptor(
        server="python",
        action="os_package_install",
        description="Install OS packages for the active FAITH Python environment.",
        args_example={"packages": ["git"]},
    ),
)


def list_available_chat_mcp_tools() -> tuple[MCPToolDescriptor, ...]:
    """Description:
        Return the canonical chat-visible MCP inventory for the Project Agent.

    Requirements:
        - Keep the inventory in one framework-owned location.
        - Return descriptors for every chat-callable MCP action currently
          supported by the PA chat runtime.

    :returns: Canonical tuple of chat-visible MCP tool descriptors.
    """

    return DEFAULT_CHAT_MCP_TOOLS


def build_tool_manifest_prompt() -> str:
    """Description:
        Return the tool manifest appended to the Project Agent system prompt.

    Requirements:
        - Keep the manifest plain-text so non-native local models can follow it.
        - Include the exact compact JSON shape expected by the PA parser.

    :returns: Tool manifest prompt text.
    """

    tool_lines = "\n".join(
        f"- {tool.name} args: {json.dumps(tool.args_example, sort_keys=True)}"
        for tool in list_available_chat_mcp_tools()
    )
    return (
        "In FAITH, MCP always means Model Context Protocol.\n"
        "Never interpret MCP as Microsoft Configuration Manager.\n\n"
        "You can use FAITH MCP tools when needed.\n\n"
        "Available MCP tools:\n"
        f"{tool_lines}\n\n"
        "When a tool is needed, reply with only one JSON object in this exact shape:\n"
        '{"type": "tool_call", "tool": "filesystem", "action": "read", '
        '"args": {"mount": "project", "path": "README.md"}}\n\n'
        "After FAITH returns a tool result, answer the user normally using that result."
    )


def is_mcp_inventory_question(user_text: str) -> bool:
    """Description:
        Detect whether the user is asking for the available MCP tool inventory.

    Requirements:
        - Match common questions about available MCP tools or servers.
        - Avoid treating ordinary execution requests as inventory questions.

    :param user_text: User-authored browser-chat message text.
    :returns: ``True`` when the message asks for available MCP tools.
    """

    normalised = user_text.strip()
    if not normalised:
        return False
    return INVENTORY_QUESTION_PATTERN.search(normalised) is not None


def build_mcp_inventory_answer(tools: tuple[MCPToolDescriptor, ...]) -> str:
    """Description:
        Build a deterministic human-readable answer for MCP inventory questions.

    Requirements:
        - State that MCP means Model Context Protocol.
        - List each available tool action by its canonical compact name.
        - Avoid relying on LLM improvisation for inventory questions.

    :param tools: Canonical tool descriptors available in the current runtime.
    :returns: User-facing inventory answer text.
    """

    lines = ["FAITH MCP means Model Context Protocol.", "", "The available tools are:", ""]
    for index, tool in enumerate(tools, start=1):
        lines.append(f"{index}. `{tool.name}`: {tool.description}")
    return "\n".join(lines)


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
        self._python_server: Any | None = None

    def list_available_tools(self) -> tuple[MCPToolDescriptor, ...]:
        """Description:
            Return the canonical chat-visible MCP inventory for this executor.

        Requirements:
            - Reuse the framework-owned canonical descriptor set.
            - Keep inventory generation centralised instead of duplicating tool
              lists across the PA chat loop.

        :returns: Canonical tuple of chat-visible MCP tool descriptors.
        """

        return list_available_chat_mcp_tools()

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
                            for tool in self.list_available_tools()
                        ]
                    },
                }
            if request.tool == "filesystem":
                result = await self._execute_filesystem(request)
                return {"success": True, "result": result}
            if request.tool == "python":
                result = await self._execute_python(request)
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

    async def _execute_python(self, request: ChatToolCall) -> dict[str, Any]:
        """Description:
            Execute one Python MCP action for the Project Agent.

        Requirements:
            - Route Python actions through the shared Python MCP server facade.
            - Keep the working directory inside the active project root by
              default when the caller did not request a subdirectory.

        :param request: Python tool-call request.
        :returns: Python MCP server result payload.
        """

        server = self._get_python_server()
        working_directory = Path(request.args.get("working_directory", self.root))
        return await server.handle_tool_call(
            request.action,
            request.args,
            agent_id="project-agent",
            working_directory=working_directory,
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

    def _get_python_server(self) -> Any:
        """Description:
            Return a lazily built Python MCP server facade.

        Requirements:
            - Reuse the server after first construction.
            - Constrain execution to the active project root by default.

        :returns: Python execution MCP server facade.
        """

        if self._python_server is None:
            self._python_server = load_server_from_faith_dir(
                project_config_dir(self.root),
                allowed_paths=[self.root],
            )
        return self._python_server
