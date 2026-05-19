# FAITH-114 — Deterministic Retention Rules & Durable History Preservation for Compaction

**Phase:** 18 — Runtime Context Compaction & Rule Promotion
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-046, FAITH-082, FAITH-113
**FRS Reference:** Section 3.5.4, 8.4

---

## Objective

Define exactly what compaction is allowed to remove from active context and what
must always be retained, while preserving full durable history on disk.

---

## Scope

- Preserve full session history, user messages, PA responses, tool activity,
  and logs on disk even after compaction changes the active prompt.
- Define the deterministic "must keep" set for active context, including at
  least active task, unresolved blockers, pending approvals, still-relevant
  failures, pinned facts, and durable user rules/preferences.
- Explicitly exclude FAITH core rules, raw `AGENTS.md`, stable include files,
  and MCP tool information from compaction.
- Make the retained-versus-compacted result inspectable for later debugging.

---

## Notes

- Compaction must never become destructive history deletion.
