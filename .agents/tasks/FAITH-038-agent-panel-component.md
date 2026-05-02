# FAITH-038 — Agent Panel Component

**Phase:** 8 — Web UI Components
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-074, FAITH-078
**FRS Reference:** Section 6.4.2, 6.5.1, 6.5.2, 6.6

---

## Objective

Implement the agent output panel for the React-based Web UI. The panel renders live agent output in xterm.js, connects to `WS /ws/agent/{agent_id}`, shows the current agent status and model in the panel header, and provides panel-local actions for clear, copy, pause, and pin.

The implementation must follow the current FRS web architecture:
- FastAPI + Jinja shell in `src/faith_web/`
- frontend assets in `web/`
- React-based bundled frontend
- Dockview workspace shell
- Node-based build pipeline executed in-container or in a build stage

---

## Architecture

```text
src/faith_web/
├── routes/
│   └── ws.py                 # Existing WebSocket bridge used by this panel
└── templates/
    └── index.html            # Loads panel assets

web/
├── js/
│   ├── src/                  # React workspace shell / panel components (modify)
│   ├── panels/
│   │   └── agent-panel.js    # Main panel module (this task)
│   └── vendor/               # Vendored xterm.js if FAITH is in offline mode
└── css/
    └── theme.css             # Shared theme styles (FAITH-042)
```

---

## Required Scope

1. Register an `agent-panel` Dockview/React panel in the bundled frontend workspace.
2. Open a dedicated WebSocket to `WS /ws/agent/{agent_id}`.
3. Render streamed output into xterm.js with ANSI handling.
4. Render compact protocol messages in a dimmed visual treatment distinct from natural-language output.
5. Show in the panel header:
- agent name
- current status badge
- model name
- disconnected state when applicable
6. Provide local actions:
- Clear
- Copy
- Pause / Resume stream processing
- Pin / Unpin
7. Reconnect automatically on WebSocket drop with bounded exponential backoff.
8. Resize correctly when the Dockview pane resizes.
9. Dispose the terminal, timers, and WebSocket cleanly when the panel is destroyed.

---

## Files to Create or Update

- `web/js/panels/agent-panel.js`
- `web/js/app.js`
- `web/css/theme.css`
- `tests/test_web_agent_panel_contract.py`

The implementation must use the bundled React frontend stack and integrate cleanly with the frontend build pipeline.

---

## Testing Requirements

Add request-style and browser-contract coverage that proves the panel can rely on the backend contract without generating a server error.

Minimum test coverage:
- `WS /ws/agent/{agent_id}` emits parseable messages for output, protocol, status, and error events.
- Disconnect and reconnect behaviour is exercised.
- The panel handles multiple messages in one frame.
- Status and model updates are reflected in the panel state.
- A regression test exists for malformed payload handling so a bad frame does not crash the panel.

If browser-level automation is used, keep it focused on:
- terminal mount
- status badge change
- pause/resume
- disconnect indicator

---

## Acceptance Criteria

1. The Web UI can open one agent panel per agent.
2. Output streams into xterm.js in real time without a page refresh.
3. Status and model updates are shown reactively in the header.
4. Pause stops processing incoming output without closing the WebSocket.
5. Copy and clear actions work from the panel toolbar.
6. The panel reconnects automatically after an unexpected socket drop.
7. The implementation uses the no-build `src/faith_web` plus `web/` structure and does not introduce npm, TypeScript, or `.vue` SFCs.
8. Tests cover the backend message contract and the expected failure paths.

---

## Notes

- This task is intentionally frontend-focused. The WebSocket bridge itself belongs to FAITH-036.
- Reuse the shared theme tokens and panel chrome from FAITH-042 rather than introducing panel-specific styling systems.
- Keep the component format consistent with the rest of the no-build Web UI: plain module exports mounted by `app.js`.
