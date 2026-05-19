# FAITH-100 — PA Project-Root AGENTS.md Instruction Source

**Phase:** 16 — Project Instruction Context & Model Intelligence
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-071, FAITH-073, FAITH-086
**FRS Reference:** Section 3.5.1.1, 7.1.2, 7.3.1

---

## Objective

Make the project-root `AGENTS.md` file the Project Agent's project-controlled
instruction source while keeping FAITH's protected internal orchestration and
safety instructions outside the editable project file.

---

## Scope

- Load `AGENTS.md` from the current project root for the PA only.
- Treat a missing `AGENTS.md` file as an empty instruction file rather than as
  an error.
- Ensure the editable PA prompt UI surface maps to the project instruction
  layer represented by `AGENTS.md`.
- Keep FAITH core/runtime instructions outside the project file and append them
  at final context-assembly time.
- Enforce precedence so FAITH core rules win if they conflict with project
  instructions.
- Hot-apply `AGENTS.md` changes automatically on the next PA turn.

---

## Notes

- This task establishes the source-of-truth file only. Include resolution,
  compiled-context caching, and debug inspection are covered by later tasks in
  this phase.
- Scope is deliberately PA-only. Specialist-agent instruction-file handling is
  future work and must not be conflated with this task.
