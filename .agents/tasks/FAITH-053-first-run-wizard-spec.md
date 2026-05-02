# FAITH-053 — First-Run Wizard: Detailed Specification

**Phase:** 10 — First-Run Wizard & Setup
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-049, FAITH-057
**FRS Reference:** Section 9.3, Appendix B Q13

---

## Objective

Define the exact behavioural contract that FAITH-049 must implement for the first-run and edit-mode wizard. This specification is the source of truth for wizard flow, validation rules, failure handling, sandbox defaults, host-worker configuration, Docker-socket disclosure language, and apply/launch semantics.

---

## Scope

This specification covers:
- step order
- required prompts and validation
- first-run vs edit-mode behaviour
- sandbox default selection and validation, including isolation controls and network posture
- optional host-worker enablement and host path allowlist handling
- bundled-vs-external Ollama handling
- project creation vs existing project onboarding
- apply/launch completion rules

It does not require a specific frontend component technology beyond the current FRS Web UI architecture:
- `src/faith_web` backend
- `web/` frontend assets
- React frontend modules aligned with the current Dockview-based Web UI shell

---

## Required Sections

The spec must define, at minimum:
1. Trigger conditions for first-run and reopen-in-edit mode.
2. Step-by-step user flow and back/forward rules.
3. Exact validation requirements for each step.
4. Error-handling behaviour for each external dependency check.
5. Wizard event contract over HTTP/WebSocket.
6. Persistence rules for project config, framework secrets, sandbox defaults, and host-worker settings.
7. Confirmation rules for high-impact changes in edit mode.

---

## Deliverables

- A maintained design contract document aligned to the current FRS.
- Inputs and outputs that FAITH-049 can implement directly.
- Explicit handling for the current sandbox and host-worker design, including the PA-only Docker socket rule and sandbox isolation constraints.

---

## Acceptance Criteria

1. FAITH-053 and FAITH-049 agree on step flow and validation.
2. The document reflects the current `src/faith_pa`, `src/faith_web`, and `web/` architecture.
3. The document does not refer to obsolete `.vue` paths, Vue-specific bundling assumptions, or the retired `faith-project-agent` / `faith-web-ui` layout.
4. Sandbox defaults and host-worker settings are first-class parts of the wizard contract.
5. Edit mode semantics are defined separately from first-run launch semantics.

---

## Notes

- Keep this document concise but authoritative. If implementation changes the wizard contract, update this spec first.
- Use the FRS as the source of truth whenever this task and older examples diverge.

