# FAITH-126 — User Terminal Panel in Active Agent Runtime Context

**Phase:** 20 — Interactive User Terminal & Runtime Console
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-038, FAITH-041, FAITH-057, FAITH-074, FAITH-098
**FRS Reference:** Section 4.2, 5.3, 5.5, 6.4.2, 8.4

---

## Objective

Add a dedicated user-facing terminal panel so the user can run commands in the
same execution context as the currently active agent or session, while keeping
security, approvals, auditability, and session ownership explicit.

---

## Important Planning Rule

This task is **discussion-gated**.

It must **not** be implemented until a later requirements discussion has
resolved the exact runtime and UX behaviour in detail.

The discussion must explicitly define at least:

- which runtime the terminal attaches to
- whether the terminal binds to the active session, active agent, or a separate
  terminal runtime
- how permissions and approvals apply to user-issued commands
- what happens when the user switches sessions while the terminal is active
- how command history, transcripts, and output are persisted
- how the panel behaves during agent execution, compaction, and project
  switching
- whether the terminal is read-write, read-only, or mode-switchable
- what happens when the runtime is unavailable or has been reset

---

## Scope Intent

When this task is eventually implemented, it is expected to cover:

- a dedicated Dockview terminal panel
- command execution in the agreed runtime context
- live streamed stdout/stderr rendering
- explicit session/task association
- approval-aware execution for risky actions
- replay-friendly audit logging for terminal commands and outputs
- clean integration with the existing browser workspace and session model

---

## Notes

- This task exists to reserve the capability in planning and to force a proper
  design discussion later rather than allowing an underspecified implementation.
- The existence of this task does **not** imply that the current Project Agent
  bubble transcript should become terminal-rendered.
