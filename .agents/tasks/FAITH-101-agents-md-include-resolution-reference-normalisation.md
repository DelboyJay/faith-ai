# FAITH-101 — AGENTS.md Include Resolution & Reference Normalisation

**Phase:** 16 — Project Instruction Context & Model Intelligence
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-100, FAITH-022
**FRS Reference:** Section 3.5.1.1

---

## Objective

Resolve the complete set of markdown files that contribute to the PA's project
instruction context by supporting both explicit include directives and inferred
file references from `AGENTS.md`.

---

## Scope

- Support direct user-authored `!include path/to/file.md` directives.
- Detect obvious referenced markdown files from `AGENTS.md` content, including
  markdown links and prose references such as "see coding_style.md".
- Validate all resolved include targets against files that actually exist inside
  the project workspace.
- Reject or warn on include targets outside the workspace.
- Detect recursive include loops and enforce maximum include depth/count limits.
- Produce a deterministic resolved include graph and stable file order for later
  context compilation.
- Estimate token counts per included file for debugging and budget checks.

---

## Notes

- The inferred-reference path should remain deterministic and auditable; it
  must not silently include arbitrary files.
- This task may use a local normalisation helper to propose explicit includes,
  but FAITH must validate every proposed file before using it.
