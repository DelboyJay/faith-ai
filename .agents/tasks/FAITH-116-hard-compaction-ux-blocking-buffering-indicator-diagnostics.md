# FAITH-116 — Hard Compaction UX Blocking, Buffering Indicator, and Diagnostics

**Phase:** 18 — Runtime Context Compaction & Rule Promotion
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-085, FAITH-103, FAITH-113, FAITH-115
**FRS Reference:** Section 3.5.4, 6.4.2

---

## Objective

Make hard compaction visible and understandable to the user so a temporarily
blocked input path looks intentional rather than broken.

---

## Scope

- Block further user inference submission while hard compaction is underway.
- Show a visible `Compaction underway` state in the Input panel with a live or
  animated buffering indicator.
- Remove the blocked state immediately once hard compaction completes.
- Surface inspectable compaction diagnostics so the user can see why compaction
  happened and what changed.

---

## Notes

- This task is UX-first: users should never be left wondering whether FAITH has
  hung during a hard compaction event.
