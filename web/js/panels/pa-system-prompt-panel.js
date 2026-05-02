/**
 * Description:
 *   Render the Project Agent system prompt editor panel inside the browser workspace.
 *
 * Requirements:
 *   - Load the active PA prompt and metadata from the server.
 *   - Support edit, save, reload, and reset actions.
 *   - Keep unsaved changes visible and warn before destructive local actions.
 */

(function initialiseFaithPaSystemPromptPanel(globalScope) {
  const PROMPT_PATH = "/api/pa/system-prompt";
  const RESET_PATH = "/api/pa/system-prompt/reset";

  /**
   * Description:
   *   Parse one prompt API response or raise a readable error.
   *
   * Requirements:
   *   - Preserve backend validation messages when available.
   *
   * @param {Response} response: Browser fetch response.
   * @returns {Promise<object>} Decoded prompt metadata.
   */
  async function parsePromptResponse(response) {
    const payload = await response.json().catch(function returnEmptyPayload() {
      return {};
    });
    if (!response.ok) {
      throw new Error(payload.detail || `Prompt request failed with status ${response.status}`);
    }
    return payload;
  }

  /**
   * Description:
   *   Mount one Project Agent system prompt editor into the supplied DOM element.
   *
   * Requirements:
   *   - Render prompt metadata, editable text, status, and action controls.
   *   - Track dirty state so unsaved edits are visible before reload/reset/browser navigation.
   *
   * @param {HTMLElement} target: Panel mount element.
   * @returns {object} Mounted panel state and cleanup hook.
   */
  function mountPanel(target) {
    const wrapper = document.createElement("section");
    wrapper.className = "faith-panel faith-panel--pa-system-prompt";

    const metadata = document.createElement("dl");
    metadata.className = "faith-pa-prompt-panel__metadata";

    const textarea = document.createElement("textarea");
    textarea.className = "faith-pa-prompt-panel__textarea";
    textarea.rows = 12;
    textarea.spellcheck = true;

    const status = document.createElement("p");
    status.className = "faith-pa-prompt-panel__status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    const controls = document.createElement("div");
    controls.className = "faith-pa-prompt-panel__controls";

    const saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.textContent = "Save";

    const reloadButton = document.createElement("button");
    reloadButton.type = "button";
    reloadButton.textContent = "Reload";

    const resetButton = document.createElement("button");
    resetButton.type = "button";
    resetButton.textContent = "Reset";

    controls.appendChild(saveButton);
    controls.appendChild(reloadButton);
    controls.appendChild(resetButton);
    wrapper.appendChild(metadata);
    wrapper.appendChild(textarea);
    wrapper.appendChild(status);
    wrapper.appendChild(controls);
    target.replaceChildren(wrapper);

    let lastSavedPrompt = "";
    let destroyed = false;

    /**
     * Description:
     *   Update the visible status message.
     *
     * Requirements:
     *   - Toggle dirty/error styling based on the current panel state.
     *
     * @param {string} message: Status message to display.
     * @param {string} variant: Status variant name.
     */
    function setStatus(message, variant) {
      status.textContent = message;
      status.className = `faith-pa-prompt-panel__status faith-pa-prompt-panel__status--${variant || "idle"}`;
    }

    /**
     * Description:
     *   Return whether the editor currently has unsaved changes.
     *
     * Requirements:
     *   - Compare the textarea value with the last server-accepted prompt.
     *
     * @returns {boolean} True when local edits are unsaved.
     */
    function hasUnsavedChanges() {
      return textarea.value !== lastSavedPrompt;
    }

    /**
     * Description:
     *   Refresh action-state and unsaved-change messaging.
     *
     * Requirements:
     *   - Disable save when there is no local edit to submit.
     *   - Keep unsaved changes visibly labelled for the user.
     */
    function refreshDirtyState() {
      const dirty = hasUnsavedChanges();
      saveButton.disabled = !dirty;
      wrapper.classList.toggle("faith-pa-prompt-panel--dirty", dirty);
      if (dirty) {
        setStatus("You have unsaved changes.", "dirty");
      }
    }

    /**
     * Description:
     *   Render prompt metadata returned by the server.
     *
     * Requirements:
     *   - Display source, path, and update timestamp when available.
     *
     * @param {object} payload: Prompt metadata payload.
     */
    function renderMetadata(payload) {
      const updatedAt = payload.updated_at || "not saved";
      metadata.replaceChildren();
      [
        ["Source", payload.source || "unknown"],
        ["Path", payload.path || "unknown"],
        ["Updated", updatedAt],
        ["Modified", payload.differs_from_default ? "Yes" : "No"],
      ].forEach(function appendMetadata(row) {
        const term = document.createElement("dt");
        term.textContent = row[0];
        const detail = document.createElement("dd");
        detail.textContent = row[1];
        metadata.appendChild(term);
        metadata.appendChild(detail);
      });
    }

    /**
     * Description:
     *   Apply one server prompt payload to the editor.
     *
     * Requirements:
     *   - Treat the loaded prompt as the current saved baseline.
     *
     * @param {object} payload: Prompt metadata payload.
     */
    function applyPromptPayload(payload) {
      lastSavedPrompt = payload.prompt || "";
      textarea.value = lastSavedPrompt;
      renderMetadata(payload);
      refreshDirtyState();
      setStatus(`Loaded ${payload.source || "active"} prompt.`, "saved");
    }

    /**
     * Description:
     *   Load the active prompt from the server.
     *
     * Requirements:
     *   - Avoid discarding unsaved edits unless the user confirms.
     *
     * @returns {Promise<void>} Promise that resolves when loading completes.
     */
    async function loadPrompt() {
      if (hasUnsavedChanges() && !globalScope.confirm("Discard unsaved prompt edits and reload?")) {
        return;
      }
      setStatus("Loading prompt...", "loading");
      try {
        const response = await globalScope.fetch(PROMPT_PATH);
        applyPromptPayload(await parsePromptResponse(response));
      } catch (error) {
        setStatus(String(error.message || error), "error");
      }
    }

    /**
     * Description:
     *   Save the edited prompt to the server.
     *
     * Requirements:
     *   - Surface validation failures without discarding local edits.
     *
     * @returns {Promise<void>} Promise that resolves when saving completes.
     */
    async function savePrompt() {
      setStatus("Saving prompt...", "loading");
      try {
        const response = await globalScope.fetch(PROMPT_PATH, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: textarea.value }),
        });
        const payload = await parsePromptResponse(response);
        applyPromptPayload(payload);
        globalScope.dispatchEvent(
          new globalScope.CustomEvent("faith:pa-system-prompt-updated", {
            detail: {
              source: payload.source || "unknown",
              updated_at: payload.updated_at || null,
            },
          }),
        );
        setStatus("Prompt saved. Future PA messages will use it.", "saved");
      } catch (error) {
        setStatus(String(error.message || error), "error");
      }
    }

    /**
     * Description:
     *   Reset the active prompt to the default.
     *
     * Requirements:
     *   - Confirm before replacing local unsaved edits or a custom prompt.
     *
     * @returns {Promise<void>} Promise that resolves when reset completes.
     */
    async function resetPrompt() {
      if (!globalScope.confirm("Reset the Project Agent prompt to the built-in default?")) {
        return;
      }
      setStatus("Resetting prompt...", "loading");
      try {
        const response = await globalScope.fetch(RESET_PATH, { method: "POST" });
        const payload = await parsePromptResponse(response);
        applyPromptPayload(payload);
        globalScope.dispatchEvent(
          new globalScope.CustomEvent("faith:pa-system-prompt-updated", {
            detail: {
              source: payload.source || "default",
              updated_at: payload.updated_at || null,
            },
          }),
        );
        setStatus("Prompt reset to the default.", "saved");
      } catch (error) {
        setStatus(String(error.message || error), "error");
      }
    }

    /**
     * Description:
     *   Confirm whether local prompt edits may be discarded for one action.
     *
     * Requirements:
     *   - Skip the confirmation when there are no unsaved edits.
     *   - Name the pending action in plain language.
     *
     * @param {string} actionLabel: User-visible action description.
     * @returns {boolean} True when the action may proceed.
     */
    function confirmDiscardChanges(actionLabel) {
      if (!hasUnsavedChanges()) {
        return true;
      }
      return globalScope.confirm(`Discard unsaved prompt edits and ${actionLabel}?`);
    }

    /**
     * Description:
     *   Warn the user before browser navigation when local edits are unsaved.
     *
     * Requirements:
     *   - Leave normal navigation untouched when there are no unsaved edits.
     *
     * @param {BeforeUnloadEvent} event: Browser unload event.
     */
    function handleBeforeUnload(event) {
      if (destroyed || !hasUnsavedChanges()) {
        return;
      }
      event.preventDefault();
      event.returnValue = "";
    }

    textarea.addEventListener("input", refreshDirtyState);
    saveButton.addEventListener("click", function onSaveClick() {
      void savePrompt();
    });
    reloadButton.addEventListener("click", function onReloadClick() {
      void loadPrompt();
    });
    resetButton.addEventListener("click", function onResetClick() {
      void resetPrompt();
    });
    globalScope.addEventListener("beforeunload", handleBeforeUnload);

    void loadPrompt();

    return {
      destroy() {
        destroyed = true;
        globalScope.removeEventListener("beforeunload", handleBeforeUnload);
      },
      getState() {
        return {
          prompt: textarea.value,
          unsaved: hasUnsavedChanges(),
          status: status.textContent,
        };
      },
      confirmDiscardChanges: confirmDiscardChanges,
      hasUnsavedChanges: hasUnsavedChanges,
      loadPrompt: loadPrompt,
      resetPrompt: resetPrompt,
      savePrompt: savePrompt,
    };
  }

  globalScope.faithPaSystemPromptPanel = {
    PROMPT_PATH: PROMPT_PATH,
    RESET_PATH: RESET_PATH,
    mountPanel: mountPanel,
    parsePromptResponse: parsePromptResponse,
  };
})(window);
