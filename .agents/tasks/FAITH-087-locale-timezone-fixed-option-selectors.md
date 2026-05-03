# FAITH-087 — Locale & Timezone Fixed-Option Selectors

**Phase:** 8 — Web UI
**Complexity:** S
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-083, FAITH-084
**FRS Reference:** Section 6.4.2, 7.2, 9.3

---

## Objective

Replace free-text locale and timezone entry in the user settings flow with validated fixed-option selectors so the saved values are predictable, easy to validate, and safe for runtime prompt injection.

---

## Scope

- Replace free-text locale entry with a dropdown or equivalent fixed-option selector.
- Add a country selector used to narrow timezone choices.
- Replace free-text timezone entry with a dropdown or equivalent fixed-option selector.
- Default locale to `en-GB`.
- Default timezone to `Europe/London`.
- Allow setup-time suggestion logic to preselect a likely timezone when appropriate, but keep timezone as an explicit saved value.
- Do not derive timezone blindly from country alone; country may only be used as a narrowing or suggestion input.

---

## Acceptance Criteria

1. The user settings UI no longer requires free-text locale entry.
2. The user settings UI no longer requires free-text timezone entry.
3. Changing country updates the timezone dropdown to show only the allowed timezones for that country.
4. The default locale is `en-GB` unless the user or setup flow explicitly chooses another value.
5. The default timezone is `Europe/London` unless the user or setup flow explicitly chooses another value.
6. Validation errors caused by malformed locale/timezone strings are removed for normal dropdown usage.
7. The implementation does not assume one timezone per country; timezone remains an explicit persisted preference.

---

## Notes

- Countries such as the United States, Canada, Australia, Brazil, and others span multiple timezones, so country is not a reliable authoritative source for timezone.
- If country is introduced later as a profile field, it should help filter or suggest timezone choices rather than overwrite the saved timezone automatically.
