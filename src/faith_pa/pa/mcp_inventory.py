"""Description:
    Bridge the canonical MCP registry to the PA and specialist-agent prompt layers.

Requirements:
    - Seed the framework-owned chat-time MCP actions into the canonical registry.
    - Consume public external MCP manager snapshots without duplicating prompt lists.
    - Provide a single internal API for manifest rendering and inventory answers.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from faith_pa.mcp_registry import (
    CanonicalMCPRegistry,
    MCPToolDescriptor,
    MCPToolRecord,
    get_canonical_mcp_registry,
)
from faith_shared.config.models import PrivacyProfile


class MCPInventoryAdapter:
    """Description:
        Adapt the canonical registry into PA-facing manifest and inventory views.

    Requirements:
        - Keep framework-owned default tools registered once.
        - Allow external MCP snapshots to refresh the same canonical inventory.
        - Expose helper methods for prompt rendering and tool listing.
    """

    def __init__(self, registry: CanonicalMCPRegistry | None = None) -> None:
        """Description:
            Initialise the inventory adapter around one canonical registry.

        Requirements:
            - Reuse the shared registry when no override is supplied.

        :param registry: Optional registry override used by tests or isolated callers.
        """

        self.registry = registry or get_canonical_mcp_registry()

    def visible_tools_for_agent(
        self,
        agent_id: str,
        *,
        permissions: Iterable[str],
        privacy_profile: PrivacyProfile,
    ) -> tuple[MCPToolRecord, ...]:
        """Description:
            Return the tool actions visible to one agent.

        Requirements:
            - Delegate permission and privacy filtering to the canonical registry.

        :param agent_id: Agent identifier to evaluate.
        :param permissions: Tool-family permissions configured for the agent.
        :param privacy_profile: Active project privacy profile.
        :returns: Ordered visible tool records.
        """

        return self.registry.visible_tools_for_agent(
            agent_id,
            permissions=permissions,
            privacy_profile=privacy_profile,
        )

    def project_agent_tools(self, *, privacy_profile: PrivacyProfile) -> tuple[MCPToolRecord, ...]:
        """Description:
            Return the active tool actions visible to the Project Agent.

        Requirements:
            - Use the canonical registry's active inventory view.

        :param privacy_profile: Active project privacy profile.
        :returns: Ordered Project Agent-visible tool records.
        """

        return self.registry.visible_tools_for_agent(
            "project-agent",
            permissions=(),
            privacy_profile=privacy_profile,
        )

    def sync_external_server_summaries(
        self,
        server_summaries: Iterable[dict[str, Any]],
    ) -> tuple[MCPToolRecord, ...]:
        """Description:
            Refresh the canonical registry from external MCP manager summaries.

        Requirements:
            - Treat each external server as a registry-backed tool surface.
            - Remove stale external entries that no longer appear in the latest snapshot.

        :param server_summaries: External MCP server summaries from ``list_servers()``.
        :returns: Registered or refreshed external tool records.
        """

        active_servers: set[str] = set()
        refreshed: list[MCPToolRecord] = []
        for summary in server_summaries:
            server_name = str(summary.get("name", "")).strip()
            if not server_name:
                continue
            active_servers.add(server_name)
            privacy_value = summary.get("privacy_tier", PrivacyProfile.INTERNAL.value)
            if isinstance(privacy_value, PrivacyProfile):
                privacy_tier = privacy_value
            else:
                privacy_tier = PrivacyProfile(str(privacy_value))
            record = self.registry.register_actions(
                server_name,
                (
                    MCPToolDescriptor(
                        server=server_name,
                        action="list_tools",
                        description=f"List the tools exposed by the external {server_name} MCP server.",
                    ),
                ),
                source="external",
                enabled=bool(summary.get("enabled", False)),
                healthy=bool(summary.get("running", False)),
                installed=str(summary.get("install_status", "registered")) != "uninstalled",
                privacy_tier=privacy_tier,
                agents=tuple(summary.get("agents", [])),
                runtime_state=str(summary.get("install_status", "registered")),
                package_version=str(summary.get("package_version"))
                if summary.get("package_version")
                else None,
            )
            refreshed.extend(record)

        stale_servers = {
            record.server
            for record in self.registry.iter_records(include_inactive=True)
            if record.source == "external" and record.server not in active_servers
        }
        for server_name in stale_servers:
            self.registry.remove_server(server_name)
        return tuple(sorted(refreshed, key=lambda record: (record.server, record.action)))


def build_project_agent_inventory(
    *,
    privacy_profile: PrivacyProfile,
    registry: CanonicalMCPRegistry | None = None,
) -> tuple[MCPToolRecord, ...]:
    """Description:
        Return the Project Agent-visible tool inventory from the canonical registry.

    Requirements:
        - Keep the inventory answer grounded in framework state.

    :param privacy_profile: Active project privacy profile.
    :param registry: Optional registry override used by tests.
    :returns: Ordered Project Agent-visible tool records.
    """

    return MCPInventoryAdapter(registry).project_agent_tools(privacy_profile=privacy_profile)


__all__ = [
    "MCPInventoryAdapter",
    "build_project_agent_inventory",
]
