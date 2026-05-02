# FAITH-028 — RAG / ChromaDB MCP Server

**Phase:** 6 — Tool Servers
**Complexity:** L
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-002, FAITH-022
**FRS Reference:** Section 4.7

---

## Objective

Optional future work. RAG is not a required FAITH-owned capability in v1 and should default to an external MCP server from the registry when users choose to add it later. If FAITH later decides to own a RAG implementation, the likely shape is a ChromaDB-backed MCP server that indexes project documents, performs embedding and retrieval, and returns source-aware chunks to the PA.

---

## Current FRS Position

- Not required for bootstrap or baseline operation.
- Not enabled by default.
- Prefer external registry-backed MCP integration in v1.
- A FAITH-owned RAG implementation is fallback or later-scope work only.

---

## If FAITH Owns This Later

Likely scope:
- document ingestion and chunking
- embedding generation
- vector storage and retrieval
- source references in retrieval results
- project-scoped indexing lifecycle
- PA-facing MCP tools for query and maintenance operations

Likely files:
- `src/faith_mcp/rag/`
- `tests/test_rag_mcp.py`
- optional supporting storage/runtime assets if ChromaDB is used

---

## Acceptance Criteria

1. The task remains explicitly optional in v1.
2. The task documentation does not assume a mandatory bundled `tool-rag` container.
3. Any later FAITH-owned implementation must preserve MCP boundaries and project-scoped operation.

---

## Notes

- Keep this task documented as optional until the external-registry-first policy changes.
- Do not treat this task as a prerequisite for CAG or baseline project operation.
