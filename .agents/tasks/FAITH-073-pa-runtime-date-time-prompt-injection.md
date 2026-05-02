# FAITH-073 - Agent Runtime Date Time Prompt Injection

**Phase:** 8 - Web UI
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** DONE
**Dependencies:** FAITH-010, FAITH-038, FAITH-071
**FRS Reference:** Section 3.5.1, 6.3, 7.3

---

## Objective

Ensure every FAITH agent always receives explicit runtime time context on every
interactive turn. The effective system-level prompt context for the PA and all
specialist agents must include:

- the current local date
- the current local time
- the user's timezone identifier

This context must be resolved using the user's configured timezone, not the
container default timezone, and it must refresh for each agent turn.

---

## Requirements

- Inject the date/time/timezone as runtime-managed prompt context for every
  agent.
- Do not rewrite persisted agent prompt files each time the clock changes.
- Keep user-editable prompt files and the runtime-injected time context as
  separate concepts so prompt editing remains stable and understandable.
- The timezone must be explicit in the prompt context so every agent can reason about
  relative dates such as `today`, `tomorrow`, `yesterday`, `this week`, and
  `next week`.
- If the user's timezone is unavailable, fall back to the configured FAITH
  project timezone or a documented safe default and surface that fallback in
  metadata/logging.

---

## Acceptance Criteria

1. Every agent LLM call includes runtime-injected current date, current time,
   and explicit timezone context.
2. The injected time context is resolved from the user's configured timezone
   rather than blindly using host/container local time.
3. The values refresh between turns without requiring agent or PA restarts.
4. Updating any editable prompt file does not remove or duplicate the runtime
   time-context injection.
5. Persisted prompt files remain stable and are not rewritten solely
   because time changed.
6. Tests are written first and cover timezone-aware injection, per-turn refresh,
   fallback behaviour, and prompt-editor compatibility.

---

## Notes

- This task is about runtime prompt assembly, not broad timezone conversion in
  the rest of the UI.
- The implementation should prefer a canonical timezone string such as an IANA
  identifier when one is available.
