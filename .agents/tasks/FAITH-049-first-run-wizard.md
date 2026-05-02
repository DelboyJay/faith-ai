# FAITH-049 — First-Run Wizard: Multi-Step UI

**Phase:** 10 — First-Run Wizard & Setup
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** IN PROGRESS
**Dependencies:** FAITH-036, FAITH-003, FAITH-014, FAITH-057
**FRS Reference:** Section 9.3

---

## Objective

Implement the first-run and edit-mode wizard delivered through the Web UI. The wizard configures a project without manual YAML editing, resolves the correct Ollama route for the current platform, supports optional external Ollama configuration, configures disposable sandbox defaults, and optionally enables the user-privileged host worker managed by `faith-cli`. The wizard must clearly disclose that the PA holds host-level Docker control while sandbox containers may run as root only inside a disposable isolated container without direct Docker socket access.

This task must follow the current monorepo architecture and the current React-based Web UI stack.

---

## Architecture

```text
src/
├── faith_pa/
│   └── wizard/
│       ├── state_machine.py
│       ├── project_scaffolder.py
│       ├── model_validator.py
│       ├── ollama_config.py
│       ├── codebase_analyser.py
│       └── privacy_kb.py
└── faith_web/
    ├── routes/
    │   └── wizard.py
    └── templates/
        └── index.html

web/
└── js/
    └── wizard/
        ├── wizard-shell.js
        ├── step-disclosure.js
        ├── step-privacy.js
        ├── step-pa-model.js
        ├── step-agent-model.js
        ├── step-project.js
        ├── step-launch.js
        └── wizard-store.js
```

---

## Required Scope

1. Trigger the wizard automatically when no project configuration exists.
2. Support first-run mode and reopen-in-edit mode.
3. Walk the user through:
- Docker disclosure including the distinction between PA host-level Docker control and sandbox in-container root access
- privacy profile
- PA model selection
- default agent model selection
- project setup
- launch
4. Resolve Ollama in a platform-aware way rather than assuming a single default path.
5. Allow the user to disable Ollama or point to an external Ollama endpoint.
6. Configure sandbox defaults:
- shared vs isolated preference
- isolated sandbox cap
- default network posture for sandbox containers
7. Configure optional host-worker enablement and host path allowlist.
8. Write project config and framework secrets through the PA-side wizard backend.
9. Stream wizard progress and analysis events through the wizard/status WebSocket flow.
10. Base local-model recommendations on measured runtime capability (working inference path, GPU availability, usable VRAM, RAM fallback), not on a fixed “best local model” list.

---

## Files to Create or Update

- `src/faith_pa/wizard/__init__.py`
- `src/faith_pa/wizard/state_machine.py`
- `src/faith_pa/wizard/project_scaffolder.py`
- `src/faith_pa/wizard/model_validator.py`
- `src/faith_pa/wizard/ollama_config.py`
- `src/faith_pa/wizard/codebase_analyser.py`
- `src/faith_pa/wizard/privacy_kb.py`
- `src/faith_web/routes/wizard.py`
- `src/faith_web/templates/index.html`
- `web/js/wizard/*.js`
- `tests/test_wizard_state_machine.py`
- `tests/test_project_scaffolder.py`
- `tests/test_model_validator.py`
- `tests/test_codebase_analyser.py`

---

## Testing Requirements

Minimum coverage:
- state-machine ordering and invalid transition failures
- privacy/profile enforcement
- platform-aware bundled-vs-host-vs-external Ollama resolution
- capability-probe driven model recommendation
- host-worker enablement and path allowlist persistence
- sandbox default persistence
- project scaffolding outputs
- secrets writing only to framework-level secrets storage
- wizard start/reopen endpoints return the expected status codes and payloads

Every wizard HTTP endpoint must have curl-style/request-style tests for its expected response codes.

---

## Acceptance Criteria

1. The wizard starts automatically when no project config exists.
2. The wizard can be reopened in edit mode after setup.
3. Ollama routing is platform-aware, with external or disabled options available.
4. Sandbox defaults and host-worker settings are captured and written correctly.
5. Project files and framework secrets are written through the PA-side wizard backend.
6. The implementation uses `src/faith_pa`, `src/faith_web`, and `web/`, not the older `faith-project-agent` or `faith-web-ui` layout.
7. Local-model recommendations are based on measured runtime capability rather than a fixed local-model preference list.
8. The task remains aligned with the current host-worker and disposable-sandbox design decisions.

---

## Notes

- The host worker is managed by `faith-cli`, runs with user privileges, and is optional.
- The wizard must not bypass the PA or `faith-cli` ownership boundaries established in the FRS.
- If the existing project already has FAITH docs/config, edit mode should preload current values rather than regenerating them blindly.


