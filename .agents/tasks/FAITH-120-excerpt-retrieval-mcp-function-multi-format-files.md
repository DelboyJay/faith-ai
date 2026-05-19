# FAITH-120 — Excerpt Retrieval MCP Function for Multi-Format Files

**Phase:** 19 — Scoped File Storage & Deterministic Excerpt Retrieval
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-118, FAITH-119
**FRS Reference:** Section 4.16

---

## Objective

Return bounded matching excerpts from many file types using stable identifiers
from the discovery step so agents can retrieve only the exact text they need.

---

## Scope

- Support deterministic excerpt retrieval for:
  - markdown and plain-text documents
  - PDF, DOCX, XLSX, and LibreOffice document formats
  - HTML and XML
  - code files
  - config/data files
- Return only the requested excerpt block types that the file supports.
- Reject unsupported block-type requests clearly instead of guessing a fallback.
- Keep the MCP contract stable enough for agents to chain discovery then
  retrieval without repeating broad full-file reads.

---

## Notes

- Performance optimisations or parsed-content caches are out of scope for v1;
  the tool may re-read the original stored file as needed.
