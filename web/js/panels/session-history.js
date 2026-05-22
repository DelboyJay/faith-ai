/**
 * Description:
 *   Register the FAITH Session History selector runtime.
 *
 * Requirements:
 *   - Render a chat-style session selector grouped into active and archived sessions.
 *   - Activate non-archived sessions through the same-origin backend before broadcasting shared session state.
 *   - Keep the list focused on user-facing session names rather than raw UUID-led detail blocks.
 */

(function initialiseFaithSessionHistoryPanel(globalScope) {
  const SESSION_CHANGE_EVENT = "faith:workspace-session-change";
  const SESSION_SWITCHING_EVENT = "faith:workspace-session-switching";
  const SESSION_TRANSCRIPT_PATH = "/api/pa/transcript";
  const SESSION_ORDER_STORAGE_KEY = "faith_session_history_order_v1";
  const DEFAULT_ORDER = "mru";

  /**
   * Description:
   *   Return one user-facing session name from a persisted session payload.
   *
   * Requirements:
   *   - Prefer the dedicated session name and fall back to a generic label.
   *
   * @param {object} session: Persisted session payload from the backend.
   * @returns {string} User-facing session name.
   */
  function getSessionDisplayName(session) {
    return String((session && session.name) || "New Session");
  }

  /**
   * Description:
   *   Return a case-insensitive name-match result for the current search filter.
   *
   * Requirements:
   *   - Use contains-match semantics for predictable list filtering.
   *
   * @param {object} session: Persisted session payload from the backend.
   * @param {string} searchText: Current search text.
   * @returns {boolean} True when the session should remain visible.
   */
  function matchesSearch(session, searchText) {
    if (!searchText) {
      return true;
    }
    return getSessionDisplayName(session).toLowerCase().includes(searchText.toLowerCase());
  }

  /**
   * Description:
   *   Sort one session list according to the selected list-order preference.
   *
   * Requirements:
   *   - Support MRU, A-Z, and Z-A ordering modes.
   *   - Use last-used timestamps when available for MRU ordering.
   *
   * @param {Array<object>} sessions: Session payloads to order.
   * @param {string} orderMode: Selected ordering mode.
   * @returns {Array<object>} Sorted session payloads.
   */
  function sortSessions(sessions, orderMode) {
    const items = sessions.slice();
    if (orderMode === "az") {
      return items.sort(function compareAz(left, right) {
        return getSessionDisplayName(left).localeCompare(getSessionDisplayName(right));
      });
    }
    if (orderMode === "za") {
      return items.sort(function compareZa(left, right) {
        return getSessionDisplayName(right).localeCompare(getSessionDisplayName(left));
      });
    }
    return items.sort(function compareMru(left, right) {
      const leftStamp = String(left.last_used_at || left.started_at || "");
      const rightStamp = String(right.last_used_at || right.started_at || "");
      if (leftStamp === rightStamp) {
        return getSessionDisplayName(left).localeCompare(getSessionDisplayName(right));
      }
      return rightStamp.localeCompare(leftStamp);
    });
  }

  /**
   * Description:
   *   Build the metadata tooltip text for one session info affordance.
   *
   * Requirements:
   *   - Surface the UUID and timing metadata needed to disambiguate duplicate names.
   *
   * @param {object} session: Persisted session payload from the backend.
   * @returns {string} Session metadata tooltip text.
   */
  function buildSessionTooltip(session) {
    return [
      `UUID: ${String(session.session_id || "")}`,
      `Started: ${String(session.started_at || "—")}`,
      `Last used: ${String(session.last_used_at || session.started_at || "—")}`,
      `Status: ${session.archived ? "archived" : String(session.status || "active")}`,
    ].join("\n");
  }

  /**
   * Description:
   *   Persist the selected session-order preference when browser storage is available.
   *
   * Requirements:
   *   - Fail quietly when local storage is unavailable.
   *
   * @param {string} value: Selected ordering mode to persist.
   */
  function persistOrderPreference(value) {
    try {
      if (globalScope.localStorage) {
        globalScope.localStorage.setItem(SESSION_ORDER_STORAGE_KEY, value);
      }
    } catch (error) {
      return;
    }
  }

  /**
   * Description:
   *   Load the persisted session-order preference when browser storage is available.
   *
   * Requirements:
   *   - Fall back to MRU ordering when no saved preference exists.
   *
   * @returns {string} Persisted or default ordering mode.
   */
  function loadOrderPreference() {
    try {
      if (globalScope.localStorage) {
        return globalScope.localStorage.getItem(SESSION_ORDER_STORAGE_KEY) || DEFAULT_ORDER;
      }
    } catch (error) {
      return DEFAULT_ORDER;
    }
    return DEFAULT_ORDER;
  }

  /**
   * Description:
   *   Broadcast a tiny workspace-level session-switching state update.
   *
   * Requirements:
   *   - Let other session-bound panels block sends during backend handover.
   *
   * @param {object} detail: Session-switching detail payload.
   */
  function dispatchSessionSwitching(detail) {
    if (typeof globalScope.dispatchEvent !== "function" || typeof globalScope.CustomEvent !== "function") {
      return;
    }
    globalScope.dispatchEvent(new globalScope.CustomEvent(SESSION_SWITCHING_EVENT, { detail: detail }));
  }

  /**
   * Description:
   *   Broadcast the newly selected session payload to all session-bound panels.
   *
   * Requirements:
   *   - Preserve the active session ID, name, transcript, and task metadata for downstream panels.
   *
   * @param {object} detail: Shared session payload.
   */
  function dispatchSessionChange(detail) {
    if (typeof globalScope.dispatchEvent !== "function" || typeof globalScope.CustomEvent !== "function") {
      return;
    }
    globalScope.dispatchEvent(new globalScope.CustomEvent(SESSION_CHANGE_EVENT, { detail: detail }));
  }

  globalScope.faithSessionHistoryPanel = {
    /**
     * Description:
     *   Mount the session-history selector into the supplied target element.
     *
     * Requirements:
     *   - Keep the selector compact and user-facing.
     *   - Expose inline restore, archive, export, and delete actions.
     *
     * @param {HTMLElement} target: Panel mount element.
     * @returns {object} Cleanup handle for the mounted panel.
     */
    mountPanel(target) {
      const state = {
        sessions: [],
        selectedSessionId: "",
        order: loadOrderPreference(),
        searchText: "",
        showArchived: false,
        loading: false,
        switching: false,
        error: "",
        destroyed: false,
      };

      const wrapper = document.createElement("section");
      wrapper.className = "faith-panel faith-log-panel faith-log-panel--session-history";

      const toolbar = document.createElement("div");
      toolbar.className = "faith-log-panel__filters";

      const newSessionButton = document.createElement("button");
      newSessionButton.type = "button";
      newSessionButton.className = "faith-toolbar__button";
      newSessionButton.textContent = "New Session";

      const refreshButton = document.createElement("button");
      refreshButton.type = "button";
      refreshButton.className = "faith-toolbar__button";
      refreshButton.textContent = "Refresh";

      toolbar.appendChild(newSessionButton);
      toolbar.appendChild(refreshButton);

      const controls = document.createElement("div");
      controls.className = "faith-session-history__controls";

      const searchInput = document.createElement("input");
      searchInput.className = "faith-log-panel__input";
      searchInput.placeholder = "Search active sessions";

      const orderSelect = document.createElement("select");
      orderSelect.className = "faith-log-panel__input";
      [
        { value: "mru", label: "Most Recently Used" },
        { value: "az", label: "Alphabetically A-Z" },
        { value: "za", label: "Alphabetically Z-A" },
      ].forEach(function appendOrderOption(option) {
        const element = document.createElement("option");
        element.value = option.value;
        element.textContent = option.label;
        orderSelect.appendChild(element);
      });
      orderSelect.value = state.order;

      const archivedToggleLabel = document.createElement("label");
      archivedToggleLabel.className = "faith-session-history__toggle";
      const archivedToggle = document.createElement("input");
      archivedToggle.type = "checkbox";
      archivedToggle.checked = state.showArchived;
      const archivedToggleText = document.createElement("span");
      archivedToggleText.textContent = "Show Archived Sessions";
      archivedToggleLabel.appendChild(archivedToggle);
      archivedToggleLabel.appendChild(archivedToggleText);

      controls.appendChild(searchInput);
      controls.appendChild(orderSelect);
      controls.appendChild(archivedToggleLabel);

      const errorBanner = document.createElement("p");
      errorBanner.className = "faith-log-panel__error";
      errorBanner.hidden = true;

      const content = document.createElement("div");
      content.className = "faith-log-panel__content";

      wrapper.appendChild(toolbar);
      wrapper.appendChild(controls);
      wrapper.appendChild(errorBanner);
      wrapper.appendChild(content);
      target.replaceChildren(wrapper);

      /**
       * Description:
       *   Render one visible session group into the selector content area.
       *
       * Requirements:
       *   - Keep the group header visible even when search removes all matching rows.
       *   - Render only user-facing session names in the primary row action.
       *
       * @param {string} title: Visible group header.
       * @param {Array<object>} sessions: Session payloads in the current group.
       * @param {boolean} archived: Whether the group contains archived sessions.
       */
      function renderGroup(title, sessions, archived) {
        const section = document.createElement("section");
        section.className = "faith-session-history__group";

        const header = document.createElement("h3");
        header.className = "faith-log-panel__summary-title";
        header.textContent = title;
        section.appendChild(header);

        if (archived && !state.showArchived) {
          const hiddenNote = document.createElement("p");
          hiddenNote.className = "faith-log-panel__empty";
          hiddenNote.textContent = "Archived sessions are hidden.";
          section.appendChild(hiddenNote);
          content.appendChild(section);
          return;
        }

        if (sessions.length === 0) {
          const empty = document.createElement("p");
          empty.className = "faith-log-panel__empty";
          empty.textContent = "No matches";
          section.appendChild(empty);
          content.appendChild(section);
          return;
        }

        const list = document.createElement("div");
        list.className = "faith-session-history__list";
        sessions.forEach(function appendSessionRow(session) {
          const row = document.createElement("div");
          row.className = "faith-session-history__row";

          const sessionButton = document.createElement("button");
          sessionButton.type = "button";
          sessionButton.className = "faith-log-panel__session-button";
          sessionButton.textContent = getSessionDisplayName(session);
          sessionButton.title = archived
            ? "Archived session. Restore it to make it active again."
            : buildSessionTooltip(session);
          if (session.session_id === state.selectedSessionId) {
            sessionButton.classList.add("faith-log-panel__session-button--active");
          }
          if (archived) {
            sessionButton.disabled = true;
            sessionButton.classList.add("faith-log-panel__session-button--archived");
          }
          sessionButton.addEventListener("click", function onSessionClick() {
            if (!archived) {
              void activateSession(session);
            }
          });

          const infoButton = document.createElement("button");
          infoButton.type = "button";
          infoButton.className = "faith-session-history__meta-button";
          infoButton.textContent = "i";
          infoButton.title = buildSessionTooltip(session);

          const actions = document.createElement("div");
          actions.className = "faith-session-history__row-actions";

          /**
           * Description:
           *   Build one inline session action button.
           *
           * Requirements:
           *   - Reuse backend-advertised action URLs and fail cleanly when a call fails.
           *
           * @param {string} label: Visible action label.
           * @param {string} url: Same-origin action URL.
           * @param {string} method: HTTP method to use.
           * @param {object|undefined} body: Optional JSON payload.
           */
          function buildActionButton(label, url, method, body) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "faith-toolbar__button";
            button.textContent = label;
            button.addEventListener("click", function onActionClick() {
              void runAction(url, method, body);
            });
            return button;
          }

          const sessionActions = session.actions || {};
          if (archived && sessionActions.unarchive) {
            actions.appendChild(buildActionButton("Restore", sessionActions.unarchive, "POST"));
          }
          if (!archived && sessionActions.archive) {
            actions.appendChild(buildActionButton("Archive", sessionActions.archive, "POST"));
          }
          if (!archived && sessionActions.export) {
            actions.appendChild(
              buildActionButton("Export", sessionActions.export, "POST", {
                export_scope: "session-only",
              }),
            );
          }
          if (sessionActions.delete) {
            actions.appendChild(buildActionButton("Delete", sessionActions.delete, "DELETE"));
          }

          row.appendChild(sessionButton);
          row.appendChild(infoButton);
          row.appendChild(actions);
          list.appendChild(row);
        });
        section.appendChild(list);
        content.appendChild(section);
      }

      /**
       * Description:
       *   Render the current selector state.
       *
       * Requirements:
       *   - Keep active and archived groups visible even when filtered.
       */
      function render() {
        errorBanner.hidden = !state.error;
        errorBanner.textContent = state.error;
        content.replaceChildren();

        if (state.loading && state.sessions.length === 0) {
          const loading = document.createElement("p");
          loading.className = "faith-log-panel__empty";
          loading.textContent = "Loading…";
          content.appendChild(loading);
          return;
        }

        const filtered = state.sessions.filter(function keepMatchingSession(session) {
          return matchesSearch(session, state.searchText);
        });
        const activeSessions = sortSessions(
          filtered.filter(function keepActive(session) {
            return !session.archived;
          }),
          state.order,
        );
        const archivedSessions = sortSessions(
          filtered.filter(function keepArchived(session) {
            return session.archived;
          }),
          state.order,
        );

        renderGroup("Active Sessions", activeSessions, false);
        renderGroup("Archived Sessions", archivedSessions, true);
      }

      /**
       * Description:
       *   Run one inline session action then refresh the visible selector state.
       *
       * Requirements:
       *   - Preserve the currently selected session when it still exists after the mutation.
       *
       * @param {string} url: Same-origin action URL.
       * @param {string} method: HTTP method to use.
       * @param {object|undefined} body: Optional JSON payload.
       */
      async function runAction(url, method, body) {
        state.error = "";
        render();
        const response = await globalScope.fetch(url, {
          method: method,
          headers: body ? { "Content-Type": "application/json" } : undefined,
          body: body ? JSON.stringify(body) : undefined,
        });
        if (!response.ok) {
          state.error = `Session action failed with status ${response.status}`;
          render();
          return;
        }
        await loadSessions();
      }

      /**
       * Description:
       *   Refresh the current-session selection from the shared transcript endpoint.
       *
       * Requirements:
       *   - Fall back quietly when the transcript endpoint is unavailable.
       *   - Broadcast the currently selected transcript so other panels start in sync.
       */
      async function syncCurrentSessionFromBackend() {
        if (typeof globalScope.fetch !== "function") {
          return;
        }
        try {
          const response = await globalScope.fetch(SESSION_TRANSCRIPT_PATH);
          if (!response || response.ok !== true) {
            return;
          }
          const payload = await response.json();
          if (!payload || !payload.session_id) {
            return;
          }
          state.selectedSessionId = String(payload.session_id);
          const session = state.sessions.find(function findSession(entry) {
            return entry.session_id === state.selectedSessionId;
          });
          dispatchSessionChange({
            sessionId: state.selectedSessionId,
            sessionName: session ? getSessionDisplayName(session) : "New Session",
            transcript: Array.isArray(payload.messages) ? payload.messages : [],
            tasks: [],
            archived: false,
          });
          render();
        } catch (error) {
          return;
        }
      }

      /**
       * Description:
       *   Refresh the persisted session list from the backend.
       *
       * Requirements:
       *   - Preserve the selected session when it still exists.
       *   - Fall back to the first active session when no selection remains.
       */
      async function loadSessions() {
        state.loading = true;
        state.error = "";
        render();
        try {
          const response = await globalScope.fetch("/api/logs/sessions");
          if (!response.ok) {
            throw new Error(`Session list failed with status ${response.status}`);
          }
          const payload = await response.json();
          state.sessions = Array.isArray(payload.items) ? payload.items : [];
          const selectedStillExists = state.sessions.some(function keepSelected(session) {
            return session.session_id === state.selectedSessionId;
          });
          if (!selectedStillExists) {
            const firstActiveSession = state.sessions.find(function findActive(session) {
              return !session.archived;
            });
            state.selectedSessionId = firstActiveSession ? String(firstActiveSession.session_id) : "";
          }
        } catch (error) {
          state.error = String(error.message || error);
        } finally {
          state.loading = false;
          if (!state.destroyed) {
            render();
          }
        }
      }

      /**
       * Description:
       *   Activate one selected non-archived session through the backend.
       *
       * Requirements:
       *   - Briefly block other session-bound panels until the backend confirms the switch.
       *   - Broadcast the selected transcript and task metadata after the activation succeeds.
       *   - Revert to the previous selection if the activation fails.
       *
       * @param {object} session: Selected non-archived session payload.
       */
      async function activateSession(session) {
        const previousSessionId = state.selectedSessionId;
        state.switching = true;
        state.error = "";
        dispatchSessionSwitching({ active: true, sessionId: session.session_id });
        render();
        try {
          const response = await globalScope.fetch(
            `/api/logs/sessions/${encodeURIComponent(session.session_id)}/activate`,
            { method: "POST" },
          );
          if (!response.ok) {
            throw new Error(`Session activation failed with status ${response.status}`);
          }
          const payload = await response.json();
          state.selectedSessionId = String(session.session_id);
          dispatchSessionChange({
            sessionId: String(session.session_id),
            sessionName: getSessionDisplayName(session),
            transcript: Array.isArray(payload.transcript) ? payload.transcript : [],
            tasks: Array.isArray(payload.tasks) ? payload.tasks : [],
            archived: false,
          });
        } catch (error) {
          state.selectedSessionId = previousSessionId;
          state.error = String(error.message || error);
        } finally {
          state.switching = false;
          dispatchSessionSwitching({ active: false, sessionId: state.selectedSessionId });
          render();
        }
      }

      /**
       * Description:
       *   Start a brand-new PA session through the same-origin backend proxy.
       *
       * Requirements:
       *   - Refresh the selector after the session is created.
       *   - Broadcast an empty transcript payload for the new active session immediately.
       */
      async function createNewSession() {
        state.error = "";
        render();
        const response = await globalScope.fetch("/api/pa/session/new", { method: "POST" });
        if (!response.ok) {
          state.error = `New session failed with status ${response.status}`;
          render();
          return;
        }
        const payload = await response.json();
        state.selectedSessionId = String(payload.session_id || "");
        await loadSessions();
        dispatchSessionChange({
          sessionId: state.selectedSessionId,
          sessionName: "New Session",
          transcript: [],
          tasks: [],
          archived: false,
        });
      }

      newSessionButton.addEventListener("click", function onNewSessionClick() {
        void createNewSession();
      });
      refreshButton.addEventListener("click", function onRefreshClick() {
        void loadSessions();
      });
      searchInput.addEventListener("input", function onSearchInput() {
        state.searchText = searchInput.value || "";
        render();
      });
      orderSelect.addEventListener("change", function onOrderChange() {
        state.order = orderSelect.value || DEFAULT_ORDER;
        persistOrderPreference(state.order);
        render();
      });
      archivedToggle.addEventListener("click", function onArchivedToggleClick() {
        state.showArchived = !state.showArchived;
        archivedToggle.checked = state.showArchived;
        render();
      });

      void (async function initialisePanel() {
        await loadSessions();
        await syncCurrentSessionFromBackend();
      })();

      return {
        destroy() {
          state.destroyed = true;
        },
      };
    },
  };
})(window);
