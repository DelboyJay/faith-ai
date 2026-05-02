# FAITH-055 — Skill Definitions & Unattended Execution

**Phase:** 11 — CLI & Skill Execution
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-054, FAITH-019
**FRS Reference:** Section 9.6.2, 9.6.3

---

## Objective

Implement reusable FAITH skills stored in `.faith/skills/` with support for AI-executed and script-executed modes, plus unattended execution rules that remain compatible with the ask-first approval model.

This task also includes a Web UI Skills panel, but it must follow the current Web UI architecture rather than the older `.vue` path layout.

---

## Architecture

```text
src/
├── faith_pa/
│   ├── skills/
│   │   ├── loader.py
│   │   ├── models.py
│   │   ├── executor.py
│   │   └── unattended.py
│   └── routes/
│       └── skills.py
└── faith_web/
    └── templates/
        └── index.html

web/
└── js/
    └── panels/
        └── skills-panel.js
```

---

## Required Scope

1. Define the skill file format.
2. Load and validate skills from `.faith/skills/`.
3. Support `executor: ai` and `executor: script`.
4. Enforce unattended execution rules through the approval engine rather than bypassing it.
5. Expose skill execution through CLI and PA routes.
6. Provide a Web UI skills panel showing skill metadata and execution actions.

---

## Files to Create or Update

- `src/faith_pa/skills/*.py`
- `src/faith_pa/routes/skills.py`
- `web/js/panels/skills-panel.js`
- `src/faith_web/templates/index.html`
- `tests/test_skills.py`
- `tests/test_unattended_skill_policy.py`

---

## Acceptance Criteria

1. Skill definitions load from `.faith/skills/`.
2. AI and script execution modes both work through the intended runtime path.
3. Unattended execution remains aligned with the ask-first approval model and remembered decisions.
4. The Web UI Skills panel uses the current `src/faith_web` plus `web/` architecture.
5. The task does not refer to obsolete `.vue` panel paths.

---

## Notes

- Keep scheduler behaviour deferred to FAITH-056.
- If the FRS and older examples disagree on unattended terminology, the FRS wins.

