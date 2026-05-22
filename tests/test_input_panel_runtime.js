/**
 * Description:
 *   Execute a tiny host-side runtime check against the FAITH input panel JavaScript.
 *
 * Requirements:
 *   - Prove the panel can send text, queue attachments, and remove attachments.
 *   - Prove duplicate send protection, validation state, and attachment scope intent do not break the panel.
 *   - Prove a hard-compaction event can block and then restore submission state.
 *   - Prove dictation requests microphone access only after an explicit click and inserts transcripts into the textarea.
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
    toggle: (name, force) => {
      if (force === undefined ? !this.classList.values.has(name) : Boolean(force)) {
        this.classList.values.add(name);
        return true;
      }
      this.classList.values.delete(name);
      return false;
    },
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

/**
 * Description:
 *   Track one fake audio stream used by the dictation runtime test.
 *
 * Requirements:
 *   - Let the panel shut microphone tracks down after dictation ends.
 */
function FakeMediaStream() {
  this.tracks = [
    {
      stopped: false,
      stop() {
        this.stopped = true;
      },
    },
  ];
}

FakeMediaStream.prototype.getTracks = function getTracks() {
  return this.tracks;
};

/**
 * Description:
 *   Provide a tiny MediaRecorder stand-in for the dictation runtime test.
 *
 * Requirements:
 *   - Allow the panel to start and stop audio capture without a real browser microphone.
 */
function FakeMediaRecorder(stream) {
  this.stream = stream;
  this.state = "inactive";
  this.mimeType = "audio/webm";
  this.eventListeners = {};
  this.ondataavailable = null;
  this.onstop = null;
}

FakeMediaRecorder.prototype.addEventListener = function addEventListener(name, handler) {
  this.eventListeners[name] = this.eventListeners[name] || [];
  this.eventListeners[name].push(handler);
};

FakeMediaRecorder.prototype.start = function start() {
  this.state = "recording";
};

FakeMediaRecorder.prototype.stop = function stop() {
  this.state = "inactive";
  const chunk = { size: 7, type: this.mimeType, text: "audio-chunk" };
  const event = { data: chunk };
  if (typeof this.ondataavailable === "function") {
    this.ondataavailable(event);
  }
  (this.eventListeners.dataavailable || []).forEach((handler) => handler(event));
  if (typeof this.onstop === "function") {
    this.onstop();
  }
  (this.eventListeners.stop || []).forEach((handler) => handler());
};

/**
 * Description:
 *   Provide a tiny Blob stand-in for the dictation runtime test.
 *
 * Requirements:
 *   - Let the panel bundle recorded chunks into a binary payload without depending on real browser Blob support.
 */
function FakeBlob(parts, options = {}) {
  this.parts = parts;
  this.type = options.type || "";
}

/**
 * Description:
 *   Locate the first element with the supplied tag name in the fake DOM tree.
 *
 * Requirements:
 *   - Let the runtime test inspect the newly added scope selector.
 *
 * @param {object} root: Fake DOM root node.
 * @param {string} tagName: Tag name to find.
 * @returns {object|null} Matching fake element when present.
 */
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

const eventListeners = {};
global.addEventListener = function addEventListener(name, handler) {
  eventListeners[name] = eventListeners[name] || [];
  eventListeners[name].push(handler);
};
global.removeEventListener = function removeEventListener(name, handler) {
  eventListeners[name] = (eventListeners[name] || []).filter((candidate) => candidate !== handler);
};
global.dispatchEvent = function dispatchEvent(event) {
  (eventListeners[event.type] || []).forEach((handler) => handler(event));
};
global.CustomEvent = function CustomEvent(type, options = {}) {
  this.type = type;
  this.detail = options.detail;
};

class FakeFormData {
  constructor() {
    this.items = [];
  }
  append(key, value) {
    this.items.push([key, value]);
  }
}

global.FormData = FakeFormData;
global.Blob = FakeBlob;

const getUserMediaCalls = [];
Object.defineProperty(global, "navigator", {
  configurable: true,
  value: {
    mediaDevices: {
      async getUserMedia(constraints) {
        getUserMediaCalls.push(constraints);
        return new FakeMediaStream();
      },
    },
  },
});
global.MediaRecorder = FakeMediaRecorder;

const fetchCalls = [];
let pendingFetchResolvers = [];
let dictationResponseMode = "success";
global.fetch = async function fetch(url, options = {}) {
  fetchCalls.push({ url, options });
  if (url === "/api/dictation/transcribe") {
    if (dictationResponseMode === "failure") {
      return {
        ok: false,
        status: 503,
        async json() {
          return { detail: "Local transcription unavailable." };
        },
        async text() {
          return "Local transcription unavailable.";
        },
      };
    }
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          status: "transcribed",
          transcript: "captured transcript",
          engine: "local",
        };
      },
    };
  }
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
  const scopeSelect = findByTag(target, "select");
  const wrapper = target.children[0];
  const sendButton = findByText(target, "Send");
  const dictationButton = findByText(target, "Dictate");
  const helperText = findByText(target, "Enter to send. Alt+Enter for a newline.");
  const compactionBanner = wrapper.children[4];
  const compactionDiagnostic = findByText(target, "Compaction is temporarily pausing new sends.");

  assert(sendButton.disabled === true, "Expected Send to start disabled while the message is empty.");
  assert(helperText, "Expected the input panel to render the keyboard shortcut helper text.");
  assert(scopeSelect, "Expected the input panel to expose an attachment scope selector.");
  assert(dictationButton, "Expected the input panel to expose a dictation control.");
  assert(panel.getState().attachmentScope === "session", "Expected the input panel to default the scope intent to session.");
  assert(getUserMediaCalls.length === 0, "Expected the panel not to request microphone access before the user clicks Dictate.");
  assert(compactionBanner.hidden === true, "Expected the compaction banner to stay hidden before hard compaction starts.");
  assert(compactionDiagnostic === null, "Expected the compaction diagnostic to stay hidden before hard compaction starts.");
  scopeSelect.value = "one-time";
  scopeSelect.dispatch("change", {
    target: scopeSelect,
  });
  assert(panel.getState().attachmentScope === "one-time", "Expected the scope selector to update the queued attachment intent.");
  textarea.value = "click send from browser";
  textarea.dispatch("input");
  assert(sendButton.disabled === false, "Expected typing in the textarea to enable Send.");
  let enterPrevented = false;
  textarea.dispatch("keydown", {
    key: "Enter",
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    preventDefault() {
      enterPrevented = true;
    },
  });
  await flushAsyncTasks();
  assert(enterPrevented === true, "Expected Enter send to prevent the browser default newline action.");
  assert(fetchCalls[0].url === "/input", "Expected pressing Enter to post to /input.");

  let altEnterPrevented = false;
  const fetchCountAfterEnter = fetchCalls.length;
  textarea.value = "draft line";
  textarea.dispatch("keydown", {
    key: "Enter",
    altKey: true,
    ctrlKey: false,
    metaKey: false,
    preventDefault() {
      altEnterPrevented = true;
    },
  });
  await flushAsyncTasks();
  assert(altEnterPrevented === false, "Expected Alt+Enter to preserve the browser newline behaviour.");
  assert(fetchCalls.length === fetchCountAfterEnter, "Expected Alt+Enter not to trigger a send.");

  textarea.value = "hello from user";
  await panel.submit();
  assert(fetchCalls[1].url === "/input", "Expected text messages to post to /input.");
  assert(
    JSON.parse(fetchCalls[1].options.body).scope === "one-time",
    "Expected the submitted text payload to include the chosen scope intent.",
  );

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

  global.dispatchEvent(
    new global.CustomEvent("faith:input-panel-compaction-state", {
      detail: {
        active: true,
        diagnostic: "Compaction is temporarily pausing new sends.",
      },
    }),
  );
  textarea.value = "blocked while compacting";
  textarea.dispatch("input");
  assert(sendButton.disabled === true, "Expected hard compaction to block Send immediately.");
  assert(wrapper.classList.contains("faith-input-panel--compaction") === true, "Expected the panel to show a compaction state class while hard compaction runs.");
  assert(findByText(target, "Compaction underway"), "Expected the compaction banner to become visible during hard compaction.");
  assert(findByText(target, "Compaction is temporarily pausing new sends."), "Expected the compaction diagnostic to be visible during hard compaction.");
  const fetchCountDuringCompaction = fetchCalls.length;
  let blockedEnterPrevented = false;
  textarea.dispatch("keydown", {
    key: "Enter",
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    preventDefault() {
      blockedEnterPrevented = true;
    },
  });
  await flushAsyncTasks();
  assert(blockedEnterPrevented === true, "Expected hard compaction to keep Enter from inserting a newline while the panel is blocked.");
  assert(fetchCalls.length === fetchCountDuringCompaction, "Expected no submit request to be sent while hard compaction is underway.");

  global.dispatchEvent(
    new global.CustomEvent("faith:input-panel-compaction-state", {
      detail: {
        active: false,
      },
    }),
  );
  textarea.dispatch("input");
  assert(wrapper.classList.contains("faith-input-panel--compaction") === false, "Expected the compaction class to clear when hard compaction completes.");
  assert(panel.getState().compactionActive === false, "Expected the panel state to clear the hard-compaction flag immediately.");
  assert(sendButton.disabled === false, "Expected Send to re-enable immediately after hard compaction completes.");

  textarea.value = "typed draft";
  dictationButton.dispatch("click");
  await flushAsyncTasks();
  assert(getUserMediaCalls.length === 1, "Expected Dictate to request microphone access only after the user clicks it.");
  assert(panel.getState().dictationState === "recording", "Expected dictation to enter the recording state after the microphone request succeeds.");
  assert(findByText(target, "Recording"), "Expected the recording state to be visible while capture is active.");
  dictationButton.dispatch("click");
  assert(findByText(target, "Transcribing"), "Expected the transcribing state to be visible while audio is processed.");
  await flushAsyncTasks();
  assert(fetchCalls.some((call) => call.url === "/api/dictation/transcribe"), "Expected recorded audio to be sent to the local transcription route.");
  assert(textarea.value.includes("typed draft"), "Expected dictation to preserve the existing typed draft.");
  assert(textarea.value.includes("captured transcript"), "Expected the transcript to be inserted into the editable textarea.");
  assert(findByText(target, "Dictation complete"), "Expected the success state to be visible after transcription finishes.");

  dictationResponseMode = "failure";
  const draftBeforeFailure = textarea.value;
  dictationButton.dispatch("click");
  await flushAsyncTasks();
  dictationButton.dispatch("click");
  assert(findByText(target, "Transcribing"), "Expected the transcribing state to be visible while a failing dictation request is still in flight.");
  await flushAsyncTasks();
  assert(textarea.value === draftBeforeFailure, "Expected dictation failures to preserve the existing typed content.");
  assert(findByText(target, "Dictation failed"), "Expected the failure state to be visible when transcription fails.");
  assert(panel.getState().dictationState === "failed", "Expected the panel state to expose dictation failures.");

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
