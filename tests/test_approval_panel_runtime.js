/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH approval panel JavaScript.
 *
 * Requirements:
 *   - Prove approval requests render as pending cards and duplicate IDs are ignored.
 *   - Prove persisted decisions show an editable rule preview before posting.
 *   - Prove submitted decisions move cards into in-panel history and expose disconnect state.
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
  this.placeholder = "";
  this.disabled = false;
  this.type = "";
  this.className = "";
  this.eventListeners = {};
  this.attributes = {};
  this.classList = {
    values: new Set(),
    add: (...names) => names.forEach((name) => this.classList.values.add(name)),
    remove: (...names) => names.forEach((name) => this.classList.values.delete(name)),
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

FakeHTMLElement.prototype.addEventListener = function addEventListener(name, handler) {
  this.eventListeners[name] = this.eventListeners[name] || [];
  this.eventListeners[name].push(handler);
};

FakeHTMLElement.prototype.dispatch = function dispatch(name, event = {}) {
  (this.eventListeners[name] || []).forEach((handler) => handler(event));
};

FakeHTMLElement.prototype.setAttribute = function setAttribute(name, value) {
  this.attributes[name] = value;
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

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

/**
 * Description:
 *   Wait for promise continuations scheduled by async event handlers.
 *
 * Requirements:
 *   - Let click handlers complete their awaited fetch calls before assertions run.
 *
 * @returns {Promise<void>} Promise that resolves after queued microtasks drain.
 */
async function flushAsyncTasks() {
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
global.location = { protocol: "http:", host: "localhost:8080" };

const timers = [];
global.setTimeout = function setTimeout(handler) {
  timers.push(handler);
  return timers.length;
};
global.clearTimeout = function clearTimeout() {};

const fetchCalls = [];
global.fetch = async function fetch(url, options = {}) {
  fetchCalls.push({ url, options });
  return {
    ok: true,
    status: 200,
    async json() {
      return { status: "sent" };
    },
  };
};

const sockets = [];
class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.closed = false;
    sockets.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(name, payload) {
    if (typeof this[`on${name}`] === "function") {
      this[`on${name}`](payload);
    }
  }
}

global.WebSocket = FakeWebSocket;

const panelSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "approval-panel.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "approval-panel.js" });

(async function run() {
  const target = new FakeHTMLElement("div");
  const panel = window.faithApprovalPanel.mountPanel(target);
  const request = {
    type: "approval_required",
    request_id: "apr-100",
    agent: "project-agent",
    tool: "filesystem",
    action: "write_file",
    detail: "Write docs",
    target: "README.md",
    timestamp: "2026-04-19T10:00:00Z",
    context_summary: "User asked for documentation.",
  };

  assert(sockets[0].url === "ws://localhost:8080/ws/approvals", "Expected approval panel to connect to /ws/approvals.");
  sockets[0].emit("open", {});
  assert(panel.getState().connectionStatus === "connected", "Expected open socket to mark the panel connected.");

  sockets[0].emit("message", { data: JSON.stringify(request) });
  sockets[0].emit("message", { data: JSON.stringify(request) });
  assert(panel.getState().pendingCount === 1, "Expected duplicate approval request IDs to be ignored.");
  assert(findByText(target, "project-agent"), "Expected the pending card to show the agent name.");
  assert(findByText(target, "filesystem"), "Expected the pending card to show the tool name.");
  assert(findByText(target, "write_file"), "Expected the pending card to show the action.");
  assert(findByText(target, "Write docs"), "Expected the pending card to show the detail.");
  assert(target.children[0].classList.contains("faith-approval-panel--alert"), "Expected new approvals to apply alert styling.");

  findByText(target, "Always allow").dispatch("click");
  const ruleEditor = findByTag(target, "textarea");
  assert(ruleEditor && ruleEditor.value.includes("filesystem"), "Expected persisted decisions to preview a generated rule.");
  ruleEditor.value = "filesystem:write_file:README.md";
  findByText(target, "Confirm rule").dispatch("click");

  assert(fetchCalls.length === 1, "Expected one approval decision POST.");
  assert(fetchCalls[0].url === "/approve/apr-100", "Expected decision POST to target the request ID.");
  const body = JSON.parse(fetchCalls[0].options.body);
  assert(body.decision === "always_allow", "Expected the persisted decision to be submitted.");
  assert(body.pattern_override === "filesystem:write_file:README.md", "Expected edited rule text to be submitted.");
  await flushAsyncTasks();
  assert(panel.getState().pendingCount === 0, "Expected submitted cards to leave the pending queue.");
  assert(panel.getState().historyCount === 1, "Expected submitted cards to move to history.");
  assert(findByText(target, "always_allow"), "Expected history to show the submitted decision.");

  sockets[0].emit("message", { data: JSON.stringify({ ...request, request_id: "apr-101" }) });
  findByText(target, "Deny once").dispatch("click");
  await flushAsyncTasks();
  assert(JSON.parse(fetchCalls[1].options.body).decision === "deny_once", "Expected non-persisted decisions to post directly.");

  sockets[0].emit("message", { data: JSON.stringify({ ...request, request_id: "apr-102" }) });
  sockets[0].emit("message", { data: JSON.stringify({ type: "approval_resolved", request_id: "apr-102", decision: "allow_once" }) });
  assert(panel.getState().pendingCount === 0, "Expected externally resolved cards to leave the queue.");

  sockets[0].emit("close", {});
  assert(panel.getState().connectionStatus === "reconnecting", "Expected closed socket to show reconnecting state.");
  assert(findByText(target, "Reconnecting"), "Expected reconnecting status text to be visible.");

  panel.destroy();
  assert(sockets[0].closed === true, "Expected destroy to close the approval WebSocket.");

  console.log("approval panel runtime checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
