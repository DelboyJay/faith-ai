/**
 * Description:
 *   Render the FAITH input panel inside the browser workspace.
 *
 * Requirements:
 *   - Send text through `/input` and files through `/upload`.
 *   - Support drag-and-drop, clipboard image paste, attachment removal, and in-flight guards.
 *   - Reflect hard-compaction state changes from the browser runtime.
 */

(function initialiseFaithInputPanel(globalScope) {
  const INPUT_PATH = "/input";
  const UPLOAD_PATH = "/upload";
  const COMPACTION_STATE_EVENT = "faith:input-panel-compaction-state";
  const DEFAULT_COMPACTION_DIAGNOSTIC =
    "Compaction is temporarily pausing new sends.";
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
  const ACCEPTED_UPLOAD_TYPES = new Set([
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/markdown",
    "text/plain",
  ]);

  /**
   * Description:
   *   Validate one queued attachment against the frontend guardrails.
   *
   * Requirements:
   *   - Reject unsupported types and oversized payloads before submission.
   *
   * @param {File} file: Browser file candidate.
   * @returns {string|null} Validation error message or null when valid.
   */
  function validateAttachment(file) {
    if (!file) {
      return "No file selected.";
    }
    if (!ACCEPTED_UPLOAD_TYPES.has(file.type || "")) {
      return `Unsupported file type: ${file.type || "unknown"}`;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      return "File too large.";
    }
    return null;
  }

  /**
   * Description:
   *   Collect image files from one clipboard paste event.
   *
   * Requirements:
   *   - Preserve only image clipboard items as uploadable files.
   *
   * @param {ClipboardEvent} event: Browser paste event.
   * @returns {Array<File>} Extracted clipboard image files.
   */
  function extractClipboardImages(event) {
    const files = [];
    const items = (event && event.clipboardData && event.clipboardData.items) || [];
    Array.from(items).forEach(function appendClipboardFile(item) {
      if (item.kind === "file" && String(item.type || "").startsWith("image/")) {
        const file = item.getAsFile();
        if (file) {
          files.push(file);
        }
      }
    });
    return files;
  }

  /**
   * Description:
   *   Mount one live input panel into the supplied DOM element.
   *
   * Requirements:
   *   - Render textarea, attachment queue, drag/drop target, and send action.
   *   - Prevent duplicate sends while a request is in flight.
   *
   * @param {HTMLElement} target: Panel mount element.
   * @returns {object} Mounted panel state and helper actions.
   */
  function mountPanel(target) {
    const wrapper = document.createElement("section");
    wrapper.className = "faith-panel faith-panel--input";

    const errorBanner = document.createElement("p");
    errorBanner.className = "faith-input-panel__error";
    errorBanner.hidden = true;

    const textarea = document.createElement("textarea");
    textarea.className = "faith-input-panel__textarea";
    textarea.rows = 6;
    textarea.placeholder = "Ask the Project Agent something...";

    const helperText = document.createElement("p");
    helperText.className = "faith-input-panel__hint";
    helperText.textContent = "Enter to send. Alt+Enter for a newline.";

    const compactionBanner = document.createElement("div");
    compactionBanner.className = "faith-input-panel__compaction";
    compactionBanner.hidden = true;

    const compactionIndicator = document.createElement("span");
    compactionIndicator.className = "faith-input-panel__compaction-indicator";

    const compactionText = document.createElement("span");
    compactionText.className = "faith-input-panel__compaction-label";
    compactionText.textContent = "Compaction underway";

    const compactionDiagnostic = document.createElement("p");
    compactionDiagnostic.className = "faith-input-panel__compaction-diagnostic";
    compactionDiagnostic.hidden = true;

    const queue = document.createElement("ul");
    queue.className = "faith-input-panel__attachments";

    const controls = document.createElement("div");
    controls.className = "faith-input-panel__controls";

    const attachInput = document.createElement("input");
    attachInput.type = "file";
    attachInput.hidden = true;
    attachInput.multiple = true;
    attachInput.accept = Array.from(ACCEPTED_UPLOAD_TYPES).join(",");

    const attachButton = document.createElement("button");
    attachButton.type = "button";
    attachButton.textContent = "Attach";

    const sendButton = document.createElement("button");
    sendButton.type = "button";
    sendButton.textContent = "Send";

    controls.appendChild(attachButton);
    controls.appendChild(sendButton);

    wrapper.appendChild(errorBanner);
    wrapper.appendChild(textarea);
    wrapper.appendChild(helperText);
    compactionBanner.appendChild(compactionIndicator);
    compactionBanner.appendChild(compactionText);
    wrapper.appendChild(compactionBanner);
    wrapper.appendChild(compactionDiagnostic);
    wrapper.appendChild(queue);
    wrapper.appendChild(controls);
    wrapper.appendChild(attachInput);
    target.replaceChildren(wrapper);

    const attachments = [];
    let isSending = false;
    let isCompactionBlocked = false;
    let compactionDiagnosticText = "";

    /**
     * Description:
     *   Show or clear the current user-facing error banner.
     *
     * Requirements:
     *   - Hide the banner when no error is present.
     *
     * @param {string} message: Error message to display.
     */
    function setError(message) {
      errorBanner.hidden = !message;
      errorBanner.textContent = message || "";
    }

    /**
     * Description:
     *   Return whether the send action should currently be enabled.
     *
     * Requirements:
     *   - Allow send when there is text or at least one attachment and no request is in flight.
     *   - Block submission while hard compaction is underway.
     *
     * @returns {boolean} True when the panel may submit.
     */
    function canSend() {
      return !isSending && !isCompactionBlocked && (textarea.value.trim().length > 0 || attachments.length > 0);
    }

    /**
     * Description:
     *   Refresh the visible state of the submission controls.
     *
     * Requirements:
     *   - Keep the Send and Attach actions disabled while hard compaction runs.
     *   - Show the compact compaction banner only during the blocked state.
     */
    function syncInteractionState() {
      const showCompaction = isCompactionBlocked;
      if (showCompaction) {
        wrapper.classList.add("faith-input-panel--compaction");
      } else {
        wrapper.classList.remove("faith-input-panel--compaction");
      }
      compactionBanner.hidden = !showCompaction;
      compactionDiagnostic.hidden = !showCompaction;
      compactionDiagnostic.textContent = showCompaction
        ? compactionDiagnosticText || DEFAULT_COMPACTION_DIAGNOSTIC
        : "";
      sendButton.disabled = !canSend();
      const interactionDisabled = isSending || isCompactionBlocked;
      attachButton.disabled = interactionDisabled;
      attachInput.disabled = interactionDisabled;
    }

    /**
     * Description:
     *   Refresh the visible attachment queue and send-button state.
     *
     * Requirements:
     *   - Render a remove action for each queued attachment.
     */
    function renderQueue() {
      queue.replaceChildren();
      attachments.forEach(function appendAttachment(file, index) {
        const item = document.createElement("li");
        item.className = "faith-input-panel__attachment";

        const label = document.createElement("span");
        label.textContent = file.name;

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.textContent = "Remove";
        removeButton.addEventListener("click", function onRemoveAttachment() {
          attachments.splice(index, 1);
          renderQueue();
        });

        item.appendChild(label);
        item.appendChild(removeButton);
        queue.appendChild(item);
      });
      syncInteractionState();
    }

    /**
     * Description:
     *   Apply the latest hard-compaction flag from the browser runtime.
     *
     * Requirements:
     *   - Accept a tiny event payload with an ``active`` flag and optional diagnostic text.
     *   - Clear the blocked state immediately when compaction ends.
     *
     * @param {object} state: Hard-compaction payload from the runtime.
     */
    function setCompactionState(state) {
      isCompactionBlocked = Boolean(state && state.active);
      if (isCompactionBlocked) {
        compactionDiagnosticText =
          state && typeof state.diagnostic === "string" && state.diagnostic.trim()
            ? state.diagnostic.trim()
            : DEFAULT_COMPACTION_DIAGNOSTIC;
      } else {
        compactionDiagnosticText = "";
      }
      syncInteractionState();
    }

    /**
     * Description:
     *   Queue one or more validated attachments.
     *
     * Requirements:
     *   - Stop and surface the first validation error encountered.
     *   - Refuse to queue new inference inputs while hard compaction is active.
     *
     * @param {Array<File>} files: Files to queue.
     * @returns {boolean} True when all files were queued successfully.
     */
    function queueAttachments(files) {
      if (isCompactionBlocked) {
        return false;
      }
      for (const file of files) {
        const error = validateAttachment(file);
        if (error) {
          setError(error);
          return false;
        }
        attachments.push(file);
      }
      setError("");
      renderQueue();
      return true;
    }

    /**
     * Description:
     *   Read a hard-compaction payload from a browser event.
     *
     * Requirements:
     *   - Support the custom event detail shape used by the runtime bridge.
     *   - Fall back to the event object itself when the caller passes a bare payload.
     *
     * @param {Event|object} event: Browser event or payload object.
     */
    function onCompactionStateChange(event) {
      const payload = event && typeof event.detail === "object" && event.detail !== null ? event.detail : event;
      setCompactionState(payload || {});
    }

    /**
     * Description:
     *   Submit the current text payload to the backend.
     *
     * Requirements:
     *   - Post JSON to `/input`.
     *   - Surface backend validation failures clearly.
     *
     * @returns {Promise<void>} Promise that resolves after the request completes.
     */
    async function sendTextMessage() {
      const response = await globalScope.fetch(INPUT_PATH, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: textarea.value.trim() }),
      });
      if (!response.ok) {
        throw new Error(`Send failed with status ${response.status}`);
      }
    }

    /**
     * Description:
     *   Submit one queued attachment to the backend.
     *
     * Requirements:
     *   - Send multipart form data to `/upload`.
     *
     * @param {File} file: Attachment to upload.
     * @returns {Promise<void>} Promise that resolves after the upload completes.
     */
    async function uploadAttachment(file) {
      const formData = new globalScope.FormData();
      formData.append("file", file);
      formData.append("message", textarea.value.trim());
      const response = await globalScope.fetch(UPLOAD_PATH, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(`Upload failed with status ${response.status}`);
      }
    }

    /**
     * Description:
     *   Submit the current panel state to the backend.
     *
     * Requirements:
     *   - Prevent duplicate concurrent sends.
     *   - Clear successful text and attachment state after submission.
     *
     * @returns {Promise<void>} Promise that resolves after submission completes.
     */
    async function submit() {
      if (!canSend()) {
        return;
      }
      isSending = true;
      syncInteractionState();
      setError("");
      try {
        if (attachments.length > 0) {
          for (const file of attachments.slice()) {
            await uploadAttachment(file);
          }
          attachments.splice(0, attachments.length);
        } else {
          await sendTextMessage();
        }
        textarea.value = "";
        renderQueue();
      } catch (error) {
        setError(String(error.message || error));
      } finally {
        isSending = false;
        syncInteractionState();
      }
    }

    attachButton.addEventListener("click", function onAttachClick() {
      attachInput.click();
    });

    attachInput.addEventListener("change", function onAttachInputChange() {
      queueAttachments(Array.from(attachInput.files || []));
      attachInput.value = "";
    });

    textarea.addEventListener("keydown", function onTextareaKeydown(event) {
      if (event.key === "Enter" && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
        event.preventDefault();
        void submit();
      }
    });

    textarea.addEventListener("input", function onTextareaInput() {
      renderQueue();
    });

    textarea.addEventListener("paste", function onTextareaPaste(event) {
      const clipboardFiles = extractClipboardImages(event);
      if (clipboardFiles.length > 0) {
        event.preventDefault();
        queueAttachments(clipboardFiles);
      }
    });

    wrapper.addEventListener("dragover", function onDragOver(event) {
      event.preventDefault();
      wrapper.classList.add("faith-input-panel--dragover");
    });

    wrapper.addEventListener("dragleave", function onDragLeave() {
      wrapper.classList.remove("faith-input-panel--dragover");
    });

    wrapper.addEventListener("drop", function onDrop(event) {
      event.preventDefault();
      wrapper.classList.remove("faith-input-panel--dragover");
      queueAttachments(Array.from((event.dataTransfer && event.dataTransfer.files) || []));
    });

    sendButton.addEventListener("click", function onSendClick() {
      void submit();
    });

    globalScope.addEventListener(COMPACTION_STATE_EVENT, onCompactionStateChange);

    renderQueue();
    setCompactionState({ active: false });

    return {
      queueAttachments: queueAttachments,
      setCompactionState: setCompactionState,
      submit: submit,
      getState() {
        return {
          attachmentCount: attachments.length,
          compactionActive: isCompactionBlocked,
          compactionDiagnostic: compactionDiagnostic.textContent,
          canSend: canSend(),
          error: errorBanner.textContent,
        };
      },
    };
  }

  globalScope.faithInputPanel = {
    INPUT_PATH: INPUT_PATH,
    COMPACTION_STATE_EVENT: COMPACTION_STATE_EVENT,
    UPLOAD_PATH: UPLOAD_PATH,
    extractClipboardImages: extractClipboardImages,
    mountPanel: mountPanel,
    validateAttachment: validateAttachment,
  };
})(window);
