# FAITH-098 - PA Chat Tool Call Audit & Session Visibility

## Summary

Persist interactive Project Agent chat-time tool calls and outcomes into the standard Audit Trail and Session History surfaces so users can inspect what the PA actually requested.

## Scope

- Write one audit-log entry for each Project Agent chat-time tool call attempt with tool family, action, and structured argument detail.
- Persist Project Agent chat-time tool-call and tool-result summaries into the active session/task channel log so Session History can surface them.
- Keep the main user transcript focused on the conversational answer while still making the raw tool details available in logs.
- Preserve compatibility with existing audit and session-history Web UI panels and their reverse-chronological ordering rules.

## Acceptance Criteria

1. After one PA browser-chat tool call, the Audit Trail data source contains a corresponding audit entry.
2. The audit entry includes enough detail for a user to inspect the requested tool family, action, and structured arguments.
3. The current session/task channel log contains persisted tool-call and tool-result summaries suitable for Session History browsing.
4. Existing transcript persistence and token/cost logging continue to work.
5. Tests prove the red/green behaviour for audit and session-history persistence.

## Dependencies

- FAITH-021 - Audit Log System
- FAITH-044 - Web UI Log Views
- FAITH-046 - Session & Task Log Writer
- FAITH-068 - PA Chat Loop MCP Tool Calling

## Notes

- This task is about transparency and observability, not exposing raw internal protocol noise in the default Project Agent transcript.
