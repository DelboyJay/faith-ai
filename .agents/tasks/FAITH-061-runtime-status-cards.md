# FAITH-061 — Runtime Status Cards

**Phase:** 8 — Web UI
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-040, FAITH-058, FAITH-074, FAITH-079
**FRS Reference:** Section 6.4.2

---

## Objective

Improve the System Status panel UX so it presents FAITH-managed runtime status
as readable cards instead of raw JSON dumps. The panel should render one
compact card per relevant container, showing name, role, state, health, and a
human-usable URL only where applicable. This gives the user immediate
operational confidence without forcing them to read raw payloads, and it must
fit cleanly inside the Dockview-based System Status panel.

---

## Required Scope

1. Replace raw JSON status rendering in the status panel with structured cards
   or an equally compact visual design.
2. Show one card per FAITH-managed bootstrap container at minimum:
   - Project Agent
   - Web UI
   - Redis
   - Ollama
   - MCP Registry
3. Add cards dynamically for agent containers, tool containers, sandbox
   containers, and the `mcp-runtime` container when those runtimes exist.
4. Show a clear status indicator on each card:
   - running/healthy -> tick or equivalent positive badge
   - stopped/error/degraded -> cross or equivalent negative badge
5. Show the container role and health text when available.
6. Show a URL only when it is useful to a human, such as Web UI or Ollama
   endpoints.
7. Keep this panel as the quick operational summary; deeper Docker detail
   remains the responsibility of FAITH-058.
8. Rely on the same authoritative runtime status source used by badge-sync work
   so the cards do not drift from other UI health indicators.

---

## Files to Create or Update

- `web/js/panels/status-panel.js`
- `web/js/app.js`
- `web/css/theme.css`
- `tests/test_status_panel_contract.py`
- `tests/test_web_server.py` (if route payload coverage needs extending)

---

## Testing Requirements

Add or update tests that prove:

- a valid runtime snapshot renders stable cards rather than raw JSON
- bootstrap containers appear in the expected order with readable state labels
- degraded containers render an explicit negative state
- optional URLs appear only when provided by the backend
- dynamic agent/tool/sandbox runtime cards can be rendered without breaking the
  panel

---

## Acceptance Criteria

1. The System Status panel no longer defaults to rendering raw JSON.
2. Bootstrap FAITH containers are visible at a glance as compact cards.
3. Running/degraded states are immediately distinguishable visually.
4. Optional service URLs are shown only when useful.
5. The detailed Docker Runtime panel remains separate and is not collapsed into
   this task.

---

## Notes

- Cards are preferred over a dense table because the goal is quick operational
  scanning.
- Keep the styling within the shared Web UI theme system.
- This task depends on FAITH-058 because the panel needs a stable runtime data
  contract for container status.
- Refresh cadence and badge/status truth are shared with FAITH-079; this task
  should not invent a conflicting health polling model.
