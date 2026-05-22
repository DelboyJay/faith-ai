/**
 * Description:
 *   Render the FAITH input panel inside the browser workspace.
 *
 * Requirements:
 *   - Send text through `/input` and files through `/upload`.
 *   - Support drag-and-drop, clipboard image paste, attachment removal, and in-flight guards.
 *   - Reflect hard-compaction state changes from the browser runtime.
 *   - Support microphone dictation through a local transcription path without auto-sending text.
 */

(function initialiseFaithInputPanel(globalScope) {
  const INPUT_PATH = "/input";
  const UPLOAD_PATH = "/upload";
  const DICTATION_TRANSCRIBE_PATH = "/api/dictation/transcribe";
  const COMPACTION_STATE_EVENT = "faith:input-panel-compaction-state";
  const SESSION_CHANGE_EVENT = "faith:workspace-session-change";
  const SESSION_DRAFTS_STORAGE_KEY = "faith.session-history.drafts";
  const DEFAULT_COMPACTION_DIAGNOSTIC =
    "Compaction is temporarily pausing new sends.";
  const DEFAULT_DICTATION_STATUS = "Dictation ready.";
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
  const DEFAULT_ATTACHMENT_SCOPE = "session";
  const ATTACHMENT_SCOPE_OPTIONS = Object.freeze([
    { value: "global", label: "Global" },
    { value: "scoped", label: "Scoped" },
    { value: "session", label: "Session" },
    { value: "one-time", label: "One-time" },
  ]);
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
   * @param {object} state: Optional session-selection state passed from the workspace shell.
   * @returns {object} Mounted panel state and helper actions.
   */
  function mountPanel(target, state) {
    const sharedState = globalScope.faithWorkspaceSessionState || (globalScope.faithWorkspaceSessionState = {});
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

    const dictationBanner = document.createElement("div");
    dictationBanner.className = "faith-input-panel__dictation";
    dictationBanner.hidden = true;

    const dictationIndicator = document.createElement("span");
    dictationIndicator.className = "faith-input-panel__dictation-indicator";

    const dictationText = document.createElement("span");
    dictationText.className = "faith-input-panel__dictation-label";
    dictationText.textContent = DEFAULT_DICTATION_STATUS;

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

    const scopeField = document.createElement("label");
    scopeField.className = "faith-input-panel__scope";

    const scopeLabel = document.createElement("span");
    scopeLabel.className = "faith-input-panel__scope-label";
    scopeLabel.textContent = "Attachment scope";

    const scopeSelect = document.createElement("select");
    scopeSelect.className = "faith-input-panel__scope-select";
    ATTACHMENT_SCOPE_OPTIONS.forEach(function appendScopeOption(option) {
      const scopeOption = document.createElement("option");
      scopeOption.value = option.value;
      scopeOption.textContent = option.label;
      scopeSelect.appendChild(scopeOption);
    });
    scopeSelect.value = DEFAULT_ATTACHMENT_SCOPE;
    scopeField.appendChild(scopeLabel);
    scopeField.appendChild(scopeSelect);

    const attachInput = document.createElement("input");
    attachInput.type = "file";
    attachInput.hidden = true;
    attachInput.multiple = true;
    attachInput.accept = Array.from(ACCEPTED_UPLOAD_TYPES).join(",");

    const attachButton = document.createElement("button");
    attachButton.type = "button";
    attachButton.textContent = "Attach";

    const dictationButton = document.createElement("button");
    dictationButton.type = "button";
    dictationButton.textContent = "Dictate";
    dictationButton.title = "Record speech and insert the transcript into the text box.";

    const sendButton = document.createElement("button");
    sendButton.type = "button";
    sendButton.textContent = "Send";

    controls.appendChild(scopeField);
    controls.appendChild(attachButton);
    controls.appendChild(dictationButton);
    controls.appendChild(sendButton);

    wrapper.appendChild(errorBanner);
    wrapper.appendChild(textarea);
    wrapper.appendChild(helperText);
    dictationBanner.appendChild(dictationIndicator);
    dictationBanner.appendChild(dictationText);
    wrapper.appendChild(dictationBanner);
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
    let isDictating = false;
    let isTranscribing = false;
    let dictationState = "idle";
    let dictationStatusText = DEFAULT_DICTATION_STATUS;
    let dictationRecorder = null;
    let dictationStream = null;
    let dictationChunks = [];
    let compactionDiagnosticText = "";
    let attachmentScope = DEFAULT_ATTACHMENT_SCOPE;
    let activeSessionId = String((state && state.sessionId) || sharedState.selectedSessionId || "");
    let sessionDrafts = {};

    /**
     * Description:
     *   Load the persisted session-draft map from browser storage.
     *
     * Requirements:
     *   - Fall back to an empty map when browser storage is unavailable.
     *
     * @returns {object} Draft map keyed by session identifier.
     */
    function readDraftMap() {
      try {
        const raw = globalScope.localStorage.getItem(SESSION_DRAFTS_STORAGE_KEY);
        if (!raw) {
          return {};
        }
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : {};
      } catch (error) {
        return {};
      }
    }

    /**
     * Description:
     *   Persist the in-memory session-draft map.
     *
     * Requirements:
     *   - Keep per-session drafts available after a session switch.
     *
     * @returns {void}
     */
    function saveDraftMap() {
      try {
        globalScope.localStorage.setItem(SESSION_DRAFTS_STORAGE_KEY, JSON.stringify(sessionDrafts));
      } catch (error) {
        return;
      }
      sharedState.sessionDrafts = sessionDrafts;
    }

    /**
     * Description:
     *   Save one draft for the active session.
     *
     * Requirements:
     *   - Store the current text so returning to the session restores the same work-in-progress draft.
     *
     * @param {string} sessionId Session identifier key.
     * @param {string} value Draft text.
     * @returns {void}
     */
    function setSessionDraft(sessionId, value) {
      if (!sessionId) {
        return;
      }
      sessionDrafts[sessionId] = String(value || "");
      saveDraftMap();
    }

    /**
     * Description:
     *   Restore the draft for one session into the textarea.
     *
     * Requirements:
     *   - Clear the composer when a session has no saved draft.
     *
     * @param {string} sessionId Session identifier key.
     * @returns {void}
     */
    function restoreSessionDraft(sessionId) {
      textarea.value = sessionId && sessionDrafts[sessionId] ? sessionDrafts[sessionId] : "";
      renderQueue();
    }

    /**
     * Description:
     *   Switch the input composer to a different session.
     *
     * Requirements:
     *   - Persist the outgoing draft before binding to the new session.
     *
     * @param {string} sessionId Selected session identifier.
     * @returns {void}
     */
    function setActiveSession(sessionId) {
      if (activeSessionId) {
        setSessionDraft(activeSessionId, textarea.value);
      }
      activeSessionId = String(sessionId || "");
      sharedState.selectedSessionId = activeSessionId;
      restoreSessionDraft(activeSessionId);
    }

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
     *   Update the visible dictation status banner and control state.
     *
     * Requirements:
     *   - Show recording, transcribing, success, and failure states explicitly.
     *   - Hide the banner when dictation is idle.
     *
     * @param {string} state: Dictation state key.
     * @param {string} message: Status text to display.
     */
    function setDictationStatus(state, message) {
      dictationState = state;
      dictationStatusText = message || DEFAULT_DICTATION_STATUS;
      dictationBanner.hidden = state === "idle";
      dictationBanner.dataset.state = state;
      dictationIndicator.textContent =
        state === "recording"
          ? "●"
          : state === "transcribing"
            ? "…"
            : state === "success"
              ? "✓"
              : state === "failed"
                ? "!"
                : "";
      dictationText.textContent = dictationStatusText;
      wrapper.classList.toggle("faith-input-panel--dictating", state === "recording");
      wrapper.classList.toggle("faith-input-panel--transcribing", state === "transcribing");
      wrapper.classList.toggle("faith-input-panel--dictation-success", state === "success");
      wrapper.classList.toggle("faith-input-panel--dictation-failed", state === "failed");
    }

    /**
     * Description:
     *   Insert one transcript into the textarea without auto-sending it.
     *
     * Requirements:
     *   - Preserve the user's existing draft by inserting the transcript into the editable field.
     *
     * @param {string} transcript: Transcribed speech text.
     */
    function insertTranscript(transcript) {
      const trimmedTranscript = transcript.trim();
      const selectionStart =
        typeof textarea.selectionStart === "number" ? textarea.selectionStart : textarea.value.length;
      const selectionEnd =
        typeof textarea.selectionEnd === "number" ? textarea.selectionEnd : textarea.value.length;
      const before = textarea.value.slice(0, selectionStart);
      const after = textarea.value.slice(selectionEnd);
      const needsSpacer = before.length > 0 && !/\s$/.test(before) && !/^\s/.test(trimmedTranscript);
      textarea.value = before + (needsSpacer ? "\n" : "") + trimmedTranscript + after;
      textarea.selectionStart = textarea.selectionEnd = before.length + (needsSpacer ? 1 : 0) + trimmedTranscript.length;
      renderQueue();
      syncInteractionState();
    }

    /**
     * Description:
     *   Stop any active microphone stream and clear recorder state.
     *
     * Requirements:
     *   - Release browser microphone tracks after dictation completes or fails.
     */
    function resetDictationResources() {
      if (dictationStream && typeof dictationStream.getTracks === "function") {
        dictationStream.getTracks().forEach(function stopTrack(track) {
          if (track && typeof track.stop === "function") {
            track.stop();
          }
        });
      }
      dictationRecorder = null;
      dictationStream = null;
      dictationChunks = [];
      isDictating = false;
    }

    /**
     * Description:
     *   Build the multipart payload for one recorded dictation session.
     *
     * Requirements:
     *   - Send the captured audio to the local transcription route.
     *
     * @param {Blob} audioBlob: Recorded browser audio payload.
     * @returns {Promise<string>} Transcript text returned by the local transcription route.
     */
    async function transcribeDictation(audioBlob) {
      const formData = new globalScope.FormData();
      formData.append("audio", audioBlob, "dictation.webm");
      formData.append("content_type", audioBlob.type || "audio/webm");
      const response = await globalScope.fetch(DICTATION_TRANSCRIBE_PATH, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        let detail = "";
        try {
          const payload = await response.json();
          detail =
            payload && typeof payload.detail === "string" ? payload.detail : String(payload || "");
        } catch (error) {
          detail = "";
        }
        throw new Error(
          detail ? `Dictation failed: ${detail}` : `Dictation failed with status ${response.status}`,
        );
      }
      const payload = await response.json();
      const transcript =
        payload && typeof payload.transcript === "string"
          ? payload.transcript
          : payload && typeof payload.text === "string"
            ? payload.text
            : "";
      if (!transcript.trim()) {
        throw new Error("Dictation returned an empty transcript.");
      }
      return transcript;
    }

    /**
     * Description:
     *   Finish one recording session and hand the captured audio to transcription.
     *
     * Requirements:
     *   - Show a transcribing state while the local speech path runs.
     *   - Preserve typed input if transcription fails.
     *
     * @returns {Promise<void>} Promise that resolves after transcription handling completes.
     */
    async function finalizeDictation() {
      const recordedChunks = dictationChunks.slice();
      const recorder = dictationRecorder;
      isDictating = false;
      isTranscribing = true;
      setDictationStatus("transcribing", "Transcribing");
      syncInteractionState();
      try {
        const audioBlob = new globalScope.Blob(recordedChunks, {
          type: (recorder && recorder.mimeType) || "audio/webm",
        });
        const transcript = await transcribeDictation(audioBlob);
        insertTranscript(transcript);
        setError("");
        setDictationStatus("success", "Dictation complete");
      } catch (error) {
        const message = String(error && error.message ? error.message : error);
        setError(message);
        setDictationStatus("failed", "Dictation failed");
      } finally {
        isTranscribing = false;
        resetDictationResources();
        syncInteractionState();
      }
    }

    /**
     * Description:
     *   Stop the current recording session when the user clicks the dictation button again.
     *
     * Requirements:
     *   - Keep the stop action local to the explicit user gesture.
     */
    function stopDictation() {
      if (dictationRecorder && isDictating && typeof dictationRecorder.stop === "function") {
        dictationRecorder.stop();
      }
    }

    /**
     * Description:
     *   Start browser microphone capture for one dictation session.
     *
     * Requirements:
     *   - Request microphone permission only after explicit user action.
     *   - Fail cleanly without clearing the user's current draft when permission or recording fails.
     *
     * @returns {Promise<void>} Promise that resolves after capture starts.
     */
    async function startDictation() {
      if (isSending || isCompactionBlocked || isTranscribing || isDictating) {
        return;
      }
      if (
        !globalScope.navigator ||
        !globalScope.navigator.mediaDevices ||
        typeof globalScope.navigator.mediaDevices.getUserMedia !== "function"
      ) {
        setError("Microphone access is unavailable in this browser.");
        setDictationStatus("failed", "Dictation failed");
        return;
      }
      setError("");
      try {
        const stream = await globalScope.navigator.mediaDevices.getUserMedia({ audio: true });
        const recorder = new globalScope.MediaRecorder(stream);
        dictationStream = stream;
        dictationRecorder = recorder;
        dictationChunks = [];
        recorder.ondataavailable = function onDictationChunk(event) {
          if (event && event.data && (!("size" in event.data) || event.data.size > 0)) {
            dictationChunks.push(event.data);
          }
        };
        recorder.onstop = function onDictationStop() {
          void finalizeDictation();
        };
        recorder.start();
        isDictating = true;
        setDictationStatus("recording", "Recording");
        syncInteractionState();
      } catch (error) {
        setDictationStatus("failed", "Dictation failed");
        setError(String(error && error.message ? error.message : error));
        resetDictationResources();
        syncInteractionState();
      }
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
      return (
        !isSending &&
        !isCompactionBlocked &&
        !isDictating &&
        !isTranscribing &&
        (textarea.value.trim().length > 0 || attachments.length > 0)
      );
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
      dictationButton.textContent = isDictating ? "Stop" : "Dictate";
      dictationButton.disabled = isSending || isCompactionBlocked || isTranscribing;
      sendButton.disabled = !canSend();
      const interactionDisabled = isSending || isCompactionBlocked || isDictating || isTranscribing;
      scopeSelect.disabled = interactionDisabled;
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
     *   Update the current attachment scope selection.
     *
     * Requirements:
     *   - Keep the browser selector and internal intent state aligned.
     *
     * @param {string} scope: Selected attachment scope intent.
     */
    function setAttachmentScope(scope) {
      const nextScope = ATTACHMENT_SCOPE_OPTIONS.some(function hasScope(option) {
        return option.value === scope;
      })
        ? scope
        : DEFAULT_ATTACHMENT_SCOPE;
      attachmentScope = nextScope;
      scopeSelect.value = nextScope;
      syncInteractionState();
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
        body: JSON.stringify({
          message: textarea.value.trim(),
          scope: attachmentScope,
          session_id: activeSessionId || null,
        }),
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
      formData.append("scope", attachmentScope);
      if (activeSessionId) {
        formData.append("session_id", activeSessionId);
      }
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
        setSessionDraft(activeSessionId, "");
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

    dictationButton.addEventListener("click", function onDictationClick() {
      if (isDictating) {
        stopDictation();
      } else {
        void startDictation();
      }
    });

    scopeSelect.addEventListener("change", function onScopeChange() {
      setAttachmentScope(scopeSelect.value);
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
      setSessionDraft(activeSessionId, textarea.value);
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
    function handleWorkspaceSessionChange(event) {
      const payload = event && event.detail ? event.detail : {};
      if (!payload.sessionId || payload.sessionId === activeSessionId) {
        return;
      }
      setActiveSession(String(payload.sessionId));
      attachments.splice(0, attachments.length);
      renderQueue();
    }
    if (typeof globalScope.addEventListener === "function") {
      globalScope.addEventListener(SESSION_CHANGE_EVENT, handleWorkspaceSessionChange);
    }

    sessionDrafts = Object.assign({}, readDraftMap(), sharedState.sessionDrafts || {});
    sharedState.sessionDrafts = sessionDrafts;
    if (activeSessionId) {
      restoreSessionDraft(activeSessionId);
    }
    renderQueue();
    setAttachmentScope(DEFAULT_ATTACHMENT_SCOPE);
    setCompactionState({ active: false });
    setDictationStatus("idle", DEFAULT_DICTATION_STATUS);

    return {
      queueAttachments: queueAttachments,
      setAttachmentScope: setAttachmentScope,
      setCompactionState: setCompactionState,
      startDictation: startDictation,
      stopDictation: stopDictation,
      submit: submit,
      destroy() {
        if (typeof globalScope.removeEventListener === "function") {
          globalScope.removeEventListener(SESSION_CHANGE_EVENT, handleWorkspaceSessionChange);
          globalScope.removeEventListener(COMPACTION_STATE_EVENT, onCompactionStateChange);
        }
      },
      getState() {
        return {
          attachmentCount: attachments.length,
          attachmentScope: attachmentScope,
          compactionActive: isCompactionBlocked,
          compactionDiagnostic: compactionDiagnostic.textContent,
          canSend: canSend(),
          error: errorBanner.textContent,
          dictationState: dictationState,
          dictationStatus: dictationStatusText,
          sessionId: activeSessionId,
          draft: textarea.value,
        };
      },
    };
  }

  globalScope.faithInputPanel = {
    DICTATION_TRANSCRIBE_PATH: DICTATION_TRANSCRIBE_PATH,
    INPUT_PATH: INPUT_PATH,
    COMPACTION_STATE_EVENT: COMPACTION_STATE_EVENT,
    UPLOAD_PATH: UPLOAD_PATH,
    ATTACHMENT_SCOPE_OPTIONS: ATTACHMENT_SCOPE_OPTIONS,
    extractClipboardImages: extractClipboardImages,
    mountPanel: mountPanel,
    validateAttachment: validateAttachment,
  };
})(window);
