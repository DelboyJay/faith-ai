/**
 * Description:
 *   Register the FAITH Effective Context inspection panel runtime.
 *
 * Requirements:
 *   - Follow the currently selected session automatically.
 *   - Hide raw internal session and turn identifiers from the default user flow.
 */

(function initialiseFaithEffectiveContextPanel(globalScope) {
  const LEGACY_SESSION_CHANGE_EVENT = "faith:session-selected";
  const SESSION_CHANGE_EVENT = "faith:workspace-session-change";

  /**
   * Description:
   *   Build the effective-context snapshot URL for one selected session state.
   *
   * Requirements:
   *   - Use the session-specific latest route when no explicit turn is selected.
   *   - Use the selected turn-specific route when the user picks a persisted task snapshot.
   *
   * @param {object} state: Effective-context panel state.
   * @returns {string} Same-origin snapshot URL or an empty string when no session is selected.
   */
  function buildSnapshotUrl(state) {
    if (!state.sessionId) {
      return "";
    }
    if (state.selectedTurnId && state.selectedTurnId !== "latest") {
      return `/api/logs/effective-context/${encodeURIComponent(state.sessionId)}/${encodeURIComponent(
        state.selectedTurnId,
      )}`;
    }
    return `/api/logs/effective-context/${encodeURIComponent(state.sessionId)}/latest`;
  }

  globalScope.faithEffectiveContextPanel = {
    /**
     * Description:
     *   Mount one effective-context inspector into the supplied target element.
     *
     * Requirements:
     *   - Keep the panel read-only and fetch-driven.
     *   - Follow workspace session changes without exposing raw identifier inputs.
     *
     * @param {HTMLElement} target: Panel mount element.
     * @param {object} state: Initial panel state.
     * @returns {object} Cleanup handle for the mounted panel.
     */
    mountPanel(target, state) {
      const panelState = Object.assign(
        {
          sessionId: "",
          sessionName: "",
          selectedTurnId: "latest",
          tasks: [],
          snapshot: null,
          loading: false,
          error: "",
          destroyed: false,
        },
        state || {},
      );

      const wrapper = document.createElement("section");
      wrapper.className = "faith-panel faith-log-panel faith-log-panel--effective-context";

      const header = document.createElement("div");
      header.className = "faith-log-panel__filters";

      const sessionLabel = document.createElement("span");
      sessionLabel.className = "faith-effective-context__label";

      const snapshotLabel = document.createElement("span");
      snapshotLabel.className = "faith-effective-context__label";

      const snapshotSelect = document.createElement("select");
      snapshotSelect.className = "faith-log-panel__input";

      const loadButton = document.createElement("button");
      loadButton.type = "button";
      loadButton.className = "faith-toolbar__button";
      loadButton.textContent = "Load Snapshot";

      header.appendChild(sessionLabel);
      header.appendChild(snapshotLabel);
      header.appendChild(snapshotSelect);
      header.appendChild(loadButton);

      const errorBanner = document.createElement("p");
      errorBanner.className = "faith-log-panel__error";
      errorBanner.hidden = true;

      const content = document.createElement("div");
      content.className = "faith-log-panel__content";

      wrapper.appendChild(header);
      wrapper.appendChild(errorBanner);
      wrapper.appendChild(content);
      target.replaceChildren(wrapper);

      /**
       * Description:
       *   Refresh the snapshot selector from the selected session tasks.
       *
       * Requirements:
       *   - Always provide a `Latest` option for the current session.
       *   - Keep any selected persisted task ID when it still exists.
       */
      function renderSnapshotOptions() {
        snapshotSelect.replaceChildren();
        const latestOption = document.createElement("option");
        latestOption.value = "latest";
        latestOption.textContent = "Latest";
        snapshotSelect.appendChild(latestOption);
        (panelState.tasks || []).forEach(function appendTaskOption(task) {
          const option = document.createElement("option");
          option.value = String(task.task_id || "");
          option.textContent = String(task.goal || task.task_id || "Snapshot");
          snapshotSelect.appendChild(option);
        });
        const validSelection =
          panelState.selectedTurnId === "latest" ||
          (panelState.tasks || []).some(function hasTask(task) {
            return String(task.task_id || "") === panelState.selectedTurnId;
          });
        snapshotSelect.value = validSelection ? panelState.selectedTurnId : "latest";
      }

      /**
       * Description:
       *   Render the current effective-context panel state.
       *
       * Requirements:
       *   - Show friendly session and snapshot labels.
       *   - Keep the panel readable when no selected session has a persisted snapshot yet.
       */
      function render() {
        sessionLabel.textContent = `Session: ${panelState.sessionName || "No session selected"}`;
        snapshotLabel.textContent = `Snapshot: ${panelState.selectedTurnId === "latest" ? "Latest" : panelState.selectedTurnId}`;
        renderSnapshotOptions();
        loadButton.disabled = !panelState.sessionId;
        errorBanner.hidden = !panelState.error;
        errorBanner.textContent = panelState.error;
        content.replaceChildren();
        if (panelState.loading) {
          const loading = document.createElement("p");
          loading.className = "faith-log-panel__empty";
          loading.textContent = "Loading…";
          content.appendChild(loading);
          return;
        }
        if (!panelState.snapshot) {
          const empty = document.createElement("p");
          empty.className = "faith-log-panel__empty";
          empty.textContent = panelState.sessionId
            ? "No effective-context snapshot has been recorded for this session yet."
            : "No effective-context snapshot loaded.";
          content.appendChild(empty);
          return;
        }
        content.appendChild(
          globalScope.faithLogPanelCommon.renderRecordCard(panelState.snapshot, [
            ["Session", panelState.snapshot.session_name || panelState.sessionName || panelState.snapshot.session_id],
            ["Turn", panelState.snapshot.turn_id],
            ["Snapshot", panelState.snapshot.snapshot_id],
            ["Hash", panelState.snapshot.hash || "—"],
            ["Compiled context", panelState.snapshot.compiled_context],
            ["Include graph", JSON.stringify(panelState.snapshot.include_graph || [])],
            ["Warnings", (panelState.snapshot.warnings || []).join(", ") || "—"],
          ]),
        );
      }

      /**
       * Description:
       *   Load the selected effective-context snapshot for the current session.
       *
       * Requirements:
       *   - Fail cleanly when no session is selected yet.
       *   - Keep the previous snapshot visible only when the fetch succeeds.
       */
      async function loadSnapshot() {
        panelState.loading = true;
        panelState.error = "";
        render();
        try {
          const url = buildSnapshotUrl(panelState);
          if (!url) {
            throw new Error("No session has been selected yet.");
          }
          const response = await globalScope.fetch(url);
          if (!response.ok) {
            throw new Error(`Effective-context snapshot failed with status ${response.status}`);
          }
          panelState.snapshot = await response.json();
        } catch (error) {
          panelState.snapshot = null;
          panelState.error = String(error.message || error);
        } finally {
          panelState.loading = false;
          if (!panelState.destroyed) {
            render();
          }
        }
      }

      /**
       * Description:
       *   Apply one workspace-level session change to the effective-context panel.
       *
       * Requirements:
       *   - Follow the selected session automatically.
       *   - Auto-load the latest snapshot for the new session.
       *
       * @param {Event|object} event: Session-change event payload.
       */
      function onSessionChange(event) {
        const detail = event && event.detail ? event.detail : event || {};
        panelState.sessionId = String(detail.sessionId || "");
        panelState.sessionName = String(detail.sessionName || "");
        panelState.tasks = Array.isArray(detail.tasks) ? detail.tasks : [];
        panelState.selectedTurnId = "latest";
        panelState.snapshot = null;
        render();
        if (panelState.sessionId) {
          void loadSnapshot();
        }
      }

      snapshotSelect.addEventListener("change", function onSnapshotChange() {
        panelState.selectedTurnId = snapshotSelect.value || "latest";
        render();
      });
      loadButton.addEventListener("click", function onLoadClick() {
        void loadSnapshot();
      });
      globalScope.addEventListener(SESSION_CHANGE_EVENT, onSessionChange);
      globalScope.addEventListener(LEGACY_SESSION_CHANGE_EVENT, onSessionChange);

      render();
      if (panelState.sessionId) {
        void loadSnapshot();
      }
      return {
        destroy() {
          panelState.destroyed = true;
          if (typeof globalScope.removeEventListener === "function") {
            globalScope.removeEventListener(SESSION_CHANGE_EVENT, onSessionChange);
            globalScope.removeEventListener(LEGACY_SESSION_CHANGE_EVENT, onSessionChange);
          }
        },
      };
    },
  };
})(window);
