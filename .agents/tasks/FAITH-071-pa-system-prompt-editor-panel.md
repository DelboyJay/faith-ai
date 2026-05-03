# FAITH-071 - PA System Prompt Editor Panel

## Summary

Add a dedicated Web UI panel for viewing and editing the Project Agent system prompt at runtime, with server-side validation and persistence on the host-backed FAITH runtime volume.

## Scope

- Add a PA System Prompt panel type to the Web UI.
- Load the active PA system prompt and metadata from the server.
- Allow the user to edit, save, reload, and reset the prompt where a safe default exists.
- Add server endpoints for reading and updating the active PA prompt.
- Validate prompt updates before applying them.
- Persist accepted updates through the approved PA prompt configuration path on the host-backed FAITH runtime volume.
- Apply prompt changes to future PA model calls without mutating historical messages.
- Surface success/failure status in the Web UI.

## Acceptance Criteria

1. The Web UI can display the active PA system prompt and its metadata.
2. The user can submit an edited prompt through the UI.
3. Invalid prompt updates are rejected with a plain-English error and leave the current prompt active.
4. Valid prompt updates persist on the host-backed FAITH runtime volume and are used on future PA turns.
5. Historical transcript entries are not rewritten when the prompt changes.
6. Unsaved edits are visible before closing, resetting, or reloading the panel.
7. Tests cover the prompt read endpoint, update validation, persistence, and UI panel behaviour.

## Dependencies

- FAITH-036 - FastAPI Server Setup & WebSocket Endpoints
- FAITH-038 - Agent Panel Component (xterm.js + React)
- FAITH-074 - React + Dockview Workspace Shell Migration

## Notes

- This task creates a narrow prompt-editing path only. It does not introduce broad in-browser config editing.
- Prompt changes should be user-initiated and auditable.
