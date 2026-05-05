# FAITH-075 - Dockview Default Layout & Panel Constraints

**Phase:** 13 - Web UI Workspace Migration
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-074, FAITH-084
**FRS Reference:** Section 6.4.1, 6.4.2, 6.10

---

## Objective

Implement the new default Dockview layout and its size/stacking constraints.

---

## Requirements

- Project Agent is the primary upper view.
- System Status is tab-stacked with Project Agent.
- The lower-left region contains Input and User Settings as a default tab stack.
- Approvals remains visible in the lower workspace without being tab-stacked by
  default with Input/User Settings.
- The Project Agent transcript area must scroll internally within the current
  panel space rather than forcing panel growth.
- Users may still drag, resize, and tab-stack panels after first load within
  Dockview capabilities.

---

## Acceptance Criteria

1. Default layout matches the agreed PA/Status upper stack and lower
   Input/User Settings plus Approvals arrangement.
2. Project Agent content scrolls internally rather than growing the panel.
3. Input and User Settings are tab-stacked by default in the lower-left region.
4. Approvals is not tab-stacked by default in the lower region.
5. Users can subsequently move and re-stack panels.
6. Tests are written first and cover default placement, scrolling, and reset.
