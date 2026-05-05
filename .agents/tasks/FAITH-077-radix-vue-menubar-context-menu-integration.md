# FAITH-077 - Radix UI Menubar & Context Menu Integration

**Phase:** 13 - Web UI Workspace Migration
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-074
**FRS Reference:** Section 2.2.5, 6.4.1, 6.4.2

---

## Objective

Introduce Radix UI as the maintained menu primitive layer for the Web UI.

---

## Requirements

- Provide a desktop-style menubar for high-level workspace actions.
- Provide context/popup menus for panel and tab actions where menus are more
  appropriate than inline controls.
- Integrate menu actions cleanly with Dockview panel identity and command
  routing.
- Preserve keyboard accessibility and predictable focus handling.

---

## Acceptance Criteria

1. A maintained Radix UI menubar is available in the Web UI shell.
2. Context menus can be attached to relevant panel or tab actions.
3. Menu actions trigger the same underlying FAITH commands as toolbar actions.
4. Keyboard navigation and focus behaviour remain usable.
5. Tests are written first and cover menubar actions, context menu opening, and
   command dispatch.
