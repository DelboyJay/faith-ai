"""Description:
    Verify specialist-agent tool manifests are derived from the canonical MCP registry.

Requirements:
    - Prove agent-visible manifests are filtered by permissions, privacy, and runtime state.
    - Prove manifest rendering updates when the underlying registry changes.
    - Prove the base agent prompt can consume the canonical tool manifest without hard-coded tool lists.
"""

from __future__ import annotations

from faith_pa.agent.base import BaseAgent
from faith_pa.agent.tool_manifest import build_agent_tool_manifest_prompt
from faith_pa.config.models import AgentConfig, PrivacyProfile, SystemConfig
from faith_pa.mcp_registry import CanonicalMCPRegistry, MCPToolDescriptor


def _build_agent_config(**overrides: object) -> AgentConfig:
    """Description:
        Build a minimal agent config for tool-manifest tests.

    Requirements:
        - Provide a valid agent configuration with a narrow tool-permission set.
        - Allow callers to override selected fields for individual scenarios.

    :param overrides: Field overrides merged into the baseline payload.
    :returns: Validated agent configuration model.
    """

    payload: dict[str, object] = {
        "name": "QA Engineer",
        "role": "qa-engineer",
        "tools": ["filesystem", "jira"],
        "mcp_native": True,
    }
    payload.update(overrides)
    return AgentConfig.model_validate(payload)


def _build_system_config(**overrides: object) -> SystemConfig:
    """Description:
        Build a minimal system config for tool-manifest tests.

    Requirements:
        - Provide the active privacy profile and PA configuration expected by the agent runtime.
        - Allow callers to override selected fields for individual scenarios.

    :param overrides: Field overrides merged into the baseline payload.
    :returns: Validated system configuration model.
    """

    payload: dict[str, object] = {
        "pa": {"model": "ollama/llama3:8b"},
        "default_agent_model": "ollama/llama3:8b",
        "privacy_profile": PrivacyProfile.INTERNAL,
    }
    payload.update(overrides)
    return SystemConfig.model_validate(payload)


def test_agent_tool_manifest_prompt_uses_registry_snapshot() -> None:
    """Description:
        Verify the rendered manifest prompt is built from the canonical registry snapshot.

    Requirements:
        - This test is needed to prove prompt content is derived from registry state rather than hard-coded tuples.
        - Verify only permitted tool actions are rendered for the selected agent.
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
        agents=("qa-engineer",),
    )

    prompt = build_agent_tool_manifest_prompt(
        agent_id="qa-engineer",
        permissions=("filesystem", "jira"),
        privacy_profile=PrivacyProfile.INTERNAL,
        registry=registry,
    )

    assert "Model Context Protocol" in prompt
    assert "filesystem.read" in prompt
    assert "jira.list_tools" in prompt
    assert "python.execute_python" not in prompt
    assert '"type": "tool_call"' in prompt


def test_agent_tool_manifest_prompt_refreshes_after_registry_update() -> None:
    """Description:
        Verify manifest rendering reflects registry hot reloads without prompt edits.

    Requirements:
        - This test is needed to prove a reconfigured tool becomes visible through the same prompt builder.
        - Verify the prompt changes after the registry updates the tool state.
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
        healthy=False,
        installed=True,
        privacy_tier=PrivacyProfile.INTERNAL,
        agents=("qa-engineer",),
    )

    initial_prompt = build_agent_tool_manifest_prompt(
        agent_id="qa-engineer",
        permissions=("github",),
        privacy_profile=PrivacyProfile.INTERNAL,
        registry=registry,
    )
    registry.set_tool_state("github.list_tools", healthy=True, description="Refreshed description.")
    refreshed_prompt = build_agent_tool_manifest_prompt(
        agent_id="qa-engineer",
        permissions=("github",),
        privacy_profile=PrivacyProfile.INTERNAL,
        registry=registry,
    )

    assert "github.list_tools" not in initial_prompt
    assert "Refreshed description." in refreshed_prompt


def test_base_agent_system_prompt_includes_canonical_tool_manifest(tmp_path) -> None:
    """Description:
        Verify the specialist-agent system prompt includes the canonical tool manifest block.

    Requirements:
        - This test is needed to prove specialist agents consume the same registry-backed manifest as the PA.
        - Verify the agent prompt includes allowed actions and omits unrelated ones.
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

    agent = BaseAgent(
        agent_id="qa-engineer",
        config=_build_agent_config(),
        system_config=_build_system_config(),
        prompt_text="You are QA.",
        project_root=tmp_path,
        mcp_registry=registry,
    )

    system_prompt = agent.build_system_prompt()

    assert "filesystem.read" in system_prompt
    assert "python.execute_python" not in system_prompt
    assert "Available MCP tools" in system_prompt
