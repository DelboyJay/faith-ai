/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH PA system prompt panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel can load AGENTS.md-backed project-instruction metadata from the server.
 *   - Prove local edits become dirty, can be saved, can block reload when cancelled, and can be reset back to an empty project-instruction layer.
 */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function FakeHTMLElement(tagName) {
  this.tagName = tagName.toUpperCase();
  this.children = [];
  this.dataset = {};
  this.style = {};
  this.hidden = false;
  this.textContent = "";
  this.value = "";
  this.rows = 0;
  this.spellcheck = false;
  this.disabled = false;
  this.type = "";
  this.className = "";
  this.attributes = {};
  this.eventListeners = {};
  this.classList = {
    values: new Set(),
    add: (...names) => names.forEach((name) => this.classList.values.add(name)),
    remove: (...names) => names.forEach((name) => this.classList.values.delete(name)),
    toggle: (name, enabled) => {
      if (enabled) {
        this.classList.values.add(name);
      } else {
        this.classList.values.delete(name);
      }
    },
    contains: (name) => this.classList.values.has(name),
  };
}

FakeHTMLElement.prototype.appendChild = function appendChild(child) {
  this.children.push(child);
  return child;
};

FakeHTMLElement.prototype.replaceChildren = function replaceChildren(...children) {
  this.children = children;
};

FakeHTMLElement.prototype.setAttribute = function setAttribute(name, value) {
  this.attributes[name] = value;
};

FakeHTMLElement.prototype.addEventListener = function addEventListener(name, handler) {
  this.eventListeners[name] = this.eventListeners[name] || [];
  this.eventListeners[name].push(handler);
};

FakeHTMLElement.prototype.removeEventListener = function removeEventListener(name, handler) {
  this.eventListeners[name] = (this.eventListeners[name] || []).filter(
    (candidate) => candidate !== handler,
  );
};

FakeHTMLElement.prototype.dispatch = function dispatch(name, event = {}) {
  (this.eventListeners[name] || []).forEach((handler) => handler(event));
};

function findByTag(root, tagName) {
  if (root.tagName === tagName.toUpperCase()) {
    return root;
  }
  for (const child of root.children || []) {
    const match = findByTag(child, tagName);
    if (match) {
      return match;
    }
  }
  return null;
}

function findByText(root, text) {
  if (root.textContent === text) {
    return root;
  }
  for (const child of root.children || []) {
    const match = findByText(child, text);
    if (match) {
      return match;
    }
  }
  return null;
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

/**
 * Description:
 *   Wait for promise continuations scheduled by async browser event handlers.
 *
 * Requirements:
 *   - Let click handlers complete their awaited fetch calls before assertions run.
 *
 * @returns {Promise<void>} Promise that resolves after queued microtasks drain.
 */
async function flushAsyncTasks() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

global.HTMLElement = FakeHTMLElement;
global.document = {
  createElement(tagName) {
    return new FakeHTMLElement(tagName);
  },
};
global.window = global;

const eventListeners = {};
global.addEventListener = function addEventListener(name, handler) {
  eventListeners[name] = eventListeners[name] || [];
  eventListeners[name].push(handler);
};
global.removeEventListener = function removeEventListener(name, handler) {
  eventListeners[name] = (eventListeners[name] || []).filter((candidate) => candidate !== handler);
};

const confirmResponses = [];
global.confirm = function confirm() {
  return confirmResponses.length > 0 ? confirmResponses.shift() : true;
};

const dispatchedEvents = [];
global.dispatchEvent = function dispatchEvent(event) {
  dispatchedEvents.push(event);
};

global.CustomEvent = function CustomEvent(type, options = {}) {
  this.type = type;
  this.detail = options.detail;
};

const fetchCalls = [];
const queuedResponses = [];
global.fetch = async function fetch(url, options = {}) {
  fetchCalls.push({ url, options });
  if (queuedResponses.length === 0) {
    throw new Error(`No queued fetch response for ${url}`);
  }
  const next = queuedResponses.shift();
  return {
    ok: next.ok,
    status: next.status,
    async json() {
      return next.json;
    },
  };
};

const panelSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "pa-system-prompt-panel.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "pa-system-prompt-panel.js" });

(async function run() {
  queuedResponses.push({
    ok: true,
    status: 200,
    json: {
      prompt: "",
      source: "project",
      path: "E:/ClaudeSharedFolder/AI Agent Framework/AGENTS.md",
      default_available: true,
      differs_from_default: false,
      updated_at: null,
    },
  });

  const target = new FakeHTMLElement("div");
  const panel = window.faithPaSystemPromptPanel.mountPanel(target);
  await flushAsyncTasks();

  const textarea = findByTag(target, "textarea");
  const saveButton = findByText(target, "Save");
  const reloadButton = findByText(target, "Reload");
  const resetButton = findByText(target, "Reset");

  assert(fetchCalls[0].url === "/api/pa/system-prompt", "Expected mount to load the active prompt.");
  assert(textarea.value === "", "Expected the loaded project instructions to populate the editor.");
  assert(saveButton.disabled === true, "Expected Save to stay disabled until the prompt is edited.");
  assert(panel.getState().unsaved === false, "Expected the initial loaded prompt not to be dirty.");

  textarea.value = "Always explain build steps briefly.\n";
  textarea.dispatch("input");
  assert(saveButton.disabled === false, "Expected local edits to enable Save.");
  assert(panel.getState().unsaved === true, "Expected local edits to mark the panel dirty.");
  assert(panel.hasUnsavedChanges() === true, "Expected the panel API to expose unsaved-change state.");

  queuedResponses.push({
    ok: true,
    status: 200,
    json: {
      prompt: "Always explain build steps briefly.\n",
      source: "project",
      path: "E:/ClaudeSharedFolder/AI Agent Framework/AGENTS.md",
      default_available: true,
      differs_from_default: true,
      updated_at: "2026-04-26T10:20:00+00:00",
    },
  });
  saveButton.dispatch("click");
  await flushAsyncTasks();

  assert(fetchCalls[1].url === "/api/pa/system-prompt", "Expected Save to call the PA prompt endpoint.");
  assert(fetchCalls[1].options.method === "PUT", "Expected Save to use PUT.");
  assert(
    JSON.parse(fetchCalls[1].options.body).prompt === "Always explain build steps briefly.\n",
    "Expected Save to submit the edited AGENTS.md text.",
  );
  assert(panel.getState().unsaved === false, "Expected a successful save to clear dirty state.");
  assert(
    dispatchedEvents.some((event) => event.type === "faith:pa-system-prompt-updated"),
    "Expected successful prompt changes to publish a UI notification event.",
  );

  textarea.value = "Unsaved local draft.";
  textarea.dispatch("input");
  confirmResponses.push(false);
  reloadButton.dispatch("click");
  await flushAsyncTasks();
  assert(fetchCalls.length === 2, "Expected cancelled reload to avoid a second GET.");
  assert(textarea.value === "Unsaved local draft.", "Expected cancelled reload to preserve local edits.");

  queuedResponses.push({
    ok: true,
    status: 200,
    json: {
      prompt: "",
      source: "project",
      path: "E:/ClaudeSharedFolder/AI Agent Framework/AGENTS.md",
      default_available: true,
      differs_from_default: false,
      updated_at: null,
    },
  });
  confirmResponses.push(true);
  resetButton.dispatch("click");
  await flushAsyncTasks();

  assert(fetchCalls[2].url === "/api/pa/system-prompt/reset", "Expected Reset to call the PA reset endpoint.");
  assert(fetchCalls[2].options.method === "POST", "Expected Reset to use POST.");
  assert(panel.getState().unsaved === false, "Expected reset to clear local dirty state.");
  assert(textarea.value === "", "Expected reset to clear the editable AGENTS.md instruction layer.");

  textarea.value = "Leave page with draft.";
  textarea.dispatch("input");
  const unloadEvent = {
    prevented: false,
    returnValue: undefined,
    preventDefault() {
      this.prevented = true;
    },
  };
  eventListeners.beforeunload[0](unloadEvent);
  assert(unloadEvent.prevented === true, "Expected unsaved prompt edits to block browser unload.");
  assert(unloadEvent.returnValue === "", "Expected unsaved prompt edits to request the browser confirmation dialog.");

  panel.destroy();

  console.log("pa system prompt panel runtime checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
