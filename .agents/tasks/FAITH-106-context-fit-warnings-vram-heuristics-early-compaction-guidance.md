# FAITH-106 — Context-Fit Warnings, VRAM Heuristics, and Early Compaction Guidance

**Phase:** 16 — Project Instruction Context & Model Intelligence
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-013, FAITH-104, FAITH-105
**FRS Reference:** Section 3.5.2, 3.6

---

## Objective

Warn users when the configured context window is likely too large for efficient
local execution and help FAITH compact earlier when effective usable context is
lower than the nominal model limit.

---

## Scope

- Estimate whether a configured Ollama context window is likely too large for
  the available VRAM/RAM and current runtime path.
- Distinguish between declared/default context, effective configured context,
  and a safe usable context estimate.
- Surface non-blocking warnings when the selected context is likely to cause
  offload, major slowdown, or aggressive early compaction.
- Feed safe-usable-context information into PA/agent context budgeting so
  compaction can happen earlier when necessary.
- Keep the warnings visible in model diagnostics rather than silently shrinking
  behaviour with no explanation.

---

## Notes

- The risk is not "the LLM loses context" but inefficient execution, CPU
  offload, or FAITH needing to compact sooner than the nominal model limit
  implies.
