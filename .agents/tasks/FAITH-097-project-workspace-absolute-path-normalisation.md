# FAITH-097 - Project-Workspace Absolute Path Normalisation for Chat Tool Calls

## Summary

Allow the Project Agent chat tool loop to accept valid absolute project-workspace paths safely by normalising them onto configured filesystem mounts before dispatch.

## Scope

- Detect Windows and POSIX absolute paths provided in filesystem chat-tool arguments.
- Resolve recognised in-workspace absolute paths against the configured filesystem mount roots.
- Rewrite recognised absolute paths into the canonical `mount` + mount-relative `path` shape before dispatch.
- Leave out-of-workspace or unknown absolute paths unchanged so normal filesystem validation still rejects them safely.
- Improve the prompt guidance so the model is less likely to emit raw absolute-path mount arguments in the first place.

## Acceptance Criteria

1. A valid absolute path under the active project root can be handled successfully by the PA chat filesystem path.
2. The normalised dispatch uses the configured mount name rather than a raw absolute path as the mount identifier.
3. Unknown or out-of-scope absolute paths are still rejected by the existing filesystem safety model.
4. Tests prove the previous unknown-mount failure and the corrected normalised behaviour.

## Dependencies

- FAITH-022 - Filesystem MCP Server
- FAITH-068 - PA Chat Loop MCP Tool Calling

## Notes

- This task only normalises paths already inside approved workspace mounts. It must not broaden filesystem access.
