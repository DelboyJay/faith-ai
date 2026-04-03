# FAITH-037 — GoldenLayout Panel Framework

**Phase:** 8 — Web UI
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-036
**FRS Reference:** Section 6.4, 6.10, 6.11

---

## Objective

Initialise GoldenLayout as the panel management system for the FAITH Web UI. Load GoldenLayout via CDN with an optional vendored fallback for offline/air-gapped deployment. Define and register the baseline panel component types (Agent, Tool, Input, Approval, Status), while keeping the panel registry extensible for additional operational panels such as the dedicated Docker Runtime panel (FAITH-058). Render a default layout on first load. Persist the user's layout to `localStorage` so it survives page refreshes. Provide a toolbar with a tab bar, a `+` button for adding new panels, and a `Reset Layout` button. Support all standard panel interactions: drag to reposition, dock, resize by dragging borders, tab-grouping by dropping a panel onto another, and detaching a panel to a floating window.

---

## Architecture

```
src/faith_web/
├── templates/
│   └── index.html           ← Jinja2 shell (loads GoldenLayout, Vue 3, xterm.js)

web/                             ← Frontend assets (Vue 3)
├── js/
│   ├── layout.js         ← GoldenLayout initialisation and panel registration (this task)
│   ├── app.js            ← Vue 3 app — panel components, WebSocket logic (stub hooks for this task)
│   └── vendor/           ← Optional vendored copies of CDN libraries
│       └── goldenlayout.js
├── css/
│   └── theme.css         ← GoldenLayout chrome overrides (minimal scope in this task)
└── fonts/
    └── JetBrainsMono.woff2
```

This task produces `layout.js` as its primary deliverable inside the `web/` frontend assets directory. It also modifies `index.html` to load GoldenLayout and initialise the layout, and creates minimal stub CSS for GoldenLayout chrome in `theme.css`. The actual panel component implementations (xterm.js rendering, WebSocket binding, approval cards, etc.) are delivered by FAITH-038 through FAITH-042 — this task registers placeholder components that render a simple container `<div>` with the panel type and title.

---

## Files to Create

### 1. `web/js/layout.js`

```javascript
/**
 * FAITH GoldenLayout initialisation and panel registration.
 *
 * Responsibilities:
 * - Load or define the default layout configuration
 * - Register five component types: Agent, Tool, Input, Approval, Status
 * - Restore saved layout from localStorage (if available)
 * - Persist layout changes to localStorage on every state change
 * - Provide toolbar actions: add panel (+), reset layout
 * - Support: drag, dock, resize, tab-group, detach to floating window
 *
 * FRS Reference: Section 6.4, 6.10
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LAYOUT_STORAGE_KEY = "faith_layout_v1";

/**
 * Component type identifiers. Each maps to a registered GoldenLayout
 * component factory. The actual rendering logic is provided by later
 * tasks (FAITH-038 to FAITH-042); this task registers placeholder
 * factories that mount a simple <div> with the component title.
 */
const COMPONENT_TYPES = Object.freeze({
    AGENT: "agent-panel",
    TOOL: "tool-panel",
    INPUT: "input-panel",
    APPROVAL: "approval-panel",
    STATUS: "status-panel",
});

// ---------------------------------------------------------------------------
// Default layout configuration
// ---------------------------------------------------------------------------

/**
 * Build the default GoldenLayout config.
 *
 * Structure (matches FRS Section 6.4.1):
 *
 *  ┌──────────────────────────────────────────────────────────┐
 *  │  [Project Agent] [Dev] [QA] [Tools ▾]             [+]   │  tab bar
 *  ├───────────────────┬──────────────────────────────────────┤
 *  │                   │                                      │
 *  │  Project Agent    │  Software Developer                  │
 *  │  (agent panel)    │  (agent panel)                       │
 *  │                   │                                      │
 *  │                   ├──────────────────────────────────────┤
 *  │                   │                                      │
 *  │                   │  QA Engineer                         │
 *  │                   │  (agent panel)                       │
 *  │                   │                                      │
 *  ├───────────────────┴──────────────────────────────────────┤
 *  │  Input            │  Approvals        │  System Status   │
 *  │  (input panel)    │  (approval panel) │  (status panel)  │
 *  └───────────────────┴───────────────────┴──────────────────┘
 *
 * The layout uses nested row/column containers:
 *   Root: column
 *     ├── Row (weight 70%) — main workspace
 *     │   ├── Stack — Project Agent (weight 35%)
 *     │   └── Column (weight 65%)
 *     │       ├── Stack — Software Developer (weight 50%)
 *     │       └── Stack — QA Engineer (weight 50%)
 *     └── Row (weight 30%) — bottom bar
 *         ├── Stack — Input (weight 34%)
 *         ├── Stack — Approvals (weight 33%)
 *         └── Stack — Status (weight 33%)
 *
 * @returns {Object} GoldenLayout configuration object.
 */
function buildDefaultLayoutConfig() {
    return {
        root: {
            type: "column",
            content: [
                {
                    type: "row",
                    weight: 70,
                    content: [
                        {
                            type: "stack",
                            weight: 35,
                            content: [
                                {
                                    type: "component",
                                    componentType: COMPONENT_TYPES.AGENT,
                                    title: "Project Agent",
                                    componentState: {
                                        agentId: "project-agent",
                                        displayName: "Project Agent",
                                    },
                                },
                            ],
                        },
                        {
                            type: "column",
                            weight: 65,
                            content: [
                                {
                                    type: "stack",
                                    weight: 50,
                                    content: [
                                        {
                                            type: "component",
                                            componentType: COMPONENT_TYPES.AGENT,
                                            title: "Software Developer",
                                            componentState: {
                                                agentId: "software-developer",
                                                displayName: "Software Developer",
                                            },
                                        },
                                    ],
                                },
                                {
                                    type: "stack",
                                    weight: 50,
                                    content: [
                                        {
                                            type: "component",
                                            componentType: COMPONENT_TYPES.AGENT,
                                            title: "QA Engineer",
                                            componentState: {
                                                agentId: "qa-engineer",
                                                displayName: "QA Engineer",
                                            },
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
                {
                    type: "row",
                    weight: 30,
                    content: [
                        {
                            type: "stack",
                            weight: 34,
                            content: [
                                {
                                    type: "component",
                                    componentType: COMPONENT_TYPES.INPUT,
                                    title: "Input",
                                    componentState: {},
                                },
                            ],
                        },
                        {
                            type: "stack",
                            weight: 33,
                            content: [
                                {
                                    type: "component",
                                    componentType: COMPONENT_TYPES.APPROVAL,
                                    title: "Approvals",
                                    componentState: {},
                                },
                            ],
                        },
                        {
                            type: "stack",
                            weight: 33,
                            content: [
                                {
                                    type: "component",
                                    componentType: COMPONENT_TYPES.STATUS,
                                    title: "System Status",
                                    componentState: {},
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    };
}

// ---------------------------------------------------------------------------
// Layout persistence
// ---------------------------------------------------------------------------

/**
 * Save the current layout state to localStorage.
 *
 * Called on every GoldenLayout stateChanged event so the user's
 * arrangement is always up to date.
 *
 * @param {GoldenLayout} layout - The active GoldenLayout instance.
 */
function saveLayout(layout) {
    try {
        const state = layout.saveLayout();
        localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(state));
    } catch (err) {
        console.warn("[FAITH] Failed to save layout to localStorage:", err);
    }
}

/**
 * Load a previously saved layout from localStorage.
 *
 * @returns {Object|null} The saved layout config, or null if none exists
 *     or the saved data is corrupt.
 */
function loadSavedLayout() {
    try {
        const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        // Basic sanity check — must have a root property
        if (parsed && parsed.root) {
            return parsed;
        }
        console.warn("[FAITH] Saved layout missing root — discarding");
        return null;
    } catch (err) {
        console.warn("[FAITH] Failed to parse saved layout — discarding:", err);
        return null;
    }
}

/**
 * Clear the saved layout from localStorage and reload the page to
 * restore the default layout.
 */
function resetLayout() {
    localStorage.removeItem(LAYOUT_STORAGE_KEY);
    window.location.reload();
}

// ---------------------------------------------------------------------------
// Component registration
// ---------------------------------------------------------------------------

/**
 * Register all five panel component types with GoldenLayout.
 *
 * Each component factory receives a container element and the
 * componentState from the layout config. In this task, factories render
 * a placeholder <div>. FAITH-038 through FAITH-042 replace these with
 * real Vue 3 component mounts.
 *
 * The placeholder pattern ensures the layout system is fully testable
 * before the real panel components exist.
 *
 * @param {GoldenLayout} layout - The GoldenLayout instance.
 */
function registerComponents(layout) {
    // --- Agent Panel (FAITH-038 replaces this placeholder) ---
    layout.registerComponentFactoryFunction(
        COMPONENT_TYPES.AGENT,
        (container, state) => {
            const el = document.createElement("div");
            el.className = "faith-panel faith-panel--agent";
            el.dataset.agentId = state.agentId || "";
            el.textContent = `Agent panel: ${state.displayName || state.agentId || "unknown"}`;
            container.element.appendChild(el);
        }
    );

    // --- Tool Panel (future task replaces this placeholder) ---
    layout.registerComponentFactoryFunction(
        COMPONENT_TYPES.TOOL,
        (container, state) => {
            const el = document.createElement("div");
            el.className = "faith-panel faith-panel--tool";
            el.dataset.toolId = state.toolId || "";
            el.textContent = `Tool panel: ${state.displayName || state.toolId || "unknown"}`;
            container.element.appendChild(el);
        }
    );

    // --- Input Panel (FAITH-041 replaces this placeholder) ---
    layout.registerComponentFactoryFunction(
        COMPONENT_TYPES.INPUT,
        (container, _state) => {
            const el = document.createElement("div");
            el.className = "faith-panel faith-panel--input";
            el.textContent = "Input panel placeholder";
            container.element.appendChild(el);
        }
    );

    // --- Approval Panel (FAITH-039 replaces this placeholder) ---
    layout.registerComponentFactoryFunction(
        COMPONENT_TYPES.APPROVAL,
        (container, _state) => {
            const el = document.createElement("div");
            el.className = "faith-panel faith-panel--approval";
            el.textContent = "Approval panel placeholder";
            container.element.appendChild(el);
        }
    );

    // --- Status Panel (FAITH-040 replaces this placeholder) ---
    layout.registerComponentFactoryFunction(
        COMPONENT_TYPES.STATUS,
        (container, _state) => {
            const el = document.createElement("div");
            el.className = "faith-panel faith-panel--status";
            el.textContent = "Status panel placeholder";
            container.element.appendChild(el);
        }
    );
}

// ---------------------------------------------------------------------------
// Toolbar: Add Panel (+) menu
// ---------------------------------------------------------------------------

/**
 * Panel templates available in the "+" dropdown menu.
 *
 * Each entry defines the componentType, a human-readable label, and
 * a factory for the componentState. Agent and Tool panels prompt the
 * user for an identifier; the other three are singletons.
 *
 * @type {Array<{label: string, componentType: string, buildState: function}>}
 */
const ADD_PANEL_TEMPLATES = [
    {
        label: "Agent Panel",
        componentType: COMPONENT_TYPES.AGENT,
        buildState: () => {
            const agentId = prompt("Enter agent ID (e.g. security-expert):");
            if (!agentId) return null;
            return { agentId, displayName: agentId };
        },
    },
    {
        label: "Tool Panel",
        componentType: COMPONENT_TYPES.TOOL,
        buildState: () => {
            const toolId = prompt("Enter tool ID (e.g. filesystem):");
            if (!toolId) return null;
            return { toolId, displayName: toolId };
        },
    },
    {
        label: "Input Panel",
        componentType: COMPONENT_TYPES.INPUT,
        buildState: () => ({}),
    },
    {
        label: "Approval Panel",
        componentType: COMPONENT_TYPES.APPROVAL,
        buildState: () => ({}),
    },
    {
        label: "Status Panel",
        componentType: COMPONENT_TYPES.STATUS,
        buildState: () => ({}),
    },
];

/**
 * Build the "+" button dropdown and attach it to the toolbar.
 *
 * Clicking the "+" button toggles a dropdown listing all available
 * panel types. Selecting one creates a new panel instance and adds
 * it to the root of the layout.
 *
 * @param {GoldenLayout} layout - The GoldenLayout instance.
 * @param {HTMLElement} toolbarEl - The toolbar container element.
 */
function buildAddPanelButton(layout, toolbarEl) {
    const wrapper = document.createElement("div");
    wrapper.className = "faith-toolbar__add-wrapper";

    const btn = document.createElement("button");
    btn.className = "faith-toolbar__btn faith-toolbar__btn--add";
    btn.textContent = "+";
    btn.title = "Add panel";

    const menu = document.createElement("div");
    menu.className = "faith-toolbar__add-menu";
    menu.style.display = "none";

    ADD_PANEL_TEMPLATES.forEach((tmpl) => {
        const item = document.createElement("button");
        item.className = "faith-toolbar__add-menu-item";
        item.textContent = tmpl.label;
        item.addEventListener("click", () => {
            menu.style.display = "none";
            const state = tmpl.buildState();
            if (state === null) return; // user cancelled prompt
            layout.addComponent(tmpl.componentType, state, tmpl.label);
        });
        menu.appendChild(item);
    });

    btn.addEventListener("click", (e) => {
        e.stopPropagation();
        menu.style.display = menu.style.display === "none" ? "block" : "none";
    });

    // Close menu when clicking elsewhere
    document.addEventListener("click", () => {
        menu.style.display = "none";
    });

    wrapper.appendChild(btn);
    wrapper.appendChild(menu);
    toolbarEl.appendChild(wrapper);
}

/**
 * Build the "Reset Layout" button and attach it to the toolbar.
 *
 * @param {HTMLElement} toolbarEl - The toolbar container element.
 */
function buildResetButton(toolbarEl) {
    const btn = document.createElement("button");
    btn.className = "faith-toolbar__btn faith-toolbar__btn--reset";
    btn.textContent = "Reset Layout";
    btn.title = "Restore default panel layout";
    btn.addEventListener("click", () => {
        if (confirm("Reset to default layout? Your current arrangement will be lost.")) {
            resetLayout();
        }
    });
    toolbarEl.appendChild(btn);
}

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------

/**
 * Initialise the GoldenLayout panel framework.
 *
 * Call this once from index.html after the DOM is ready and the
 * GoldenLayout library has loaded.
 *
 * Steps:
 *  1. Attempt to load a saved layout from localStorage.
 *  2. If no saved layout, use the default layout config.
 *  3. Create the GoldenLayout instance bound to the container element.
 *  4. Register all five component types.
 *  5. Subscribe to stateChanged to persist layout on every change.
 *  6. Build the toolbar (tab bar is managed by GoldenLayout; we add
 *     the "+" and "Reset Layout" buttons).
 *  7. Load the layout into the DOM.
 *
 * @param {HTMLElement} containerEl - The DOM element to mount GoldenLayout into.
 * @param {HTMLElement} toolbarEl - The DOM element for the toolbar.
 * @returns {GoldenLayout} The initialised GoldenLayout instance (exported
 *     so app.js can interact with it).
 */
function initLayout(containerEl, toolbarEl) {
    // 1-2. Resolve layout config
    const savedConfig = loadSavedLayout();
    const layoutConfig = savedConfig || buildDefaultLayoutConfig();

    // 3. Create GoldenLayout instance
    const layout = new goldenLayout.GoldenLayout(containerEl);

    // 4. Register component types
    registerComponents(layout);

    // 5. Persist on state changes (debounced internally by GoldenLayout)
    layout.on("stateChanged", () => {
        saveLayout(layout);
    });

    // 6. Build toolbar buttons
    buildAddPanelButton(layout, toolbarEl);
    buildResetButton(toolbarEl);

    // 7. Load layout
    layout.loadLayout(layoutConfig);

    // GoldenLayout popout support — enables detach-to-floating-window
    // GoldenLayout 2 handles this natively when the header popout
    // button is enabled (which it is by default).

    console.info("[FAITH] GoldenLayout initialised");
    return layout;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Add an agent panel dynamically (e.g. when the PA creates a new agent).
 *
 * @param {GoldenLayout} layout - The active GoldenLayout instance.
 * @param {string} agentId - The agent's unique identifier.
 * @param {string} displayName - Human-readable name for the tab title.
 */
function addAgentPanel(layout, agentId, displayName) {
    layout.addComponent(
        COMPONENT_TYPES.AGENT,
        { agentId, displayName },
        displayName || agentId
    );
}

/**
 * Add a tool panel dynamically (e.g. when a new MCP tool comes online).
 *
 * @param {GoldenLayout} layout - The active GoldenLayout instance.
 * @param {string} toolId - The tool's unique identifier.
 * @param {string} displayName - Human-readable name for the tab title.
 */
function addToolPanel(layout, toolId, displayName) {
    layout.addComponent(
        COMPONENT_TYPES.TOOL,
        { toolId, displayName },
        displayName || toolId
    );
}

// Export for use by app.js and index.html
window.faithLayout = {
    COMPONENT_TYPES,
    initLayout,
    addAgentPanel,
    addToolPanel,
    resetLayout,
    buildDefaultLayoutConfig,
};
```

### 2. Modifications to `templates/index.html`

The Jinja2 template (created by FAITH-036) must be updated to load GoldenLayout and initialise the layout. Add the following to the `<head>` and `<body>`:

```html
<!-- In <head> — after existing meta/title tags -->

<!-- GoldenLayout 2 — CDN with vendored fallback -->
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/golden-layout@2.6.0/dist/css/goldenlayout-base.css"
      onerror="this.href='/static/js/vendor/goldenlayout-base.css'">
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/golden-layout@2.6.0/dist/css/themes/goldenlayout-dark-theme.css"
      onerror="this.href='/static/js/vendor/goldenlayout-dark-theme.css'">
<script src="https://cdn.jsdelivr.net/npm/golden-layout@2.6.0/dist/goldenlayout.umd.js"
        onerror="loadVendoredFallback('goldenlayout')"></script>

<!-- FAITH layout module -->
<script src="/static/js/layout.js"></script>

<!-- Custom theme overrides (minimal in FAITH-037; expanded by FAITH-042) -->
<link rel="stylesheet" href="/static/css/theme.css">
```

```html
<!-- In <body> — the layout mount points -->

<div id="faith-toolbar" class="faith-toolbar">
    <!-- Tab bar is rendered by GoldenLayout header; + and Reset added by layout.js -->
</div>
<div id="faith-layout" class="faith-layout-container">
    <!-- GoldenLayout mounts here -->
</div>

<script>
    // Vendored fallback loader (used by onerror on CDN <script> tags)
    function loadVendoredFallback(lib) {
        console.warn(`[FAITH] CDN failed for ${lib} — loading vendored copy`);
        const s = document.createElement("script");
        s.src = `/static/js/vendor/${lib}.umd.js`;
        document.head.appendChild(s);
    }

    // Initialise layout after DOM is ready
    document.addEventListener("DOMContentLoaded", () => {
        const container = document.getElementById("faith-layout");
        const toolbar = document.getElementById("faith-toolbar");
        window.faithLayoutInstance = window.faithLayout.initLayout(container, toolbar);
    });
</script>
```

### 3. `web/css/theme.css` (GoldenLayout overrides — minimal scope)

```css
/**
 * FAITH GoldenLayout chrome overrides.
 *
 * This file provides the minimal GoldenLayout styling needed for
 * FAITH-037. The full terminal dark theme is delivered by FAITH-042.
 *
 * FRS Reference: Section 6.6
 */

/* ------------------------------------------------------------------ */
/* Layout container                                                    */
/* ------------------------------------------------------------------ */

.faith-layout-container {
    width: 100%;
    height: calc(100vh - 40px); /* subtract toolbar height */
    background: #0d1117;
}

/* ------------------------------------------------------------------ */
/* Toolbar                                                             */
/* ------------------------------------------------------------------ */

.faith-toolbar {
    display: flex;
    align-items: center;
    height: 40px;
    padding: 0 8px;
    background: #161b22;
    border-bottom: 1px solid #30363d;
    gap: 8px;
}

.faith-toolbar__btn {
    background: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 4px 12px;
    cursor: pointer;
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
}

.faith-toolbar__btn:hover {
    background: #30363d;
}

.faith-toolbar__btn--add {
    font-size: 18px;
    font-weight: bold;
    padding: 2px 10px;
    line-height: 1;
}

.faith-toolbar__add-wrapper {
    position: relative;
}

.faith-toolbar__add-menu {
    position: absolute;
    top: 100%;
    left: 0;
    z-index: 1000;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 4px;
    margin-top: 4px;
    min-width: 160px;
}

.faith-toolbar__add-menu-item {
    display: block;
    width: 100%;
    text-align: left;
    background: none;
    color: #c9d1d9;
    border: none;
    padding: 8px 12px;
    cursor: pointer;
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
}

.faith-toolbar__add-menu-item:hover {
    background: #21262d;
}

/* ------------------------------------------------------------------ */
/* GoldenLayout chrome overrides — minimal dark theme                  */
/* ------------------------------------------------------------------ */

.lm_goldenlayout {
    background: #0d1117;
}

.lm_header {
    background: #161b22 !important;
    border-bottom: 1px solid #30363d;
}

.lm_header .lm_tab {
    background: #21262d;
    color: #8b949e;
    border: 1px solid #30363d;
    font-family: "JetBrains Mono", monospace;
    font-size: 12px;
    margin-right: 2px;
}

.lm_header .lm_tab.lm_active {
    background: #0d1117;
    color: #c9d1d9;
    border-bottom-color: #0d1117;
}

.lm_content {
    background: #0d1117;
    border: none;
}

.lm_splitter {
    background: #30363d;
}

/* ------------------------------------------------------------------ */
/* Placeholder panel styling (replaced by FAITH-038 to FAITH-042)     */
/* ------------------------------------------------------------------ */

.faith-panel {
    padding: 16px;
    color: #8b949e;
    font-family: "JetBrains Mono", monospace;
    font-size: 14px;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
}
```

### 4. `web/js/vendor/` — Vendored fallback

Create the vendor directory and include a `README` noting that vendored copies of the following files should be placed here for offline deployment:

- `goldenlayout.umd.js` (from `golden-layout@2.6.0`)
- `goldenlayout-base.css`
- `goldenlayout-dark-theme.css`

The vendored files themselves are not committed to the repo by default — the first-run wizard (FAITH-049) offers to download and vendor them. For development, CDN loading is the default.

### 5. `tests/test_layout.html`

A standalone HTML test harness that loads GoldenLayout from CDN, includes `layout.js`, and renders the default layout in a browser. Used for manual visual testing during development.

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FAITH Layout Test Harness</title>
    <link rel="stylesheet"
          href="https://cdn.jsdelivr.net/npm/golden-layout@2.6.0/dist/css/goldenlayout-base.css">
    <link rel="stylesheet"
          href="https://cdn.jsdelivr.net/npm/golden-layout@2.6.0/dist/css/themes/goldenlayout-dark-theme.css">
    <link rel="stylesheet" href="../static/css/theme.css">
    <script src="https://cdn.jsdelivr.net/npm/golden-layout@2.6.0/dist/goldenlayout.umd.js"></script>
    <script src="../static/js/layout.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { height: 100%; background: #0d1117; }
    </style>
</head>
<body>
    <div id="faith-toolbar" class="faith-toolbar"></div>
    <div id="faith-layout" class="faith-layout-container"></div>
    <script>
        document.addEventListener("DOMContentLoaded", () => {
            const container = document.getElementById("faith-layout");
            const toolbar = document.getElementById("faith-toolbar");
            window.faithLayoutInstance = window.faithLayout.initLayout(container, toolbar);
        });
    </script>
</body>
</html>
```

---

## Integration Points

This task is the foundation that all other Web UI panel tasks build upon:

```
FAITH-037 (this task)
  ├── FAITH-038 — Agent Panel Component
  │   Replaces the agent-panel placeholder factory with a Vue 3 + xterm.js
  │   component. Uses COMPONENT_TYPES.AGENT and the agentId/displayName
  │   from componentState.
  │
  ├── FAITH-039 — Approval Panel Component
  │   Replaces the approval-panel placeholder. Connects to /ws/approvals.
  │
  ├── FAITH-040 — Status Bar & System Health Panel
  │   Replaces the status-panel placeholder. Connects to /ws/status.
  │
  ├── FAITH-041 — Input Panel & File Upload
  │   Replaces the input-panel placeholder. Posts to /input and /upload.
  │
  ├── FAITH-042 — Terminal Dark Theme CSS
  │   Expands theme.css with full terminal styling, font loading,
  │   per-agent colour coding, and status badge styles.
  │
  ├── FAITH-043 — Project Switcher UI
  │   Adds a project dropdown to the toolbar element.
  │
  └── FAITH-044 — Web UI Log Views
      Registers additional panel component types for log viewers.
```

```javascript
// How FAITH-038 replaces the agent placeholder (illustrative):
//
// In app.js, after layout.js has run:
//
// layout.registerComponentFactoryFunction(
//     faithLayout.COMPONENT_TYPES.AGENT,
//     (container, state) => {
//         // Mount Vue 3 component with xterm.js
//         const app = Vue.createApp(AgentPanelComponent, {
//             agentId: state.agentId,
//             displayName: state.displayName,
//         });
//         app.mount(container.element);
//     }
// );
//
// Note: GoldenLayout 2 allows re-registration of component factories,
// so later tasks can override the placeholders registered here.
```

```javascript
// How the PA dynamically adds an agent panel when a new agent is created:
//
// Triggered by a WebSocket status message from /ws/status:
// {"type": "agent_created", "agent_id": "security-expert", "display_name": "Security Expert"}
//
// Handler in app.js:
// faithLayout.addAgentPanel(
//     window.faithLayoutInstance,
//     "security-expert",
//     "Security Expert"
// );
```

---

## Acceptance Criteria

1. GoldenLayout loads successfully from CDN. If CDN is unavailable and vendored files exist in `static/js/vendor/`, the vendored copies load as a fallback.
2. Five component types are registered: `agent-panel`, `tool-panel`, `input-panel`, `approval-panel`, `status-panel`. Each renders a visible placeholder `<div>` with the correct CSS class and identifying text.
3. The default layout matches the FRS Section 6.4.1 structure: Project Agent on the left, Software Developer and QA Engineer stacked on the right, Input/Approvals/Status in a bottom row.
4. On first load (no `localStorage` entry), the default layout renders. On subsequent loads, the saved layout restores from `localStorage` under key `faith_layout_v1`.
5. Every layout change (drag, resize, tab-group, close) triggers a save to `localStorage`. Refreshing the page restores the exact arrangement.
6. The `+` toolbar button opens a dropdown menu listing all five panel types. Selecting "Agent Panel" or "Tool Panel" prompts for an ID. The new panel appears in the layout.
7. The "Reset Layout" button prompts for confirmation, clears `localStorage`, and reloads the page to restore the default layout.
8. Panels can be dragged by their header to a new position; other panels reflow automatically.
9. Dropping a panel onto another panel's header creates a tab group.
10. Panel borders can be dragged to resize adjacent panels.
11. GoldenLayout's popout button (detach to floating window) works — clicking it opens the panel in a new browser window.
12. The toolbar, GoldenLayout chrome, and placeholder panels render with the dark theme colours from `theme.css` (background `#0d1117`, borders `#30363d`, text `#c9d1d9`).
13. `window.faithLayout` is exposed as a global object with `initLayout`, `addAgentPanel`, `addToolPanel`, `resetLayout`, `buildDefaultLayoutConfig`, and `COMPONENT_TYPES` — enabling downstream tasks and `app.js` to interact with the layout.
14. The test harness (`tests/test_layout.html`) loads and renders the full default layout when opened in a browser.

---

## Notes for Implementer

- **GoldenLayout version**: Use GoldenLayout 2.x (the `golden-layout` npm package, version 2.6.0 or latest 2.x). GoldenLayout 2 is a complete TypeScript rewrite of the original and has a different API from 1.x. The CDN UMD build exposes `goldenLayout.GoldenLayout` as the constructor. Consult the GoldenLayout 2 documentation at https://golden-layout.com/ for the current API surface.
- **Popout (detach to floating window)**: GoldenLayout 2 supports popouts natively. The popout button appears in each stack header by default. For popouts to work, the browser must allow popups from the FAITH origin. No additional code is needed beyond the standard GoldenLayout initialisation.
- **CDN fallback strategy**: The `onerror` handler on CDN `<script>` and `<link>` tags loads vendored copies from `/static/js/vendor/`. This is a simple degradation path. The `onerror` on `<link>` tags works in most modern browsers but is not universally reliable; FAITH-042 may refine this with a JS-based CSS loader if needed.
- **No npm, no build step**: All JavaScript is plain ES2020 loaded via `<script>` tags. Do not use ES module `import`/`export` syntax — use `window.faithLayout` for cross-file communication. The FRS explicitly prohibits any Node.js build pipeline.
- **Tab bar**: GoldenLayout 2 renders tab headers automatically for stacked components. The FRS "tab bar" at the top of the layout is the row of GoldenLayout stack tabs, not a custom element. The toolbar (`#faith-toolbar`) sits above the GoldenLayout container and holds only the `+` and `Reset Layout` buttons (plus the project switcher added by FAITH-043).
- **Component factory re-registration**: GoldenLayout 2 allows calling `registerComponentFactoryFunction` with the same component type name to replace a previously registered factory. FAITH-038 through FAITH-041 rely on this to replace placeholder factories with real Vue 3 components without modifying `layout.js`.
- **State shape contract**: The `componentState` objects passed to component factories are the contract between `layout.js` and the panel components. Agent panels receive `{ agentId: string, displayName: string }`. Tool panels receive `{ toolId: string, displayName: string }`. Input, Approval, and Status panels receive `{}`. Do not change these shapes without coordinating with FAITH-038 through FAITH-041.
- **localStorage quota**: GoldenLayout state serialisation can produce moderately large JSON strings (10-50 KB typical). This is well within the 5 MB localStorage quota. No compression is needed.
- **FAITH-036 dependency**: This task assumes FAITH-036 has created the FastAPI server, `index.html` template, and static file serving. If `index.html` does not yet exist, create a minimal shell sufficient to test the layout — but coordinate with the FAITH-036 implementer to avoid conflicts.

