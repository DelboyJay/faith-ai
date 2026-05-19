/**
 * Description:
 *   Register the FAITH Effective Context inspection panel runtime.
 *
 * Requirements:
 *   - Fetch one persisted redacted effective-context snapshot from the same-origin Web UI API.
 *   - Render the snapshot read-only with include graph, warnings, and hash/session metadata.
 */

(function initialiseFaithEffectiveContextPanel(globalScope) {
  function buildSnapshotUrl(state) {
    if (!state.sessionId || !state.turnId) {
      return "";
    }
    return `/api/logs/effective-context/${encodeURIComponent(state.sessionId)}/${encodeURIComponent(
      state.turnId,
    )}`;
  }

  globalScope.faithEffectiveContextPanel = {
    /**
     * Description:
     *   Mount one effective-context inspector into the supplied target element.
     *
     * Requirements:
     *   - Keep the panel read-only and fetch-driven.
     *
     * @param {HTMLElement} target Panel mount element.
     * @param {object} state Initial panel state.
     * @returns {object} Cleanup handle for the mounted panel.
     */
    mountPanel(target, state) {
      const panelState = Object.assign(
        { sessionId: "", turnId: "", snapshot: null, loading: false, error: "", destroyed: false },
        state || {},
      );

      const wrapper = document.createElement("section");
      wrapper.className = "faith-panel faith-log-panel faith-log-panel--effective-context";

      const filters = document.createElement("div");
      filters.className = "faith-log-panel__filters";

      const sessionInput = document.createElement("input");
      sessionInput.className = "faith-log-panel__input";
      sessionInput.placeholder = "Session ID";
      sessionInput.value = panelState.sessionId;

      const turnInput = document.createElement("input");
      turnInput.className = "faith-log-panel__input";
      turnInput.placeholder = "Turn ID";
      turnInput.value = panelState.turnId;

      const loadButton = document.createElement("button");
      loadButton.type = "button";
      loadButton.className = "faith-toolbar__button";
      loadButton.textContent = "Load Snapshot";

      filters.appendChild(sessionInput);
      filters.appendChild(turnInput);
      filters.appendChild(loadButton);

      const errorBanner = document.createElement("p");
      errorBanner.className = "faith-log-panel__error";
      errorBanner.hidden = true;

      const content = document.createElement("div");
      content.className = "faith-log-panel__content";

      wrapper.appendChild(filters);
      wrapper.appendChild(errorBanner);
      wrapper.appendChild(content);
      target.replaceChildren(wrapper);

      function render() {
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
          empty.textContent = "No effective-context snapshot loaded.";
          content.appendChild(empty);
          return;
        }
        content.appendChild(
          globalScope.faithLogPanelCommon.renderRecordCard(panelState.snapshot, [
            ["Session", panelState.snapshot.session_id],
            ["Turn", panelState.snapshot.turn_id],
            ["Snapshot", panelState.snapshot.snapshot_id],
            ["Hash", panelState.snapshot.hash || "—"],
            ["Compiled context", panelState.snapshot.compiled_context],
            ["Include graph", JSON.stringify(panelState.snapshot.include_graph || [])],
            ["Warnings", (panelState.snapshot.warnings || []).join(", ") || "—"],
          ]),
        );
      }

      async function loadSnapshot() {
        panelState.loading = true;
        panelState.error = "";
        render();
        try {
          const url = buildSnapshotUrl(panelState);
          if (!url) {
            throw new Error("Session ID and turn ID are required.");
          }
          const response = await globalScope.fetch(url);
          if (!response.ok) {
            throw new Error(`Effective-context snapshot failed with status ${response.status}`);
          }
          panelState.snapshot = await response.json();
        } catch (error) {
          panelState.error = String(error.message || error);
        } finally {
          panelState.loading = false;
          if (!panelState.destroyed) {
            render();
          }
        }
      }

      sessionInput.addEventListener("input", function onSessionInput() {
        panelState.sessionId = sessionInput.value;
      });
      turnInput.addEventListener("input", function onTurnInput() {
        panelState.turnId = turnInput.value;
      });
      loadButton.addEventListener("click", function onLoadClick() {
        void loadSnapshot();
      });

      render();
      return {
        destroy() {
          panelState.destroyed = true;
        },
      };
    },
  };
})(window);
