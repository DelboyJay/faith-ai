# FAITH-039 — Approval Panel Component

**Phase:** 8 — Web UI Components
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-020, FAITH-074
**FRS Reference:** Section 5.6, 6.4.2

---

## Objective

Implement the approval queue panel for the current React-based Web UI. The panel
subscribes to `WS /ws/approvals`, renders pending approval requests inside the
Dockview workspace, and submits user decisions to `POST /approve/{request_id}`.
It must reflect the current FRS approval model exactly:

- `allow_once`
- `approve_session`
- `always_allow`
- `always_ask`
- `deny_once`
- `deny_permanently`

Persisted actions must preview the generated learned rule before submission. The
panel must also provide a resolved-history view for recently handled requests.

---

## Architecture

```text
src/faith_web/
├── routes/
│   ├── http.py                # POST /approve/{request_id}
│   └── ws.py                  # WS /ws/approvals bridge
└── templates/
    └── index.html

web/
├── js/
│   ├── app.js                 # Web UI shell registration hook (modify)
│   └── panels/
│       └── approval-panel.js  # This task
└── css/
    └── theme.css              # Shared approval styling hooks
```

---

## Required Scope

1. Register an `approval-panel` Dockview-compatible panel component.
2. Subscribe to `WS /ws/approvals` and render a live queue of pending
   approvals.
3. Render each approval card with:
- agent
- tool
- action
- target/detail
- timestamp
- optional context summary
4. Provide the six canonical decision actions.
5. For persisted actions (`always_allow`, `always_ask`, `deny_permanently`),
   show a preview/edit step for the generated rule before submission.
6. Submit decisions through `POST /approve/{request_id}`.
7. Move resolved items to an in-panel history list.
8. Ignore duplicate request IDs.
9. Show a subtle visual alert when new approvals arrive.
10. Show explicit disconnected / reconnecting state for the WebSocket.

---

## Files to Create or Update

- `web/js/panels/approval-panel.js`
- `web/js/app.js`
- `web/css/theme.css`
- `tests/test_approval_panel_contract.py`

Use the current React-based Web UI patterns and keep the panel compatible with the
Dockview workspace shell.

---

## Testing Requirements

Add tests that prove the HTTP and WebSocket contract the panel depends on.

Minimum coverage:
- `WS /ws/approvals` connects and emits approval payloads with the required
  fields.
- `POST /approve/{request_id}` accepts all six valid decisions under the
  expected conditions.
- Persisted decisions require rule payload data when the backend expects it.
- Invalid decision values return the expected validation error.
- Unknown request IDs return the expected not-found response.
- A regression test exists for duplicate or externally resolved requests.

---

## Acceptance Criteria

1. The panel renders all pending approvals from the approval WebSocket feed.
2. The user can choose any of the six FRS-defined actions.
3. Persisted actions show an editable rule preview before the POST is sent.
4. Resolved cards leave the queue and appear in history.
5. WebSocket disconnect state is visible and reconnects automatically.
6. The implementation uses `src/faith_web` plus `web/` and does not reintroduce
   the old `faith-web-ui/...` layout.
7. The panel remains compatible with the Dockview workspace migration and does
   not depend on legacy layout-engine-specific APIs.
8. The task uses the current canonical approval vocabulary and does not
   introduce legacy `auto_approved` wording.

---

## Notes

- User-facing action text may say “Deny permanently”, but the underlying
  persisted rule target remains the learned deny section defined by the FRS.
- Keep the POST payload and audit vocabulary aligned with FAITH-020 and
  FAITH-021.
- This task must not add direct Redis access from the browser.
