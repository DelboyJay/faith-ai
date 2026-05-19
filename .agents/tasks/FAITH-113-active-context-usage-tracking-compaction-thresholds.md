# FAITH-113 — Active Context Usage Tracking & Compaction Thresholds

**Phase:** 18 — Runtime Context Compaction & Rule Promotion
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-013, FAITH-102, FAITH-104, FAITH-105
**FRS Reference:** Section 3.5.4

---

## Objective

Track how full the assembled active context is and trigger soft or hard
compaction before the PA overruns the active model context window.

---

## Scope

- Measure the assembled active-context size as a percentage of the selected
  model's reliable context-window limit when known.
- Support a soft/background compaction threshold below the hard limit.
- Trigger hard pre-turn compaction automatically when active-context usage is
  95% or higher before the next user inference is processed.
- Keep the thresholding logic honest when the model limit is unknown by using
  only the information FAITH can justify.

---

## Notes

- This task is about threshold detection and decision points, not about the
  summarisation strategy itself.
