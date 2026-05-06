# FAITH-088 - Runtime Specialist-Agent Materialisation & Lifecycle

## Summary

Implement the runtime path that lets the PA reuse an existing specialist agent or create/start one on demand during an active session.

## Scope

- Resolve whether a suitable specialist agent already exists for the delegated need.
- Reuse a healthy running agent when one already matches the required role and permissions.
- Create or update `.faith/agents/{id}/config.yaml` and `prompt.md` when a new specialist agent is required.
- Start the specialist agent container and ensure its reserved `pa-{agent-id}` channel is available.
- Apply the existing paid-model approval rule before creating or starting a paid specialist agent.
- Surface team changes back to runtime state so later delegation and UI features see the same roster.

## Acceptance Criteria

1. The PA can resolve a suitable existing specialist agent for a delegated role when one already exists.
2. The PA can materialise a new specialist agent definition on demand and persist it under `.faith/agents/{id}/`.
3. The PA can start the corresponding specialist agent container and reserved direct channel.
4. Creating or starting a paid-model specialist agent requires explicit user approval.
5. Free/local specialist-agent creation can proceed without a prompt and still notifies the user.
6. Tests prove reuse, new creation, paid-approval gating, and failed-start error handling.

## Dependencies

- FAITH-014 - PA Container Setup & Docker SDK Integration
- FAITH-015 - PA Session & Task Management
- FAITH-049 - First-Run Wizard: Multi-Step UI

## Notes

- This task is about runtime creation/reuse, not the full chat delegation loop.
- The goal is to make specialist agents real runtime entities the PA can delegate to, not just planned team entries.
