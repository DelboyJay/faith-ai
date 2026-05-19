# FAITH-117 — Explicit Durable Rule Promotion from Inference to AGENTS.md

**Phase:** 18 — Runtime Context Compaction & Rule Promotion
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-100, FAITH-102, FAITH-098
**FRS Reference:** Section 3.5.1.1, 3.5.4

---

## Objective

Persist clearly declared durable user rules from normal PA conversation into the
project `AGENTS.md` automatically so important project behavior survives beyond
the current prompt window.

---

## Scope

- Detect clear durable-rule phrasing in user inference such as "new rule" or
  equivalent permanent-instruction language.
- Auto-persist the rule into project `AGENTS.md` without asking for a second
  confirmation prompt.
- Tell the user in the response that the new rule was added to `AGENTS.md`.
- Record the change in session/audit history.
- Avoid auto-promoting ambiguous one-off requests or temporary preferences.

---

## Notes

- This task complements context compaction by ensuring durable rules move into
  project instructions instead of depending on conversational memory alone.
