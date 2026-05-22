/**
 * Description:
 *   Execute host-side runtime checks against the FAITH Session History and Effective Context panel JavaScript.
 *
 * Requirements:
 *   - Prove the session selector renders grouped human-readable rows without UUID-led list text.
 *   - Prove archived sessions stay non-resumable until restored and expose explanatory tooltips.
 *   - Prove selecting a session emits the shared session-change signal used by session-bound panels.
 *   - Prove the effective-context panel hides raw session/turn entry fields from the default flow.
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
  this.className = "";
  this.attributes = {};
  this.eventListeners = {};
  this.parentNode = null;
  this.parentElement = null;
  this.value = "";
  this.disabled = false;
  this.title = "";
  this.placeholder = "";
  this.checked = false;
  this.classList = {
    values: new Set(),
    add: (...names) => names.forEach((name) => this.classList.values.add(name)),
    remove: (...names) => names.forEach((name) => this.classList.values.delete(name)),
    contains: (name) => this.classList.values.has(name),
  };
}

FakeHTMLElement.prototype.appendChild = function appendChild(child) {
  child.parentNode = this;
  child.parentElement = this;
  this.children.push(child);
  return child;
};

FakeHTMLElement.prototype.replaceChildren = function replaceChildren(...children) {
  this.children = children;
  for (const child of children) {
    child.parentNode = this;
    child.parentElement = this;
  }
};

FakeHTMLElement.prototype.setAttribute = function setAttribute(name, value) {
  this.attributes[name] = String(value);
};

FakeHTMLElement.prototype.getAttribute = function getAttribute(name) {
  return this.attributes[name];
};

FakeHTMLElement.prototype.addEventListener = function addEventListener(name, handler) {
  this.eventListeners[name] = this.eventListeners[name] || [];
  this.eventListeners[name].push(handler);
};

FakeHTMLElement.prototype.dispatch = function dispatch(name, event = {}) {
  const payload = event;
  if (!payload.target) {
    payload.target = this;
  }
  (this.eventListeners[name] || []).forEach((handler) => handler(payload));
};

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

function findByPlaceholder(root, placeholder) {
  if (root.placeholder === placeholder) {
    return root;
  }
  for (const child of root.children || []) {
    const match = findByPlaceholder(child, placeholder);
    if (match) {
      return match;
    }
  }
  return null;
}

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

function findAllByTag(root, tagName, bucket = []) {
  if (root.tagName === tagName.toUpperCase()) {
    bucket.push(root);
  }
  for (const child of root.children || []) {
    findAllByTag(child, tagName, bucket);
  }
  return bucket;
}

function collectText(root) {
  let text = root.textContent || "";
  for (const child of root.children || []) {
    text += collectText(child);
  }
  return text;
}

function collectButtons(root, bucket = []) {
  if (root.tagName === "BUTTON") {
    bucket.push(root);
  }
  for (const child of root.children || []) {
    collectButtons(child, bucket);
  }
  return bucket;
}

async function flushAsyncTasks() {
  for (let index = 0; index < 12; index += 1) {
    await Promise.resolve();
  }
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

global.HTMLElement = FakeHTMLElement;
global.document = {
  createElement(tagName) {
    return new FakeHTMLElement(tagName);
  },
};
global.window = global;
const windowEventListeners = new Map();
global.addEventListener = function addEventListener(name, handler) {
  const listeners = windowEventListeners.get(name) || [];
  listeners.push(handler);
  windowEventListeners.set(name, listeners);
};
global.dispatchEvent = function dispatchEvent(event) {
  const listeners = windowEventListeners.get(event && event.type) || [];
  listeners.forEach((handler) => handler(event));
  return true;
};
global.CustomEvent = class CustomEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail;
  }
};
const localStorageState = new Map();
global.localStorage = {
  getItem(key) {
    return localStorageState.has(key) ? localStorageState.get(key) : null;
  },
  setItem(key, value) {
    localStorageState.set(key, String(value));
  },
  removeItem(key) {
    localStorageState.delete(key);
  },
};
global.setInterval = () => 1;
global.clearInterval = () => {};

const fetchCalls = [];
const sessionListPayload = {
  items: [
    {
      session_id: "sess-active-2",
      name: "Alpha Planning",
      status: "active",
      archived: false,
      started_at: "2026-05-11T14:30:00Z",
      last_used_at: "2026-05-11T16:30:00Z",
      actions: {
        archive: "/api/logs/sessions/sess-active-2/archive",
        delete: "/api/logs/sessions/sess-active-2",
        export: "/api/logs/sessions/sess-active-2/export",
        rename: "/api/logs/sessions/sess-active-2/rename",
      },
    },
    {
      session_id: "sess-active-1",
      name: "Beta Notes",
      status: "active",
      archived: false,
      started_at: "2026-05-11T14:00:00Z",
      last_used_at: "2026-05-11T15:00:00Z",
      actions: {
        archive: "/api/logs/sessions/sess-active-1/archive",
        delete: "/api/logs/sessions/sess-active-1",
        export: "/api/logs/sessions/sess-active-1/export",
        rename: "/api/logs/sessions/sess-active-1/rename",
      },
    },
    {
      session_id: "sess-archived-1",
      name: "Archived Research",
      status: "archived",
      archived: true,
      started_at: "2026-05-10T18:00:00Z",
      actions: {
        unarchive: "/api/logs/sessions/sess-archived-1/unarchive",
        delete: "/api/logs/sessions/sess-archived-1",
      },
    },
  ],
};

const sessionDetailPayload = {
  session: {
    session_id: "sess-active-2",
    name: "Alpha Planning",
    status: "active",
    archived: false,
    started_at: "2026-05-11T14:30:00Z",
    trigger: "web-ui",
    actions: {
      archive: "/api/logs/sessions/sess-active-2/archive",
      delete: "/api/logs/sessions/sess-active-2",
      export: "/api/logs/sessions/sess-active-2/export",
      rename: "/api/logs/sessions/sess-active-2/rename",
    },
  },
  tasks: [
    {
      task_id: "task-2-160000.000",
      goal: "Discuss launch checklist",
      status: "complete",
      started_at: "2026-05-11T16:00:00Z",
      channels: {},
    },
    {
      task_id: "task-1-150000.000",
      goal: "Draft the rollout plan",
      status: "complete",
      started_at: "2026-05-11T15:00:00Z",
      channels: {},
    },
  ],
  transcript: [],
};

const archivedDetailPayload = {
  session: {
    session_id: "sess-archived-1",
    name: "Archived Research",
    status: "archived",
    archived: true,
    started_at: "2026-05-10T18:00:00Z",
    trigger: "web-ui",
    actions: {
      unarchive: "/api/logs/sessions/sess-archived-1/unarchive",
      delete: "/api/logs/sessions/sess-archived-1",
    },
  },
  tasks: [],
  transcript: [],
};

global.fetch = async function fetch(url, options = {}) {
  fetchCalls.push({ url, options });
  if (url === "/api/logs/sessions") {
    return {
      ok: true,
      async json() {
        return sessionListPayload;
      },
    };
  }
  if (url === "/api/logs/sessions/sess-active-2") {
    return {
      ok: true,
      async json() {
        return sessionDetailPayload;
      },
    };
  }
  if (url === "/api/logs/sessions/sess-active-2/activate") {
    return {
      ok: true,
      async json() {
        return {
          session_id: "sess-active-2",
          name: "Alpha Planning",
          transcript: [
            { role: "user", content: "Continue the launch plan." },
            { role: "assistant", content: "Sure, let's continue." },
          ],
          tasks: sessionDetailPayload.tasks,
        };
      },
    };
  }
  if (url === "/api/logs/sessions/sess-archived-1") {
    return {
      ok: true,
      async json() {
        return archivedDetailPayload;
      },
    };
  }
  if (url === "/api/logs/sessions/sess-active-2/export") {
    return { ok: true, async json() { return { ok: true }; } };
  }
  if (url === "/api/logs/sessions/sess-active-2/archive") {
    return { ok: true, async json() { return { ok: true }; } };
  }
  if (url === "/api/logs/sessions/sess-active-2/activate") {
    return {
      ok: true,
      async json() {
        return {
          transcript: [{ role: "assistant", content: "Activated session transcript." }],
          tasks: [{ task_id: "task-2-160000.000", goal: "Discuss launch checklist" }],
        };
      },
    };
  }
  if (url === "/api/logs/sessions/sess-active-2/delete") {
    return { ok: true, async json() { return { ok: true }; } };
  }
  if (url === "/api/logs/sessions/sess-archived-1/unarchive") {
    return { ok: true, async json() { return { ok: true }; } };
  }
  if (url === "/api/logs/sessions/sess-archived-1/delete") {
    return { ok: true, async json() { return { ok: true }; } };
  }
  if (url === "/api/logs/sessions/sess-active-2/rename") {
    return { ok: true, async json() { return { ok: true }; } };
  }
  if (url === "/api/logs/effective-context/sess-active-2/task-2-160000.000") {
    return {
      ok: true,
      async json() {
        return {
          session_id: "sess-active-2",
          turn_id: "task-2-160000.000",
          snapshot_id: "snap-1",
          hash: "abc123",
          compiled_context: "redacted context",
          include_graph: [{ path: "AGENTS.md", tokens: 42 }],
          warnings: ["missing include target"],
        };
      },
    };
  }
  throw new Error(`Unexpected fetch URL: ${url}`);
};

global.faithLogPanelCommon = {
  renderRecordCard(record, rows) {
    const card = new FakeHTMLElement("article");
    card.className = "faith-log-panel__record-card";
    card.textContent = JSON.stringify({ record, rows });
    return card;
  },
};

const sessionHistorySource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "session-history.js"),
  "utf8",
);
const effectiveContextSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "effective-context-panel.js"),
  "utf8",
);
vm.runInThisContext(sessionHistorySource, { filename: "session-history.js" });
vm.runInThisContext(effectiveContextSource, { filename: "effective-context-panel.js" });

async function testSessionHistorySelector() {
  const target = new FakeHTMLElement("div");
  const observedSelections = [];
  window.addEventListener("faith:workspace-session-change", function onSessionChange(event) {
    observedSelections.push(event.detail);
  });

  const panel = window.faithSessionHistoryPanel.mountPanel(target);
  await flushAsyncTasks();

  assert(findByText(target, "Active Sessions"), "Expected an Active Sessions group header.");
  assert(findByText(target, "Archived Sessions"), "Expected an Archived Sessions group header.");
  assert(findByText(target, "Show Archived Sessions"), "Expected the archived-sessions toggle.");

  const activeButtons = collectButtons(target).filter((button) => button.textContent === "Alpha Planning" || button.textContent === "Beta Notes");
  assert(activeButtons.length === 2, "Expected active session rows to show human-readable names only.");
  assert(!collectText(target).includes("sess-active-2 ·"), "Expected the selector rows to avoid UUID-led text.");

  const activeSearch = findByPlaceholder(target, "Search active sessions");
  assert(activeSearch, "Expected the active-session search input.");
  activeSearch.value = "beta";
  activeSearch.dispatch("input");
  await flushAsyncTasks();
  assert(findByText(target, "Beta Notes"), "Expected case-insensitive contains search to keep matching rows.");

  const orderSelect = findByTag(target, "select");
  assert(orderSelect, "Expected an ordering selector.");
  orderSelect.value = "az";
  orderSelect.dispatch("change");
  await flushAsyncTasks();

  activeSearch.value = "";
  activeSearch.dispatch("input");
  await flushAsyncTasks();

  const archivedToggle = findAllByTag(target, "input").find((input) => input.type === "checkbox");
  archivedToggle.checked = true;
  archivedToggle.dispatch("click");
  await flushAsyncTasks();

  const archivedButton = collectButtons(target).find((button) => button.textContent === "Archived Research");
  assert(archivedButton, "Expected archived sessions to render when requested.");
  assert(archivedButton.disabled, "Expected archived session rows to be non-resumable until restored.");
  assert(
    String(archivedButton.title || "").toLowerCase().includes("restore"),
    "Expected archived rows to explain how to make them resumable again.",
  );

  const restoreButton = collectButtons(target).find((button) => button.textContent === "Restore");
  assert(restoreButton, "Expected an inline restore action for archived sessions.");
  restoreButton.dispatch("click");
  await flushAsyncTasks();

  const selectionButton = collectButtons(target).find((button) => button.textContent === "Alpha Planning");
  selectionButton.dispatch("click");
  await flushAsyncTasks();

  assert(
    fetchCalls.some((call) => call.url === "/api/logs/sessions/sess-active-2/activate"),
    "Expected selecting a session to activate it through the backend before broadcasting the shared session-change event.",
  );
  assert(
    observedSelections.some((payload) => payload && payload.sessionId === "sess-active-2"),
    "Expected session selection to emit a shared session-change event for downstream panels.",
  );

  panel.destroy();
}

async function testEffectiveContextSessionBinding() {
  const target = new FakeHTMLElement("div");
  const panel = window.faithEffectiveContextPanel.mountPanel(target, {
    sessionId: "sess-active-2",
  });

  const sessionInput = findByPlaceholder(target, "Session ID");
  const turnInput = findByPlaceholder(target, "Turn ID");
  assert(!sessionInput, "Expected the effective-context panel to hide the raw session input from the default flow.");
  assert(!turnInput, "Expected the effective-context panel to hide the raw turn input from the default flow.");

  await flushAsyncTasks();
  const taskSelect = findByTag(target, "select");
  assert(taskSelect, "Expected a human-facing snapshot selector.");
  taskSelect.value = "task-2-160000.000";
  taskSelect.dispatch("change");

  const loadButton = findByText(target, "Load Snapshot");
  assert(loadButton, "Expected a load-snapshot action for the selected session.");
  loadButton.dispatch("click");
  await flushAsyncTasks();

  assert(
    fetchCalls.some(
      (call) => call.url === "/api/logs/effective-context/sess-active-2/task-2-160000.000",
    ),
    "Expected the panel to resolve snapshots from the selected session and chosen human-facing turn.",
  );

  panel.destroy();
}

async function main() {
  await testSessionHistorySelector();
  await testEffectiveContextSessionBinding();
  console.log("session history and effective-context panel runtime checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
