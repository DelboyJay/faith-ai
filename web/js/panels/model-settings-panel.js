/**
 * Description:
 *   Render the model-settings editor panel inside the browser workspace.
 *
 * Requirements:
 *   - Load the persisted PA/default-agent model settings and catalog metadata from the server.
 *   - Support edit, save, and reload actions for the PA model, default agent model, per-agent overrides, and context-window overrides.
 *   - Keep unsaved changes visible and warn before destructive local actions.
 */

(function initialiseFaithModelSettingsPanel(globalScope) {
  const SETTINGS_PATH = "/api/model-settings";

  /**
   * Description:
   *   Parse one model-settings API response or raise a readable error.
   *
   * Requirements:
   *   - Preserve backend validation messages when available.
   *
   * @param {Response} response Browser fetch response.
   * @returns {Promise<object>} Decoded model-settings payload.
   */
  async function parseSettingsResponse(response) {
    const payload = await response.json().catch(function returnEmptyPayload() {
      return {};
    });
    if (!response.ok) {
      throw new Error(payload.detail || `Model settings request failed with status ${response.status}`);
    }
    return payload;
  }

  /**
   * Description:
   *   Replace the options inside one select element.
   *
   * Requirements:
   *   - Preserve the requested selected value when it exists in the new option list.
   *   - Fall back to the first option when the requested value is unavailable.
   *
   * @param {HTMLSelectElement} select Select element to populate.
   * @param {Array<object>} options Ordered option payloads from the server.
   * @param {string} selectedValue Preferred selected value.
   * @returns {string} Final selected value after the options are applied.
   */
  function applySelectOptions(select, options, selectedValue) {
    const optionNodes = (options || []).map(function buildOption(optionPayload) {
      const option = document.createElement("option");
      option.value = optionPayload.value;
      option.textContent = optionPayload.label;
      return option;
    });
    select.replaceChildren(...optionNodes);
    const availableValues = optionNodes.map(function extractValue(optionNode) {
      return optionNode.value;
    });
    if (availableValues.includes(selectedValue)) {
      select.value = selectedValue;
    } else if (availableValues.length > 0) {
      select.value = availableValues[0];
    } else {
      select.value = "";
    }
    return select.value;
  }

  /**
   * Description:
   *   Build one labelled select row for the settings panel.
   *
   * Requirements:
   *   - Keep the label bound to the generated select for accessibility.
   *
   * @param {string} labelText User-visible label text.
   * @returns {{row: HTMLElement, select: HTMLSelectElement}} Select row and its select element.
   */
  function buildSelectField(labelText) {
    const row = document.createElement("label");
    row.className = "faith-user-settings-panel__field";

    const label = document.createElement("span");
    label.className = "faith-user-settings-panel__field-label";
    label.textContent = labelText;

    const select = document.createElement("select");
    select.className = "faith-user-settings-panel__input";

    row.appendChild(label);
    row.appendChild(select);
    return { row, select };
  }

  /**
   * Description:
   *   Build one labelled numeric input row for the settings panel.
   *
   * Requirements:
   *   - Keep the label bound to the generated input for accessibility.
   *
   * @param {string} labelText User-visible label text.
   * @returns {{row: HTMLElement, label: HTMLElement, input: HTMLInputElement, hint: HTMLElement}} Input row, label, input, and hint node.
   */
  function buildNumberField(labelText) {
    const row = document.createElement("label");
    row.className = "faith-user-settings-panel__field";

    const label = document.createElement("span");
    label.className = "faith-user-settings-panel__field-label";
    label.textContent = labelText;

    const input = document.createElement("input");
    input.type = "number";
    input.className = "faith-user-settings-panel__input";
    input.min = "0";
    input.step = "1";

    const hint = document.createElement("small");
    hint.className = "faith-user-settings-panel__hint";

    row.appendChild(label);
    row.appendChild(input);
    row.appendChild(hint);
    return { row, label, input, hint };
  }

  /**
   * Description:
   *   Mount one model-settings editor into the supplied DOM element.
   *
   * Requirements:
   *   - Render editable settings, config metadata, and action controls.
   *   - Track dirty state so unsaved edits remain visible before reload or close actions.
   *
   * @param {HTMLElement} target Panel mount element.
   * @returns {object} Mounted panel state and cleanup hook.
   */
  function mountPanel(target) {
    const wrapper = document.createElement("section");
    wrapper.className = "faith-panel faith-panel--model-settings";

    const metadata = document.createElement("dl");
    metadata.className = "faith-user-settings-panel__metadata";

    const form = document.createElement("div");
    form.className = "faith-user-settings-panel__form";

    const paModelField = buildSelectField("PA model");
    const defaultAgentModelField = buildSelectField("Default agent model");
    const agentOverridesContainer = document.createElement("div");
    agentOverridesContainer.className = "faith-user-settings-panel__group";
    const contextOverridesContainer = document.createElement("div");
    contextOverridesContainer.className = "faith-user-settings-panel__group";

    form.appendChild(paModelField.row);
    form.appendChild(defaultAgentModelField.row);
    form.appendChild(agentOverridesContainer);
    form.appendChild(contextOverridesContainer);

    const status = document.createElement("p");
    status.className = "faith-user-settings-panel__status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    const controls = document.createElement("div");
    controls.className = "faith-user-settings-panel__controls";

    const saveButton = document.createElement("button");
    saveButton.type = "button";
    saveButton.textContent = "Save";

    const reloadButton = document.createElement("button");
    reloadButton.type = "button";
    reloadButton.textContent = "Reload";

    controls.appendChild(saveButton);
    controls.appendChild(reloadButton);

    wrapper.appendChild(metadata);
    wrapper.appendChild(form);
    wrapper.appendChild(status);
    wrapper.appendChild(controls);
    target.replaceChildren(wrapper);

    let destroyed = false;
    let lastPayload = null;
    let agentOverrideFields = [];
    let contextOverrideFields = [];
    let lastSavedState = {
      pa_model: "",
      default_agent_model: "",
      agent_overrides: {},
      context_window_overrides: {},
    };

    /**
     * Description:
     *   Update the visible panel status message.
     *
     * Requirements:
     *   - Toggle error and dirty styling based on the current variant.
     *
     * @param {string} message Status message to display.
     * @param {string} variant Status variant name.
     * @returns {void}
     */
    function setStatus(message, variant) {
      status.textContent = message;
      status.className = `faith-user-settings-panel__status faith-user-settings-panel__status--${variant || "idle"}`;
    }

    /**
     * Description:
     *   Return the current local editor state.
     *
     * Requirements:
     *   - Normalize blank override values to `null` for stable persistence payloads.
     *
     * @returns {object} Current local editor state.
     */
    function currentState() {
      const agentOverrides = {};
      agentOverrideFields.forEach(function appendAgentOverride(entry) {
        agentOverrides[entry.agentId] = entry.select.value || null;
      });
      const contextWindowOverrides = {};
      contextOverrideFields.forEach(function appendContextOverride(entry) {
        contextWindowOverrides[entry.modelKey] = entry.input.value ? Number(entry.input.value) : null;
      });
      return {
        pa_model: paModelField.select.value || "",
        default_agent_model: defaultAgentModelField.select.value || "",
        agent_overrides: agentOverrides,
        context_window_overrides: contextWindowOverrides,
      };
    }

    /**
     * Description:
     *   Return whether the editor currently has unsaved changes.
     *
     * Requirements:
     *   - Compare the serialized state against the last accepted server payload.
     *
     * @returns {boolean} True when local edits are unsaved.
     */
    function hasUnsavedChanges() {
      return JSON.stringify(currentState()) !== JSON.stringify(lastSavedState);
    }

    /**
     * Description:
     *   Refresh the dirty-state affordances for the panel.
     *
     * Requirements:
     *   - Disable save when no local edit is pending.
     *   - Keep unsaved changes visibly labelled for the user.
     *
     * @returns {void}
     */
    function refreshDirtyState() {
      const dirty = hasUnsavedChanges();
      saveButton.disabled = !dirty;
      wrapper.classList.toggle("faith-user-settings-panel--dirty", dirty);
      if (dirty) {
        setStatus("You have unsaved changes.", "dirty");
      }
    }

    /**
     * Description:
     *   Render config metadata returned by the server.
     *
     * Requirements:
     *   - Display the backing system and catalog paths together with the update timestamp.
     *
     * @param {object} payload Model-settings payload returned by the server.
     * @returns {void}
     */
    function renderMetadata(payload) {
      metadata.replaceChildren();
      [
        ["System path", payload.system_path || "unknown"],
        ["Catalog path", payload.catalog_path || "unknown"],
        ["Updated", payload.updated_at || "not saved"],
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
     *   Return one catalog entry by fully qualified model key.
     *
     * Requirements:
     *   - Return `null` when the payload does not know the requested model.
     *
     * @param {object} payload Model-settings payload returned by the server.
     * @param {string} modelKey Provider-qualified model key.
     * @returns {object|null} Matching catalog entry or `null`.
     */
    function findCatalogEntry(payload, modelKey) {
      const match = (payload.catalog || []).find(function findEntry(entry) {
        return entry.key === modelKey;
      });
      return match || null;
    }

    /**
     * Description:
     *   Apply one catalog entry to a rendered context-window field.
     *
     * Requirements:
     *   - Surface the context provenance and safe-usable warning for the mapped model.
     *
     * @param {object} field Context-window field descriptor.
     * @param {object} entry Catalog entry payload.
     * @returns {void}
     */
    function applyContextFieldEntry(field, entry) {
      field.modelKey = entry.key;
      field.label.textContent = `Context window: ${entry.key}`;
      field.input.value = String((entry.context_window && entry.context_window.value) || "");
      field.hint.textContent = [
        `provenance ${(entry.context_window && entry.context_window.provenance) || "unknown"}`,
        entry.runtime && entry.runtime.safe_usable_context
          ? `safe ${entry.runtime.safe_usable_context}`
          : "",
        entry.runtime && (entry.runtime.context_warning || entry.runtime.warning)
          ? `warning ${entry.runtime.context_warning || entry.runtime.warning}`
          : "",
      ]
        .filter(Boolean)
        .join(" • ");
    }

    /**
     * Description:
     *   Render editable per-agent override rows from one payload.
     *
     * Requirements:
     *   - Preserve the server-provided agent order.
     *   - Reuse the known model options for each override select.
     *
     * @param {object} payload Model-settings payload returned by the server.
     * @returns {void}
     */
    function renderAgentOverrideFields(payload) {
      agentOverrideFields = [];
      agentOverridesContainer.replaceChildren();
      (payload.agent_overrides || []).forEach(function appendAgentOverride(entry) {
        const field = buildSelectField(`${entry.role || entry.agent_id} override`);
        applySelectOptions(field.select, payload.model_options || [], entry.model || "");
        field.select.addEventListener("change", refreshDirtyState);
        agentOverrideFields.push({
          agentId: entry.agent_id,
          select: field.select,
        });
        agentOverridesContainer.appendChild(field.row);
      });
    }

    /**
     * Description:
     *   Render editable context-window override rows from one payload.
     *
     * Requirements:
     *   - Preserve the server-provided catalog order.
     *   - Surface provenance and safe-usable-context diagnostics beside each editable value.
     *
     * @param {object} payload Model-settings payload returned by the server.
     * @returns {void}
     */
    function renderContextOverrideFields(payload) {
      contextOverrideFields = [];
      contextOverridesContainer.replaceChildren();
      (payload.catalog || []).forEach(function appendContextOverride(entry) {
        const field = buildNumberField(`Context window: ${entry.key}`);
        applyContextFieldEntry(field, entry);
        field.input.addEventListener("input", refreshDirtyState);
        contextOverrideFields.push({
          modelKey: entry.key,
          label: field.label,
          input: field.input,
          hint: field.hint,
        });
        contextOverridesContainer.appendChild(field.row);
      });
    }

    /**
     * Description:
     *   Apply one server model-settings payload to the editor.
     *
     * Requirements:
     *   - Treat the loaded values as the new saved baseline.
     *   - Keep per-agent and per-model override rows in sync with the latest payload.
     *
     * @param {object} payload Model-settings payload returned by the server.
     * @returns {void}
     */
    function applySettingsPayload(payload) {
      lastPayload = payload;
      applySelectOptions(paModelField.select, payload.model_options || [], payload.pa_model || "");
      applySelectOptions(
        defaultAgentModelField.select,
        payload.model_options || [],
        payload.default_agent_model || "",
      );
      renderAgentOverrideFields(payload);
      renderContextOverrideFields(payload);
      synchronisePrimaryContextOverride();
      lastSavedState = currentState();
      renderMetadata(payload);
      refreshDirtyState();
      setStatus("Loaded saved model settings.", "saved");
    }

    /**
     * Description:
     *   Rebind the first context-window row to the currently selected PA model.
     *
     * Requirements:
     *   - Keep the first editable context-window field aligned with the active PA model.
     *   - Preserve the existing DOM node so browser/runtime harnesses keep a stable element reference.
     *
     * @returns {void}
     */
    function synchronisePrimaryContextOverride() {
      if (!lastPayload || contextOverrideFields.length === 0) {
        return;
      }
      const selectedModelKey = paModelField.select.value;
      const selectedEntry = findCatalogEntry(lastPayload, selectedModelKey) || {
        key: selectedModelKey,
        context_window: { value: contextOverrideFields[0].input.value || 0, provenance: "unknown" },
        runtime: {},
      };
      applyContextFieldEntry(contextOverrideFields[0], selectedEntry);
    }

    /**
     * Description:
     *   Load the current persisted settings from the server.
     *
     * Requirements:
     *   - Avoid discarding unsaved edits unless the user confirms.
     *
     * @returns {Promise<void>} Promise that resolves when loading completes.
     */
    async function loadSettings() {
      if (hasUnsavedChanges() && !globalScope.confirm("Discard unsaved model-settings edits and reload?")) {
        return;
      }
      setStatus("Loading model settings...", "loading");
      try {
        const response = await globalScope.fetch(SETTINGS_PATH);
        applySettingsPayload(await parseSettingsResponse(response));
      } catch (error) {
        setStatus(String(error.message || error), "error");
      }
    }

    /**
     * Description:
     *   Persist the edited model settings to the server.
     *
     * Requirements:
     *   - Surface backend validation failures without discarding local edits.
     *
     * @returns {Promise<void>} Promise that resolves when saving completes.
     */
    async function saveSettings() {
      setStatus("Saving model settings...", "loading");
      try {
        const response = await globalScope.fetch(SETTINGS_PATH, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(currentState()),
        });
        const payload = await parseSettingsResponse(response);
        applySettingsPayload(payload);
        setStatus("Model settings saved. Future agent turns will use them.", "saved");
      } catch (error) {
        setStatus(String(error.message || error), "error");
      }
    }

    /**
     * Description:
     *   Confirm whether local model-settings edits may be discarded for one action.
     *
     * Requirements:
     *   - Skip the confirmation when there are no unsaved edits.
     *   - Name the pending action in plain language.
     *
     * @param {string} actionLabel User-visible action description.
     * @returns {boolean} True when the action may proceed.
     */
    function confirmDiscardChanges(actionLabel) {
      if (!hasUnsavedChanges()) {
        return true;
      }
      return globalScope.confirm(`Discard unsaved model-settings edits and ${actionLabel}?`);
    }

    /**
     * Description:
     *   Warn the user before browser navigation when local edits are unsaved.
     *
     * Requirements:
     *   - Leave normal navigation untouched when there are no unsaved edits.
     *
     * @param {BeforeUnloadEvent} event Browser unload event.
     * @returns {void}
     */
    function handleBeforeUnload(event) {
      if (destroyed || !hasUnsavedChanges()) {
        return;
      }
      event.preventDefault();
      event.returnValue = "";
    }

    paModelField.select.addEventListener("change", function onPaModelChange() {
      synchronisePrimaryContextOverride();
      refreshDirtyState();
    });
    defaultAgentModelField.select.addEventListener("change", refreshDirtyState);
    saveButton.addEventListener("click", function onSaveClick() {
      void saveSettings();
    });
    reloadButton.addEventListener("click", function onReloadClick() {
      void loadSettings();
    });
    if (typeof globalScope.addEventListener === "function") {
      globalScope.addEventListener("beforeunload", handleBeforeUnload);
    }

    void loadSettings();

    return {
      destroy() {
        destroyed = true;
        if (typeof globalScope.removeEventListener === "function") {
          globalScope.removeEventListener("beforeunload", handleBeforeUnload);
        }
      },
      getState() {
        return {
          pa_model: paModelField.select.value,
          default_agent_model: defaultAgentModelField.select.value,
          agent_overrides: agentOverrideFields.map(function mapAgentOverride(entry) {
            return { agent_id: entry.agentId, model: entry.select.value };
          }),
          context_window_overrides: contextOverrideFields.map(function mapContextOverride(entry) {
            return { model_key: entry.modelKey, value: entry.input.value };
          }),
          unsaved: hasUnsavedChanges(),
          status: status.textContent,
        };
      },
      confirmDiscardChanges: confirmDiscardChanges,
      hasUnsavedChanges: hasUnsavedChanges,
      loadSettings: loadSettings,
      saveSettings: saveSettings,
    };
  }

  globalScope.faithModelSettingsPanel = {
    SETTINGS_PATH: SETTINGS_PATH,
    mountPanel: mountPanel,
    parseSettingsResponse: parseSettingsResponse,
  };
})(window);
