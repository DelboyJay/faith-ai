# FAITH-081 - Canonical MCP Registry & Agent Tool Manifest Propagation

**Status:** DONE
**Phase:** 7 - CAG & External MCP Integration
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning

## Objective

Create a framework-owned canonical MCP inventory so FAITH-owned and external MCP
servers register their callable actions once and the framework automatically
propagates the correct tool manifest to the PA and all specialist agents.

## Scope

- Define a canonical runtime inventory for enabled MCP servers and actions.
- Require FAITH-owned MCP servers and external MCP registrations to register
  their actions into that inventory when they become enabled, healthy, or are
  reconfigured.
- Derive per-agent visible tool manifests from that inventory subject to agent
  permissions, privacy profile, and runtime health.
- Make new tools available to the PA and eligible specialist agents
  immediately after registration or hot-reload without bespoke prompt wiring.
- Remove hard-coded chat-loop tool lists as the long-term source of truth.
- Keep `mcp.list_tools` and other inventory answers grounded in the canonical
  registry rather than duplicated static manifests.

## Dependencies

- FAITH-012 - MCP Adapter Layer
- FAITH-014 - PA Container Setup & Docker SDK Integration
- FAITH-035 - External MCP Server Registration & Lifecycle

## FRS References

- Section 4.1.1
- Section 4.1.2
- Section 4.11

## Acceptance Notes

- Registering or enabling a new MCP tool does not require manual per-agent
  prompt edits.
- `mcp.list_tools` reflects current enabled and healthy tool actions.
- PA chat-loop manifests are derived from the canonical inventory.
- Specialist agents receive tool awareness from the same framework layer,
  filtered by their permissions and current runtime state.
