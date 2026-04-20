/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH layout JavaScript.
 *
 * Requirements:
 *   - Prove the exported layout helpers dedupe singleton/runtime panels.
 *   - Prove panel removal updates saved layout state cleanly.
 *   - Prove duplicate requests reveal the existing panel instead of appending another item.
 */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

/**
 * Description:
 *   Provide a minimal DOM element stand-in for the layout runtime tests.
 *
 * Requirements:
 *   - Support the DOM methods used by `web/js/layout.js`.
 *   - Track dataset, children, classList, and scroll calls for assertions.
 *
 * @param {string} tagName HTML tag name used to create the element.
 */
function FakeHTMLElement(tagName) {
  this.tagName = tagName.toUpperCase();
  this.children = [];
  this.dataset = {};
  this.style = {};
  this.hidden = false;
  this.textContent = "";
  this.className = "";
  this.attributes = {};
  this.eventListeners = {};
  this.wasScrolled = false;
  this.classList = {
    values: new Set(),
    add: (...names) => {
      names.forEach((name) => this.classList.values.add(name));
    },
    remove: (...names) => {
      names.forEach((name) => this.classList.values.delete(name));
    },
    contains: (name) => this.classList.values.has(name),
  };
}

/**
 * Description:
 *   Append a child element to the fake DOM node.
 *
 * Requirements:
 *   - Preserve insertion order for later assertions.
 *
 * @param {FakeHTMLElement} child Child element to append.
 * @returns {FakeHTMLElement} The appended child.
 */
FakeHTMLElement.prototype.appendChild = function appendChild(child) {
  this.children.push(child);
  return child;
};

/**
 * Description:
 *   Replace all current children with a new child list.
 *
 * Requirements:
 *   - Support the `replaceChildren` API used by the layout runtime.
 *
 * @param {...FakeHTMLElement} children Replacement child list.
 */
FakeHTMLElement.prototype.replaceChildren = function replaceChildren(...children) {
  this.children = children;
};

/**
 * Description:
 *   Record one attribute assignment.
 *
 * Requirements:
 *   - Preserve attribute values for later assertions.
 *
 * @param {string} name Attribute name.
 * @param {string} value Attribute value.
 */
FakeHTMLElement.prototype.setAttribute = function setAttribute(name, value) {
  this.attributes[name] = value;
};

/**
 * Description:
 *   Register one event listener on the fake element.
 *
 * Requirements:
 *   - Support click handlers used by the fallback layout.
 *
 * @param {string} eventName Event name.
 * @param {Function} handler Listener callback.
 */
FakeHTMLElement.prototype.addEventListener = function addEventListener(eventName, handler) {
  this.eventListeners[eventName] = handler;
};

/**
 * Description:
 *   Trigger one registered event handler.
 *
 * Requirements:
 *   - Ignore missing handlers safely.
 *
 * @param {string} eventName Event name to dispatch.
 */
FakeHTMLElement.prototype.dispatch = function dispatch(eventName) {
  if (typeof this.eventListeners[eventName] === "function") {
    this.eventListeners[eventName]();
  }
};

/**
 * Description:
 *   Record that the element was scrolled into view.
 *
 * Requirements:
 *   - Match the API shape used by the layout runtime.
 */
FakeHTMLElement.prototype.scrollIntoView = function scrollIntoView() {
  this.wasScrolled = true;
};

const elementRegistry = [];

const documentStub = {
  createElement(tagName) {
    const element = new FakeHTMLElement(tagName);
    elementRegistry.push(element);
    return element;
  },
  querySelectorAll(selector) {
    if (selector === "[data-faith-panel-key]") {
      return elementRegistry.filter((element) => Boolean(element.dataset.faithPanelKey));
    }
    return [];
  },
  addEventListener() {},
};

const localStorageState = new Map();

global.HTMLElement = FakeHTMLElement;
global.document = documentStub;
global.window = global;
global.localStorage = {
  getItem(key) {
    return localStorageState.has(key) ? localStorageState.get(key) : null;
  },
  setItem(key, value) {
    localStorageState.set(key, value);
  },
  removeItem(key) {
    localStorageState.delete(key);
  },
};
let reloadCount = 0;
global.location = {
  reload() {
    reloadCount += 1;
  },
};
global.prompt = () => null;
global.confirm = () => true;
global.setTimeout = (handler) => {
  handler();
  return 0;
};

const layoutSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "layout.js"),
  "utf8",
);
vm.runInThisContext(layoutSource, { filename: "layout.js" });

/**
 * Description:
 *   Collect every panel title from one saved layout tree.
 *
 * Requirements:
 *   - Traverse nested layout containers recursively.
 *
 * @param {object|null} item Layout node to inspect.
 * @returns {string[]} Flattened list of panel titles.
 */
function collectPanelTitles(item) {
  if (!item || typeof item !== "object") {
    return [];
  }
  if (item.type === "component") {
    return [item.title];
  }
  return (item.content || []).flatMap((child) => collectPanelTitles(child));
}

/**
 * Description:
 *   Fail fast when a runtime expectation is not met.
 *
 * Requirements:
 *   - Exit the script with a clear error message.
 *
 * @param {boolean} condition Condition that must hold.
 * @param {string} message Failure message.
 */
function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const defaultLayout = window.faithLayout.buildDefaultLayoutConfig();

assert(
  window.faithLayout.hasExistingPanel(
    { saveLayout: () => defaultLayout },
    {
      componentType: window.faithLayout.COMPONENT_TYPES.INPUT,
      componentState: {},
    },
  ),
  "Expected the default layout to contain the Input singleton panel.",
);

assert(
  window.faithLayout.hasExistingPanel(
    { saveLayout: () => defaultLayout },
    {
      componentType: window.faithLayout.COMPONENT_TYPES.AGENT,
      componentState: { agentId: "project-agent", displayName: "Project Agent" },
    },
  ),
  "Expected the default layout to contain the Project Agent panel.",
);

assert(
  !window.faithLayout.hasExistingPanel(
    { saveLayout: () => defaultLayout },
    {
      componentType: window.faithLayout.COMPONENT_TYPES.AGENT,
      componentState: { agentId: "qa-engineer", displayName: "QA Engineer" },
    },
  ),
  "Expected a different agent identity to remain addable.",
);

const focusedPanel = new FakeHTMLElement("div");
focusedPanel.dataset.faithPanelKey = "agent-panel:project-agent";
elementRegistry.push(focusedPanel);

assert(
  window.faithLayout.focusExistingPanel({
    componentType: window.faithLayout.COMPONENT_TYPES.AGENT,
    componentState: { agentId: "project-agent", displayName: "Project Agent" },
  }),
  "Expected duplicate panel requests to reveal the existing panel.",
);
assert(focusedPanel.wasScrolled, "Expected the existing panel to be scrolled into view.");

const removableLayout = JSON.parse(JSON.stringify(defaultLayout));
const layoutHarness = {
  current: removableLayout,
  saveLayout() {
    return this.current;
  },
  loadLayout(nextLayout) {
    this.current = nextLayout;
  },
};

assert(
  window.faithLayout.removePanelByIdentity(
    layoutHarness,
    window.faithLayout.COMPONENT_TYPES.APPROVAL,
    {},
  ),
  "Expected singleton panel removal to succeed.",
);

const remainingTitles = collectPanelTitles(layoutHarness.current.root);
assert(!remainingTitles.includes("Approvals"), "Expected the removed panel to disappear from saved layout state.");
assert(remainingTitles.includes("Input"), "Expected sibling panels to remain after removal.");

localStorageState.set(window.faithLayout.LAYOUT_STORAGE_KEY, JSON.stringify(layoutHarness.current));
window.faithLayout.resetLayout(layoutHarness);
const resetTitles = collectPanelTitles(layoutHarness.current.root);
assert(resetTitles.includes("Approvals"), "Expected reset layout to restore the default Approvals panel.");
assert(resetTitles.includes("Project Agent"), "Expected reset layout to restore the default Project Agent panel.");
assert(
  !localStorageState.has(window.faithLayout.LAYOUT_STORAGE_KEY),
  "Expected reset layout to clear stale persisted layout state.",
);
assert(reloadCount === 0, "Expected reset layout to avoid a browser reload that can race layout persistence.");

const addHarness = {
  current: JSON.parse(JSON.stringify(defaultLayout)),
  addCalls: 0,
  saveLayout() {
    return this.current;
  },
  loadLayout(nextLayout) {
    this.current = nextLayout;
  },
  addItem(itemConfig) {
    this.addCalls += 1;
    this.current.root.content.push(itemConfig);
  },
};

window.faithLayout.addAgentPanel(addHarness, "project-agent", "Project Agent");
assert(addHarness.addCalls === 0, "Expected duplicate agent panels to be deduped.");

window.faithLayout.addAgentPanel(addHarness, "qa-engineer", "QA Engineer");
assert(addHarness.addCalls === 1, "Expected a distinct agent identity to create a new panel.");

console.log("layout runtime checks passed");
