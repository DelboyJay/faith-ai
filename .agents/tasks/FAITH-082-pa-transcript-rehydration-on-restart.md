# FAITH-082 — Project Agent Transcript Rehydration on Restart

**Phase:** 8 — Web UI & User Interaction
**Complexity:** S
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-015, FAITH-038, FAITH-046, FAITH-074
**FRS Reference:** Section 6.4.2, 8.4

---

## Objective

Restore the latest persisted Project Agent conversation back into the Web UI panel after browser reload, Web UI restart, or PA restart so the visible transcript matches the retained backend context.

---

## Delivered Behaviour

- Persist the Project Agent user/assistant transcript at session level in `pa-user.log`.
- Resume the latest persisted Project Agent session when appropriate.
- Expose the latest saved transcript through a dedicated PA/API path.
- Rehydrate the Project Agent panel from the saved transcript before live WebSocket streaming resumes.
- Keep the restored transcript in chronological order and continue appending new live messages normally.

---

## Notes

- This task covers Project Agent transcript restore only.
- Broader session/task/channel log coverage remains part of FAITH-046.
