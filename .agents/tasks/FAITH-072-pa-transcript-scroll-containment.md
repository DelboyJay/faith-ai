# FAITH-072 - PA Transcript Scroll Containment

**Status:** DONE

## Summary

Keep the Project Agent transcript readable as chat history grows by making the transcript scroll internally and adding a clear way to jump to the newest text.

## Scope

- Add an internal scrollbar when transcript content exceeds the available space.
- Auto-scroll to new PA text when the user is already at or near the bottom.
- Preserve the user's scroll position when they have intentionally scrolled up to read earlier messages.
- Show a visible `Jump to latest` control when newer content exists below the current viewport.

## Acceptance Criteria

1. Overflowing transcript content scrolls inside the panel body.
2. New PA streamed text remains visible automatically while the transcript is pinned near the bottom.
3. User scroll position is not stolen when the user is reading earlier transcript content.
4. A `Jump to latest` button scrolls directly to the newest transcript entry or streamed chunk.
5. Tests cover scrollbar presence/behaviour, auto-scroll behaviour, user-scroll preservation, and the jump-to-latest control.

## Dependencies

- FAITH-038 - Agent Panel Component (xterm.js + React)
- FAITH-070 - Theme-Aware Chat Transcript Bubbles

## Notes

- This task is UI-only and should not change PA message generation.
- The scroll behaviour should work with streamed assistant responses, not only completed messages.
- The containment behaviour must remain correct inside the Dockview-based Project Agent panel body.
