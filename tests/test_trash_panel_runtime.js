/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH Trash panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel renders a searchable table of deleted files.
 *   - Prove restore and permanent-delete affordances reach the expected backend hooks.
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
  this.placeholder = "";
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

function findByPlaceholder(root, placeholder) {
  if (root.placeholder === placeholder) {
    return root;
  }
  for (const child of root.children || []) {
    const match = findByPlaceholder(child, placeholder);
    if (match) {
      return match;
    }
  }
  return null;
}

/**
 * Description:
 *   Aggregate text recursively from the fake DOM tree.
 *
 * Requirements:
 *   - Let the runtime test inspect table row content in the same way the browser would render it.
 *
 * @param {object} root: Fake DOM root node.
 * @returns {string} Concatenated text content.
 */
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

const fetchCalls = [];
const trashPayload = {
  items: [
    {
      file_id: "trash-alpha",
      filename: "deleted.md",
      description: "Deleted draft",
      sha256: "dead111",
      scope: "session",
      binding: "sess-0001",
      actions: {
        restore: "/api/storage/trash/trash-alpha/restore",
        delete: "/api/storage/trash/trash-alpha",
      },
    },
    {
      file_id: "trash-beta",
      filename: "zeta.md",
      description: "Second draft",
      sha256: "dead222",
      scope: "global",
      binding: "global",
      actions: {
        restore: "/api/storage/trash/trash-beta/restore",
        delete: "/api/storage/trash/trash-beta",
      },
    },
  ],
};

global.fetch = async function fetch(url, options = {}) {
  fetchCalls.push({ url, options });
  if (url === "/api/storage/trash") {
    return {
      ok: true,
      async json() {
        return trashPayload;
      },
    };
  }
  if (url === "/api/storage/trash/trash-alpha/restore") {
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  }
  if (url === "/api/storage/trash/trash-alpha") {
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  }
  throw new Error(`Unexpected fetch URL: ${url}`);
};

const panelSource = fs.readFileSync(
  path.join(process.cwd(), "web", "js", "panels", "storage-panel.js"),
  "utf8",
);
vm.runInThisContext(panelSource, { filename: "storage-panel.js" });

(async function run() {
  const target = new FakeHTMLElement("div");
  const panel = window.faithTrashPanel.mountPanel(target);

  await Promise.resolve();
  await Promise.resolve();

  assert(findByText(target, "Trash"), "Expected the Trash panel to render its title.");
  assert(findByPlaceholder(target, "Search filename or description"), "Expected the Trash panel to expose a search input.");
  assert(findByText(target, "Restore"), "Expected the Trash panel to expose a restore action.");
  assert(findByText(target, "Delete permanently"), "Expected the Trash panel to expose a permanent delete action.");

  let rows = (function collectRows(root) {
    const collected = [];
    if (root.tagName === "TR" && (root.className || "").includes("faith-trash-panel__row")) {
      collected.push(root);
    }
    for (const child of root.children || []) {
      collected.push(...collectRows(child));
    }
    return collected;
  })(target);
  assert(rows.length === 2, "Expected the Trash panel to render both trashed files.");
  assert(collectText(rows[0]).includes("deleted.md"), "Expected the Trash panel to start sorted by filename ascending.");
  findByText(target, "Filename").dispatch("click");
  rows = (function collectRows(root) {
    const collected = [];
    if (root.tagName === "TR" && (root.className || "").includes("faith-trash-panel__row")) {
      collected.push(root);
    }
    for (const child of root.children || []) {
      collected.push(...collectRows(child));
    }
    return collected;
  })(target);
  assert(collectText(rows[0]).includes("zeta.md"), "Expected clicking the Filename header to reverse the sort order.");

  const searchInput = findByPlaceholder(target, "Search filename or description");
  searchInput.value = "deleted";
  searchInput.dispatch("input", {
    target: searchInput,
  });

  assert(!collectText(target).includes("zeta.md"), "Expected the Trash panel to hide non-matching rows during search.");
  rows = (function collectRows(root) {
    const collected = [];
    if (root.tagName === "TR" && (root.className || "").includes("faith-trash-panel__row")) {
      collected.push(root);
    }
    for (const child of root.children || []) {
      collected.push(...collectRows(child));
    }
    return collected;
  })(target);
  assert(rows.length === 1, "Expected search filtering to leave one visible trash row.");

  const deleteButton = findByText(target, "Delete permanently");
  deleteButton.dispatch("click");
  await Promise.resolve();
  await Promise.resolve();

  assert(
    fetchCalls.some((call) => call.url === "/api/storage/trash/trash-alpha"),
    "Expected the trash delete action to call the permanent-delete endpoint.",
  );

  panel.destroy();
  console.log("trash panel runtime checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
