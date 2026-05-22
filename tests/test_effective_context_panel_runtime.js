/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH effective-context panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel follows the currently selected session automatically.
 *   - Prove the panel no longer asks the user for raw session and turn identifiers in the default view.
 */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function FakeHTMLElement(tagName) {
  this.tagName = tagName.toUpperCase();
  this.children = [];
  this.textContent = "";
  this.value = "";
  this.hidden = false;
  this.className = "";
  this.dataset = {};
  this.eventListeners = {};
  this.attributes = {};
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

FakeHTMLElement.prototype.getAttribute = function getAttribute(name) {
  return this.attributes[name];
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
global._listeners = {};
global.addEventListener = function addEventListener(name, handler) {
  global._listeners[name] = global._listeners[name] || [];
  global._listeners[name].push(handler);
};
global.removeEventListener = function removeEventListener(name, handler) {
  global._listeners[name] = (global._listeners[name] || []).filter((entry) => entry !== handler);
};
global.dispatchEvent = function dispatchEvent(event) {
  (global._listeners[event.type] || []).forEach((handler) => handler(event));
};
global.CustomEvent = function CustomEvent(type, init = {}) {
  this.type = type;
  this.detail = init.detail;
};

global.faithLogPanelCommon = {
  renderRecordCard(record, rows) {
    const card = new FakeHTMLElement("article");
    card.textContent = JSON.stringify({ record, rows });
    return card;
  },
};

let fetchedLatestSnapshot = false;
global.fetch = async function fetch(url) {
  assert(
    url === "/api/logs/effective-context/sess-1/latest",
    "Expected the effective-context panel to fetch the latest snapshot for the selected session.",
  );
  fetchedLatestSnapshot = true;
  return {
    ok: true,
    async json() {
      return {
        session_name: "Initial Session",
        session_id: "sess-1",
        turn_id: "turn-1",
        snapshot_id: "snap-1",
        hash: "abc123",
        compiled_context: "redacted context",
        include_graph: [{ path: "AGENTS.md", tokens: 42 }],
        warnings: ["missing include target"],
      };
    },
  };
};

const panelSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "effective-context-panel.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "effective-context-panel.js" });

(async function run() {
  const target = new FakeHTMLElement("div");
  const panel = window.faithEffectiveContextPanel.mountPanel(target, {});
  const wrapper = target.children[0];
  assert(
    !JSON.stringify(wrapper).includes("Session ID") && !JSON.stringify(wrapper).includes("Turn ID"),
    "Expected the default Effective Context panel to hide raw session and turn inputs.",
  );
  window.dispatchEvent(
    new window.CustomEvent("faith:session-selected", {
      detail: {
        sessionId: "sess-1",
        sessionName: "Initial Session",
      },
    }),
  );
  const loadButton = findByText(target, "Load Snapshot");
  if (loadButton) {
    loadButton.dispatch("click");
  }
  for (let index = 0; index < 12; index += 1) {
    await Promise.resolve();
  }
  assert(
    fetchedLatestSnapshot,
    "Expected the panel to request the latest snapshot for the selected session.",
  );

  panel.destroy();
  console.log("effective-context panel runtime checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
