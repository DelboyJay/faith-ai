# FAITH-074 - React + Dockview Workspace Shell Migration

**Phase:** 13 - Web UI Workspace Migration
**Complexity:** L
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-036, FAITH-038, FAITH-041
**FRS Reference:** Section 2.2.5, 6.4.1, 6.10

---

## Objective

Replace the current workspace shell with a bundled React + Dockview shell.

---

## Requirements

- Remove the legacy workspace layout engine as the primary layout runtime.
- Build the main workspace around Dockview groups, tabs, docking, and layout
  serialization.
- Preserve existing panel identities and command wiring so current FAITH panels
  can be migrated rather than re-invented.
- Consume the bundled frontend assets produced by the React build pipeline.
- Keep reset-layout, persistence, close/reopen, and dedupe behaviour working in
  the new shell.

---

## Acceptance Criteria

1. The Web UI uses Dockview as the main workspace layout manager.
2. Existing core panels can be rendered inside Dockview groups/panels.
3. Layout save/restore works through Dockview serialization.
4. The legacy workspace layout runtime is no longer required for normal workspace operation.
5. Tests are written first and cover first render, layout restore, reset, and
   panel dedupe behaviour.
