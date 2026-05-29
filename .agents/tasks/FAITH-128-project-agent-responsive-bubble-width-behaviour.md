# FAITH-128 — Project Agent Responsive Bubble Width Behaviour

## Summary

Make the Project Agent transcript bubble width adapt to the current resizable
panel width so narrow layouts still compress cleanly while wider layouts use
space more effectively.

## Scope

- Adjust the Project Agent transcript bubble sizing rules in the browser UI.
- Preserve the current bubble alignment and wrapping behaviour.
- Keep the change CSS-focused unless a minimal JavaScript hook is strictly
  required.

## Requirements

- Bubble widths must remain comfortable and compact in narrow panel layouts.
- Bubble widths must expand more naturally when the Project Agent panel is made
  significantly wider.
- The behaviour must be responsive to panel/container width rather than relying
  only on one fixed max-width cap.
- Existing code-block rendering inside transcript bubbles must continue to work.

## Acceptance Criteria

1. On a narrow Project Agent panel, user and assistant bubbles still wrap and
   compress cleanly without forcing unnecessary horizontal overflow.
2. On a wider Project Agent panel, user and assistant bubbles occupy more of
   the available width instead of remaining artificially narrow.
3. Existing transcript alignment, spacing, and code-block styling remain
   intact.
4. Regression coverage proves the shipped stylesheet includes the responsive
   container-aware bubble sizing rules.
