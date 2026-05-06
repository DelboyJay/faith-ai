# FAITH-089 - PA Chat Specialist Delegation Loop

## Summary

Extend the interactive Project Agent chat runtime so it can delegate one user request to a single specialist agent, wait for the result, and then continue the main conversation.

## Scope

- Teach the interactive PA chat path how to decide that a request should be delegated to one specialist agent.
- Create or reuse the backing session task for the delegated work.
- Send a bounded assignment over the specialist agent's reserved `pa-{agent-id}` channel.
- Wait for completion, blocked, timeout, or error events instead of assuming an immediate synchronous result.
- Keep the user informed with visible PA progress/status updates while the delegated work is running.
- Resume the normal PA conversation using the specialist's returned result.

## Acceptance Criteria

1. A user request that clearly maps to one specialist role can trigger a single-agent delegation path from the PA chat runtime.
2. The PA sends the delegated assignment over `pa-{agent-id}` rather than treating the work as a direct PA-only reply.
3. The PA waits for the specialist result or timeout/error state before finalising the user-visible answer.
4. The PA surfaces visible progress while delegation is in flight.
5. Timeout, blocked, and error outcomes are handled deterministically and surfaced cleanly.
6. Tests prove an end-to-end single-specialist delegation round trip from PA input through specialist completion and back to the PA reply.

## Dependencies

- FAITH-015 - PA Session & Task Management
- FAITH-016 - PA Event Dispatcher & Intervention Logic
- FAITH-068 - PA Chat MCP Tool-Calling Loop
- FAITH-088 - Runtime Specialist-Agent Materialisation & Lifecycle

## Notes

- This task is intentionally limited to one delegated specialist at a time.
- Multi-specialist fan-out/fan-in should stay out of scope until this path is stable.
