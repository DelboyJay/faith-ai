/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH Storage panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel renders a searchable, sortable inventory table.
 *   - Prove inline scope changes and per-row delete affordances reach the expected backend hooks.
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
  this.checked = false;
  this.disabled = false;
  this.placeholder = "";
  this.className = "";
  this.files = [];
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

function collectRows(root) {
  const rows = [];
  if (root.tagName === "TR") {
    rows.push(root);
  }
  for (const child of root.children || []) {
    rows.push(...collectRows(child));
  }
  return rows;
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
global.FormData = function FormData() {
  this.entries = [];
};
global.FormData.prototype.append = function append(name, value) {
  this.entries.push([name, value]);
};

const fetchCalls = [];
let inventoryPayload = {
  items: [
    {
      file_id: "file-alpha",
      filename: "alpha.md",
      description: "Alpha notes",
      sha256: "aaa111",
      scope: "session",
      binding: "sess-0001",
      actions: {
        scope: "/api/storage/files/file-alpha/scope",
        delete: "/api/storage/files/file-alpha",
      },
    },
    {
      file_id: "file-beta",
      filename: "beta.txt",
      description: "Beta notes",
      sha256: "bbb222",
      scope: "global",
      binding: "global",
      actions: {
        scope: "/api/storage/files/file-beta/scope",
        delete: "/api/storage/files/file-beta",
      },
    },
  ],
};

global.fetch = async function fetch(url, options = {}) {
  fetchCalls.push({ url, options });
  if (url === "/api/storage/files") {
    if ((options.method || "GET").toUpperCase() === "POST") {
      return {
        ok: true,
        async json() {
          return {
            file_id: "upload-gamma",
            filename: "gamma.txt",
            description: "",
            sha256: "ccc333",
            scope: "global",
            session_bindings: [],
            path: "E:/tmp/gamma.txt",
            size_bytes: 17,
            created_at: "2026-05-19T10:00:00Z",
            updated_at: "2026-05-19T10:00:00Z",
            trashed_at: null,
            inference_id: null,
          };
        },
      };
    }
    return {
      ok: true,
      async json() {
        return inventoryPayload;
      },
    };
  }
  if (url === "/api/storage/files/file-alpha/scope") {
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  }
  if (url === "/api/storage/files/file-alpha") {
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  }
  if (url === "/api/storage/files/file-beta/scope") {
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  }
  if (url === "/api/storage/files/file-beta") {
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
  const panel = window.faithStoragePanel.mountPanel(target);

  await Promise.resolve();
  await Promise.resolve();

  assert(findByText(target, "Storage Inventory"), "Expected the Storage panel to render its title.");
  assert(findByPlaceholder(target, "Search filename or description"), "Expected the Storage panel to expose a search input.");
  assert(findByText(target, "Filename"), "Expected the Storage panel to render sortable table headers.");
  assert(findByText(target, "Delete selected"), "Expected the Storage panel to expose a bulk delete action hook.");
  assert(findByText(target, "Export selected"), "Expected the Storage panel to expose a bulk export action hook.");
  assert(findByText(target, "Drop files here"), "Expected the Storage panel to expose a visible upload drop zone.");

  let rows = collectRows(target).filter((row) => (row.className || "").includes("faith-storage-panel__row"));
  assert(rows.length === 2, "Expected the Storage panel to render both inventory rows.");
  assert(collectText(rows[0]).includes("alpha.md"), "Expected the Storage panel to start sorted by filename ascending.");
  findByText(target, "Filename").dispatch("click");
  rows = collectRows(target).filter((row) => (row.className || "").includes("faith-storage-panel__row"));
  assert(collectText(rows[0]).includes("beta.txt"), "Expected clicking the Filename header to reverse the sort order.");

  const scopeSelect = findByTag(target, "select");
  assert(scopeSelect, "Expected the Storage panel to expose an inline scope dropdown.");
  scopeSelect.value = "session";
  scopeSelect.dispatch("change", {
    target: scopeSelect,
  });

  const searchInput = findByPlaceholder(target, "Search filename or description");
  searchInput.value = "beta";
  searchInput.dispatch("input", {
    target: searchInput,
  });

  assert(collectText(target).includes("beta.txt"), "Expected the Storage panel to filter the table by search text.");
  assert(!collectText(target).includes("alpha.md"), "Expected the Storage panel to hide non-matching rows during search.");

  rows = collectRows(target).filter((row) => (row.className || "").includes("faith-storage-panel__row"));
  assert(rows.length === 1, "Expected search filtering to leave one visible storage row.");

  const uploadInput = (function findUploadInput(root) {
    if (root.tagName === "INPUT" && root.type === "file") {
      return root;
    }
    for (const child of root.children || []) {
      const match = findUploadInput(child);
      if (match) {
        return match;
      }
    }
    return null;
  })(target);
  assert(uploadInput, "Expected the Storage panel to include a file-input uploader.");
  uploadInput.files = [{ name: "gamma.txt", type: "text/plain" }];
  uploadInput.dispatch("change", { target: uploadInput });
  await Promise.resolve();
  await Promise.resolve();

  assert(
    fetchCalls.some(
      (call) =>
        call.url === "/api/storage/files" &&
        (call.options.method || "GET").toUpperCase() === "POST",
    ),
    "Expected the Storage panel uploader to post files to the storage endpoint.",
  );

  const deleteButton = findByText(target, "Delete");
  deleteButton.dispatch("click");
  await Promise.resolve();
  await Promise.resolve();

  assert(
    fetchCalls.some((call) => call.url === "/api/storage/files/file-beta"),
    "Expected per-row delete to call the file delete endpoint.",
  );

  panel.destroy();
  console.log("storage panel runtime checks passed");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
