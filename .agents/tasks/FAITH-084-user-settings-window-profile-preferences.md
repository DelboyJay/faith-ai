# FAITH-084 — User Settings Window & Profile Preferences

**Phase:** 8 — Web UI
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-003, FAITH-004, FAITH-049, FAITH-074, FAITH-078, FAITH-083
**FRS Reference:** Section 6.4.2, 7.2, 9.3

---

## Objective

Add a dedicated Web UI settings window/panel where the user can review and update user-scoped information and preferences after first-run setup.

---

## Scope

- Add a settings panel or window to the Web UI.
- Preload persisted user-scoped values instead of presenting an empty state when settings already exist.
- Expose the timezone used by runtime time-context injection and allow the user to update it explicitly.
- Provide room for future user profile fields such as display name, preferred locale, or other user-scoped metadata.
- Validate and persist updates through the approved configuration pipeline on the host-backed FAITH runtime volume rather than bypassing it.
- Make accepted changes available to future agent turns without requiring manual file edits or a full FAITH restart.

---

## Notes

- This task complements `FAITH-049` and `FAITH-083`: the wizard can establish initial values, while the settings UI becomes the ongoing edit surface.
- The remaining architectural question is the authoritative storage location for user-scoped settings if FAITH later supports multiple users or per-project overrides.
