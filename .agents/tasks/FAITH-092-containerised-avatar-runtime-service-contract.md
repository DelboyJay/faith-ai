# FAITH-092 - Containerised Avatar Runtime & Service Contract

## Summary

Define and implement an optional dedicated Docker image for an independent avatar runtime that can be installed and removed separately from the core FAITH stack.

## Scope

- Create a dedicated avatar runtime container contract for health, session lifecycle, and avatar animation or viseme events.
- Keep the avatar runtime optional so FAITH can run normally when the avatar service is absent.
- Ensure the runtime can be enabled, disabled, installed, or removed without changing the core PA reasoning path.
- Define the local integration contract that the Web UI and PA can call without coupling to a specific avatar implementation.
- Keep the runtime isolated behind Docker so users can add or remove it cleanly based on preference.
- Allow the avatar runtime to consume optional TTS output or timing data when present, without forcing TTS to be enabled globally.

## Acceptance Criteria

1. FAITH has a documented and implemented optional Docker image for the avatar runtime.
2. The runtime exposes a stable local API or WebSocket contract for health and avatar animation metadata.
3. The core FAITH stack continues to work when the avatar runtime is not installed or enabled.
4. Runtime install and removal do not require manual edits to unrelated core services.
5. Tests prove the service contract and optional-runtime behaviour work without making the avatar feature mandatory.

## Dependencies

- FAITH-001 - Project Directory Structure & Base Scaffolding
- FAITH-005 - FAITH CLI (`faith-cli` Package)
- FAITH-036 - FastAPI + WebSocket Web UI Backend
- FAITH-095 - Optional Text-to-Speech Runtime & Spoken Reply Integration

## Notes

- This task is about the avatar runtime container boundary and contract, not the final Web UI avatar experience.
- The runtime should complement the existing PA text chat rather than replace it.
- STT, TTS, and avatar/video should remain separable user choices even when they work well together.
