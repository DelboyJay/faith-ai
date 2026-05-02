# FAITH-041 — Input Panel & File Upload

**Phase:** 8 — Web UI
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-074, FAITH-078
**FRS Reference:** Section 6.4.2

---

## Objective

Implement the primary user input panel for the React-based Web UI. The panel sends text to `POST /input`, uploads files to `POST /upload`, supports clipboard image paste and drag-and-drop attachments, and leaves message echoing to the backend so the user sees their own input in the conversation stream.

---

## Architecture

```text
src/faith_web/
├── routes/
│   └── http.py               # /input and /upload endpoints
└── templates/
    └── index.html

web/
├── js/
│   ├── src/                  # React input panel implementation
│   └── panels/
│       └── input-panel.js    # This task
└── css/
    ├── theme.css
    └── input-panel.css       # Optional task-local styles if needed
```

---

## Required Scope

1. Register an `input-panel` React/Dockview panel component.
2. Provide a multi-line text area with keyboard send shortcut.
3. Send text-only messages through `POST /input`.
4. Send files through `POST /upload` using multipart form data.
5. Support drag-and-drop upload for allowed file types.
6. Support clipboard image paste with inline preview.
7. Allow removing queued attachments before send.
8. Render user-facing validation and failure states clearly.
9. Prevent duplicate sends while a request is in flight.

---

## Files to Create or Update

- `web/js/panels/input-panel.js`
- `web/js/app.js`
- `web/css/theme.css`
- `web/css/input-panel.css` if separate styles are needed
- `tests/test_input_panel_contract.py`

---

## Testing Requirements

Add request-style tests for the HTTP contract plus frontend behaviour tests where useful.

Minimum coverage:
- `POST /input` success path
- `POST /input` validation error path
- `POST /input` degraded/unavailable backend path
- `POST /upload` success path
- `POST /upload` expected `413`, `415`, `422`, and `503` cases
- attachment queue removal and send-disabled-empty behaviour
- clipboard image paste and drag-and-drop happy paths

---

## Acceptance Criteria

1. Users can send text-only messages from the panel.
2. Users can attach supported files and clipboard images.
3. Unsupported files and oversized uploads return explicit error states.
4. The implementation uses the current `src/faith_web` plus bundled `web/` frontend architecture.
5. The panel depends on backend echoing rather than implementing browser-side conversation injection.
6. Required HTTP endpoint status-code tests exist for the related endpoints.

---

## Notes

- Keep file-type and size validation aligned with the backend contract; frontend validation is a guard, not the only enforcement layer.
- This task must not add a separate frontend build pipeline.
