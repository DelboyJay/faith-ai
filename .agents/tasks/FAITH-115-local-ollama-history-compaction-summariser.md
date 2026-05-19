# FAITH-115 — Local-Ollama History Compaction Summariser

**Phase:** 18 — Runtime Context Compaction & Rule Promotion
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-113, FAITH-114
**FRS Reference:** Section 3.5.4

---

## Objective

Use a local free Ollama model to compress older resolved PA/user history into
short, inspectable working-memory notes without trusting the model to decide
everything on its own.

---

## Scope

- Summarise older resolved user inference and PA response history into compact
  `done/decided` notes.
- Operate only on history layers approved by the deterministic retention rules.
- Never compact or rewrite FAITH core rules, raw `AGENTS.md`, stable included
  project instruction files, or MCP tool information/tool-manifest context.
- Produce compaction output that is inspectable and suitable for later
  debugging.

---

## Notes

- The local summariser is subordinate to deterministic retention rules; it is
  not allowed to decide what can be forgotten by itself.
