/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH token usage panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel renders split context/input versus inference/output diagnostics.
 *   - Prove the panel shows context-window percentage and effective-context snapshot identifiers.
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
  createListPanel(config) {
    return {
      mountPanel(target) {
        const summary = config.renderSummary({
          session: {
            context_input_tokens: 123,
            inference_output_tokens: 45,
            total_tokens: 168,
            context_window_percentage: 80,
            effective_context_snapshot_id: "snap-1",
            effective_context_turn_id: "turn-1",
          },
          last_message: {
            context_input_tokens: 12,
            inference_output_tokens: 3,
            total_tokens: 15,
            context_window_percentage: null,
            effective_context_snapshot_id: "snap-1",
            effective_context_turn_id: "turn-1",
          },
          by_model: {},
          by_agent: {},
        });
        const item = config.renderItem({
          agent: "project-agent",
          model: "ollama/llama3:8b",
          session_id: "sess-1",
          task_id: "task-1",
          input_tokens: 123,
          output_tokens: 45,
          context_window_percentage: 80,
          effective_context_snapshot_id: "snap-1",
          effective_context_turn_id: "turn-1",
          estimated_cost: 0.1,
        });
        target.appendChild(summary);
        target.appendChild(item);
        return { destroy() {} };
      },
    };
  },
  renderTokenSummary(summaryPayload) {
    const el = new FakeHTMLElement("div");
    el.textContent = JSON.stringify(summaryPayload);
    return el;
  },
  renderRecordCard(record, rows) {
    const el = new FakeHTMLElement("article");
    el.textContent = JSON.stringify({ record, rows });
    return el;
  },
};

const panelSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "token-usage.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "token-usage.js" });

const target = new FakeHTMLElement("div");
window.faithTokenUsagePanel.mountPanel(target);

const summaryText = target.children[0].textContent;
const itemText = target.children[1].textContent;

assert(summaryText.includes("context_input_tokens"), "Expected context/input summary data.");
assert(summaryText.includes("effective_context_snapshot_id"), "Expected snapshot correlation in summary.");
assert(itemText.includes("Context/input tokens"), "Expected split context/input row.");
assert(itemText.includes("Inference/output tokens"), "Expected split inference/output row.");
assert(itemText.includes("Context window"), "Expected context window row.");
assert(itemText.includes("Effective-context snapshot"), "Expected snapshot link row.");

console.log("token usage panel runtime checks passed");
