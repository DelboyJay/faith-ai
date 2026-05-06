# FAITH-063 — Snap-Grid Panel Layout Refinement

**Phase:** 13 — Web UI Workspace Migration
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Dependencies:** FAITH-062, FAITH-074, FAITH-075
**FRS Reference:** Section 6.4.1, 6.10

---

## Objective

Refine panel movement and resizing so the Dockview workspace feels structured
and dashboard-like. Where practical, Dockview placement and resizing should be
augmented with grid-aligned or layout-guided refinement so panels settle into
tidy arrangements instead of messy layouts. This is a workspace-mechanics task,
not a panel-content task, so it belongs with the shell/layout migration work.

Current implementation note: FAITH now applies a lightweight layout-guided snap
layer when persisting and restoring Dockview layouts, rounding clear ratio or
percentage split collections into tidy increments while leaving native Dockview
drag, docking, tab, and float behaviour intact. Exact Datadog-style live grid
snapping is not attempted because Dockview is not a free-form grid engine; the
refinement is intentionally persistence-oriented and non-invasive.

---

## Required Scope

1. Improve panel movement/resizing so it behaves more like a dashboard grid
   than a free-form canvas.
2. Preserve Dockview docking, tab-grouping, floating, and split-pane
   behaviour.
3. Apply snapping or grid-alignment rules that help panels land in consistent
   positions and sizes.
4. Keep the behaviour intuitive on first use and avoid fighting the user during
   drag operations.
5. Ensure saved layouts persist the snapped/final arrangement correctly.
6. Document any practical Dockview limitation if exact Datadog-style snapping
   is not achievable without excessive customisation.

---

## Files to Create or Update

- `web/src/main.jsx`
- `web/js/layout.js`
- `web/css/theme.css`
- `tests/test_layout.html`
- `tests/test_web_server.py` (if asset-level coverage needs extending)

---

## Testing Requirements

Add or update tests or harness checks that prove:

- the layout still supports drag, dock, resize, float, and tab-group
  operations
- the final saved layout reflects grid-aligned or snapped positions/sizes
- snap behaviour does not break popout, close, reset, or add-panel actions

---

## Acceptance Criteria

1. Panel movement and resizing feel structured and intentional.
2. The workspace remains compatible with Dockview persistence and panel
   lifecycle flows.
3. The refinement improves default UX without fighting Dockview's native model.
4. Any unavoidable Dockview limitation is documented clearly in the task notes
   or implementation comments.

---

## Notes

- This is a Phase 13 workspace-mechanics task rather than a panel-feature task.
- If exact Datadog-style behaviour is impractical, prefer lightweight alignment
  and predictable placement over excessive customisation.
