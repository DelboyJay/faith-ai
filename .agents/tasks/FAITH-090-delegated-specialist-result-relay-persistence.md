# FAITH-090 - Delegated Specialist Result Relay & Persistence

## Summary

Persist and surface the single-specialist delegation trace cleanly so the user sees useful progress and results while FAITH retains the underlying audit trail.

## Scope

- Persist PA-to-specialist assignment logs for delegated chat work.
- Persist delegated task metadata, completion summaries, and error outcomes.
- Rehydrate delegated chat activity on restart alongside the main PA transcript.
- Show user-facing PA transcript updates that explain delegation and completion without exposing raw compact-protocol traffic by default.
- Keep full details available in logs and session history for debugging and audit.

## Acceptance Criteria

1. Delegated single-specialist work writes durable assignment and completion traces into the session/task logs.
2. Restart-time transcript rehydration preserves the visible delegation story in the PA panel.
3. The PA panel shows that work was delegated and completed without dumping raw protocol frames by default.
4. Session history and log views retain the deeper underlying assignment/completion details.
5. Tests prove persisted delegated traces survive restart and remain readable from the Web UI endpoints.

## Dependencies

- FAITH-046 - Session & Task Log Writer
- FAITH-082 - PA Transcript Rehydration on Restart
- FAITH-089 - PA Chat Specialist Delegation Loop

## Notes

- This task is about visibility and persistence, not the decision to delegate.
- The UI may later gain richer delegated-task visualisation, but this task only requires the current panels and logs to stay coherent.
