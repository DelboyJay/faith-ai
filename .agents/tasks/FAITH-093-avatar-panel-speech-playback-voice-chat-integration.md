# FAITH-093 - Avatar Panel, Speech Playback, and Voice Chat Integration

## Summary

Add a Web UI avatar panel that can render a talking avatar, play spoken PA replies, and optionally support microphone-driven voice chat through the existing conversation flow.

## Scope

- Create an Avatar panel in the Web UI that can connect to the optional avatar runtime when enabled.
- Make the Avatar panel behave like a normal FAITH workspace panel: dockable, tab-stackable, minimisable, restorable, and removable from the active layout without uninstalling the underlying avatar runtime.
- Render avatar state updates such as speaking, idle, and animation or viseme-driven mouth movement.
- Play spoken PA replies when optional TTS is enabled, while keeping the normal text transcript as the authoritative conversation record.
- Optionally route user microphone speech through the existing dictation or voice input path so the transcript remains editable and auditable.
- Surface clear connection, enabled, disabled, and failure states without degrading the normal text-only experience.

## Acceptance Criteria

1. The Web UI can show an Avatar panel when the feature is enabled.
2. The Avatar panel participates in normal Dockview workspace behavior, including docking, tab stacking, minimize/restore, and close/reopen flows.
3. Spoken PA replies are synchronized with avatar speaking state through the avatar runtime contract when TTS is enabled.
4. The Project Agent transcript remains the source of truth even when voice/avatar mode is active.
5. Voice/avatar failures degrade gracefully back to normal text chat.
6. Tests prove the panel integration and fallback behaviour work without requiring the avatar feature to be enabled globally.

## Dependencies

- FAITH-080 - Speech-to-Text Dictation Input
- FAITH-084 - User Settings Window & Profile Preferences
- FAITH-092 - Containerised Avatar Runtime & Service Contract
- FAITH-095 - Optional Text-to-Speech Runtime & Spoken Reply Integration

## Notes

- This task should avoid creating a second conversation pathway.
- The avatar should visually reflect the PA conversation, not invent independent behaviour.
- The avatar/video feature should remain a separate user choice from STT and TTS even if it works best with TTS enabled.
