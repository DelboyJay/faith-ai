"""Description:
    Render per-agent MCP tool manifests from the canonical runtime registry.

Requirements:
    - Derive agent-visible tool lists from the shared MCP inventory.
    - Keep the prompt text and tool inventory grounded in the same framework state.
    - Avoid hard-coded per-agent tool manifests.
"""

from __future__ import annotations

from collections.abc import Iterable

from faith_pa.mcp_registry import CanonicalMCPRegistry, MCPToolRecord, get_canonical_mcp_registry
from faith_pa.pa.mcp_inventory import MCPInventoryAdapter
from faith_shared.config.models import PrivacyProfile


def build_agent_tool_manifest(
    *,
    agent_id: str,
    permissions: Iterable[str],
    privacy_profile: PrivacyProfile,
    registry: CanonicalMCPRegistry | None = None,
) -> tuple[MCPToolRecord, ...]:
    """Description:
        Return the visible MCP tools for one agent.

    Requirements:
        - Filter by runtime state, privacy profile, and configured permissions.

    :param agent_id: Agent identifier whose manifest is being built.
    :param permissions: Tool-family permissions configured for the agent.
    :param privacy_profile: Active project privacy profile.
    :param registry: Optional registry override used by tests.
    :returns: Ordered visible tool records.
    """

    adapter = MCPInventoryAdapter(registry or get_canonical_mcp_registry())
    return adapter.visible_tools_for_agent(
        agent_id,
        permissions=permissions,
        privacy_profile=privacy_profile,
    )


def build_agent_tool_manifest_prompt(
    *,
    agent_id: str,
    permissions: Iterable[str],
    privacy_profile: PrivacyProfile,
    registry: CanonicalMCPRegistry | None = None,
) -> str:
    """Description:
        Render the MCP tool manifest text for one agent.

    Requirements:
        - Explicitly define MCP as Model Context Protocol.
        - Include the exact compact JSON tool-call shape for non-native models.
        - List only the tool actions visible to the selected agent.

    :param agent_id: Agent identifier whose manifest is being rendered.
    :param permissions: Tool-family permissions configured for the agent.
    :param privacy_profile: Active project privacy profile.
    :param registry: Optional registry override used by tests.
    :returns: Plain-text manifest block for prompt assembly.
    """

    tools = build_agent_tool_manifest(
        agent_id=agent_id,
        permissions=permissions,
        privacy_profile=privacy_profile,
        registry=registry,
    )
    tool_lines = "\n".join(
        f"- {tool.name}: {tool.description} args: {tool.args_example}" for tool in tools
    )
    return (
        "In FAITH, MCP always means Model Context Protocol.\n"
        "Never interpret MCP as Microsoft Configuration Manager.\n\n"
        "Available MCP tools:\n"
        f"{tool_lines if tool_lines else '- (none)'}\n\n"
        "Filesystem tool-call guidance:\n"
        "- Use a canonical mount name such as `project` in the `mount` field.\n"
        "- Do not put raw absolute host paths into the `mount` field.\n"
        "- Put only mount-relative paths into the `path` field.\n\n"
        "When a tool is needed, reply with only one JSON object in this exact shape:\n"
        '{"type": "tool_call", "tool": "filesystem", "action": "read", '
        '"args": {"mount": "project", "path": "README.md"}}\n\n'
        "After FAITH returns a tool result, answer the user normally using that result."
    )


__all__ = [
    "build_agent_tool_manifest",
    "build_agent_tool_manifest_prompt",
]
