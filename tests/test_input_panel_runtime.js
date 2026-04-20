/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH input panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel can send text, queue attachments, and remove attachments.
 *   - Prove duplicate send protection and validation state do not break the panel.
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
  this.type = "";
  this.accept = "";
  this.multiple = false;
  this.files = [];
  this.className = "";
  this.eventListeners = {};
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

FakeHTMLElement.prototype.click = function click() {
  this.dispatch("click");
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
}

global.HTMLElement = FakeHTMLElement;
global.document = {
  createElement(tagName) {
    return new FakeHTMLElement(tagName);
  },
};
global.window = global;

class FakeFormData {
  constructor() {
    this.items = [];
  }
  append(key, value) {
    this.items.push([key, value]);
  }
}

global.FormData = FakeFormData;

const fetchCalls = [];
let pendingFetchResolvers = [];
global.fetch = async function fetch(url, options = {}) {
  fetchCalls.push({ url, options });
  if (options.headers && options.headers["X-Pending-Test"] === "true") {
    await new Promise((resolve) => pendingFetchResolvers.push(resolve));
  }
  return {
    ok: true,
    status: 200,
    async json() {
      return { status: "sent" };
    },
  };
};

const panelSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "input-panel.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "input-panel.js" });

(async function run() {
  const target = new FakeHTMLElement("div");
  const panel = window.faithInputPanel.mountPanel(target);
  const textarea = findByTag(target, "textarea");
  const wrapper = target.children[0];
  const sendButton = findByText(target, "Send");

  assert(sendButton.disabled === true, "Expected Send to start disabled while the message is empty.");
  textarea.value = "click send from browser";
  textarea.dispatch("input");
  assert(sendButton.disabled === false, "Expected typing in the textarea to enable Send.");
  sendButton.click();
  await flushAsyncTasks();
  assert(fetchCalls[0].url === "/input", "Expected clicking the enabled Send button to post to /input.");

  textarea.value = "hello from user";
  await panel.submit();
  assert(fetchCalls[1].url === "/input", "Expected text messages to post to /input.");

  const file = { name: "note.txt", type: "text/plain", size: 10 };
  const queued = panel.queueAttachments([file]);
  assert(queued === true, "Expected a valid attachment to be queued.");
  assert(panel.getState().attachmentCount === 1, "Expected attachment count to increase after queueing.");

  const removeButton = findByText(target, "Remove");
  removeButton.dispatch("click");
  assert(panel.getState().attachmentCount === 0, "Expected queued attachments to be removable.");

  panel.queueAttachments([file]);
  await panel.submit();
  assert(fetchCalls[2].url === "/upload", "Expected attachment sends to post to /upload.");

  textarea.dispatch("paste", {
    preventDefault() {},
    clipboardData: {
      items: [
        {
          kind: "file",
          type: "image/png",
          getAsFile() {
            return { name: "clip.png", type: "image/png", size: 10 };
          },
        },
      ],
    },
  });
  assert(panel.getState().attachmentCount === 1, "Expected clipboard image paste to queue an attachment.");

  wrapper.dispatch("drop", {
    preventDefault() {},
    dataTransfer: {
      files: [{ name: "drop.txt", type: "text/plain", size: 10 }],
    },
  });
  assert(panel.getState().attachmentCount === 2, "Expected drag-and-drop to queue attachments.");

  const invalid = panel.queueAttachments([{ name: "bad.exe", type: "application/octet-stream", size: 5 }]);
  assert(invalid === false, "Expected invalid attachments to be rejected.");
  assert(panel.getState().error.includes("Unsupported"), "Expected invalid attachment errors to be visible.");

  global.fetch = async function pendingFetch(url, options = {}) {
    fetchCalls.push({ url, options });
    await new Promise((resolve) => pendingFetchResolvers.push(resolve));
    return {
      ok: true,
      status: 200,
      async json() {
        return { status: "sent" };
      },
    };
  };
  while (panel.getState().attachmentCount > 0) {
    findByText(target, "Remove").dispatch("click");
  }
  textarea.value = "pending send";
  const firstSubmit = panel.submit();
  const secondSubmit = panel.submit();
  assert(fetchCalls.filter((call) => call.url === "/input").length === 3, "Expected only one additional input request while pending.");
  pendingFetchResolvers.splice(0).forEach((resolve) => resolve());
  await Promise.all([firstSubmit, secondSubmit]);

  console.log("input panel runtime checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
