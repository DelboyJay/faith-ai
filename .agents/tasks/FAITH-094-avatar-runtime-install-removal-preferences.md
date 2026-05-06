# FAITH-094 - Avatar Runtime Install, Removal, and Preference Management

## Summary

Allow users to enable, disable, install, remove, and configure the optional avatar runtime through FAITH setup and settings flows.

## Scope

- Add user-facing controls for installing or removing the avatar runtime cleanly.
- Persist avatar feature preferences separately from core runtime settings.
- Allow the avatar feature to be disabled without removing the user's underlying text chat settings.
- Integrate avatar enablement with first-run or later settings flows without making it a mandatory setup step.
- Keep avatar-related configuration isolated so users who do not want the feature can keep FAITH lean.

## Acceptance Criteria

1. Users can enable or disable the avatar runtime from FAITH-managed configuration surfaces.
2. Users can install or remove the avatar container cleanly through FAITH-managed workflows.
3. Avatar preferences persist across restarts and rebuilds on the host-backed runtime volume.
4. Disabling or removing the avatar feature does not break normal text chat or PA behaviour.
5. Tests prove install, remove, enable, and disable flows behave predictably.

## Dependencies

- FAITH-049 - First-Run Wizard: Multi-Step UI Flow
- FAITH-084 - User Settings Window & Profile Preferences
- FAITH-092 - Containerised Avatar Runtime & Service Contract

## Notes

- This task should treat the avatar as an optional add-on, not a core always-on component.
- Preference storage should align with the existing host-backed persistence rules for Web UI-saved state.
