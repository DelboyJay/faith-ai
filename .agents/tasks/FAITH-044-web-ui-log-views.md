# FAITH-044 — Web UI Log Views

**Phase:** 8 — Web UI
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-021, FAITH-074
**FRS Reference:** Section 8.7

---

## Objective

Implement the read-only log viewer panels for the Web UI. These panels surface
FAITH runtime and audit data through FastAPI GET endpoints and allow the user to
browse audit history, events, sessions, token usage, and approval history
without any write access, while remaining compatible with the Dockview
workspace shell.

Current implementation note: the read-only endpoints and Dockview-openable
panels now exist with reverse-chronological ordering, pagination, filtering,
and internal scrolling. The token usage view now includes richer aggregate
presentation for per-agent usage and cross-session comparisons, and the session
history wording is aligned with the host-backed PA session root used by the
current runtime.

---

## Architecture

```text
src/faith_web/
├── routes/
│   └── logs.py                 # Read-only log endpoints for this task
└── templates/
    └── index.html

web/
├── src/
│   └── main.jsx                # Dockview shell registration hook
├── js/
│   └── panels/
│       ├── audit-trail.js
│       ├── event-timeline.js
│       ├── session-history.js
│       ├── token-usage.js
│       └── approval-history.js
└── css/
    └── theme.css
```

---

## Required Scope

1. Add read-only endpoints for:
- audit trail
- event timeline
- session list/detail/channel view
- token usage
- approval history
2. Register one Dockview-compatible panel for each view.
3. Support filtering, search, and pagination where the FRS requires it.
4. Mount the project `logs/` directory into the Web UI service read-only.
5. Never add write endpoints or browser-side write actions in this task.

---

## Files to Create or Update

- `src/faith_web/routes/logs.py`
- `src/faith_web/templates/index.html`
- `web/src/main.jsx`
- `web/js/panels/audit-trail.js`
- `web/js/panels/event-timeline.js`
- `web/js/panels/session-history.js`
- `web/js/panels/token-usage.js`
- `web/js/panels/approval-history.js`
- `web/css/theme.css`
- `tests/test_log_endpoints.py`
- `docker-compose.yml` or the relevant bundled compose asset used by `faith-cli`

---

## Testing Requirements

Add request-style coverage for every HTTP status code these endpoints are
expected to return.

Minimum coverage:
- empty logs directory returns empty results, not `500`
- normal audit/event/token/session responses return `200`
- not-found session/channel cases return the documented error status
- invalid traversal/path input returns the documented validation status
- malformed JSON lines are skipped without breaking the endpoint contract

---

## Acceptance Criteria

1. All log endpoints are read-only GET endpoints.
2. The log panels are available from the Web UI panel registry.
3. The logs mount is read-only.
4. Missing or empty logs do not cause server errors.
5. Approval-history terminology remains aligned with the current approval model.
6. The task uses `src/faith_web` plus `web/` and not the retired
   `faith-web-ui/...` layout.

---

## Notes

- Keep response models aligned with FAITH-021’s audit vocabulary.
- This task is historical visibility only; live operational container
  visibility belongs to FAITH-058.
- This task owns the read-only log-panel feature surfaces, not workspace
  placement or layout mechanics.
