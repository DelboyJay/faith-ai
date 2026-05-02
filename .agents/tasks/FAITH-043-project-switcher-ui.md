# FAITH-043 — Project Switcher UI

**Phase:** 8 — Web UI Enhancement
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-015, FAITH-074
**FRS Reference:** Section 6.9, 2.5

---

## Objective

Implement the project switcher in the Web UI shell. The switcher displays the current project, lists recent projects from the backend, and requests a coordinated project switch through the PA without bypassing the PA lifecycle rules.

---

## Architecture

```text
src/faith_web/
├── routes/
│   ├── http.py                 # Existing POST /input integration
│   └── projects.py             # Recent-project listing endpoint for this task
└── templates/
    └── index.html             # Toolbar mount point

web/
├── js/
│   ├── app.js                 # Toolbar mount / WebSocket event wiring
│   └── project-switcher.js    # This task
└── css/
    └── theme.css
```

---

## Required Scope

1. Show the active project in the toolbar at all times.
2. Fetch recent projects from a dedicated backend endpoint.
3. Allow selecting another recent project.
4. Route the switch request through the PA using the approved project-switch contract.
5. Reflect `project:switched` and `project:switch_failed` events in the UI.
6. Prevent repeated switch attempts while a switch is already in progress.

---

## Files to Create or Update

- `src/faith_web/routes/projects.py`
- `src/faith_web/templates/index.html`
- `web/js/project-switcher.js`
- `web/js/app.js`
- `tests/test_project_switcher.py`

---

## Testing Requirements

Add curl-style/request-style coverage for the recent-project endpoint and the expected failure cases.

Minimum coverage:
- normal recent-project listing
- missing recent-projects file
- empty recent-projects file
- current project not present in the recent list
- malformed configuration returns the expected error shape if the backend chooses to expose it

---

## Acceptance Criteria

1. The toolbar shows the current project name or an explicit no-project state.
2. The switcher lists recent projects from the backend.
3. Selecting a different project triggers the PA-mediated project switch flow.
4. The UI reflects success and failure events from the backend.
5. The task uses `src/faith_web` plus `web/` and not the retired `faith-web-ui/...` path layout.

---

## Notes

- The Web UI must not switch projects by mutating files directly; the PA remains the orchestration authority.
- Keep the toolbar implementation consistent with the shared no-build frontend architecture.
