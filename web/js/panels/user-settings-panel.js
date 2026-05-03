/**
 * Description:
 *   Render the user-settings editor panel inside the browser workspace.
 *
 * Requirements:
 *   - Load the persisted settings payload from the server.
 *   - Support browser-timezone suggestion, country-filtered timezone selection, edit, save, and reload actions.
 *   - Keep unsaved changes visible and warn before destructive local actions.
 */

(function initialiseFaithUserSettingsPanel(globalScope) {
  const SETTINGS_PATH = "/api/user-settings";

  /**
   * Description:
   *   Parse one user-settings API response or raise a readable error.
   *
   * Requirements:
   *   - Preserve backend validation messages when available.
   *
   * @param {Response} response Browser fetch response.
   * @returns {Promise<object>} Decoded user-settings payload.
   */
  async function parseSettingsResponse(response) {
    const payload = await response.json().catch(function returnEmptyPayload() {
      return {};
    });
    if (!response.ok) {
      throw new Error(payload.detail || `Settings request failed with status ${response.status}`);
    }
    return payload;
  }

  /**
   * Description:
   *   Return the browser-detected timezone identifier when available.
   *
   * Requirements:
   *   - Return `null` when browser timezone detection is unavailable.
   *
   * @returns {string|null} Detected browser timezone identifier.
   */
  function detectBrowserTimezone() {
    try {
      const timezone = globalScope.Intl.DateTimeFormat().resolvedOptions().timeZone;
      return timezone || null;
    } catch (error) {
      return null;
    }
  }

  /**
   * Description:
   *   Mount one user-settings editor into the supplied DOM element.
   *
   * Requirements:
   *   - Render editable text fields, config metadata, status, and action controls.
   *   - Track dirty state so unsaved edits remain visible before reload or close actions.
   *
   * @param {HTMLElement} target Panel mount element.
   * @returns {object} Mounted panel state and cleanup hook.
   */
  function mountPanel(target) {
    const wrapper = document.createElement("section");
    wrapper.className = "faith-panel faith-panel--user-settings";

    const metadata = document.createElement("dl");
    metadata.className = "faith-user-settings-panel__metadata";

    const form = document.createElement("div");
    form.className = "faith-user-settings-panel__form";

    /**
     * Description:
     *   Build one labelled text input row for the settings panel.
     *
     * Requirements:
     *   - Keep the label bound to the generated input for accessibility.
     *
     * @param {string} labelText User-visible label text.
     * @param {string} placeholder Placeholder text for the input.
     * @returns {{row: HTMLElement, input: HTMLInputElement}} Input row and its input element.
     */
    function buildTextField(labelText, placeholder) {
      const row = document.createElement("label");
      row.className = "faith-user-settings-panel__field";

      const label = document.createElement("span");
      label.className = "faith-user-settings-panel__field-label";
      label.textContent = labelText;

      const input = document.createElement("input");
      input.type = "text";
      input.className = "faith-user-settings-panel__input";
      input.placeholder = placeholder;

      row.appendChild(label);
      row.appendChild(input);
      return { row, input };
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

    const displayNameField = buildTextField("Display name", "How FAITH should address you");
    const countryField = buildSelectField("Country");
    const localeField = buildSelectField("Preferred locale");
    const timezoneField = buildSelectField("Timezone");

    form.appendChild(displayNameField.row);
    form.appendChild(countryField.row);
    form.appendChild(localeField.row);
    form.appendChild(timezoneField.row);

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

    const detectTimezoneButton = document.createElement("button");
    detectTimezoneButton.type = "button";
    detectTimezoneButton.textContent = "Use browser timezone";

    controls.appendChild(saveButton);
    controls.appendChild(reloadButton);
    controls.appendChild(detectTimezoneButton);

    wrapper.appendChild(metadata);
    wrapper.appendChild(form);
    wrapper.appendChild(status);
    wrapper.appendChild(controls);
    target.replaceChildren(wrapper);

    const browserTimezone = detectBrowserTimezone();
    detectTimezoneButton.hidden = !browserTimezone;

    let destroyed = false;
    let localeOptionsByCountry = {};
    let timezoneOptionsByCountry = {};
    let lastSavedState = {
      display_name: "",
      country_code: "",
      preferred_locale: "",
      timezone: "",
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
     *   - Normalise blank values to empty strings for stable comparisons.
     *
     * @returns {object} Current local editor state.
     */
    function currentState() {
      return {
        display_name: displayNameField.input.value || "",
        country_code: countryField.select.value || "",
        preferred_locale: localeField.select.value || "",
        timezone: timezoneField.select.value || "",
      };
    }

    /**
     * Description:
     *   Return whether the editor currently has unsaved changes.
     *
     * Requirements:
     *   - Compare all supported fields against the last accepted server payload.
     *
     * @returns {boolean} True when local edits are unsaved.
     */
    function hasUnsavedChanges() {
      const current = currentState();
      return (
        current.display_name !== lastSavedState.display_name
        || current.country_code !== lastSavedState.country_code
        || current.preferred_locale !== lastSavedState.preferred_locale
        || current.timezone !== lastSavedState.timezone
      );
    }

    /**
     * Description:
     *   Refresh the dirty-state affordances for the panel.
     *
     * Requirements:
     *   - Disable save when no local edit is pending.
     *   - Keep unsaved changes visibly labelled for the user.
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
     *   - Display the backing config path and update timestamp when available.
     *
     * @param {object} payload User-settings payload returned by the server.
     */
    function renderMetadata(payload) {
      metadata.replaceChildren();
      const updatedAt = payload.updated_at || "not saved";
      [
        ["Path", payload.path || "unknown"],
        ["Updated", updatedAt],
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
     *   Apply one server user-settings payload to the editor.
     *
     * Requirements:
     *   - Treat the loaded values as the new saved baseline.
     *
     * @param {object} payload User-settings payload returned by the server.
     */
    function repopulateLocaleOptions(selectedCountryCode, selectedLocale) {
      const localeOptions = localeOptionsByCountry[selectedCountryCode] || payloadLocaleOptionsFallback();
      return applySelectOptions(localeField.select, localeOptions, selectedLocale);
    }

    /**
     * Description:
     *   Replace the timezone options for the currently selected country.
     *
     * Requirements:
     *   - Preserve the selected timezone when it is valid for the selected country.
     *   - Fall back to the first available timezone option when the current value is no longer valid.
     *
     * @param {string} selectedCountryCode Selected two-letter country code.
     * @param {string} selectedTimezone Preferred selected timezone value.
     * @returns {string} Final selected timezone value after the options are applied.
     */
    function repopulateTimezoneOptions(selectedCountryCode, selectedTimezone) {
      const timezoneOptions = timezoneOptionsByCountry[selectedCountryCode] || payloadTimezoneOptionsFallback();
      return applySelectOptions(timezoneField.select, timezoneOptions, selectedTimezone);
    }

    /**
     * Description:
     *   Return the current fallback locale options from the locale select itself.
     *
     * Requirements:
     *   - Preserve already-rendered options when a country has no explicit locale mapping.
     *
     * @returns {Array<object>} Fallback option payloads based on the current select content.
     */
    function payloadLocaleOptionsFallback() {
      return (localeField.select.children || []).map(function mapOption(optionNode) {
        return {
          value: optionNode.value,
          label: optionNode.textContent,
        };
      });
    }

    /**
     * Description:
     *   Return the current fallback timezone options from the timezone select itself.
     *
     * Requirements:
     *   - Preserve already-rendered options when a country has no explicit mapping.
     *
     * @returns {Array<object>} Fallback option payloads based on the current select content.
     */
    function payloadTimezoneOptionsFallback() {
      return (timezoneField.select.children || []).map(function mapOption(optionNode) {
        return {
          value: optionNode.value,
          label: optionNode.textContent,
        };
      });
    }

    /**
     * Description:
     *   Apply one server user-settings payload to the editor.
     *
     * Requirements:
     *   - Treat the loaded values as the new saved baseline.
     *   - Refresh the country-filtered timezone dropdown from server-provided option maps.
     *
     * @param {object} payload User-settings payload returned by the server.
     */
    function applySettingsPayload(payload) {
      lastSavedState = {
        display_name: payload.display_name || "",
        country_code: payload.country_code || "",
        preferred_locale: payload.preferred_locale || "",
        timezone: payload.timezone || "",
      };
      localeOptionsByCountry = payload.locale_options_by_country || {};
      timezoneOptionsByCountry = payload.timezone_options_by_country || {};
      displayNameField.input.value = lastSavedState.display_name;
      applySelectOptions(countryField.select, payload.country_options || [], lastSavedState.country_code);
      repopulateLocaleOptions(countryField.select.value, lastSavedState.preferred_locale);
      repopulateTimezoneOptions(countryField.select.value, lastSavedState.timezone);
      renderMetadata(payload);
      refreshDirtyState();
      setStatus("Loaded saved user settings.", "saved");
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
      if (hasUnsavedChanges() && !globalScope.confirm("Discard unsaved settings edits and reload?")) {
        return;
      }
      setStatus("Loading settings...", "loading");
      try {
        const response = await globalScope.fetch(SETTINGS_PATH);
        applySettingsPayload(await parseSettingsResponse(response));
      } catch (error) {
        setStatus(String(error.message || error), "error");
      }
    }

    /**
     * Description:
     *   Persist the edited settings to the server.
     *
     * Requirements:
     *   - Surface backend validation failures without discarding local edits.
     *
     * @returns {Promise<void>} Promise that resolves when saving completes.
     */
    async function saveSettings() {
      setStatus("Saving settings...", "loading");
      try {
        const response = await globalScope.fetch(SETTINGS_PATH, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: displayNameField.input.value,
            country_code: countryField.select.value,
            preferred_locale: localeField.select.value,
            timezone: timezoneField.select.value,
          }),
        });
        const payload = await parseSettingsResponse(response);
        applySettingsPayload(payload);
        globalScope.dispatchEvent(
          new globalScope.CustomEvent("faith:user-settings-updated", {
            detail: {
              display_name: payload.display_name || null,
              country_code: payload.country_code || null,
              preferred_locale: payload.preferred_locale || null,
              timezone: payload.timezone || null,
              updated_at: payload.updated_at || null,
            },
          }),
        );
        setStatus("Settings saved. Future agent turns will use them.", "saved");
      } catch (error) {
        setStatus(String(error.message || error), "error");
      }
    }

    /**
     * Description:
     *   Confirm whether local settings edits may be discarded for one action.
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
      return globalScope.confirm(`Discard unsaved settings edits and ${actionLabel}?`);
    }

    /**
     * Description:
     *   Warn the user before browser navigation when local edits are unsaved.
     *
     * Requirements:
     *   - Leave normal navigation untouched when there are no unsaved edits.
     *
     * @param {BeforeUnloadEvent} event Browser unload event.
     */
    function handleBeforeUnload(event) {
      if (destroyed || !hasUnsavedChanges()) {
        return;
      }
      event.preventDefault();
      event.returnValue = "";
    }

    [displayNameField.input].forEach(function bindDirtyTracking(input) {
      input.addEventListener("input", refreshDirtyState);
    });
    saveButton.addEventListener("click", function onSaveClick() {
      void saveSettings();
    });
    reloadButton.addEventListener("click", function onReloadClick() {
      void loadSettings();
    });
    detectTimezoneButton.addEventListener("click", function onDetectTimezoneClick() {
      if (!browserTimezone) {
        return;
      }
      const matchingCountryCode = Object.keys(timezoneOptionsByCountry).find(function findCountryCode(countryCode) {
        return (timezoneOptionsByCountry[countryCode] || []).some(function hasTimezone(option) {
          return option.value === browserTimezone;
        });
      });
      if (matchingCountryCode) {
        countryField.select.value = matchingCountryCode;
        repopulateLocaleOptions(matchingCountryCode, localeField.select.value);
        repopulateTimezoneOptions(matchingCountryCode, browserTimezone);
      }
      refreshDirtyState();
    });
    countryField.select.addEventListener("change", function onCountryChange() {
      const selectedCountryCode = countryField.select.value;
      repopulateLocaleOptions(selectedCountryCode, localeField.select.value);
      repopulateTimezoneOptions(selectedCountryCode, timezoneField.select.value);
      if (localeField.select.value !== lastSavedState.preferred_locale) {
        localeField.select.dispatch("change");
        return;
      }
      refreshDirtyState();
    });
    localeField.select.addEventListener("change", function onLocaleChange() {
      const matchingCountryCode = Object.keys(localeOptionsByCountry).find(function findCountryCode(countryCode) {
        return (localeOptionsByCountry[countryCode] || []).some(function hasLocale(option) {
          return option.value === localeField.select.value;
        });
      });
      if (matchingCountryCode && countryField.select.value !== matchingCountryCode) {
        countryField.select.value = matchingCountryCode;
        repopulateLocaleOptions(matchingCountryCode, localeField.select.value);
        repopulateTimezoneOptions(matchingCountryCode, timezoneField.select.value);
      }
      refreshDirtyState();
    });
    timezoneField.select.addEventListener("change", refreshDirtyState);
    globalScope.addEventListener("beforeunload", handleBeforeUnload);

    void loadSettings();

    return {
      destroy() {
        destroyed = true;
        globalScope.removeEventListener("beforeunload", handleBeforeUnload);
      },
      getState() {
        return {
          display_name: displayNameField.input.value,
          country_code: countryField.select.value,
          preferred_locale: localeField.select.value,
          timezone: timezoneField.select.value,
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

  globalScope.faithUserSettingsPanel = {
    SETTINGS_PATH: SETTINGS_PATH,
    detectBrowserTimezone: detectBrowserTimezone,
    mountPanel: mountPanel,
    parseSettingsResponse: parseSettingsResponse,
  };
})(window);
