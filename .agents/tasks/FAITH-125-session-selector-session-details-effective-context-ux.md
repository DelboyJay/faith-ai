# FAITH-125 — Session Selector, Session Details Panel, and Effective Context UX Cleanup

**Phase:** 8 — Web UI
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-044, FAITH-099, FAITH-103, FAITH-084
**FRS Reference:** Section 6.4.2, 8.4, 8.7

---

## Objective

Make session-driven navigation behave like a normal chat-session selector rather
than a technical log browser. Users should select sessions by human-readable
name, resume them immediately, and inspect deeper metadata through lightweight
session metadata affordances instead of a verbose selector layout.

---

## Scope

- Change Session History into a compact session selector that lists only
  user-facing session names by default.
- Group sessions into `Active Sessions` and `Archived Sessions`, with archived
  sessions shown only when the user enables `Show Archived Sessions`.
- Support per-list persisted ordering preferences with `Most Recently Used`,
  `Alphabetically A-Z`, and `Alphabetically Z-A`, applied consistently to all
  visible groups in that list.
- Add per-list case-insensitive contains-search and keep group headers visible
  with a `No matches` message when filtering removes all rows from a group.
- Remove raw UUID-focused session presentation from the normal selector rows,
  but provide a hover/click metadata affordance that can show UUID and other
  identifying metadata for duplicate names.
- Add inline per-row actions for archive, restore, export, and delete as
  appropriate for the session state, with required disabled-state tooltips.
- Make selecting one non-archived session immediately activate it for future
  user inference and update all session-bound panels to that selected session.
- Persist one input draft per session and restore the matching draft when the
  user switches back to that session.
- Switch the backend session first, then repaint the Project Agent panel from
  the selected session's persisted transcript so messages from different
  sessions do not mix visually.
- Block input briefly while the backend confirms a session switch, and revert to
  the previously active session if the switch fails.
- Write a compact resume marker only when the user actually sends the first new
  message into a previously resumed session, not merely when browsing.
- Keep workspace layout unchanged while session-bound content swaps.
- Update the Effective Context panel so it binds to the currently selected
  session automatically, shows human-facing snapshot controls, and hides raw
  session/turn identifier entry from the default view.
- Remove the current default `System Status` panel from the workspace while it
  remains functionally redundant with the Docker Runtime view.

---

## Acceptance Criteria

1. Session History shows a clean grouped list of session names rather than raw
   UUID-led entries in the normal view.
2. Clicking a non-archived session immediately makes that session the active
   session for the next inference turn and updates the Project Agent, Input
   draft, Effective Context, Token Usage, and other session-specific panels.
3. Archived sessions appear only when requested, stay clearly non-resumable
   until restored, and expose restore plus delete affordances inline.
4. Search, ordering, and disabled-state tooltip behavior match the agreed list
   UX rules without hiding group context confusingly.
5. The Effective Context panel no longer requires the user to know or type raw
   session IDs or turn IDs during normal use.
6. The default workspace no longer opens System Status while that panel remains
   functionally duplicated by Docker Runtime.

---

## Notes

- The session selector should behave more like Codex/Claude style conversation
  switching than like a general-purpose audit browser.
- Raw internal identifiers may still exist behind an advanced/debug affordance,
  but must not be the primary UX path.
- Hover or click metadata affordances may replace a heavyweight dedicated
  details panel where they cover the required session-identification needs.
