# FAITH-083 — User Timezone Preference Resolution & Persistence

**Phase:** 10 — First-Run Wizard & Setup
**Complexity:** S
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-003, FAITH-049, FAITH-073
**FRS Reference:** Section 3.5.1, 7.2, 9.3

---

## Objective

Make the timezone used by runtime date/time prompt injection explicit, user-controlled, and persistent.

---

## Scope

- Add a persisted user-configurable timezone setting.
- Allow FAITH to suggest an initial timezone from browser or host signals during setup.
- Let the user override the suggested timezone explicitly.
- Ensure runtime time-context injection always uses the persisted resolved timezone rather than the PA container timezone.

---

## Notes

- This task complements `FAITH-073`; it does not replace runtime per-turn injection.
- The remaining product decision is where this preference should live if FAITH later supports one user working across multiple projects in different timezones.
