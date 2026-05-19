# FAITH-103 — Effective Context Debug Panel & Redacted Snapshot Inspection

**Phase:** 16 — Project Instruction Context & Model Intelligence
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-044, FAITH-084, FAITH-102
**FRS Reference:** Section 8.7

---

## Objective

Add a dedicated Web UI inspection surface that shows the redacted effective PA
context for debugging without exposing protected secrets or internal-only
implementation details unsafely.

---

## Scope

- Add a dockable Web UI panel for effective-context inspection.
- Show the redacted compiled PA context text used for a selected session/turn.
- Show the resolved include graph and per-file token estimates.
- Show warnings for missing include targets, invalid references, or detected
  include cycles.
- Show the compiled-context hash/version metadata and related session/turn IDs.
- Make the panel read-only and suitable for debugging, not editing.

---

## Notes

- This panel complements, but does not replace, the token usage panel.
- Secret redaction must happen before persisted/debug views are rendered.
