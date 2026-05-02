# FAITH-042 — Terminal Dark Theme CSS

**Phase:** 8 — Web UI Polish
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-074, FAITH-077
**FRS Reference:** Section 6.6

---

## Objective

Implement the shared dark theme for the Web UI. The theme defines the global
colour tokens, typography, Dockview chrome styling, Radix UI menu styling,
xterm.js integration styling, approval-card treatment, input panel styling,
status panel styling, and reusable utility classes. It also self-hosts the
primary monospace font.

---

## Architecture

```text
src/faith_web/
└── templates/
    └── index.html           # Loads the shared stylesheet

web/
├── css/
│   └── theme.css            # This task
└── fonts/
    └── JetBrainsMono-Regular.woff2
```

---

## Required Scope

1. Define shared CSS custom properties for the FAITH UI palette and typography.
2. Self-host the primary monospace font.
3. Style Dockview chrome to match the FRS visual direction.
4. Style Radix UI menubar and context-menu surfaces so they match the shared
   FAITH theme.
5. Provide shared classes or tokens used by:
- agent panels
- approval panel
- input panel
- status panel
- log views
- Docker runtime panel
6. Keep motion minimal and intentional.
7. Avoid any external CSS dependency beyond approved runtime libraries already
   allowed by the FRS.

---

## Files to Create or Update

- `web/css/theme.css`
- `web/fonts/JetBrainsMono-Regular.woff2`
- `src/faith_web/templates/index.html`

---

## Acceptance Criteria

1. The Web UI uses a consistent dark theme rooted in FRS Section 6.6.
2. Dockview tabs, splitters, floating surfaces, and content panes inherit the
   shared visual system.
3. Radix UI menus inherit the shared visual system.
4. The primary font is self-hosted and referenced from the theme.
5. Theme hooks exist for all Web UI panel tasks in Phase 8 and Phase 13,
   including log and Docker panels.
6. The task reflects the current monorepo layout and does not refer to the
   retired `faith-web-ui/...` path scheme.

---

## Notes

- The theme itself should remain a local project asset even when Dockview,
  Radix UI, and xterm.js are provided through approved runtime packaging.
- Keep this file as the visual authority; avoid scattering incompatible
  panel-specific theme systems across later tasks.
