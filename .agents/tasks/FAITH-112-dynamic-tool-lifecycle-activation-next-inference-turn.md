# FAITH-112 — Dynamic Tool Lifecycle Activation on Next Inference Turn

**Phase:** 17 — Managed MCP Tool Acquisition & Governance
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-081, FAITH-108, FAITH-111
**FRS Reference:** Section 4.11.2.1

---

## Objective

Ensure future tool lifecycle changes are reflected dynamically without
requiring a FAITH restart, while keeping in-flight agent turns stable.

---

## Scope

- Update the canonical MCP registry immediately when a tool is installed,
  updated, removed, enabled, disabled, or has function permissions changed.
- Make the resulting tool availability visible to the PA and agents on the next
  user inference turn.
- Avoid interrupting an in-flight turn merely to refresh tool context.
- Persist enough lifecycle metadata and audit information that the change is
  inspectable later.
- Treat a restart as a last resort only when the underlying runtime makes it
  physically unavoidable.

---

## Notes

- This task extends the already-dynamic MCP registry model into future managed
  tool acquisition and governance flows.
