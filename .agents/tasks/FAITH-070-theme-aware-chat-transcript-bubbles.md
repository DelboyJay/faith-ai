# FAITH-070 - Theme-Aware Chat Transcript Bubbles

## Summary

Improve the Project Agent chat transcript so it reads like a modern conversation view rather than a terminal dump, while keeping retained PA context visible or clearly summarised.

## Scope

- Replace visible `User:` and `PA:` transcript prefixes with speaker-aware message bubbles.
- Render user messages as right-aligned bubbles with blue-accent themed text.
- Render PA messages as readable assistant bubbles or message blocks with white/light themed text.
- Use CSS theme variables and browser/OS colour-scheme support instead of hard-coded colours.
- Stream PA output into the active assistant bubble.
- Keep retained conversation context visible, or show a clear retained-context summary when hidden context is used.
- Keep thinking/tool progress visible as compact status rows without replacing the final transcript.

## Acceptance Criteria

1. User and PA messages are visually distinguished without literal `User:` or `PA:` prefixes.
2. User messages are right-aligned and use a theme-controlled blue text/accent.
3. PA messages remain readable in the active theme and use theme-controlled white/light text.
4. Streaming responses append into one active assistant message bubble.
5. Resetting/reloading the layout does not hide retained conversation context in a way that makes the PA appear to remember invisible text.
6. Automated UI or component tests cover transcript rendering, streaming append behaviour, and theme token usage.

## Dependencies

- FAITH-038 - Agent Panel Component (xterm.js + React)
- FAITH-041 - Input Panel & File Upload
- FAITH-064 - Panel Title-Bar Actions
- FAITH-069 - PA MCP Inventory Grounding

## Notes

- This task is UI-focused and does not change the PA's reasoning logic.
- The transcript must remain compatible with future tool-progress rendering from the PA MCP loop.
- The transcript must render cleanly inside the Dockview-based Project Agent panel and must not depend on any legacy layout-specific DOM structure.
