# FAITH-078 - Frontend Build Pipeline & Bundled Asset Integration

**Phase:** 13 - Web UI Workspace Migration
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-036
**FRS Reference:** Section 2.2.5, 6.3, 6.10

---

## Objective

Introduce the Node-based frontend build pipeline required by the React +
Dockview migration while keeping Node off the host-machine requirement path.

---

## Requirements

- Add package manifests and pinned frontend dependencies for React, Dockview,
  Radix UI, and xterm.js.
- Produce compiled browser bundles that are served by the existing FastAPI Web
  UI service.
- Keep the supported workflow containerised or build-stage based so FAITH does
  not require Node to be installed directly on the host machine.
- Document how bundled assets are versioned and cache-busted.

---

## Acceptance Criteria

1. The React frontend can be built into static browser assets.
2. FastAPI serves the compiled bundle successfully.
3. The web-ui container build performs the frontend build as part of the image
   pipeline.
4. Asset versioning and cache-busting remain reliable after rebuilds.
5. Tests are written first and cover bundle serving and shell asset references.
