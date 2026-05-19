/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH Session History panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel refreshes session summaries and can start a new session from the toolbar.
 *   - Prove the panel keeps the latest session selected after a successful new-session action.
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
  createElement(tagName) {
    return new FakeHTMLElement(tagName);
  },
};
global.window = global;
global.setInterval = () => 1;
global.clearInterval = () => {};

const fetchCalls = [];
let sessionListPayload = {
  items: [
    {
      session_id: "sess-0001-20260511140000",
      status: "active",
      started_at: "2026-05-11T14:00:00Z",
    },
  ],
};
let sessionDetailPayload = {
  session: {
    session_id: "sess-0002-20260511143000",
    status: "active",
    started_at: "2026-05-11T14:30:00Z",
    trigger: "web-ui",
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
  if (url === "/api/pa/session/new") {
    sessionListPayload = {
      items: [
        {
          session_id: "sess-0002-20260511143000",
          status: "active",
          started_at: "2026-05-11T14:30:00Z",
        },
        ...sessionListPayload.items,
      ],
    };
    return {
      ok: true,
      async json() {
        return {
          session_id: "sess-0002-20260511143000",
          previous_session_id: "sess-0001-20260511140000",
          status: "active",
          started_at: "2026-05-11T14:30:00Z",
          task_count: 0,
        };
      },
    };
  }
  if (url === "/api/logs/sessions/sess-0002-20260511143000") {
    return {
      ok: true,
      async json() {
        return sessionDetailPayload;
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

const panelSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "session-history.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "session-history.js" });

async function main() {
  const target = new FakeHTMLElement("div");
  const panel = window.faithSessionHistoryPanel.mountPanel(target);

  await Promise.resolve();
  await Promise.resolve();

  assert(
    collectText(target).includes("sess-0001-20260511140000"),
    "Expected the panel to render the initial session summary list.",
  );

  const newSessionButton = findByText(target, "New Session");
  assert(newSessionButton, "Expected the Session History panel to expose a New Session button.");

  newSessionButton.dispatch("click");
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();

  assert(
    fetchCalls.some(
      (call) =>
        call.url === "/api/pa/session/new" &&
        (call.options.method || "GET").toUpperCase() === "POST",
    ),
    "Expected the New Session button to POST to the same-origin session-start endpoint.",
  );
  assert(
    fetchCalls.some((call) => call.url.startsWith("/api/logs/sessions/sess-0002-20260511143000")),
    "Expected the panel to load the new session detail after creating it.",
  );
  assert(
    collectText(target).includes("sess-0002-20260511143000"),
    "Expected the refreshed session list to include the newly created session.",
  );

  panel.destroy();
  console.log("session history panel runtime checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
