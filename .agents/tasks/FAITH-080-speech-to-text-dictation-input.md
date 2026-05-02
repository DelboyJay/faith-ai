# FAITH-080 - Speech-to-Text Dictation Input

**Status:** TODO
**Phase:** 8 - Web UI
**Complexity:** M
**Model:** Sonnet / GPT-5.4

## Objective

Add microphone-driven dictation to the Web UI Input panel so the user can speak a message, have it transcribed locally by default, and then review or edit the resulting text before sending it to the Project Agent.

## Scope

- Add a microphone control to the Input panel.
- Request browser microphone permission only when the user explicitly starts dictation.
- Capture audio from the browser and send it to a local speech-to-text path by default.
- Prefer a free local/offline transcription runtime in v1 rather than a paid hosted API by default.
- Insert the resulting transcript back into the editable input area instead of sending it automatically.
- Show explicit recording, transcribing, success, and failure UI states.
- Preserve normal typed-input behaviour even if dictation fails or is cancelled.

## Dependencies

- FAITH-041 - Input Panel & File Upload
- FAITH-074 - Dockview Workspace Shell Migration
- FAITH-078 - Frontend Build Pipeline & Bundled Assets

## FRS References

- Section 6.4.1
- Section 6.4.2

## Acceptance Notes

- The user can start and stop dictation from the Input panel.
- The transcript appears in the text box and can be edited before send.
- Normal typed input still works with or without microphone access.
- The implementation defaults to a local/free STT path and does not require a paid speech API for baseline operation.
