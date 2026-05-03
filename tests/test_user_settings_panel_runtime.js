/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH user-settings panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel can load persisted settings from the server.
 *   - Prove browser timezone suggestion, dirty tracking, save, and reload guards work together.
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

global.Intl = {
  DateTimeFormat() {
    return {
      resolvedOptions() {
        return { timeZone: "Europe/London" };
      },
    };
  },
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
  path.join(process.cwd(), "web", "js", "panels", "user-settings-panel.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "user-settings-panel.js" });

(async function run() {
  queuedResponses.push({
    ok: true,
    status: 200,
    json: {
      display_name: null,
      country_code: "GB",
      preferred_locale: "en-GB",
      timezone: "Europe/London",
      country_options: [
        { value: "GB", label: "United Kingdom" },
        { value: "US", label: "United States" },
      ],
      locale_options: [
        { value: "en-GB", label: "English (United Kingdom)" },
        { value: "en-US", label: "English (United States)" },
      ],
      timezone_options: [
        { value: "Europe/London", label: "Europe/London" },
      ],
      timezone_options_by_country: {
        GB: [{ value: "Europe/London", label: "Europe/London" }],
        US: [
          { value: "America/New_York", label: "America/New_York" },
          { value: "America/Chicago", label: "America/Chicago" },
        ],
      },
      path: ".faith/system.yaml",
      updated_at: null,
    },
  });

  const target = new FakeHTMLElement("div");
  const panel = window.faithUserSettingsPanel.mountPanel(target);
  await flushAsyncTasks();

  const inputNodes = [];
  const selectNodes = [];
  (function collectControls(node) {
    if (node.tagName === "INPUT") {
      inputNodes.push(node);
    }
    if (node.tagName === "SELECT") {
      selectNodes.push(node);
    }
    for (const child of node.children || []) {
      collectControls(child);
    }
  })(target);

  const displayNameInput = inputNodes[0];
  const countrySelect = selectNodes[0];
  const localeSelect = selectNodes[1];
  const timezoneSelect = selectNodes[2];
  const saveButton = findByText(target, "Save");
  const reloadButton = findByText(target, "Reload");
  const detectButton = findByText(target, "Use browser timezone");

  assert(fetchCalls[0].url === "/api/user-settings", "Expected mount to load persisted user settings.");
  assert(countrySelect.value === "GB", "Expected the saved country to preload.");
  assert(localeSelect.value === "en-GB", "Expected the saved locale to preload.");
  assert(timezoneSelect.value === "Europe/London", "Expected the saved timezone to preload.");
  assert(saveButton.disabled === true, "Expected Save to start disabled when nothing is dirty.");
  assert(detectButton.hidden === false, "Expected the browser timezone helper to be visible when detection is available.");
  assert(
    timezoneSelect.children.length === 1 && timezoneSelect.children[0].value === "Europe/London",
    "Expected the initial timezone list to be filtered to the selected country.",
  );

  detectButton.dispatch("click");
  assert(timezoneSelect.value === "Europe/London", "Expected clicking the helper to populate the detected browser timezone.");
  assert(saveButton.disabled === true, "Expected identical browser-timezone values to avoid creating a fake dirty state.");

  displayNameInput.value = "Del";
  displayNameInput.dispatch("input");
  countrySelect.value = "US";
  countrySelect.dispatch("change");
  assert(
    timezoneSelect.children.some((child) => child.value === "America/New_York"),
    "Expected changing country to repopulate timezone choices for that country.",
  );
  localeSelect.value = "en-US";
  localeSelect.dispatch("change");
  timezoneSelect.value = "America/New_York";
  timezoneSelect.dispatch("change");

  queuedResponses.push({
    ok: true,
    status: 200,
    json: {
      display_name: "Del",
      country_code: "US",
      preferred_locale: "en-US",
      timezone: "America/New_York",
      country_options: [
        { value: "GB", label: "United Kingdom" },
        { value: "US", label: "United States" },
      ],
      locale_options: [
        { value: "en-GB", label: "English (United Kingdom)" },
        { value: "en-US", label: "English (United States)" },
      ],
      timezone_options: [
        { value: "America/New_York", label: "America/New_York" },
        { value: "America/Chicago", label: "America/Chicago" },
      ],
      timezone_options_by_country: {
        GB: [{ value: "Europe/London", label: "Europe/London" }],
        US: [
          { value: "America/New_York", label: "America/New_York" },
          { value: "America/Chicago", label: "America/Chicago" },
        ],
      },
      path: ".faith/system.yaml",
      updated_at: "2026-05-02T10:15:00+00:00",
    },
  });
  saveButton.dispatch("click");
  await flushAsyncTasks();

  assert(fetchCalls[1].url === "/api/user-settings", "Expected Save to call the user-settings endpoint.");
  assert(fetchCalls[1].options.method === "PUT", "Expected Save to use PUT.");
  assert(
    JSON.parse(fetchCalls[1].options.body).timezone === "America/New_York",
    "Expected Save to submit the edited timezone.",
  );
  assert(
    JSON.parse(fetchCalls[1].options.body).country_code === "US",
    "Expected Save to submit the selected country.",
  );
  assert(
    JSON.parse(fetchCalls[1].options.body).preferred_locale === "en-US",
    "Expected Save to submit the selected locale.",
  );
  assert(panel.getState().unsaved === false, "Expected a successful save to clear dirty state.");
  assert(
    dispatchedEvents.some((event) => event.type === "faith:user-settings-updated"),
    "Expected successful settings changes to publish a UI notification event.",
  );

  timezoneSelect.value = "America/Chicago";
  timezoneSelect.dispatch("change");
  confirmResponses.push(false);
  reloadButton.dispatch("click");
  await flushAsyncTasks();
  assert(fetchCalls.length === 2, "Expected cancelled reload to avoid another GET.");
  assert(timezoneSelect.value === "America/Chicago", "Expected cancelled reload to preserve local edits.");

  queuedResponses.push({
    ok: true,
    status: 200,
    json: {
      display_name: "Del",
      country_code: "GB",
      preferred_locale: "en-GB",
      timezone: "Europe/London",
      country_options: [
        { value: "GB", label: "United Kingdom" },
        { value: "US", label: "United States" },
      ],
      locale_options: [
        { value: "en-GB", label: "English (United Kingdom)" },
        { value: "en-US", label: "English (United States)" },
      ],
      timezone_options: [
        { value: "Europe/London", label: "Europe/London" },
      ],
      timezone_options_by_country: {
        GB: [{ value: "Europe/London", label: "Europe/London" }],
        US: [
          { value: "America/New_York", label: "America/New_York" },
          { value: "America/Chicago", label: "America/Chicago" },
        ],
      },
      path: ".faith/system.yaml",
      updated_at: "2026-05-02T10:15:00+00:00",
    },
  });
  confirmResponses.push(true);
  reloadButton.dispatch("click");
  await flushAsyncTasks();
  assert(timezoneSelect.value === "Europe/London", "Expected confirmed reload to restore persisted settings.");
  assert(countrySelect.value === "GB", "Expected confirmed reload to restore the persisted country.");

  panel.destroy();

  console.log("user settings panel runtime checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
