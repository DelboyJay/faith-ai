# FAITH-099 — Session History Live Session Creation & Default Placement

**Phase:** 8 — Web UI
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-015, FAITH-044, FAITH-074, FAITH-082
**FRS Reference:** Section 6.4.1, 8.4, 8.7

---

## Objective

Make Session History behave like a live browser-facing session controller rather than a passive archive only.

---

## Delivered Behaviour

- The Session History panel now auto-refreshes so the first user message causes the new session to appear without requiring a manual browser reload.
- The panel exposes a visible `New Session` action that ends the current PA browser-chat session and starts a fresh one immediately.
- Starting a new session clears the live Project Agent runtime transcript/task state before the next message is sent.
- The Web UI proxies the new-session action through a same-origin API route.
- The default workspace places Session History in the upper-left region beside the Project Agent / System Status workspace.

---

## Notes

- This task is limited to the Project Agent browser-chat session flow and the Session History panel UX.
- Broader session/task persistence remains covered by FAITH-015, FAITH-046, and FAITH-082.
