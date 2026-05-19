# FAITH-102 — Effective PA Context Compiler, Hash Cache, and Persistence

**Phase:** 16 — Project Instruction Context & Model Intelligence
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-100, FAITH-101, FAITH-082, FAITH-086
**FRS Reference:** Section 3.5.1.1, 8.4

---

## Objective

Compile the final PA context from stable and runtime layers, cache the stable
compiled instruction block by content hash, and persist redacted snapshots on
the host-backed session store for later inspection.

---

## Scope

- Define the compiled PA context order: FAITH core instructions, project
  `AGENTS.md`, resolved include content, runtime time/user/tool context, and
  recent conversational/task layers as appropriate.
- Cache the stable project-instruction portion by content hash so it is reused
  until the underlying files change.
- Recompute the compiled block automatically when `AGENTS.md` or any resolved
  included file changes.
- Persist redacted compiled-context snapshots to the host-backed runtime/session
  store whenever the compiled context changes.
- Tie persisted snapshots to session/turn metadata so the exact effective
  context used at a point in time can be inspected later.

---

## Notes

- Persist snapshots only when the compiled context changes rather than on every
  turn to avoid noisy duplication.
- Redaction rules are handled as a deterministic safety layer, not as best-effort
  LLM reasoning.
