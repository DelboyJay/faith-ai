# FAITH-095 - Optional Text-to-Speech Runtime & Spoken Reply Integration

## Summary

Add optional text-to-speech support as a separate installable and configurable feature so PA replies can be spoken aloud without requiring microphone dictation or the talking-avatar feature.

## Scope

- Define a separate optional text-to-speech runtime or service contract for spoken PA replies.
- Keep TTS install, enablement, and configuration independent from STT and avatar/video features.
- Add spoken-reply playback controls and clear enable/disable states in the Web UI.
- Ensure TTS can be used on its own without the avatar feature.
- Degrade gracefully back to text-only chat when TTS is disabled, unavailable, or fails.

## Acceptance Criteria

1. Users can enable TTS without enabling STT or the avatar feature.
2. PA replies can be spoken through a dedicated optional TTS path when enabled.
3. Disabling TTS leaves normal text chat unaffected.
4. The avatar feature does not have to be enabled for TTS to work.
5. Tests prove TTS enable/disable and fallback behaviour work predictably.

## Dependencies

- FAITH-036 - FastAPI + WebSocket Web UI Backend
- FAITH-084 - User Settings Window & Profile Preferences

## Notes

- TTS should be treated as its own install/configuration decision in the wizard and settings UI.
- Avatar/video presentation may depend on or strongly prefer TTS, but TTS must remain useful on its own.
