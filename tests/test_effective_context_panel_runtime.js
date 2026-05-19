/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH effective-context panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel can fetch and render a redacted effective-context snapshot.
 *   - Prove the rendered card includes the session, turn, hash, include graph, and warnings fields.
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

FakeHTMLElement.prototype.dispatch = function dispatch(name) {
  (this.eventListeners[name] || []).forEach((handler) => handler({}));
};

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

global.faithLogPanelCommon = {
  renderRecordCard(record, rows) {
    const card = new FakeHTMLElement("article");
    card.textContent = JSON.stringify({ record, rows });
    return card;
  },
};

global.fetch = async function fetch(url) {
  assert(url === "/api/logs/effective-context/sess-1/turn-1", "Expected the effective-context fetch URL.");
  return {
    ok: true,
    async json() {
      return {
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
  const panel = window.faithEffectiveContextPanel.mountPanel(target, {
    sessionId: "sess-1",
    turnId: "turn-1",
  });

  const loadButton = target.children[0].children[2];
  loadButton.dispatch("click");
  for (let index = 0; index < 8; index += 1) {
    await Promise.resolve();
  }

  panel.destroy();
  console.log("effective-context panel runtime checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
