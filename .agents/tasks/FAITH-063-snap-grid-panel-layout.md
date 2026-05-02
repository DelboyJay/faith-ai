# FAITH-063 — Snap-Grid Panel Layout Refinement

**Phase:** 8 — Web UI
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-062, FAITH-074, FAITH-075
**FRS Reference:** Section 6.4.1, 6.10

---

## Objective

Refine panel movement and resizing so the Dockview workspace feels structured
and dashboard-like. Where practical, Dockview placement and resizing should be
augmented with grid-aligned or layout-guided refinement so panels settle into
tidy arrangements instead of messy layouts.

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

- This is now a Dockview workspace UX refinement task rather than a legacy
  enhancement.
- If exact Datadog-style behaviour is impractical, prefer lightweight alignment
  and predictable placement over excessive customisation.
