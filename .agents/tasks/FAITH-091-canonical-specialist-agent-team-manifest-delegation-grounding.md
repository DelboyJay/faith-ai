# FAITH-091 - Canonical Specialist-Agent Team Manifest & Delegation Grounding

## Summary

Create a framework-owned runtime roster of specialist agents so the PA always knows which agents exist, what they are for, and whether they are currently available for delegation.

## Scope

- Build a canonical runtime manifest of specialist agents derived from the current project team and runtime health.
- Include role, model, tool permissions, trust level, runtime status, and delegation availability in that manifest.
- Expose deterministic answers for questions about available specialist agents.
- Inject the manifest into the PA's delegation context so the model does not rely on memory or stale assumptions.
- Keep the manifest refreshed when agents are created, removed, reconfigured, or restarted.

## Acceptance Criteria

1. The framework exposes a canonical runtime specialist-agent roster rather than relying on hard-coded panel assumptions.
2. The PA can answer user questions about available specialist agents from that manifest without hallucinating roles or capabilities.
3. The PA's delegation prompt context is derived from the same canonical manifest.
4. Creating, removing, or restarting an agent refreshes the manifest automatically.
5. Tests prove the manifest updates and deterministic roster answers work without calling the LLM.

## Dependencies

- FAITH-015 - PA Session & Task Management
- FAITH-081 - Canonical MCP Registry & Agent Tool Manifest Propagation
- FAITH-088 - Runtime Specialist-Agent Materialisation & Lifecycle

## Notes

- This is the agent-side equivalent of the MCP canonical inventory work.
- The PA should not have to infer the available team from the currently open UI panels.
