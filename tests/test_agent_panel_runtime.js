/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH agent panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel can parse streamed frames and reflect status/model changes.
 *   - Prove pause/resume and malformed payload handling do not crash the panel.
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
  this.placeholder = "";
  this.disabled = false;
  this.className = "";
  this.attributes = {};
  this.isConnected = true;
  this.eventListeners = {};
  this.classList = {
    values: new Set(),
    add: (...names) => names.forEach((name) => this.classList.values.add(name)),
    remove: (...names) => names.forEach((name) => this.classList.values.delete(name)),
    toggle: (name, force) => {
      const enabled = force === undefined ? !this.classList.values.has(name) : force;
      if (enabled) {
        this.classList.values.add(name);
      } else {
        this.classList.values.delete(name);
      }
      return enabled;
    },
    contains: (name) => this.classList.values.has(name),
  };
}

/**
 * Description:
 *   Store one DOM attribute on the fake element used by the runtime harness.
 *
 * Requirements:
 *   - Let accessibility attributes be tested without a full browser DOM.
 *
 * @param {string} name: Attribute name.
 * @param {string} value: Attribute value.
 * @returns {void}
 */
FakeHTMLElement.prototype.setAttribute = function setAttribute(name, value) {
  this.attributes[name] = String(value);
};

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

function findByClass(root, className) {
  if ((root.className || "").split(/\s+/).includes(className)) {
    return root;
  }
  for (const child of root.children || []) {
    const match = findByClass(child, className);
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

global.HTMLElement = FakeHTMLElement;
global.document = {
  body: new FakeHTMLElement("body"),
  createElement(tagName) {
    return new FakeHTMLElement(tagName);
  },
};
global.window = global;
global.location = { href: "http://localhost:8080/" };
global.navigator = {
  clipboard: {
    written: "",
    async writeText(value) {
      this.written = value;
    },
  },
};
global.setTimeout = (handler) => {
  handler();
  return 1;
};
global.clearTimeout = () => {};
global.addEventListener = () => {};
global.removeEventListener = () => {};

class FakeTerminal {
  constructor() {
    this.writes = [];
    this.buffer = {
      active: {
        length: 0,
        getLine() {
          return null;
        },
      },
    };
    FakeTerminal.instances.push(this);
  }

  open(host) {
    this.host = host;
  }

  write(text) {
    this.writes.push(text);
  }

  writeln(text) {
    this.writes.push(`${text}\n`);
  }

  clear() {
    this.writes = [];
  }

  dispose() {}
}

FakeTerminal.instances = [];
global.Terminal = FakeTerminal;

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    FakeWebSocket.instances.push(this);
  }

  addEventListener(name, handler) {
    this.listeners[name] = this.listeners[name] || [];
    this.listeners[name].push(handler);
  }

  emit(name, payload) {
    (this.listeners[name] || []).forEach((handler) => handler(payload));
  }

  close() {
    this.closed = true;
  }
}

FakeWebSocket.instances = [];
global.WebSocket = FakeWebSocket;

const panelSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "agent-panel.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "agent-panel.js" });

const target = new FakeHTMLElement("div");
const cleanup = window.faithAgentPanel.mountPanel(target, {
  agentId: "project-agent",
  displayName: "Project Agent",
});

const socket = FakeWebSocket.instances[0];
assert(socket.url.includes("/ws/agent/project-agent"), "Expected the agent panel to target the agent websocket.");

socket.emit("open", {});
socket.emit("message", { data: JSON.stringify({ type: "status", status: "active", model: "ollama/test" }) });

const thinkingIndicator = findByClass(target, "faith-agent-panel__thinking");
assert(thinkingIndicator, "Expected the agent panel to render a thinking indicator.");
assert(thinkingIndicator.hidden === false, "Expected active status to show the thinking indicator.");
assert(
  thinkingIndicator.textContent.includes("Project Agent is thinking"),
  "Expected the thinking indicator to explain that the Project Agent is still working.",
);

socket.emit("message", { data: JSON.stringify({ type: "status", status: "idle", model: "ollama/test" }) });
assert(thinkingIndicator.hidden === true, "Expected idle status to hide the thinking indicator.");

socket.emit("message", { data: JSON.stringify({ type: "status", status: "running", model: "ollama/test" }) });
socket.emit("message", { data: JSON.stringify({ type: "output", text: "hello world" }) });
socket.emit("message", { data: JSON.stringify({ type: "output", text: "streamed ", stream: true }) });
socket.emit("message", { data: JSON.stringify({ type: "output", text: "reply", stream: true }) });
socket.emit("message", { data: JSON.stringify([{ type: "output", text: "batch one" }, { type: "output", text: "batch two" }]) });

const statusBadge = findByClass(target, "faith-agent-panel__status");
const modelLabel = findByClass(target, "faith-agent-panel__model");
assert(statusBadge.textContent === "running", "Expected status updates to change the visible badge.");
assert(modelLabel.textContent === "ollama/test", "Expected model updates to change the visible label.");

socket.emit("message", { data: JSON.stringify({ type: "protocol", text: "compact:task:update" }) });
socket.emit("message", { data: "not-json" });

const terminal = findByClass(target, "faith-agent-panel__fallback-terminal");
assert(terminal.textContent.includes("hello world"), "Expected output frames to render into the terminal.");
assert(terminal.textContent.includes("streamed reply"), "Expected streamed output chunks to append inline.");
assert(terminal.textContent.includes("batch one"), "Expected multi-message frames to render every contained item.");
assert(terminal.textContent.includes("batch two"), "Expected multi-message frames to render every contained item.");
assert(terminal.textContent.includes("compact:task:update"), "Expected protocol frames to render into the terminal.");
assert(terminal.textContent.includes("Malformed agent payload"), "Expected malformed frames to become non-fatal errors.");

const pauseButton = findByText(target, "Pause");
pauseButton.dispatch("click");
socket.emit("message", { data: JSON.stringify({ type: "output", text: "queued while paused" }) });
assert(!terminal.textContent.includes("queued while paused"), "Expected paused panels to queue output.");

const resumeButton = findByText(target, "Resume");
resumeButton.dispatch("click");
assert(terminal.textContent.includes("queued while paused"), "Expected queued output to flush on resume.");

socket.emit("close", {});
assert(FakeWebSocket.instances.length >= 2, "Expected disconnects to trigger a reconnect attempt.");

cleanup();

console.log("agent panel runtime checks passed");
