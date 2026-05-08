# FAITH-066 — Project `cag/` Auto-Loading & Budget Guidance

**Phase:** 7 — CAG & External MCP Integration
**Priority:** Medium
**Status:** DONE
**Dependencies:** FAITH-034, FAITH-022
**FRS Reference:** Section 4.10.3.2, Section 4.10.4, Section 9.3.5

## Summary

Promote the project-root `cag/` folder to a first-class default CAG source. On project load, the PA should discover supported markdown/text files under `cag/`, estimate their combined token usage, and auto-load them into the PA/agent CAG flow when they fit within budget. If the corpus exceeds the effective CAG budget, the PA should not silently drop documents; it should surface budget pressure and suggest high-level reduction options such as curated summaries, splitting high-value rules from bulky background material, or moving lower-value documents to RAG. FAITH v1 must not perform lossy compression automatically without an explicit user request.

## High-Level Implementation Scope

1. Extend the CAG manager and/or session-start validation flow to discover project-root `cag/` documents automatically.
2. Define the supported file types and deterministic discovery order for `cag/`.
3. Apply token-budget estimation before automatic injection into context.
4. Auto-load discovered `cag/` content when it fits within the configured budget.
5. Surface actionable over-budget guidance when it does not fit, including the largest contributing files and suggested reduction approaches.
6. Reuse the existing file-event path so changed `cag/` files are reloaded on the next relevant model call.
7. Add tests covering discovery, budget-fit auto-loading, over-budget guidance, and reload behaviour.

## Acceptance Intent

- A non-empty project `cag/` folder is automatically considered during CAG setup.
- Documents that fit budget are loaded without requiring manual per-file registration.
- Over-budget `cag/` corpora produce guidance rather than silent omission.
- No automatic lossy rewrite or summarisation of user `cag/` files occurs without explicit user instruction.
