# FAITH-119 — Excerpt Discovery Summary MCP Function

**Phase:** 19 — Scoped File Storage & Deterministic Excerpt Retrieval
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-118
**FRS Reference:** Section 4.16

---

## Objective

Add the first deterministic excerpt MCP function that tells the caller where
matches exist and how many candidate excerpts are available without returning
all matching text immediately.

---

## Scope

- Accept search terms plus one or more files.
- Return per-file match counts grouped by supported block type.
- Return stable match identifiers or offsets suitable for follow-up retrieval.
- Keep the response intentionally compact so agents can decide what to fetch
  next instead of over-consuming tokens on the first call.

---

## Notes

- This is the discovery stage only; the actual excerpt text is returned by a
  separate follow-up function.
