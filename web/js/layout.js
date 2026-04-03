/**
 * Description:
 *   Initialise the FAITH GoldenLayout workspace and expose a small public API
 *   for later panel tasks.
 *
 * Requirements:
 *   - Register the baseline panel placeholder component types required by FAITH-037.
 *   - Restore and persist layout state using localStorage under the canonical FAITH key.
 *   - Support toolbar actions for adding panels and resetting the layout.
 *   - Stay compatible with the no-build browser runtime used by the Web UI.
 */

(function initialiseFaithLayoutApi(globalScope) {
  /**
   * Description:
   *   Store the localStorage namespace for persisted layout state.
   *
   * Requirements:
   *   - Keep the key stable so the browser restores layouts across reloads.
   */
  const LAYOUT_STORAGE_KEY = "faith_layout_v1";

  /**
   * Description:
   *   Define the baseline component type identifiers used by the workspace.
   *
   * Requirements:
   *   - Expose stable names so later tasks can replace the placeholder factories.
   */
  const COMPONENT_TYPES = Object.freeze({
    AGENT: "agent-panel",
    TOOL: "tool-panel",
    INPUT: "input-panel",
    APPROVAL: "approval-panel",
    STATUS: "status-panel",
  });

  /**
   * Description:
   *   Return the GoldenLayout constructor from the browser global scope.
   *
   * Requirements:
   *   - Support the CDN UMD bundle used by FAITH.
   *   - Return null when the library is not available yet.
   *
   * @returns {Function|null} GoldenLayout constructor or null when unavailable.
   */
  function getGoldenLayoutConstructor() {
    if (globalScope.goldenLayout && typeof globalScope.goldenLayout.GoldenLayout === "function") {
      return globalScope.goldenLayout.GoldenLayout;
    }
    if (typeof globalScope.GoldenLayout === "function") {
      return globalScope.GoldenLayout;
    }
    return null;
  }

  /**
   * Description:
   *   Build the default workspace layout described by the FRS.
   *
   * Requirements:
   *   - Place Project Agent on the left.
   *   - Stack Software Developer and QA Engineer on the right.
   *   - Render Input, Approvals, and System Status in the bottom row.
   *
   * @returns {object} GoldenLayout configuration for the default workspace.
   */
  function buildDefaultLayoutConfig() {
    return {
      root: {
        type: "column",
        content: [
          {
            type: "row",
            size: 70,
            content: [
              {
                type: "stack",
                size: 36,
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
                size: 64,
                content: [
                  {
                    type: "stack",
                    size: 50,
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
                    size: 50,
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
            size: 30,
            content: [
              {
                type: "stack",
                size: 34,
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
                size: 33,
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
                size: 33,
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

  /**
   * Description:
   *   Return the best available DOM element for a GoldenLayout container.
   *
   * Requirements:
   *   - Support the plain HTMLElement shape used by GoldenLayout 2.
   *   - Gracefully handle array-like wrappers used by some builds.
   *
   * @param {object} container: GoldenLayout component container.
   * @returns {HTMLElement} DOM element that should receive rendered content.
   */
  function resolveContainerElement(container) {
    if (container && container.element instanceof HTMLElement) {
      return container.element;
    }
    if (container && Array.isArray(container.element) && container.element[0] instanceof HTMLElement) {
      return container.element[0];
    }
    throw new Error("Unsupported GoldenLayout container element shape.");
  }

  /**
   * Description:
   *   Create one placeholder panel element for the current task.
   *
   * Requirements:
   *   - Render visible identifying text for the panel type and title.
   *   - Render a status output pre block in the placeholder status panel.
   *
   * @param {string} variant: Panel variant name.
   * @param {string} title: Human-readable panel title.
   * @returns {HTMLElement} Placeholder panel element.
   */
  function buildPlaceholderPanel(variant, title) {
    const wrapper = document.createElement("section");
    wrapper.className = `faith-panel faith-panel--${variant}`;

    const heading = document.createElement("h2");
    heading.className = "faith-panel__title";
    heading.textContent = title;
    wrapper.appendChild(heading);

    if (variant === "status") {
      const output = document.createElement("pre");
      output.id = "status-output";
      output.textContent = "Waiting for /api/status...";
      wrapper.appendChild(output);
      return wrapper;
    }

    const text = document.createElement("p");
    text.className = "faith-panel__placeholder";
    text.textContent = `${title} placeholder`;
    wrapper.appendChild(text);
    return wrapper;
  }

  /**
   * Description:
   *   Register the baseline placeholder component factories with GoldenLayout.
   *
   * Requirements:
   *   - Register the five panel types needed by FAITH-037.
   *   - Keep the component-state shape stable for later tasks.
   *
   * @param {object} layout: Active GoldenLayout instance.
   */
  function registerComponents(layout) {
    layout.registerComponentFactoryFunction(COMPONENT_TYPES.AGENT, function mountAgentPanel(container, state) {
      const target = resolveContainerElement(container);
      target.replaceChildren(buildPlaceholderPanel("agent", state.displayName || state.agentId || "Agent"));
    });

    layout.registerComponentFactoryFunction(COMPONENT_TYPES.TOOL, function mountToolPanel(container, state) {
      const target = resolveContainerElement(container);
      target.replaceChildren(buildPlaceholderPanel("tool", state.displayName || state.toolId || "Tool"));
    });

    layout.registerComponentFactoryFunction(COMPONENT_TYPES.INPUT, function mountInputPanel(container) {
      const target = resolveContainerElement(container);
      target.replaceChildren(buildPlaceholderPanel("input", "Input"));
    });

    layout.registerComponentFactoryFunction(COMPONENT_TYPES.APPROVAL, function mountApprovalPanel(container) {
      const target = resolveContainerElement(container);
      target.replaceChildren(buildPlaceholderPanel("approval", "Approvals"));
    });

    layout.registerComponentFactoryFunction(COMPONENT_TYPES.STATUS, function mountStatusPanel(container) {
      const target = resolveContainerElement(container);
      target.replaceChildren(buildPlaceholderPanel("status", "System Status"));
    });
  }

  /**
   * Description:
   *   Save the current layout state to localStorage.
   *
   * Requirements:
   *   - Ignore persistence failures quietly so the workspace stays usable.
   *
   * @param {object} layout: Active GoldenLayout instance.
   */
  function saveLayout(layout) {
    try {
      globalScope.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout.saveLayout()));
    } catch (error) {
      console.warn("[FAITH] Failed to save layout state.", error);
    }
  }

  /**
   * Description:
   *   Load a previously saved layout from localStorage.
   *
   * Requirements:
   *   - Return null when no saved layout exists.
   *   - Discard malformed saved state safely.
   *
   * @returns {object|null} Saved layout config or null.
   */
  function loadSavedLayout() {
    try {
      const raw = globalScope.localStorage.getItem(LAYOUT_STORAGE_KEY);
      if (!raw) {
        return null;
      }
      const parsed = JSON.parse(raw);
      return parsed && parsed.root ? parsed : null;
    } catch (error) {
      console.warn("[FAITH] Failed to parse saved layout state.", error);
      return null;
    }
  }

  /**
   * Description:
   *   Clear the saved layout and reload the browser page.
   *
   * Requirements:
   *   - Remove the canonical FAITH layout key before reloading.
   */
  function resetLayout() {
    globalScope.localStorage.removeItem(LAYOUT_STORAGE_KEY);
    globalScope.location.reload();
  }

  /**
   * Description:
   *   Add one component to the current layout root.
   *
   * Requirements:
   *   - Use GoldenLayout's public addItem API when available.
   *   - Fall back to root-item insertion when needed.
   *
   * @param {object} layout: Active GoldenLayout instance.
   * @param {object} itemConfig: Component configuration to add.
   */
  function addComponentToLayout(layout, itemConfig) {
    if (typeof layout.addItem === "function") {
      layout.addItem(itemConfig);
      return;
    }
    if (layout.rootItem && typeof layout.rootItem.addChild === "function") {
      layout.rootItem.addChild(itemConfig);
      return;
    }
    throw new Error("GoldenLayout instance does not support dynamic item insertion.");
  }

  /**
   * Description:
   *   Prompt the user to create a new panel from the toolbar add menu.
   *
   * Requirements:
   *   - Prompt for IDs on dynamic agent and tool panels.
   *   - Add all supported placeholder panel types.
   *
   * @param {object} layout: Active GoldenLayout instance.
   * @param {string} panelType: Requested component type.
   */
  function promptAndAddPanel(layout, panelType) {
    if (panelType === COMPONENT_TYPES.AGENT) {
      const agentId = globalScope.prompt("Enter agent ID", "new-agent");
      if (!agentId) {
        return;
      }
      addComponentToLayout(layout, {
        type: "component",
        componentType: COMPONENT_TYPES.AGENT,
        title: agentId,
        componentState: { agentId: agentId, displayName: agentId },
      });
      return;
    }

    if (panelType === COMPONENT_TYPES.TOOL) {
      const toolId = globalScope.prompt("Enter tool ID", "tool-id");
      if (!toolId) {
        return;
      }
      addComponentToLayout(layout, {
        type: "component",
        componentType: COMPONENT_TYPES.TOOL,
        title: toolId,
        componentState: { toolId: toolId, displayName: toolId },
      });
      return;
    }

    const titles = {
      [COMPONENT_TYPES.INPUT]: "Input",
      [COMPONENT_TYPES.APPROVAL]: "Approvals",
      [COMPONENT_TYPES.STATUS]: "System Status",
    };
    addComponentToLayout(layout, {
      type: "component",
      componentType: panelType,
      title: titles[panelType] || "Panel",
      componentState: {},
    });
  }

  /**
   * Description:
   *   Build the toolbar controls required by the task.
   *
   * Requirements:
   *   - Render the add-panel dropdown trigger.
   *   - Render the reset-layout button.
   *
   * @param {object} layout: Active GoldenLayout instance.
   * @param {HTMLElement} toolbarEl: Toolbar container element.
   */
  function buildToolbar(layout, toolbarEl) {
    const controls = document.createElement("div");
    controls.className = "faith-toolbar__controls";

    const addWrapper = document.createElement("div");
    addWrapper.className = "faith-toolbar__add-wrapper";

    const addButton = document.createElement("button");
    addButton.className = "faith-toolbar__button faith-toolbar__button--add";
    addButton.type = "button";
    addButton.textContent = "+";
    addButton.setAttribute("aria-label", "Add panel");

    const menu = document.createElement("div");
    menu.className = "faith-toolbar__menu";
    menu.hidden = true;

    const menuItems = [
      { label: "Agent Panel", type: COMPONENT_TYPES.AGENT },
      { label: "Tool Panel", type: COMPONENT_TYPES.TOOL },
      { label: "Input Panel", type: COMPONENT_TYPES.INPUT },
      { label: "Approval Panel", type: COMPONENT_TYPES.APPROVAL },
      { label: "Status Panel", type: COMPONENT_TYPES.STATUS },
    ];

    menuItems.forEach(function appendMenuItem(item) {
      const button = document.createElement("button");
      button.className = "faith-toolbar__menu-item";
      button.type = "button";
      button.textContent = item.label;
      button.addEventListener("click", function onAddPanelClick() {
        menu.hidden = true;
        promptAndAddPanel(layout, item.type);
      });
      menu.appendChild(button);
    });

    addButton.addEventListener("click", function onToggleMenu(event) {
      event.stopPropagation();
      menu.hidden = !menu.hidden;
    });

    document.addEventListener("click", function onDocumentClick() {
      menu.hidden = true;
    });

    addWrapper.appendChild(addButton);
    addWrapper.appendChild(menu);

    const resetButton = document.createElement("button");
    resetButton.className = "faith-toolbar__button";
    resetButton.type = "button";
    resetButton.textContent = "Reset Layout";
    resetButton.addEventListener("click", function onResetLayoutClick() {
      if (globalScope.confirm("Reset to the default FAITH layout?")) {
        resetLayout();
      }
    });

    controls.appendChild(addWrapper);
    controls.appendChild(resetButton);
    toolbarEl.appendChild(controls);
  }

  /**
   * Description:
   *   Initialise the FAITH GoldenLayout workspace in the browser.
   *
   * Requirements:
   *   - Load the saved layout when present, otherwise render the default layout.
   *   - Persist future layout changes.
   *   - Build the toolbar controls required by the task.
   *
   * @param {HTMLElement} containerEl: Layout mount element.
   * @param {HTMLElement} toolbarEl: Toolbar mount element.
   * @returns {object|null} Active GoldenLayout instance, or null when unavailable.
   */
  function initLayout(containerEl, toolbarEl) {
    const GoldenLayoutConstructor = getGoldenLayoutConstructor();
    if (!GoldenLayoutConstructor || !(containerEl instanceof HTMLElement)) {
      return null;
    }

    const layout = new GoldenLayoutConstructor(containerEl);
    registerComponents(layout);
    if (typeof layout.on === "function") {
      layout.on("stateChanged", function onStateChanged() {
        saveLayout(layout);
      });
    }

    buildToolbar(layout, toolbarEl);
    layout.loadLayout(loadSavedLayout() || buildDefaultLayoutConfig());
    return layout;
  }

  /**
   * Description:
   *   Add one agent panel to the current workspace.
   *
   * Requirements:
   *   - Preserve the stable component-state shape for agent panels.
   *
   * @param {object} layout: Active GoldenLayout instance.
   * @param {string} agentId: Agent identifier.
   * @param {string} displayName: Human-readable agent name.
   */
  function addAgentPanel(layout, agentId, displayName) {
    addComponentToLayout(layout, {
      type: "component",
      componentType: COMPONENT_TYPES.AGENT,
      title: displayName || agentId,
      componentState: {
        agentId: agentId,
        displayName: displayName || agentId,
      },
    });
  }

  /**
   * Description:
   *   Add one tool panel to the current workspace.
   *
   * Requirements:
   *   - Preserve the stable component-state shape for tool panels.
   *
   * @param {object} layout: Active GoldenLayout instance.
   * @param {string} toolId: Tool identifier.
   * @param {string} displayName: Human-readable tool name.
   */
  function addToolPanel(layout, toolId, displayName) {
    addComponentToLayout(layout, {
      type: "component",
      componentType: COMPONENT_TYPES.TOOL,
      title: displayName || toolId,
      componentState: {
        toolId: toolId,
        displayName: displayName || toolId,
      },
    });
  }

  globalScope.faithLayout = {
    COMPONENT_TYPES: COMPONENT_TYPES,
    LAYOUT_STORAGE_KEY: LAYOUT_STORAGE_KEY,
    buildDefaultLayoutConfig: buildDefaultLayoutConfig,
    initLayout: initLayout,
    addAgentPanel: addAgentPanel,
    addToolPanel: addToolPanel,
    resetLayout: resetLayout,
  };
})(window);
