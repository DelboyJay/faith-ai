# FAITH-068 - PA Chat MCP Tool-Calling Loop

## Summary

Expose MCP tools to the interactive Project Agent chat path so local non-native models such as `llama3` can discover and request tool calls through the PA rather than being limited to plain text replies.

## Scope

- Add a compact tool manifest to the Project Agent browser-chat system prompt.
- Parse assistant-emitted JSON tool-call requests using the compact shape: `{"type": "tool_call", "tool": "...", "action": "...", "args": {...}}`.
- Execute supported chat-time tool calls through the PA MCP execution path.
- Feed structured tool results back into the model as the next turn.
- Stream visible tool-use progress to the Project Agent panel while the PA is working.
- Stop the loop when the model returns a normal answer or when a bounded safety iteration limit is reached.
- Initial v1 support covers filesystem `read`, `list`, and `stat` for Project Agent project inspection.

## Acceptance Criteria

1. The Project Agent browser-chat prompt advertises the available MCP tools and required JSON `tool_call` shape.
2. A valid filesystem tool-call JSON response from the model is parsed and executed.
3. The structured tool result is sent back to the model before the final assistant answer is streamed to the UI.
4. Tool-use progress is published to the Project Agent output feed.
5. The tool-use loop has a deterministic maximum iteration limit.
6. Tests prove both tool-manifest exposure and a complete tool-call/result/final-answer path.

## Dependencies

- FAITH-012 - MCP Adapter Layer
- FAITH-016 - PA Event Dispatcher & Intervention Logic
- FAITH-022 - Filesystem MCP Server
- FAITH-036 - FastAPI Server Setup & WebSocket Endpoints
- FAITH-038 - Agent Panel Component

## Notes

- This task does not replace the filesystem MCP server. It connects that server to the interactive PA chat loop.
- Mutating filesystem actions should remain behind the broader approval policy before being added to this browser-chat manifest.
