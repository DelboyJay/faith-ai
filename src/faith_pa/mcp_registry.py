"""Description:
    Maintain the canonical runtime MCP inventory shared by PA and specialist agents.

Requirements:
    - Record MCP tool actions once in a framework-owned registry.
    - Track enablement, health, install state, privacy, and agent assignment for each action.
    - Derive filtered tool views without hard-coded per-loop lists.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from faith_shared.config.models import PrivacyProfile

PROJECT_AGENT_IDS = {"pa", "project-agent", "project_agent"}


def _privacy_rank(profile: PrivacyProfile) -> int:
    """Description:
        Return the restrictiveness rank for one privacy profile.

    Requirements:
        - Treat public as least restrictive and confidential as most restrictive.

    :param profile: Privacy profile to rank.
    :returns: Numeric restrictiveness rank.
    """

    ordering = {
        PrivacyProfile.PUBLIC: 0,
        PrivacyProfile.INTERNAL: 1,
        PrivacyProfile.CONFIDENTIAL: 2,
    }
    return ordering[profile]


def _privacy_allows(
    active_profile: PrivacyProfile, required_profile: PrivacyProfile | None
) -> bool:
    """Description:
        Decide whether one active privacy profile may see a tool with a required tier.

    Requirements:
        - Deny access when the active profile is more restrictive than the tool's tier.

    :param active_profile: Current project privacy profile.
    :param required_profile: Minimum privacy tier declared by the tool, if any.
    :returns: ``True`` when the tool is visible under the active profile.
    """

    if required_profile is None:
        return True
    return _privacy_rank(active_profile) <= _privacy_rank(required_profile)


@dataclass(frozen=True, slots=True)
class MCPToolDescriptor:
    """Description:
        Describe one MCP tool action registered in the canonical inventory.

    Requirements:
        - Preserve the server name, action name, description, and example payload.

    :param server: MCP server or tool family name.
    :param action: Action exposed by the server.
    :param description: Human-readable action description.
    :param args_example: Example JSON args payload shown in manifests.
    """

    server: str
    action: str
    description: str
    args_example: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Description:
            Return the compact ``server.action`` name for the tool.

        Requirements:
            - Join the server and action names with a single period.

        :returns: Compact tool name.
        """

        return f"{self.server}.{self.action}"


@dataclass(slots=True)
class MCPToolRecord:
    """Description:
        Represent one registry entry with runtime state attached.

    Requirements:
        - Preserve the immutable descriptor together with the active runtime flags.
        - Carry enough metadata to derive agent-visible manifests and inventory answers.

    :param descriptor: Static tool descriptor.
    :param source: Framework source label such as ``faith`` or ``external``.
    :param enabled: Whether the tool is currently enabled.
    :param healthy: Whether the runtime backing the tool is currently healthy.
    :param installed: Whether the tool is installed and ready for use.
    :param privacy_tier: Minimum privacy tier required to expose the tool.
    :param agents: Optional list of agent identifiers permitted to see the tool.
    :param runtime_state: Short runtime status label.
    :param package_version: Optional version pin for external tools.
    :param updated_at: UTC timestamp of the last registry update.
    """

    descriptor: MCPToolDescriptor
    source: str
    enabled: bool = True
    healthy: bool = True
    installed: bool = True
    privacy_tier: PrivacyProfile | None = None
    agents: tuple[str, ...] = ()
    runtime_state: str = "ready"
    package_version: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def server(self) -> str:
        """Description:
            Return the MCP server name for this registry entry.

        Requirements:
            - Mirror the underlying descriptor value.

        :returns: MCP server name.
        """

        return self.descriptor.server

    @property
    def action(self) -> str:
        """Description:
            Return the MCP action name for this registry entry.

        Requirements:
            - Mirror the underlying descriptor value.

        :returns: MCP action name.
        """

        return self.descriptor.action

    @property
    def name(self) -> str:
        """Description:
            Return the compact ``server.action`` name for this registry entry.

        Requirements:
            - Mirror the descriptor name.

        :returns: Compact tool name.
        """

        return self.descriptor.name

    @property
    def description(self) -> str:
        """Description:
            Return the human-readable action description.

        Requirements:
            - Mirror the descriptor description.

        :returns: Tool description string.
        """

        return self.descriptor.description

    @property
    def args_example(self) -> dict[str, Any]:
        """Description:
            Return the example argument payload for the tool.

        Requirements:
            - Mirror the descriptor example payload.

        :returns: Example JSON arguments.
        """

        return self.descriptor.args_example

    def is_active(self) -> bool:
        """Description:
            Return whether this registry entry is currently usable.

        Requirements:
            - Require enablement, health, and install state to all be true.

        :returns: ``True`` when the tool is active.
        """

        return self.enabled and self.healthy and self.installed

    def visible_to(
        self,
        agent_id: str,
        *,
        permissions: Iterable[str],
        privacy_profile: PrivacyProfile,
    ) -> bool:
        """Description:
            Return whether one agent may see this registry entry.

        Requirements:
            - Hide inactive tools.
            - Honour the active privacy profile.
            - Restrict specialist agents to their configured permissions and assignments.

        :param agent_id: Agent identifier being evaluated.
        :param permissions: Tool-family permissions configured for the agent.
        :param privacy_profile: Active project privacy profile.
        :returns: ``True`` when the agent may see the tool action.
        """

        if not self.is_active():
            return False
        if not _privacy_allows(privacy_profile, self.privacy_tier):
            return False
        if agent_id not in PROJECT_AGENT_IDS:
            allowed = set(permissions)
            if self.agents and agent_id not in self.agents:
                return False
            if self.server not in allowed and self.name not in allowed and "mcp" not in allowed:
                return False
        return True


class CanonicalMCPRegistry:
    """Description:
        Store and query the canonical MCP inventory.

    Requirements:
        - Deduplicate registrations by compact tool name.
        - Preserve the latest runtime state for each tool action.
        - Expose filtered inventory views for the PA and specialist agents.
    """

    def __init__(self) -> None:
        """Description:
            Initialise an empty canonical MCP registry.

        Requirements:
            - Start with no registered tool actions.
        """

        self._records: dict[str, MCPToolRecord] = {}

    def register_actions(
        self,
        server: str,
        actions: Iterable[MCPToolDescriptor],
        *,
        source: str,
        enabled: bool = True,
        healthy: bool = True,
        installed: bool = True,
        privacy_tier: PrivacyProfile | None = None,
        agents: Iterable[str] = (),
        runtime_state: str = "ready",
        package_version: str | None = None,
    ) -> tuple[MCPToolRecord, ...]:
        """Description:
            Register or refresh one server's tool actions.

        Requirements:
            - Upsert each supplied action by its compact tool name.
            - Refresh runtime metadata each time the server is re-registered.

        :param server: MCP server name being registered.
        :param actions: Tool descriptors exposed by the server.
        :param source: Framework source label such as ``faith`` or ``external``.
        :param enabled: Whether the server is enabled.
        :param healthy: Whether the server runtime is healthy.
        :param installed: Whether the server is installed.
        :param privacy_tier: Minimum privacy tier required for visibility.
        :param agents: Optional agent identifiers allowed to see the server.
        :param runtime_state: Short runtime status label.
        :param package_version: Optional version pin for external tools.
        :returns: Registered tool records in deterministic order.
        """

        updated: list[MCPToolRecord] = []
        agent_tuple = tuple(agents)
        for descriptor in actions:
            if descriptor.server != server:
                descriptor = replace(descriptor, server=server)
            record = MCPToolRecord(
                descriptor=descriptor,
                source=source,
                enabled=enabled,
                healthy=healthy,
                installed=installed,
                privacy_tier=privacy_tier,
                agents=agent_tuple,
                runtime_state=runtime_state,
                package_version=package_version,
            )
            self._records[record.name] = record
            updated.append(record)
        return tuple(sorted(updated, key=lambda record: (record.server, record.action)))

    def set_tool_state(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        healthy: bool | None = None,
        installed: bool | None = None,
        privacy_tier: PrivacyProfile | None = None,
        agents: Iterable[str] | None = None,
        runtime_state: str | None = None,
        package_version: str | None = None,
        description: str | None = None,
        args_example: dict[str, Any] | None = None,
    ) -> bool:
        """Description:
            Update the runtime state for one registered tool action.

        Requirements:
            - Leave unknown actions untouched.
            - Allow hot reload to refresh description and state fields in place.

        :param name: Compact ``server.action`` identifier.
        :param enabled: Optional enablement override.
        :param healthy: Optional health override.
        :param installed: Optional install-state override.
        :param privacy_tier: Optional privacy-tier override.
        :param agents: Optional agent-assignment override.
        :param runtime_state: Optional runtime-state label override.
        :param package_version: Optional package-version override.
        :param description: Optional description override.
        :param args_example: Optional example-argument override.
        :returns: ``True`` when the action existed and was updated.
        """

        record = self._records.get(name)
        if record is None:
            return False
        if description is not None or args_example is not None:
            record.descriptor = replace(
                record.descriptor,
                description=description if description is not None else record.description,
                args_example=args_example if args_example is not None else record.args_example,
            )
        if enabled is not None:
            record.enabled = enabled
        if healthy is not None:
            record.healthy = healthy
        if installed is not None:
            record.installed = installed
        if privacy_tier is not None:
            record.privacy_tier = privacy_tier
        if agents is not None:
            record.agents = tuple(agents)
        if runtime_state is not None:
            record.runtime_state = runtime_state
        if package_version is not None:
            record.package_version = package_version
        record.updated_at = datetime.now(timezone.utc)
        return True

    def remove_server(self, server: str) -> int:
        """Description:
            Remove every action registered for one server.

        Requirements:
            - Delete all actions that share the supplied server name.

        :param server: MCP server name to remove.
        :returns: Number of removed actions.
        """

        to_remove = [name for name, record in self._records.items() if record.server == server]
        for name in to_remove:
            del self._records[name]
        return len(to_remove)

    def list_tools(
        self,
        *,
        privacy_profile: PrivacyProfile = PrivacyProfile.INTERNAL,
        include_inactive: bool = False,
    ) -> tuple[MCPToolRecord, ...]:
        """Description:
            Return the canonical inventory as an ordered tool list.

        Requirements:
            - Default to active tools only.
            - Keep the ordering stable for deterministic answers and tests.

        :param privacy_profile: Active project privacy profile.
        :param include_inactive: Whether to include disabled or unhealthy actions.
        :returns: Ordered tool records from the registry.
        """

        records = sorted(self._records.values(), key=lambda record: (record.server, record.action))
        if include_inactive:
            return tuple(records)
        return tuple(
            record
            for record in records
            if record.is_active() and _privacy_allows(privacy_profile, record.privacy_tier)
        )

    def visible_tools_for_agent(
        self,
        agent_id: str,
        *,
        permissions: Iterable[str],
        privacy_profile: PrivacyProfile,
        include_inactive: bool = False,
    ) -> tuple[MCPToolRecord, ...]:
        """Description:
            Return the tools visible to one agent under the current runtime policy.

        Requirements:
            - Apply the registry's runtime and privacy filters.
            - Honour the agent's tool permissions and explicit assignments.

        :param agent_id: Agent identifier to evaluate.
        :param permissions: Tool-family permissions configured for the agent.
        :param privacy_profile: Active project privacy profile.
        :param include_inactive: Whether to include inactive actions in the result.
        :returns: Ordered visible tool records.
        """

        allowed_permissions = tuple(permissions)
        records = self.list_tools(
            privacy_profile=privacy_profile,
            include_inactive=include_inactive,
        )
        if agent_id in PROJECT_AGENT_IDS:
            return records
        return tuple(
            record
            for record in records
            if record.visible_to(
                agent_id,
                permissions=allowed_permissions,
                privacy_profile=privacy_profile,
            )
        )

    def get_tool(self, name: str) -> MCPToolRecord | None:
        """Description:
            Return one registered tool action by compact name.

        Requirements:
            - Return ``None`` when the action has not been registered.

        :param name: Compact ``server.action`` identifier.
        :returns: Registered tool record when present.
        """

        return self._records.get(name)

    def iter_records(self, *, include_inactive: bool = True) -> tuple[MCPToolRecord, ...]:
        """Description:
            Return every registry record in deterministic order.

        Requirements:
            - Preserve sorting by server and action.
            - Optionally hide inactive actions.

        :param include_inactive: Whether to include inactive records.
        :returns: Ordered registry records.
        """

        records = self.list_tools(include_inactive=True)
        if include_inactive:
            return records
        return tuple(record for record in records if record.is_active())


_DEFAULT_REGISTRY: CanonicalMCPRegistry | None = None


def _seed_default_registry(registry: CanonicalMCPRegistry) -> None:
    """Description:
        Populate the shared registry with the framework-owned baseline MCP actions.

    Requirements:
        - Register the PA inventory surface and the FAITH-owned chat-time actions once.
        - Keep the seed data in the framework-owned registry rather than the chat loop.
    """

    registry.register_actions(
        "mcp",
        (
            MCPToolDescriptor(
                server="mcp",
                action="list_tools",
                description="List the FAITH MCP tools exposed to the Project Agent chat loop.",
            ),
        ),
        source="framework",
        enabled=True,
        healthy=True,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
    )
    registry.register_actions(
        "filesystem",
        (
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
        ),
        source="faith",
        enabled=True,
        healthy=True,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
    )


def get_canonical_mcp_registry() -> CanonicalMCPRegistry:
    """Description:
        Return the shared canonical MCP registry instance.

    Requirements:
        - Lazily seed the framework-owned baseline actions once per process.

    :returns: Shared canonical registry.
    """

    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = CanonicalMCPRegistry()
        _seed_default_registry(_DEFAULT_REGISTRY)
    return _DEFAULT_REGISTRY


__all__ = [
    "CanonicalMCPRegistry",
    "MCPToolDescriptor",
    "MCPToolRecord",
    "get_canonical_mcp_registry",
]
