# FAITH-085 — Input Panel Enter-to-Send & Newline Hint

**Phase:** 8 — Web UI
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-041, FAITH-074, FAITH-078
**FRS Reference:** Section 6.4.2

---

## Objective

Improve the Input panel so common chat-composer keyboard behaviour works naturally and is discoverable.

---

## Scope

- Pressing `Enter` in the main message input submits the current message.
- Pressing `Alt+Enter` inserts a newline instead of sending.
- Show small helper text beneath the text box and above the action buttons explaining the shortcuts.
- Preserve normal button-based sending and multiline drafting behaviour.

---

## Notes

- This task is limited to the Input panel interaction model and visible hint text.
- It should remain compatible with future dictation, attachment, and richer composer features.
