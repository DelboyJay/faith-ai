# Versioning Rules

Apply this file whenever shipping a user-facing product change.

- Always update the visible project version when shipping a change so the
  running UI proves the latest code is present.
- Use `major.minor.revision` versioning.
- Increment the `major` number for breaking changes or incompatible behaviour
  changes.
- Increment the `minor` number for new features or meaningful new user-facing
  capabilities.
- Increment the `revision` number for bug fixes, small behaviour fixes, and
  other minor changes.
- When a version is changed, update every source of that displayed version
  needed by the product so the UI and served metadata stay in sync.
- If the task is only a focused bug fix, do not update planning docs, epic
  files, or visible version numbers unless the user-facing shipped behaviour
  genuinely changes or the user explicitly asks for those updates.

