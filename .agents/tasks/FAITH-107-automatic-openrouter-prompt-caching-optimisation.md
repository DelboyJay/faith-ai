# FAITH-107 — Automatic OpenRouter Prompt-Caching Optimisation

**Phase:** 16 — Project Instruction Context & Model Intelligence
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-013, FAITH-102, FAITH-104, FAITH-105
**FRS Reference:** Section 8.5

---

## Objective

Make paid-model usage "just work" by automatically structuring stable PA
requests to benefit from OpenRouter/provider prompt caching where supported.

---

## Scope

- Detect when the selected OpenRouter model/provider path supports prompt
  caching or cache-related usage reporting.
- Keep the stable PA prefix as stable as possible so caching opportunities are
  maximised automatically.
- Apply prompt-caching-friendly request construction without requiring user
  configuration.
- Record cache-hit/cached-token diagnostics in logs and expose them in the UI.
- Degrade gracefully when the selected model/provider path does not support
  caching.

---

## Notes

- Users should not need to understand prompt caching for normal use.
- This task is optimisation-focused and must not alter the semantic correctness
  of the compiled PA context.
