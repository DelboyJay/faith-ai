# FAITH-096 - Deterministic User-Requested Tool Selection in PA Chat

## Summary

Make the interactive Project Agent chat loop obey explicit user tool-choice instructions deterministically instead of relying only on LLM interpretation.

## Scope

- Detect when the user explicitly asks the PA to use a specific MCP tool family for the current turn.
- Stop broad MCP inventory-question matching from swallowing imperative requests such as "use the python mcp tool".
- Ground the current turn with the requested tool family in the PA system/runtime prompt.
- Reject model-emitted tool calls that do not match the explicit user-requested tool family and allow the bounded loop to retry.
- Keep normal inventory questions working deterministically when the user is actually asking what tools are available.

## Acceptance Criteria

1. A message such as `please use the python mcp tool` is not treated as an MCP inventory question.
2. When the user explicitly requests one tool family for the current turn, mismatched tool-family calls are not executed.
3. The PA can continue the bounded tool loop and accept a later matching tool call for the same user message.
4. Existing canonical inventory answers remain deterministic and do not regress.
5. Tests prove the red/green behaviour for both inventory matching and explicit tool-choice enforcement.

## Dependencies

- FAITH-068 - PA Chat Loop MCP Tool Calling
- FAITH-069 - MCP Tool Inventory Grounding
- FAITH-081 - Canonical MCP Registry & Agent Manifest Propagation

## Notes

- This task is about the live Project Agent browser-chat runtime, not future multi-agent delegation.
