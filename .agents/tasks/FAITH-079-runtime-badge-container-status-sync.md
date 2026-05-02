# FAITH-079 — Runtime Badge & Container Status Sync

**Phase:** 8 — Web UI
**Complexity:** S
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-038, FAITH-040, FAITH-058, FAITH-061, FAITH-074
**FRS Reference:** Section 6.4.2, 6.5

---

## Objective

Ensure that visible runtime-derived status badges in the Web UI reflect actual
FAITH container/service health rather than only local client connection state.
The Project Agent badge, and any similar runtime-backed badges, must stay in
sync with the same authoritative runtime data shown in the System Status panel.

---

## Required Scope

1. Introduce a shared runtime status refresh path that updates no slower than
   every 10 seconds unless newer push data is already available.
2. Use the authoritative runtime snapshot as the source of truth for:
   - Project Agent badge state
   - other runtime-backed panel badges that surface service/container health
   - System Status runtime cards
3. When a backing container or service stops, render a degraded/disconnected
   badge state within the same refresh window.
4. Avoid contradictory UI states where the badge shows healthy/connected while
   the System Status panel shows the same service as stopped or degraded.
5. Keep the implementation compatible with the React + Dockview shell and the
   existing Docker runtime/status data contracts.

---

## Files to Create or Update

- `src/faith_web/routes/docker_runtime.py`
- `web/src/main.jsx`
- `web/js/panels/agent-panel.js`
- `web/js/panels/docker-runtime-panel.js`
- `web/css/theme.css`
- `tests/test_web_server.py`
- `tests/test_web_agent_panel_contract.py`

---

## Testing Requirements

Add or update tests that prove:

- runtime-backed badges update from shared runtime truth rather than only socket
  connectivity
- a stopped Project Agent container is reflected in both the badge and the
  System Status view
- the periodic refresh path runs at the configured interval boundary
- push updates can still refresh sooner without waiting for the next poll

---

## Acceptance Criteria

1. Project Agent and similar runtime-backed badges no longer remain falsely
   healthy when their backing service/container has stopped.
2. Runtime cards and badges reflect the same backend truth source.
3. Runtime state refresh happens at least every 10 seconds when no newer push
   update arrives first.
4. The implementation remains compatible with the current React + Dockview Web
   UI architecture.
