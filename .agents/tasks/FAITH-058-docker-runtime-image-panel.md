# FAITH-058 — Docker Runtime & Image Panel

**Phase:** 8 — Web UI Panels
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-014, FAITH-036, FAITH-074, FAITH-078
**FRS Reference:** Section 6.4.2, 6.5

---

## Objective

Implement a dedicated read-only Web UI panel that shows FAITH-managed Docker resources. The panel must surface bootstrap containers, PA-managed containers, sandbox containers, and the image metadata they are using so the user can understand container state at a glance.

---

## Architecture

```text
src/faith_web/
├── routes/
│   └── docker_runtime.py        # Backend status feed for this task
└── templates/
    └── index.html

web/
├── js/
│   ├── src/                     # React/Dockview panel registration hook
│   └── panels/
│       └── docker-runtime-panel.js
└── css/
    └── theme.css
```

---

## Required Scope

1. Show bootstrap services:
- PA
- Redis
- Web UI
- Ollama
- MCP Registry
2. Show PA-managed resources:
- agent containers
- FAITH-owned MCP/tool containers
- `mcp-runtime`
- disposable sandbox containers
3. Show per-container metadata:
- name
- role
- state
- health if available
- image name
- tag and/or digest
- restart count
- ownership metadata where applicable
4. Show image inventory used by the current FAITH environment.
5. Prefer an event-driven backend feed such as `WS /ws/docker`, with a documented fallback only if necessary.
6. Keep the panel read-only in v1.

---

## Files to Create or Update

- `src/faith_web/routes/docker_runtime.py`
- `src/faith_web/templates/index.html`
- `web/js/panels/docker-runtime-panel.js`
- `web/js/app.js`
- `web/css/theme.css`
- `tests/test_docker_runtime_panel_contract.py`

---

## Acceptance Criteria

1. A dedicated Docker Runtime panel exists in the Web UI panel registry.
2. Bootstrap and project-scoped containers are visually separated.
3. Image name plus version tag or digest is shown for each relevant container.
4. Sandboxes are visible and attributable to their owner context where possible.
5. Docker-unavailable state is explicit.
6. The task follows the current `src/faith_web` plus `web/` architecture.

---

## Notes

- Do not fold this into the generic status panel; the FRS explicitly separates the concerns.
- Lifecycle controls remain owned by the PA and CLI; this task is visibility only.
