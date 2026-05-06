# FAITH-094 - Avatar Runtime Install, Removal, and Preference Management

## Summary

Allow users to enable, disable, install, remove, and configure speech-to-text, text-to-speech, and the optional avatar runtime through FAITH setup and settings flows.

## Scope

- Add user-facing controls for installing or removing optional STT, TTS, and avatar runtime features cleanly.
- Persist speech/avatar feature preferences separately from core runtime settings.
- Allow each feature to be enabled or disabled independently without removing the user's underlying text chat settings.
- Integrate feature enablement with first-run or later settings flows without making them mandatory setup steps.
- Keep speech/avatar-related configuration isolated so users who do not want the features can keep FAITH lean.

## Acceptance Criteria

1. Users can choose STT, TTS, and avatar/video separately from FAITH-managed configuration surfaces.
2. Users can install or remove the optional runtime containers cleanly through FAITH-managed workflows.
3. Speech/avatar preferences persist across restarts and rebuilds on the host-backed runtime volume.
4. Disabling or removing any one optional feature does not break normal text chat or PA behaviour.
5. Tests prove install, remove, enable, and disable flows behave predictably.

## Dependencies

- FAITH-049 - First-Run Wizard: Multi-Step UI Flow
- FAITH-084 - User Settings Window & Profile Preferences
- FAITH-092 - Containerised Avatar Runtime & Service Contract
- FAITH-095 - Optional Text-to-Speech Runtime & Spoken Reply Integration

## Notes

- This task should treat speech/avatar features as optional add-ons, not core always-on components.
- Preference storage should align with the existing host-backed persistence rules for Web UI-saved state.
