/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH model-settings panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel can load persisted model settings from the server.
 *   - Prove edit, save, and reload guards work together for PA model and per-agent override updates.
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

function collectByTag(root, tagName, matches = []) {
  if (root.tagName === tagName.toUpperCase()) {
    matches.push(root);
  }
  for (const child of root.children || []) {
    collectByTag(child, tagName, matches);
  }
  return matches;
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

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

const confirmResponses = [];
global.confirm = function confirm() {
  return confirmResponses.length > 0 ? confirmResponses.shift() : true;
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
  path.join(process.cwd(), "web", "js", "panels", "model-settings-panel.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "model-settings-panel.js" });

(async function run() {
  queuedResponses.push({
    ok: true,
    status: 200,
    json: {
      pa_model: "ollama/llama3:8b",
      default_agent_model: "openrouter/openai/gpt-4o",
      system_path: ".faith/system.yaml",
      catalog_path: "data/pa-runtime/model-catalog.json",
      updated_at: "2026-05-18T12:00:00+00:00",
      model_options: [
        { value: "ollama/llama3:8b", label: "ollama/llama3:8b" },
        { value: "openrouter/openai/gpt-4o", label: "openrouter/openai/gpt-4o" },
        { value: "ollama/mistral:7b", label: "ollama/mistral:7b" },
      ],
      catalog: [
        {
          key: "ollama/llama3:8b",
          provider: "ollama",
          model: "llama3:8b",
          context_window: { value: 8192, provenance: "discovered" },
          runtime: { safe_usable_context: 7168, warning: "usable_context_limited_by_vram" },
        },
      ],
      agent_overrides: [
        {
          agent_id: "researcher",
          role: "Researcher",
          model: "ollama/mistral:7b",
          path: ".faith/agents/researcher/config.yaml",
        },
      ],
    },
  });

  const target = new FakeHTMLElement("div");
  const panel = window.faithModelSettingsPanel.mountPanel(target);
  await flushAsyncTasks();

  const selects = collectByTag(target, "select");
  const inputs = collectByTag(target, "input");
  const paModelSelect = selects[0];
  const defaultAgentModelSelect = selects[1];
  const agentOverrideSelect = selects[2];
  const contextWindowInput = inputs[0];
  const saveButton = findByText(target, "Save");
  const reloadButton = findByText(target, "Reload");

  assert(fetchCalls[0].url === "/api/model-settings", "Expected mount to load persisted model settings.");
  assert(paModelSelect.value === "ollama/llama3:8b", "Expected the saved PA model to preload.");
  assert(defaultAgentModelSelect.value === "openrouter/openai/gpt-4o", "Expected the default agent model to preload.");
  assert(agentOverrideSelect.value === "ollama/mistral:7b", "Expected the saved per-agent override to preload.");
  assert(contextWindowInput.value === "8192", "Expected the catalog context window to preload.");
  assert(saveButton.disabled === true, "Expected Save to start disabled when nothing is dirty.");

  paModelSelect.value = "openrouter/openai/gpt-4o";
  paModelSelect.dispatch("change");
  contextWindowInput.value = "200000";
  contextWindowInput.dispatch("input");

  queuedResponses.push({
    ok: true,
    status: 200,
    json: {
      pa_model: "openrouter/openai/gpt-4o",
      default_agent_model: "openrouter/openai/gpt-4o",
      system_path: ".faith/system.yaml",
      catalog_path: "data/pa-runtime/model-catalog.json",
      updated_at: "2026-05-18T12:05:00+00:00",
      model_options: [
        { value: "ollama/llama3:8b", label: "ollama/llama3:8b" },
        { value: "openrouter/openai/gpt-4o", label: "openrouter/openai/gpt-4o" },
        { value: "ollama/mistral:7b", label: "ollama/mistral:7b" },
      ],
      catalog: [
        {
          key: "openrouter/openai/gpt-4o",
          provider: "openrouter",
          model: "openai/gpt-4o",
          context_window: { value: 200000, provenance: "user_override" },
          runtime: {},
        },
      ],
      agent_overrides: [
        {
          agent_id: "researcher",
          role: "Researcher",
          model: "ollama/mistral:7b",
          path: ".faith/agents/researcher/config.yaml",
        },
      ],
    },
  });
  saveButton.dispatch("click");
  await flushAsyncTasks();

  assert(fetchCalls[1].url === "/api/model-settings", "Expected Save to call the model-settings endpoint.");
  assert(fetchCalls[1].options.method === "PUT", "Expected Save to use PUT.");
  assert(
    JSON.parse(fetchCalls[1].options.body).context_window_overrides["openrouter/openai/gpt-4o"] === 200000,
    "Expected Save to submit the edited context-window override.",
  );
  assert(panel.getState().unsaved === false, "Expected a successful save to clear dirty state.");

  paModelSelect.value = "ollama/llama3:8b";
  paModelSelect.dispatch("change");
  confirmResponses.push(false);
  reloadButton.dispatch("click");
  await flushAsyncTasks();
  assert(fetchCalls.length === 2, "Expected cancelled reload to avoid another GET.");
  assert(paModelSelect.value === "ollama/llama3:8b", "Expected cancelled reload to preserve local edits.");

  queuedResponses.push({
    ok: true,
    status: 200,
    json: {
      pa_model: "openrouter/openai/gpt-4o",
      default_agent_model: "openrouter/openai/gpt-4o",
      system_path: ".faith/system.yaml",
      catalog_path: "data/pa-runtime/model-catalog.json",
      updated_at: "2026-05-18T12:05:00+00:00",
      model_options: [
        { value: "ollama/llama3:8b", label: "ollama/llama3:8b" },
        { value: "openrouter/openai/gpt-4o", label: "openrouter/openai/gpt-4o" },
        { value: "ollama/mistral:7b", label: "ollama/mistral:7b" },
      ],
      catalog: [
        {
          key: "openrouter/openai/gpt-4o",
          provider: "openrouter",
          model: "openai/gpt-4o",
          context_window: { value: 200000, provenance: "user_override" },
          runtime: {},
        },
      ],
      agent_overrides: [
        {
          agent_id: "researcher",
          role: "Researcher",
          model: "ollama/mistral:7b",
          path: ".faith/agents/researcher/config.yaml",
        },
      ],
    },
  });
  confirmResponses.push(true);
  reloadButton.dispatch("click");
  await flushAsyncTasks();
  assert(paModelSelect.value === "openrouter/openai/gpt-4o", "Expected confirmed reload to restore persisted settings.");

  panel.destroy();

  console.log("model settings panel runtime checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
