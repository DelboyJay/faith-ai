# FAITH-123 — Session Naming, Scoped File Access, and Session Export Controls

**Phase:** 19 — Scoped File Storage & Deterministic Excerpt Retrieval
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-099, FAITH-121, FAITH-122
**FRS Reference:** Section 4.16, 8.4

---

## Objective

Tighten session lifecycle behaviour so storage scopes, exports, and UI selection
all work from stable UUID-backed sessions with useful user-facing names.

---

## Scope

- Ensure every session has a UUID and a user-visible name.
- Auto-create the first session on first startup so there is always at least
  one session ready for the first inference.
- Default the user-visible session name from the first inference input.
- Allow rename, archive, unarchive, delete, and export from the Session History
  panel.
- Use session names in scoped-file selection UI rather than exposing raw UUIDs
  to the user.
- Keep all sessions host-persisted and available for later inspection or export.

---

## Notes

- Archived sessions remain persisted and inspectable but should not participate
  in normal active-session flows until restored.
