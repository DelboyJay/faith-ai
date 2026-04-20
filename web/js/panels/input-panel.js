/**
 * Description:
 *   Render the FAITH input panel inside the browser workspace.
 *
 * Requirements:
 *   - Send text through `/input` and files through `/upload`.
 *   - Support drag-and-drop, clipboard image paste, attachment removal, and in-flight guards.
 */

(function initialiseFaithInputPanel(globalScope) {
  const INPUT_PATH = "/input";
  const UPLOAD_PATH = "/upload";
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
    wrapper.appendChild(queue);
    wrapper.appendChild(controls);
    wrapper.appendChild(attachInput);
    target.replaceChildren(wrapper);

    const attachments = [];
    let isSending = false;

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
     *
     * @returns {boolean} True when the panel may submit.
     */
    function canSend() {
      return !isSending && (textarea.value.trim().length > 0 || attachments.length > 0);
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
      sendButton.disabled = !canSend();
    }

    /**
     * Description:
     *   Queue one or more validated attachments.
     *
     * Requirements:
     *   - Stop and surface the first validation error encountered.
     *
     * @param {Array<File>} files: Files to queue.
     * @returns {boolean} True when all files were queued successfully.
     */
    function queueAttachments(files) {
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
      sendButton.disabled = true;
      attachButton.disabled = true;
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
        attachButton.disabled = false;
        sendButton.disabled = !canSend();
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
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
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

    renderQueue();

    return {
      queueAttachments: queueAttachments,
      submit: submit,
      getState() {
        return {
          attachmentCount: attachments.length,
          canSend: canSend(),
          error: errorBanner.textContent,
        };
      },
    };
  }

  globalScope.faithInputPanel = {
    INPUT_PATH: INPUT_PATH,
    UPLOAD_PATH: UPLOAD_PATH,
    extractClipboardImages: extractClipboardImages,
    mountPanel: mountPanel,
    validateAttachment: validateAttachment,
  };
})(window);
