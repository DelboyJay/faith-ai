# FAITH-075 - Dockview Default Layout & Panel Constraints

**Phase:** 13 - Web UI Workspace Migration
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-074, FAITH-070, FAITH-072
**FRS Reference:** Section 6.4.1, 6.4.2, 6.10

---

## Objective

Implement the new default Dockview layout and its size/stacking constraints.

---

## Requirements

- Project Agent is the primary upper view.
- System Status is tab-stacked with Project Agent.
- The lower region contains the chat/input area plus a non-stacked Approvals
  panel positioned to the left.
- The Project Agent transcript area must keep a fixed layout height with
  internal scrolling.
- Users may still drag, resize, and tab-stack panels after first load within
  Dockview capabilities.

---

## Acceptance Criteria

1. Default layout matches the agreed PA/Status upper stack and lower
   chat-plus-Approvals arrangement.
2. Project Agent content scrolls internally rather than growing the panel.
3. Approvals is not tab-stacked by default in the lower region.
4. Users can subsequently move and re-stack panels.
5. Tests are written first and cover default placement, scrolling, and reset.
