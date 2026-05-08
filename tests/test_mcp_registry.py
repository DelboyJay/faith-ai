"""Description:
    Verify the canonical MCP registry records runtime state and derives visible inventories.

Requirements:
    - Prove tool actions register once in a framework-owned inventory.
    - Prove enablement, health, install state, and privacy filtering affect visibility.
    - Prove registry updates propagate through hot reload and reconfiguration.
"""

from __future__ import annotations

from faith_pa.mcp_registry import CanonicalMCPRegistry, MCPToolDescriptor
from faith_shared.config.models import PrivacyProfile


def test_registry_filters_inactive_tool_actions() -> None:
    """Description:
        Verify the canonical registry hides disabled, unhealthy, or uninstalled tool actions.

    Requirements:
        - This test is needed to prove registry visibility follows runtime state rather than static registration.
        - Verify active tool actions remain visible while inactive ones are filtered out.
    """

    registry = CanonicalMCPRegistry()
    registry.register_actions(
        "filesystem",
        (
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
        ),
        source="faith",
        enabled=True,
        healthy=True,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
    )
    registry.register_actions(
        "github",
        (
            MCPToolDescriptor(
                server="github",
                action="list_tools",
                description="List the tools exposed by the external GitHub MCP server.",
            ),
        ),
        source="external",
        enabled=True,
        healthy=True,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
        agents=("qa-engineer",),
    )
    registry.register_actions(
        "disabled",
        (
            MCPToolDescriptor(
                server="disabled",
                action="list_tools",
                description="This registration should not remain visible.",
            ),
        ),
        source="external",
        enabled=False,
        healthy=True,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
    )

    visible = registry.list_tools()

    assert [tool.name for tool in visible] == [
        "filesystem.list",
        "filesystem.read",
        "github.list_tools",
    ]

    registry.set_tool_state("github.list_tools", healthy=False)
    registry.set_tool_state("filesystem.list", installed=False)

    refreshed = registry.list_tools()

    assert [tool.name for tool in refreshed] == ["filesystem.read"]


def test_registry_derives_agent_visible_manifests_from_permissions_and_privacy() -> None:
    """Description:
        Verify the canonical registry can derive per-agent visible manifests.

    Requirements:
        - This test is needed to prove specialist agents only see the tool actions they are allowed to use.
        - Verify privacy restrictions and tool permissions both affect the derived manifest.
    """

    registry = CanonicalMCPRegistry()
    registry.register_actions(
        "filesystem",
        (
            MCPToolDescriptor(
                server="filesystem",
                action="read",
                description="Read a file from an allowed project mount.",
            ),
        ),
        source="faith",
        enabled=True,
        healthy=True,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
    )
    registry.register_actions(
        "python",
        (
            MCPToolDescriptor(
                server="python",
                action="execute_python",
                description="Execute Python code inside the FAITH workspace sandbox.",
            ),
        ),
        source="faith",
        enabled=True,
        healthy=True,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
    )
    registry.register_actions(
        "jira",
        (
            MCPToolDescriptor(
                server="jira",
                action="list_tools",
                description="List the tools exposed by the external Jira MCP server.",
            ),
        ),
        source="external",
        enabled=True,
        healthy=True,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
        agents=("project-agent", "qa-engineer"),
    )

    qa_manifest = registry.visible_tools_for_agent(
        "qa-engineer",
        permissions=("filesystem", "jira"),
        privacy_profile=PrivacyProfile.INTERNAL,
    )
    restricted_manifest = registry.visible_tools_for_agent(
        "qa-engineer",
        permissions=("filesystem", "jira"),
        privacy_profile=PrivacyProfile.CONFIDENTIAL,
    )
    project_manifest = registry.visible_tools_for_agent(
        "project-agent",
        permissions=(),
        privacy_profile=PrivacyProfile.INTERNAL,
    )

    assert [tool.name for tool in qa_manifest] == ["filesystem.read", "jira.list_tools"]
    assert [tool.name for tool in restricted_manifest] == []
    assert [tool.name for tool in project_manifest] == [
        "filesystem.read",
        "jira.list_tools",
        "python.execute_python",
    ]


def test_registry_hot_reload_updates_existing_records() -> None:
    """Description:
        Verify registry updates replace the prior state for an existing tool action.

    Requirements:
        - This test is needed to prove hot reload can flip enablement and refresh descriptions without creating duplicates.
        - Verify the same tool action changes visibility after being reconfigured.
    """

    registry = CanonicalMCPRegistry()
    registry.register_actions(
        "github",
        (
            MCPToolDescriptor(
                server="github",
                action="list_tools",
                description="Initial description.",
            ),
        ),
        source="external",
        enabled=True,
        healthy=True,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
    )
    registry.register_actions(
        "github",
        (
            MCPToolDescriptor(
                server="github",
                action="list_tools",
                description="Refreshed description.",
            ),
        ),
        source="external",
        enabled=True,
        healthy=False,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
    )

    assert registry.list_tools() == ()
    registry.set_tool_state("github.list_tools", healthy=True)

    refreshed = registry.list_tools()

    assert len(refreshed) == 1
    assert refreshed[0].description == "Refreshed description."
