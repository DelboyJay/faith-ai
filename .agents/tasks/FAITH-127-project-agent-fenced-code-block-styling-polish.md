# FAITH-127 - Project Agent Fenced Code Block Styling Polish

## Summary

Polish the Project Agent transcript so triple-backtick fenced code blocks read
as unmistakable code rather than ordinary prose.

## Scope

- Preserve the existing rich transcript bubble renderer for the Project Agent.
- Keep triple-backtick fenced code blocks rendered as dedicated code elements.
- Use a fixed-width font stack for rendered code content.
- Strengthen the code-block visual treatment so code stands out clearly inside
  assistant bubbles.
- Keep optional language labels readable and visually associated with the code
  block they describe.

## Acceptance Criteria

1. Triple-backtick fenced code blocks render inside Project Agent transcript
   bubbles as dedicated code blocks rather than raw fence text.
2. Rendered code uses a fixed-width font stack.
3. Code blocks have a clearly distinct visual treatment from ordinary message
   prose.
4. Existing transcript bubbles, streaming behaviour, and non-code text remain
   intact.
5. Tests cover the stylesheet contract for rendered code blocks.

## Dependencies

- FAITH-038 - Agent Panel Component

## Notes

- This task is transcript-polish work only. It does not introduce a terminal
  renderer or syntax-highlighting engine.
