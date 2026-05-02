# FAITH-064 — Panel Title-Bar Actions

**Phase:** 8 — Web UI
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-074, FAITH-062
**FRS Reference:** Section 6.4.1, 6.10

---

## Objective

Clean up panel chrome so panel names live in the title bar and closable panels expose an obvious close action there as well. This removes wasted left-side label space, improves alignment, and makes panel removal discoverable.

---

## Required Scope

1. Remove duplicated left-side panel labels where the title bar already identifies the panel.
2. Use the title bar as the single primary label for the panel name.
3. Add a visible close affordance in the title bar for closable panels, such as an `×` button in the top-right area.
4. Keep non-closable/singleton panels visually consistent if some are intentionally protected from closure.
5. Preserve compatibility with panel deduping and reopen flows from FAITH-062.
6. Keep the panel chrome compact and aligned across the workspace.

---

## Files to Create or Update

- `web/js/layout.js`
- `web/css/theme.css`
- `tests/test_layout.html`
- `tests/test_web_server.py` (if static shell assertions need updating)

---

## Testing Requirements

Add or update checks that prove:

- panel names appear in the title bar
- duplicated side labels are removed
- closable panels expose a visible close affordance
- closing from the title bar still cooperates with panel lifecycle and dedupe logic

---

## Acceptance Criteria

1. Panel chrome no longer wastes space on duplicated side labels.
2. Panel names are readable from the title bar alone.
3. Closable panels expose a clear title-bar close action.
4. The updated chrome aligns cleanly across the workspace.
5. The updated chrome works cleanly within the bundled React + Dockview frontend.

---

## Notes

- This is a focused UX polish task, separate from the broader lifecycle and grid behaviour tasks.
- Prefer Dockview-native title-bar affordances where they meet the UX requirement before adding custom chrome logic.
