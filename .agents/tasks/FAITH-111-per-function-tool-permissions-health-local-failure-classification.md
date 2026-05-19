# FAITH-111 — Per-Function Tool Permissions, Health States, and Local Failure Classification

**Phase:** 17 — Managed MCP Tool Acquisition & Governance
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-109, FAITH-110
**FRS Reference:** Section 4.11.2.1, 5.3

---

## Objective

Give users fine-grained control over tool-function usage while preserving
failure history and using local-only assistance to recommend when retrying a
broken function is likely pointless.

---

## Scope

- Support per-function permission states:
  - `Do not use`
  - `Can use but prompt user each time`
  - `Can use without permission`
- Allow tool-level defaults with function-level overrides.
- Default newly installed third-party functions to `Can use but prompt user
  each time`.
- Track function health separately from permission state, including states such
  as `Untested`, `Working`, `Partially working`, and `Previously failed`.
- When a function fails during real use, run a local Ollama-only advisory
  classification to determine whether the failure looks retryable or likely
  pointless to retry.
- Let the LLM recommend that a user mark a function `Do not use`, but never
  allow it to make that permission change automatically.

---

## Notes

- The actual evidence comes from real tool failures and logs, not from the LLM
  alone.
- This task is about governing future tool use, not about mandatory post-install
  function sweeps.
