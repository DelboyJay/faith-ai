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
  let isResettingLayout = false;

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
    DOCKER_RUNTIME: "docker-runtime-panel",
  });

  /**
   * Description:
   *   Build a stable logical identity key for one panel instance.
   *
   * Requirements:
   *   - Keep singleton panels keyed only by component type.
   *   - Keep agent and tool panels keyed by runtime identity.
   *
   * @param {string} componentType: Panel component type.
   * @param {object} componentState: Panel component state.
   * @returns {string} Stable panel identity key.
   */
  function buildPanelIdentityKey(componentType, componentState) {
    if (
      componentType === COMPONENT_TYPES.INPUT ||
      componentType === COMPONENT_TYPES.APPROVAL ||
      componentType === COMPONENT_TYPES.STATUS
    ) {
      return componentType;
    }
    if (componentType === COMPONENT_TYPES.AGENT) {
      return `${componentType}:${componentState && componentState.agentId ? componentState.agentId : ""}`;
    }
    if (componentType === COMPONENT_TYPES.TOOL) {
      return `${componentType}:${componentState && componentState.toolId ? componentState.toolId : ""}`;
    }
    return componentType;
  }

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
   *   - Render the minimal first-load workspace on a fresh browser load.
   *   - Keep Project Agent, Input, Approvals, and System Status present by default.
   *   - Avoid pre-creating specialist agent panels before the PA decides they are needed.
   *
   * @returns {object} GoldenLayout configuration for the default workspace.
   */
  function buildDefaultLayoutConfig() {
    return {
      root: {
        type: "column",
        content: [
          {
            type: "component",
            componentType: COMPONENT_TYPES.AGENT,
            title: "Project Agent",
            size: 58,
            componentState: {
              agentId: "project-agent",
              displayName: "Project Agent",
            },
          },
          {
            type: "row",
            size: 42,
            content: [
              {
                type: "component",
                componentType: COMPONENT_TYPES.INPUT,
                size: 34,
                title: "Input",
                componentState: {},
              },
              {
                type: "component",
                componentType: COMPONENT_TYPES.APPROVAL,
                size: 33,
                title: "Approvals",
                componentState: {},
              },
              {
                type: "component",
                componentType: COMPONENT_TYPES.STATUS,
                size: 33,
                title: "System Status",
                componentState: {},
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

    if (variant === "status") {
      const output = document.createElement("pre");
      output.id = "status-output";
      output.textContent = "Waiting for /api/status...";
      wrapper.appendChild(output);
      return wrapper;
    }

    const text = document.createElement("p");
    text.className = "faith-panel__placeholder";
    text.textContent = `${title} panel placeholder`;
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
      target.dataset.faithPanelKey = buildPanelIdentityKey(COMPONENT_TYPES.AGENT, state || {});
      if (typeof target.__faithCleanup === "function") {
        target.__faithCleanup();
        target.__faithCleanup = null;
      }
      if (globalScope.faithAgentPanel && typeof globalScope.faithAgentPanel.mountPanel === "function") {
        target.__faithCleanup = globalScope.faithAgentPanel.mountPanel(target, state || {});
        return;
      }
      target.replaceChildren(buildPlaceholderPanel("agent", state.displayName || state.agentId || "Agent"));
    });

    layout.registerComponentFactoryFunction(COMPONENT_TYPES.TOOL, function mountToolPanel(container, state) {
      const target = resolveContainerElement(container);
      target.dataset.faithPanelKey = buildPanelIdentityKey(COMPONENT_TYPES.TOOL, state || {});
      target.replaceChildren(buildPlaceholderPanel("tool", state.displayName || state.toolId || "Tool"));
    });

    layout.registerComponentFactoryFunction(COMPONENT_TYPES.INPUT, function mountInputPanel(container) {
      const target = resolveContainerElement(container);
      target.dataset.faithPanelKey = buildPanelIdentityKey(COMPONENT_TYPES.INPUT, {});
      if (typeof target.__faithCleanup === "function") {
        target.__faithCleanup();
        target.__faithCleanup = null;
      }
      if (globalScope.faithInputPanel && typeof globalScope.faithInputPanel.mountPanel === "function") {
        target.__faithCleanup = globalScope.faithInputPanel.mountPanel(target);
        return;
      }
      target.replaceChildren(buildPlaceholderPanel("input", "Input"));
    });

    layout.registerComponentFactoryFunction(COMPONENT_TYPES.APPROVAL, function mountApprovalPanel(container) {
      const target = resolveContainerElement(container);
      target.dataset.faithPanelKey = buildPanelIdentityKey(COMPONENT_TYPES.APPROVAL, {});
      if (typeof target.__faithCleanup === "function") {
        target.__faithCleanup();
        target.__faithCleanup = null;
      }
      if (globalScope.faithApprovalPanel && typeof globalScope.faithApprovalPanel.mountPanel === "function") {
        const panel = globalScope.faithApprovalPanel.mountPanel(target);
        if (panel && typeof panel.destroy === "function") {
          target.__faithCleanup = panel.destroy;
        }
        return;
      }
      target.replaceChildren(buildPlaceholderPanel("approval", "Approvals"));
    });

    layout.registerComponentFactoryFunction(COMPONENT_TYPES.STATUS, function mountStatusPanel(container) {
      const target = resolveContainerElement(container);
      target.dataset.faithPanelKey = buildPanelIdentityKey(COMPONENT_TYPES.STATUS, {});
      target.replaceChildren(buildPlaceholderPanel("status", "System Status"));
    });

    layout.registerComponentFactoryFunction(COMPONENT_TYPES.DOCKER_RUNTIME, function mountDockerRuntimePanel(container) {
      const target = resolveContainerElement(container);
      target.dataset.faithPanelKey = buildPanelIdentityKey(COMPONENT_TYPES.DOCKER_RUNTIME, {});
      if (
        globalScope.faithDockerRuntimePanel &&
        typeof globalScope.faithDockerRuntimePanel.mountPanel === "function"
      ) {
        void globalScope.faithDockerRuntimePanel.mountPanel(target);
        return;
      }
      target.replaceChildren(buildPlaceholderPanel("tool", "Docker Runtime"));
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
    if (isResettingLayout) {
      return;
    }
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
   *   Clear the saved layout and restore the default workspace immediately.
   *
   * Requirements:
   *   - Remove the canonical FAITH layout key before rebuilding the workspace.
   *   - Avoid a reload-time race where GoldenLayout can re-save the stale layout.
   *   - Fall back to a browser reload only when the live layout cannot be reset.
   *
   * @param {object} layout: Active GoldenLayout instance.
   */
  function resetLayout(layout) {
    isResettingLayout = true;
    globalScope.localStorage.removeItem(LAYOUT_STORAGE_KEY);
    try {
      if (layout && typeof layout.loadLayout === "function") {
        layout.loadLayout(buildDefaultLayoutConfig());
        return;
      }
      globalScope.location.reload();
    } finally {
      isResettingLayout = false;
    }
  }

  /**
   * Description:
   *   Decide whether two panel definitions represent the same logical panel.
   *
   * Requirements:
   *   - Treat Input, Approvals, and System Status as singleton panels.
   *   - Compare agent and tool panels by runtime identity.
   *
   * @param {string} componentType: Panel component type being compared.
   * @param {object} existingState: Existing panel component state.
   * @param {object} nextState: Requested panel component state.
   * @returns {boolean} True when the two panel definitions should dedupe.
   */
  function isSamePanelIdentity(componentType, existingState, nextState) {
    if (
      componentType === COMPONENT_TYPES.INPUT ||
      componentType === COMPONENT_TYPES.APPROVAL ||
      componentType === COMPONENT_TYPES.STATUS
    ) {
      return true;
    }
    if (componentType === COMPONENT_TYPES.AGENT) {
      return Boolean(existingState && nextState && existingState.agentId === nextState.agentId);
    }
    if (componentType === COMPONENT_TYPES.TOOL) {
      return Boolean(existingState && nextState && existingState.toolId === nextState.toolId);
    }
    return false;
  }

  /**
   * Description:
   *   Walk a saved layout tree and find a matching panel definition.
   *
   * Requirements:
   *   - Traverse nested row, column, and stack containers safely.
   *   - Return the first matching component definition when present.
   *
   * @param {object} item: Layout node to inspect.
   * @param {string} componentType: Requested component type.
   * @param {object} componentState: Requested component state.
   * @returns {object|null} Matching component item or null when absent.
   */
  function findPanelInLayout(item, componentType, componentState) {
    if (!item || typeof item !== "object") {
      return null;
    }
    if (item.type === "component" && item.componentType === componentType) {
      if (isSamePanelIdentity(componentType, item.componentState || {}, componentState || {})) {
        return item;
      }
    }
    if (Array.isArray(item.content)) {
      for (const child of item.content) {
        const match = findPanelInLayout(child, componentType, componentState);
        if (match) {
          return match;
        }
      }
    }
    return null;
  }

  /**
   * Description:
   *   Return whether the current workspace already contains the requested panel.
   *
   * Requirements:
   *   - Use the layout's serialised state so singleton and identity-based dedupe stay stable.
   *   - Return false safely when layout state is unavailable.
   *
   * @param {object} layout: Active GoldenLayout instance.
   * @param {object} itemConfig: Requested component configuration.
   * @returns {boolean} True when the panel already exists.
   */
  function hasExistingPanel(layout, itemConfig) {
    if (!layout || typeof layout.saveLayout !== "function") {
      return false;
    }
    const savedLayout = layout.saveLayout();
    return Boolean(
      findPanelInLayout(savedLayout && savedLayout.root, itemConfig.componentType, itemConfig.componentState),
    );
  }

  /**
   * Description:
   *   Reveal an existing panel instead of silently ignoring a duplicate add request.
   *
   * Requirements:
   *   - Scroll the matching panel into view when the DOM element is available.
   *   - Apply a brief highlight so the user can see which panel was reused.
   *
   * @param {object} itemConfig: Requested component configuration.
   * @returns {boolean} True when an existing panel was found and revealed.
   */
  function focusExistingPanel(itemConfig) {
    const identityKey = buildPanelIdentityKey(itemConfig.componentType, itemConfig.componentState || {});
    const panel = Array.from(document.querySelectorAll("[data-faith-panel-key]")).find(function findPanel(element) {
      return element.dataset.faithPanelKey === identityKey;
    });
    if (!(panel instanceof HTMLElement)) {
      return false;
    }
    panel.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
    panel.classList.add("faith-panel--focused");
    globalScope.setTimeout(function clearFocusClass() {
      panel.classList.remove("faith-panel--focused");
    }, 900);
    return true;
  }

  /**
   * Description:
   *   Remove one matching panel from a saved layout tree.
   *
   * Requirements:
   *   - Remove the first component that matches the requested identity.
   *   - Collapse empty wrapper nodes so saved layouts stay tidy.
   *
   * @param {object|null} item: Layout node to inspect.
   * @param {string} componentType: Requested component type.
   * @param {object} componentState: Requested component state.
   * @returns {object|null} Updated layout node, or null when removed entirely.
   */
  function removePanelFromLayout(item, componentType, componentState) {
    if (!item || typeof item !== "object") {
      return item;
    }
    if (item.type === "component" && item.componentType === componentType) {
      if (isSamePanelIdentity(componentType, item.componentState || {}, componentState || {})) {
        return null;
      }
      return item;
    }
    if (!Array.isArray(item.content)) {
      return item;
    }

    const nextContent = item.content
      .map(function removeChild(child) {
        return removePanelFromLayout(child, componentType, componentState);
      })
      .filter(Boolean);

    if (nextContent.length === 0) {
      return null;
    }
    if (nextContent.length === 1 && (item.type === "row" || item.type === "column" || item.type === "stack")) {
      return nextContent[0];
    }
    return Object.assign({}, item, { content: nextContent });
  }

  /**
   * Description:
   *   Remove one panel from the live layout by its logical identity.
   *
   * Requirements:
   *   - Reload the layout from the updated saved tree so persistence stays correct.
   *   - Keep the workspace valid even when removing the last remaining panel.
   *
   * @param {object} layout: Active GoldenLayout instance.
   * @param {string} componentType: Requested component type.
   * @param {object} componentState: Requested component state.
   * @returns {boolean} True when a panel was removed.
   */
  function removePanelByIdentity(layout, componentType, componentState) {
    if (!layout || typeof layout.saveLayout !== "function" || typeof layout.loadLayout !== "function") {
      return false;
    }
    const savedLayout = layout.saveLayout();
    const nextRoot = removePanelFromLayout(savedLayout && savedLayout.root, componentType, componentState);
    if (nextRoot === savedLayout.root) {
      return false;
    }
    layout.loadLayout({
      root:
        nextRoot ||
        {
          type: "column",
          content: [],
        },
    });
    return true;
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
    // Keep singleton and runtime-identity panels from being added repeatedly.
    if (hasExistingPanel(layout, itemConfig)) {
      focusExistingPanel(itemConfig);
      return;
    }
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
      [COMPONENT_TYPES.DOCKER_RUNTIME]: "Docker Runtime",
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
      { label: "Docker Runtime Panel", type: COMPONENT_TYPES.DOCKER_RUNTIME },
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
        resetLayout(layout);
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
    focusExistingPanel: focusExistingPanel,
    hasExistingPanel: hasExistingPanel,
    initLayout: initLayout,
    addAgentPanel: addAgentPanel,
    addToolPanel: addToolPanel,
    removePanelByIdentity: removePanelByIdentity,
    resetLayout: resetLayout,
  };
})(window);
