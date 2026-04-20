/**
 * Description:
 *   Provide a minimal FAITH-local fallback for the GoldenLayout browser API.
 *
 * Requirements:
 *   - Expose `window.goldenLayout.GoldenLayout` when the CDN build is unavailable.
 *   - Support the small API surface used by `web/js/layout.js`.
 *   - Render nested row, column, stack, and component nodes well enough for the
 *     default FAITH workspace to remain usable offline.
 */
(function initialiseFaithGoldenLayoutFallback(globalScope) {
  /**
   * Description:
   *   Create a deep-cloned copy of one serialisable layout object.
   *
   * Requirements:
   *   - Keep saved layout state isolated from later mutations.
   *
   * @param {object|null} value Layout value to clone.
   * @returns {object|null} Deep-cloned layout value.
   */
  function cloneLayoutValue(value) {
    return value === null || value === undefined ? value : JSON.parse(JSON.stringify(value));
  }

  /**
   * Description:
   *   Represent the root item API expected by the FAITH layout wrapper.
   *
   * Requirements:
   *   - Allow new child items to be appended dynamically.
   *
   * @param {FaithGoldenLayout} layout Owning layout instance.
   */
  function FaithRootItem(layout) {
    this.layout = layout;
  }

  /**
   * Description:
   *   Append one child item to the current root layout node.
   *
   * Requirements:
   *   - Preserve the existing root node when it already contains content.
   *   - Trigger a re-render and state-changed event after insertion.
   *
   * @param {object} itemConfig New item configuration to append.
   */
  FaithRootItem.prototype.addChild = function addChild(itemConfig) {
    if (!this.layout._config || !this.layout._config.root) {
      this.layout._config = { root: { type: "row", content: [] } };
    }

    if (!Array.isArray(this.layout._config.root.content)) {
      this.layout._config.root.content = [];
    }

    this.layout._config.root.content.push(cloneLayoutValue(itemConfig));
    this.layout._render();
    this.layout._emit("stateChanged");
  };

  /**
   * Description:
   *   Provide a minimal GoldenLayout-compatible constructor for FAITH.
   *
   * Requirements:
   *   - Support component factory registration, layout loading, state saving,
   *     add-item insertion, and event subscription.
   *   - Render a simple nested panel workspace into the supplied container.
   *
   * @param {HTMLElement} containerElement DOM node used as the layout mount.
   */
  function FaithGoldenLayout(containerElement) {
    this.containerElement = containerElement;
    this._componentFactories = {};
    this._eventHandlers = {};
    this._config = null;
    this.rootItem = new FaithRootItem(this);
  }

  /**
   * Description:
   *   Register one component factory callback.
   *
   * Requirements:
   *   - Allow later registrations to replace earlier ones for the same type.
   *
   * @param {string} componentType Registered component identifier.
   * @param {Function} factory Component factory callback.
   */
  FaithGoldenLayout.prototype.registerComponentFactoryFunction = function registerComponentFactoryFunction(
    componentType,
    factory
  ) {
    this._componentFactories[componentType] = factory;
  };

  /**
   * Description:
   *   Register one event listener on the fallback layout.
   *
   * Requirements:
   *   - Support the `stateChanged` event used by FAITH persistence.
   *
   * @param {string} eventName Event name to listen for.
   * @param {Function} handler Handler callback.
   */
  FaithGoldenLayout.prototype.on = function on(eventName, handler) {
    if (!this._eventHandlers[eventName]) {
      this._eventHandlers[eventName] = [];
    }
    this._eventHandlers[eventName].push(handler);
  };

  /**
   * Description:
   *   Emit one layout event to all registered handlers.
   *
   * Requirements:
   *   - Ignore handler failures so one bad listener does not break the layout.
   *
   * @param {string} eventName Event name to emit.
   */
  FaithGoldenLayout.prototype._emit = function _emit(eventName) {
    var handlers = this._eventHandlers[eventName] || [];
    handlers.forEach(function invokeHandler(handler) {
      try {
        handler();
      } catch (error) {
        console.warn("[FAITH] GoldenLayout fallback handler failed.", error);
      }
    });
  };

  /**
   * Description:
   *   Load one full layout configuration and render it immediately.
   *
   * Requirements:
   *   - Clone the incoming config before storing it.
   *
   * @param {object} config Layout configuration to render.
   */
  FaithGoldenLayout.prototype.loadLayout = function loadLayout(config) {
    this._config = cloneLayoutValue(config);
    this._render();
    this._emit("stateChanged");
  };

  /**
   * Description:
   *   Return the last loaded layout configuration.
   *
   * Requirements:
   *   - Return a clone so external callers cannot mutate internal state.
   *
   * @returns {object|null} Cloned layout configuration.
   */
  FaithGoldenLayout.prototype.saveLayout = function saveLayout() {
    return cloneLayoutValue(this._config);
  };

  /**
   * Description:
   *   Append one item to the root layout node.
   *
   * Requirements:
   *   - Delegate to the root-item helper used by FAITH.
   *
   * @param {object} itemConfig New item configuration.
   */
  FaithGoldenLayout.prototype.addItem = function addItem(itemConfig) {
    this.rootItem.addChild(itemConfig);
  };

  /**
   * Description:
   *   Render the current layout tree into the mount container.
   *
   * Requirements:
   *   - Replace any previously rendered content.
   *   - Attach the standard `lm_goldenlayout` root class expected by FAITH CSS.
   */
  FaithGoldenLayout.prototype._render = function _render() {
    if (!(this.containerElement instanceof HTMLElement)) {
      return;
    }

    this.containerElement.replaceChildren();
    this.containerElement.classList.add("lm_goldenlayout");

    if (!this._config || !this._config.root) {
      return;
    }

    this.containerElement.appendChild(this._renderNode(this._config.root));
  };

  /**
   * Description:
   *   Render one layout node recursively.
   *
   * Requirements:
   *   - Support `row`, `column`, `stack`, and `component` node types.
   *
   * @param {object} node Layout node to render.
   * @returns {HTMLElement} Rendered DOM node.
   */
  FaithGoldenLayout.prototype._renderNode = function _renderNode(node) {
    if (!node || typeof node !== "object") {
      var emptyNode = document.createElement("div");
      return emptyNode;
    }

    if (node.type === "row" || node.type === "column") {
      return this._renderLinearNode(node);
    }

    if (node.type === "stack") {
      return this._renderStackNode(node);
    }

    if (node.type === "component") {
      return this._renderComponentNode(node);
    }

    var unknownNode = document.createElement("div");
    unknownNode.textContent = "Unsupported layout node";
    return unknownNode;
  };

  /**
   * Description:
   *   Render one row or column node using flexbox sizing.
   *
   * Requirements:
   *   - Map GoldenLayout `size` values onto flex-grow ratios.
   *
   * @param {object} node Row or column layout node.
   * @returns {HTMLElement} Rendered row or column wrapper.
   */
  FaithGoldenLayout.prototype._renderLinearNode = function _renderLinearNode(node) {
    var wrapper = document.createElement("div");
    wrapper.className = node.type === "row" ? "lm_row" : "lm_column";

    (node.content || []).forEach(
      function appendChild(child) {
        var childElement = this._renderNode(child);
        if (child && typeof child.size === "number") {
          childElement.style.flexGrow = String(child.size);
          childElement.style.flexBasis = "0";
        }
        wrapper.appendChild(childElement);
      }.bind(this)
    );

    return wrapper;
  };

  /**
   * Description:
   *   Render one stack node with a simple tab header.
   *
   * Requirements:
   *   - Show a tab for each child component.
   *   - Render the first child as the active content area.
   *
   * @param {object} node Stack node to render.
   * @returns {HTMLElement} Rendered stack wrapper.
   */
  FaithGoldenLayout.prototype._renderStackNode = function _renderStackNode(node) {
    var wrapper = document.createElement("section");
    wrapper.className = "lm_stack";

    var header = document.createElement("div");
    header.className = "lm_header";

    var contentHost = document.createElement("div");
    contentHost.className = "lm_content";

    (node.content || []).forEach(
      function renderStackChild(child, index) {
        var tab = document.createElement("button");
        tab.className = "lm_tab" + (index === 0 ? " lm_active" : "");
        tab.type = "button";
        tab.textContent = child.title || child.componentType || "Panel";
        header.appendChild(tab);

        if (index === 0) {
          contentHost.appendChild(this._renderNode(child));
        }
      }.bind(this)
    );

    wrapper.appendChild(header);
    wrapper.appendChild(contentHost);
    return wrapper;
  };

  /**
   * Description:
   *   Render one component node using the registered factory when available.
   *
   * Requirements:
   *   - Provide a container object with the `.element` shape expected by FAITH.
   *   - Fall back to a simple placeholder when no factory has been registered.
   *
   * @param {object} node Component node to render.
   * @returns {HTMLElement} Rendered component wrapper.
   */
  FaithGoldenLayout.prototype._renderComponentNode = function _renderComponentNode(node) {
    var shell = document.createElement("section");
    shell.className = "lm_item_shell";

    var header = document.createElement("div");
    header.className = "lm_header faith-panel__fallback-header";

    var title = document.createElement("span");
    title.className = "lm_tab lm_active";
    title.textContent = node.title || node.componentType || "Panel";

    var closeButton = document.createElement("button");
    closeButton.className = "faith-panel__close";
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "Close panel");
    closeButton.textContent = "×";

    closeButton.addEventListener(
      "click",
      function onClosePanelClick() {
        if (
          globalScope.faithLayout &&
          typeof globalScope.faithLayout.removePanelByIdentity === "function" &&
          globalScope.faithLayoutInstance
        ) {
          globalScope.faithLayout.removePanelByIdentity(
            globalScope.faithLayoutInstance,
            node.componentType,
            cloneLayoutValue(node.componentState || {})
          );
        }
      }.bind(this)
    );

    header.appendChild(title);
    header.appendChild(closeButton);

    var element = document.createElement("div");
    element.className = "lm_item_container";

    var factory = this._componentFactories[node.componentType];
    if (typeof factory === "function") {
      factory({ element: element }, cloneLayoutValue(node.componentState || {}));
      shell.appendChild(header);
      shell.appendChild(element);
      return shell;
    }

    element.textContent = node.title || node.componentType || "Panel";
    shell.appendChild(header);
    shell.appendChild(element);
    return shell;
  };

  globalScope.goldenLayout = globalScope.goldenLayout || {};
  if (typeof globalScope.goldenLayout.GoldenLayout !== "function") {
    globalScope.goldenLayout.GoldenLayout = FaithGoldenLayout;
  }
})(window);
