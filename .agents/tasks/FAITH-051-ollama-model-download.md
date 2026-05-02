# FAITH-051 — Ollama Model Download Integration

**Phase:** 10 — First-Run Wizard & Setup
**Complexity:** S
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-049, FAITH-057
**FRS Reference:** Section 10.5

---

## Objective

Implement the Ollama model download step within the first-run wizard. This step is only shown when Ollama is enabled. It must display model metadata and licence acknowledgement, trigger downloads against the resolved Ollama endpoint, and stream progress back into the wizard UI. Endpoint resolution must respect the platform-aware Ollama routing policy defined by the wizard and PA runtime.

---

## Architecture

```text
src/
├── faith_pa/
│   └── wizard/
│       └── ollama_config.py       # Resolved Ollama endpoint and enablement
└── faith_web/
    ├── routes/
    │   └── wizard.py              # Download and progress endpoints/events
    └── templates/
        └── index.html

web/
└── js/
    └── wizard/
        ├── step-ollama-models.js  # This task
        └── wizard-store.js
```

---

## Required Scope

1. Skip the step entirely when Ollama is disabled.
2. Use the resolved Ollama endpoint rather than assuming only `localhost:11434`.
3. Honour the platform-aware local routing policy before falling back to any explicit external override.
4. Show licence summary and acknowledgement requirement per model.
5. Trigger model downloads through the wizard backend.
6. Stream progress updates into the wizard UI.
7. Support the embedding model requirement when the selected tool set needs it.

---

## Files to Create or Update

- `web/js/wizard/step-ollama-models.js`
- `web/js/wizard/wizard-store.js`
- `src/faith_web/routes/wizard.py`
- `src/faith_pa/wizard/ollama_config.py`
- `tests/test_ollama_model_download.py`

---

## Testing Requirements

Add request-style tests for the wizard download endpoints and expected failure cases.

Minimum coverage:
- Ollama enabled path
- Ollama disabled skip path
- resolved endpoint selection for Linux, Windows, and macOS routing cases
- licence acknowledgement required before download
- download progress event handling
- unreachable Ollama endpoint error handling

---

## Acceptance Criteria

1. The wizard only shows this step when Ollama is enabled.
2. Downloads target the resolved Ollama endpoint.
3. Platform-aware routing is honoured before any download begins.
4. Licence acknowledgement is required before pulling a model.
5. Progress is visible in the wizard UI.
6. The task uses the current `src/faith_pa`, `src/faith_web`, and `web/` architecture and does not refer to `.vue` SFCs.

