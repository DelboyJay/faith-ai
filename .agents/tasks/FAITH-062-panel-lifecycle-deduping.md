# FAITH-062 — Panel Lifecycle & Deduping

**Phase:** 8 — Web UI
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-074, FAITH-075, FAITH-061
**FRS Reference:** Section 6.4.1, 6.10

---

## Objective

Make panel lifecycle behaviour reliable and user-friendly. Users must be able to close panels cleanly, reopen them from the toolbar, and avoid accidental duplicates. Singleton panels such as Input, Approvals, and System Status must never multiply. Agent and tool panels must also dedupe by runtime identity.

---

## Required Scope

1. Support reliable panel removal through the UI.
2. Allow closed panels to be reopened from the add-panel menu or toolbar.
3. Prevent duplicate singleton panels:
   - Input
   - Approvals
   - System Status
4. Prevent duplicate agent panels for the same `agent_id`.
5. Prevent duplicate tool panels for the same `tool_id`.
6. When the user attempts to add a panel that already exists, focus or reveal the existing panel instead of creating another copy.
7. Persist the deduped/closed state correctly in `localStorage`.

---

## Files to Create or Update

- `web/js/layout.js`
- `web/src/main.jsx`
- `web/css/theme.css` (only if lifecycle controls need styling)
- `tests/test_web_server.py`
- `tests/test_layout.html`

---

## Testing Requirements

Add or update tests that prove:

- singleton panels cannot be duplicated
- duplicate agent/tool panel requests for the same ID do not create another panel
- closing a panel removes it from saved layout state
- reopening a panel creates exactly one instance
- add-panel actions can still create distinct agent/tool panels for different IDs

---

## Acceptance Criteria

1. Singleton panels cannot be duplicated through the add-panel workflow.
2. Agent and tool panels dedupe by identity.
3. Closing and reopening panels works cleanly.
4. Layout persistence reflects the user’s final deduped panel state.
5. The behaviour works within the bundled React + Dockview frontend stack.

---

## Notes

- This task depends on the Phase 13 workspace shell and default layout, but it
  remains a Phase 8 panel-behaviour concern because it governs how user-facing
  panels open, close, and dedupe.
- Prefer “focus existing panel” behaviour over silently doing nothing when the user selects an already-open panel type.
