# FAITH-124 — Replay-Friendly MCP Tool Audit Artifacts

**Phase:** 19 — Scoped File Storage & Deterministic Excerpt Retrieval
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-021, FAITH-098, FAITH-120
**FRS Reference:** Section 4.16, 5.5

---

## Objective

Keep the main audit log small while preserving enough MCP request/response
detail for later inspection and safe rerun guidance.

---

## Scope

- Keep `audit.log` as a compact index-style record of MCP tool execution.
- Persist full per-call MCP audit artifacts under
  `logs/audit/tools/<session_uuid>/<tool_call_id>.json`.
- Record the artifact location and tool-call id in the main audit entry.
- Preserve bounded request/response detail and references to larger payloads
  where needed.
- Make the artifact format usable by both the user and the PA when explaining
  what happened or preparing a rerun through the normal approval path.

---

## Notes

- This task is about audit artifact structure and persistence, not about a full
  dedicated audit-browser UI by itself.
