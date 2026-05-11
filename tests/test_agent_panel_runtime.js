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
  this.scrollTop = 0;
  this.clientHeight = 120;
  this.scrollHeight = 120;
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

/**
 * Description:
 *   Simulate one fake element scrolling into view.
 *
 * Requirements:
 *   - Let runtime tests prove jump-to-latest behaviour without a browser.
 *
 * @returns {void}
 */
FakeHTMLElement.prototype.scrollIntoView = function scrollIntoView() {
  this.didScrollIntoView = true;
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

function findAllByClass(root, className, matches = []) {
  if ((root.className || "").split(/\s+/).includes(className)) {
    matches.push(root);
  }
  for (const child of root.children || []) {
    findAllByClass(child, className, matches);
  }
  return matches;
}

function collectText(root) {
  let text = root.textContent || "";
  for (const child of root.children || []) {
    text += collectText(child);
  }
  return text;
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
let transcriptMessages = [
  { role: "user", content: "Recovered user message." },
  { role: "assistant", content: "Recovered assistant reply." },
];

global.fetch = async function fetch(url) {
  return {
    ok: true,
    async json() {
      return {
        session_id: "sess-0001-20260502120000",
        messages: transcriptMessages,
      };
    },
    url,
  };
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

async function main() {
  const target = new FakeHTMLElement("div");
  const cleanup = window.faithAgentPanel.mountPanel(target, {
    agentId: "project-agent",
    displayName: "Project Agent",
  });

  assert(typeof window.fetch === "function", "Expected transcript bootstrap to use fetch when available.");
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();

  const socket = FakeWebSocket.instances[0];
  assert(socket.url.includes("/ws/agent/project-agent"), "Expected the agent panel to target the agent websocket.");

  const restoredTerminal = findByClass(target, "faith-agent-panel__fallback-terminal");
  const restoredUserBubble = findByClass(target, "faith-agent-panel__message--user");
  const restoredAssistantMessage = findByClass(target, "faith-agent-panel__message--assistant");
  assert(restoredTerminal, "Expected the panel to render a transcript container.");
  assert(restoredUserBubble, "Expected the saved user transcript to render as a user bubble.");
  assert(
    restoredUserBubble.textContent.includes("Recovered user message."),
    "Expected the user bubble to include the saved user transcript text.",
  );
  assert(
    restoredAssistantMessage,
    "Expected the saved assistant transcript to render as an assistant message block.",
  );
  assert(
    restoredAssistantMessage.textContent.includes("Recovered assistant reply."),
    "Expected the assistant message block to include the saved assistant transcript text.",
  );
  assert(
    !collectText(restoredTerminal).includes("User: Recovered user message."),
    "Expected saved transcript rendering to omit the literal 'User:' prefix.",
  );
  assert(
    !collectText(restoredTerminal).includes("PA: Recovered assistant reply."),
    "Expected saved transcript rendering to omit the literal 'PA:' prefix.",
  );

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
  socket.emit("message", { data: JSON.stringify({ type: "output", text: "User: show me the files" }) });
  socket.emit("message", { data: JSON.stringify({ type: "output", text: "PA: here is the list" }) });
  socket.emit("message", { data: JSON.stringify([{ type: "output", text: "batch one" }, { type: "output", text: "batch two" }]) });

  const statusBadge = findByClass(target, "faith-agent-panel__status");
  const modelLabel = findByClass(target, "faith-agent-panel__model");
  assert(statusBadge.textContent === "running", "Expected status updates to change the visible badge.");
  assert(modelLabel.textContent === "ollama/test", "Expected model updates to change the visible label.");

  socket.emit("message", { data: JSON.stringify({ type: "protocol", text: "compact:task:update" }) });
  socket.emit("message", { data: "not-json" });

  const terminal = findByClass(target, "faith-agent-panel__fallback-terminal");
  const liveUserBubbles = findAllByClass(target, "faith-agent-panel__message--user");
  assert(collectText(terminal).includes("hello world"), "Expected output frames to render into the terminal.");
  assert(collectText(terminal).includes("streamed reply"), "Expected streamed output chunks to append inline.");
  assert(
    liveUserBubbles.some((bubble) => bubble.textContent.includes("show me the files")),
    "Expected live user output to render inside a user bubble.",
  );
  assert(!collectText(terminal).includes("User: show me the files"), "Expected live user output to omit the literal 'User:' prefix.");
  assert(!collectText(terminal).includes("PA: here is the list"), "Expected live assistant output to omit the literal 'PA:' prefix.");
  assert(collectText(terminal).includes("batch one"), "Expected multi-message frames to render every contained item.");
  assert(collectText(terminal).includes("batch two"), "Expected multi-message frames to render every contained item.");
  assert(collectText(terminal).includes("compact:task:update"), "Expected protocol frames to render into the terminal.");
  assert(collectText(terminal).includes("Malformed agent payload"), "Expected malformed frames to become non-fatal errors.");

  const pauseButton = findByText(target, "Pause");
  pauseButton.dispatch("click");
  socket.emit("message", { data: JSON.stringify({ type: "output", text: "queued while paused" }) });
  assert(!collectText(terminal).includes("queued while paused"), "Expected paused panels to queue output.");

  const resumeButton = findByText(target, "Resume");
  resumeButton.dispatch("click");
  assert(collectText(terminal).includes("queued while paused"), "Expected queued output to flush on resume.");

  const terminalHost = findByClass(target, "faith-agent-panel__terminal");
  assert(terminalHost, "Expected the transcript host element to exist.");
  assert(
    terminalHost.scrollTop === terminalHost.scrollHeight - terminalHost.clientHeight,
    "Expected the transcript to stay pinned to the bottom while new output arrives.",
  );

  terminalHost.scrollTop = 0;
  terminalHost.dispatch("scroll");
  socket.emit("message", { data: JSON.stringify({ type: "output", text: "newest while reading history" }) });
  assert(
    terminalHost.scrollTop === 0,
    "Expected new output not to steal the user's scroll position when they have scrolled upward.",
  );

  const jumpButton = findByText(target, "Jump to latest");
  assert(jumpButton, "Expected the panel to render a jump-to-latest control.");
  assert(jumpButton.hidden === false, "Expected the jump-to-latest control to appear when unread text exists.");
  jumpButton.dispatch("click");
  assert(
    terminalHost.scrollTop === terminalHost.scrollHeight - terminalHost.clientHeight,
    "Expected the jump-to-latest control to scroll to the newest transcript content.",
  );
  assert(jumpButton.hidden === true, "Expected the jump-to-latest control to hide once the transcript is back at the bottom.");

  socket.emit("close", {});
  await Promise.resolve();
  assert(FakeWebSocket.instances.length >= 2, "Expected disconnects to trigger a reconnect attempt.");

  transcriptMessages = transcriptMessages.concat([
    { role: "user", content: "Question sent while websocket was down." },
    { role: "assistant", content: "Answer generated while websocket was down." },
  ]);

  const reconnectingSocket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  reconnectingSocket.emit("error", {});
  await Promise.resolve();
  assert(
    FakeWebSocket.instances.length >= 3,
    "Expected websocket error events to trigger another reconnect attempt.",
  );
  const recoveredSocket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  recoveredSocket.emit("open", {});
  await Promise.resolve();
  await Promise.resolve();
  assert(
    collectText(terminal).includes("Question sent while websocket was down."),
    "Expected reconnect to rehydrate user transcript entries created while the websocket was disconnected.",
  );
  assert(
    collectText(terminal).includes("Answer generated while websocket was down."),
    "Expected reconnect to rehydrate assistant transcript entries created while the websocket was disconnected.",
  );
  recoveredSocket.emit("message", { data: JSON.stringify({ type: "output", text: "after reconnect" }) });
  assert(
    collectText(terminal).includes("after reconnect"),
    "Expected the panel to keep rendering output after a reconnect.",
  );

  cleanup();

  console.log("agent panel runtime checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
