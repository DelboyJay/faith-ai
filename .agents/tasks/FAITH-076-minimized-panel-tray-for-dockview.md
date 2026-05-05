# FAITH-076 - Minimized Panel Tray for Dockview

**Phase:** 13 - Web UI Workspace Migration
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-074
**FRS Reference:** Section 6.4.1, 6.10

---

## Objective

Add FAITH-managed minimize/restore behaviour on top of Dockview.

---

## Requirements

- Minimized panels leave the active Dockview layout.
- Each minimized panel appears as a small restore button in a bottom strip.
- Restoring a panel returns it to its prior layout position and size where
  possible.
- Minimized state must coexist cleanly with normal layout persistence and reset
  behaviour.

---

## Acceptance Criteria

1. Supported panels can be minimized from the workspace.
2. Minimized panels appear in a bottom restore tray.
3. Clicking a tray item restores the corresponding panel.
4. Layout persistence handles minimized state predictably.
5. Tests are written first and cover minimize, restore, persistence, and reset.
