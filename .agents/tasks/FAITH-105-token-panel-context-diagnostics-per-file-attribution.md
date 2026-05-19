# FAITH-105 — Token Panel Context Diagnostics & Per-File Attribution

**Phase:** 16 — Project Instruction Context & Model Intelligence
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-047, FAITH-103, FAITH-104
**FRS Reference:** Section 8.5, 8.7

---

## Objective

Extend token and cost diagnostics so users can see how much of each request is
being spent on project/context instructions versus actual inference.

---

## Scope

- Record and display separate token estimates for:
  - context/input prompt
  - inference/output
  - total
- Show those values for both the last message and the current session.
- Show context-window usage percentage whenever the active model's reliable
  limit is known.
- Attribute context cost to the contributing project files where possible,
  especially `AGENTS.md` and included markdown files.
- Link token diagnostics to the matching effective-context snapshot so users can
  correlate text and cost.

---

## Notes

- When the model context-window limit is unknown, display `unknown` rather than
  an invented percentage.
- The goal is debugging transparency, not perfect token-accounting precision.
