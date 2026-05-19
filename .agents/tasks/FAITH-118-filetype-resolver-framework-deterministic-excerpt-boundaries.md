# FAITH-118 — Filetype Resolver Framework for Deterministic Excerpt Boundaries

**Phase:** 19 — Scoped File Storage & Deterministic Excerpt Retrieval
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-027, FAITH-032
**FRS Reference:** Section 4.16

---

## Objective

Create the shared framework that maps file groups to the deterministic excerpt
boundary types they support so excerpt retrieval can behave consistently across
documents, code, and config/data files.

---

## Scope

- Define file groups such as document, code, and config/data.
- Define the supported block types for each group such as line, sentence,
  paragraph, section, module, class, function, entry, or object.
- Fail clearly when a caller requests a boundary type the target file group does
  not support.
- Reuse existing parsing capability where possible instead of inventing a second
  independent parser stack for code.

---

## Notes

- This task is about the resolver framework and capability map, not the final
  user-facing MCP function contract.
