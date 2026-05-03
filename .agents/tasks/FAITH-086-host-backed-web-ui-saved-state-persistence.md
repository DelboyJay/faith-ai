# FAITH-086 — Host-Backed Web UI Saved State Persistence

**Phase:** 8 — Web UI
**Complexity:** S
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-015, FAITH-071, FAITH-084
**FRS Reference:** Section 2.3, 6.4.2, 8.4

---

## Objective

Enforce the framework rule that any value saved through the FAITH Web UI must persist on the host-backed FAITH runtime volume rather than on container-local filesystem paths.

---

## Scope

- Define the persistence rule explicitly in the FRS so future Web UI save features inherit the same storage contract.
- Move Project Agent prompt edits onto the host-backed runtime volume used by the PA.
- Move user-settings updates saved from the Web UI onto the same host-backed runtime volume.
- Preserve existing validation and runtime reload behaviour for those saved values.
- Add regression tests proving the persisted paths resolve under the host-backed runtime volume when FAITH runs in container mode.

---

## Acceptance Criteria

1. The FRS states that any user-saved Web UI state must persist on a host-backed FAITH runtime volume.
2. Saving the Project Agent prompt through the Web UI writes to the host-backed runtime volume rather than to container-local project paths when runtime volume configuration is present.
3. Saving user settings through the Web UI writes to the host-backed runtime volume rather than to container-local project paths when runtime volume configuration is present.
4. Persisted prompt and settings updates continue to affect future agent turns without requiring manual recovery steps after a container rebuild.
5. Tests prove the host-backed persistence paths are used for both prompt and settings flows.

---

## Notes

- This task establishes the persistence rule for current Web UI save surfaces. Future browser-save features must follow the same host-backed volume policy by default.
