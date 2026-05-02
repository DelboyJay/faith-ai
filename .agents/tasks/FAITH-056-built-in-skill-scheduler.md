# FAITH-056 — Built-in Skill Scheduler

**Phase:** 11 — CLI & Skill Execution
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-055, FAITH-004
**FRS Reference:** Section 9.6.5

---

## Objective

Implement the built-in skill scheduler inside the PA so scheduled skill execution does not depend on host cron or external schedulers. The scheduler must hot-reload skill changes, avoid overlapping duplicate runs, log scheduling activity, and expose schedule state through the Web UI.

---

## Architecture

```text
src/
├── faith_pa/
│   ├── scheduler/
│   │   ├── models.py
│   │   ├── registry.py
│   │   ├── scheduler.py
│   │   └── log.py
│   └── routes/
│       └── scheduler.py
└── faith_web/
    └── templates/
        └── index.html

web/
└── js/
    └── panels/
        └── scheduled-skills-panel.js
```

---

## Required Scope

1. Load scheduled skills from `.faith/skills/`.
2. Run the scheduler inside the PA.
3. Respect skill executor mode and unattended policy.
4. Prevent overlapping duplicate runs.
5. Hot-reload schedule definitions when skill files change.
6. Log scheduler events.
7. Expose schedule state and recent run state in the Web UI.

---

## Files to Create or Update

- `src/faith_pa/scheduler/*.py`
- `src/faith_pa/routes/scheduler.py`
- `web/js/panels/scheduled-skills-panel.js`
- `src/faith_web/templates/index.html`
- `tests/test_scheduler.py`

---

## Acceptance Criteria

1. Scheduled skills run without relying on host cron.
2. Duplicate overlapping runs are skipped and logged.
3. Hot-reload updates in-memory schedule state.
4. Scheduler events are written to the dedicated scheduler log.
5. The Web UI scheduler view uses the current `src/faith_web` plus `web/` architecture.
6. The task does not refer to obsolete `.vue` panel paths.

---

## Notes

- Keep the scheduler aligned with FAITH-055 skill semantics; do not invent a separate scheduling-specific skill format.
- Treat schedule visibility and control as PA-backed operations surfaced through the Web UI.

