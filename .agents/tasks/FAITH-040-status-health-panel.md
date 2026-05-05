# FAITH-040 — System Status Panel & Health Summary

**Phase:** 8 — Web UI Panels
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-036, FAITH-074
**FRS Reference:** Section 6.4.2

---

## Objective

Implement the system status panel for the current React-based Web UI. The panel
subscribes to `WS /ws/status` and renders a live operational summary covering
agent state, active channels, MCP tool health, Redis connectivity, session
token usage, estimated cost, hot-reload state, and per-tool links into each MCP
server's dedicated configuration page.

---

## Architecture

```text
src/faith_web/
├── routes/
│   └── ws.py               # WS /ws/status backend bridge
└── templates/
    └── index.html

web/
├── src/
│   └── main.jsx            # Dockview shell registration hook (modify)
├── js/
│   └── panels/
│       └── status-panel.js # This task
└── css/
    └── theme.css
```

---

## Required Scope

1. Register a `status-panel` Dockview-compatible panel component.
2. Subscribe to `WS /ws/status`.
3. Render at minimum:
- agents and current status
- active channels
- tool health
- Redis connection state
- session token totals
- estimated cost
- hot-reload indicator
4. Show a direct config-page link or route for every MCP server that exposes
   configuration in the Web UI.
5. Replace the panel state on each full snapshot; do not depend on incremental
   client-side diffing.
6. Surface disconnected, reconnecting, and degraded states clearly.

---

## Files to Create or Update

- `web/js/panels/status-panel.js`
- `web/src/main.jsx`
- `web/css/theme.css`
- `tests/test_status_panel_contract.py`

---

## Testing Requirements

Add tests that prove the WebSocket status contract and the panel’s
degraded-state behaviour.

Minimum coverage:
- a valid `WS /ws/status` snapshot renders without errors
- missing agents/channels/tools produce stable empty states
- Redis-disconnected payloads are rendered explicitly
- token-cost warning state is rendered when threshold is exceeded
- tool config links are present only when the backend advertises them
- a reconnect path exists after socket drop

---

## Acceptance Criteria

1. The panel renders a full status snapshot from `WS /ws/status`.
2. Empty and degraded states are explicit and non-crashing.
3. MCP tools expose a route into their configuration page when available.
4. The implementation follows the current `src/faith_web` plus bundled `web/`
   architecture and remains compatible with the Dockview shell.
5. This task owns panel content and health summarisation, not workspace layout
   arrangement, minimization, or menu behaviour.

---

## Notes

- This panel is operational summary only. The dedicated Docker visibility panel
  belongs to FAITH-058.
- Keep styling inside the shared theme system from FAITH-042.
- Treat the backend snapshot schema as the source of truth; do not add
  incompatible client-only fields.
