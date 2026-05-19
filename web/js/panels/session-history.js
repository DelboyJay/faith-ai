/**
 * Description:
 *   Register the FAITH Session History panel runtime.
 *
 * Requirements:
 *   - Fetch session summaries from `/api/logs/sessions`.
 *   - Allow the user to start a new session through the same-origin PA proxy route.
 *   - Load detailed session metadata and selected channel logs on demand.
 *   - Keep the main results area internally scrollable.
 */

(function initialiseFaithSessionHistoryPanel(globalScope) {
  globalScope.faithSessionHistoryPanel = {
    /**
     * Description:
     *   Mount the session-history panel into the supplied target element.
     *
     * Requirements:
     *   - Render reverse-chronological session summaries.
     *   - Load session detail and channel-log content on demand through read-only GET endpoints.
     *
     * @param {HTMLElement} target Panel mount element.
     * @returns {object} Cleanup handle for the mounted panel.
     */
    mountPanel(target) {
      const state = {
        sessions: [],
        selectedSessionId: "",
        selectedChannelName: "",
        sessionDetail: null,
        channelLog: null,
        loading: false,
        error: "",
        destroyed: false,
      };

      const wrapper = document.createElement("section");
      wrapper.className = "faith-panel faith-log-panel faith-log-panel--session-history";

      const header = document.createElement("div");
      header.className = "faith-log-panel__filters";
      const newSessionButton = document.createElement("button");
      newSessionButton.type = "button";
      newSessionButton.className = "faith-toolbar__button";
      newSessionButton.textContent = "New Session";
      header.appendChild(newSessionButton);
      const refreshButton = document.createElement("button");
      refreshButton.type = "button";
      refreshButton.className = "faith-toolbar__button";
      refreshButton.textContent = "Refresh";
      header.appendChild(refreshButton);

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
       *   Render the current session-history state.
       *
       * Requirements:
       *   - Keep the content area internally scrollable.
       *   - Show session summaries above any loaded details.
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

        if (state.sessions.length === 0) {
          const empty = document.createElement("p");
          empty.className = "faith-log-panel__empty";
          empty.textContent = "No sessions found.";
          content.appendChild(empty);
          return;
        }

        const sessionList = document.createElement("div");
        sessionList.className = "faith-log-panel__record-list";
        state.sessions.forEach(function appendSession(session) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "faith-log-panel__session-button";
          button.textContent = `${session.session_id} · ${session.status || "unknown"} · ${session.started_at || "—"}`;
          if (session.session_id === state.selectedSessionId) {
            button.classList.add("faith-log-panel__session-button--active");
          }
          button.addEventListener("click", function onSessionClick() {
            void loadSessionDetail(session.session_id);
          });
          sessionList.appendChild(button);
        });
        content.appendChild(sessionList);

        if (!state.sessionDetail) {
          return;
        }

        const detail = document.createElement("section");
        detail.className = "faith-log-panel__session-detail";
        detail.appendChild(
          globalScope.faithLogPanelCommon.renderRecordCard(state.sessionDetail.session, [
            ["Session", state.sessionDetail.session.session_id],
            ["Status", state.sessionDetail.session.status],
            ["Started", state.sessionDetail.session.started_at],
            ["Trigger", state.sessionDetail.session.trigger],
          ]),
        );

        const transcriptTitle = document.createElement("h3");
        transcriptTitle.className = "faith-log-panel__summary-title";
        transcriptTitle.textContent = "Project Agent Transcript";
        detail.appendChild(transcriptTitle);
        state.sessionDetail.transcript.forEach(function appendTranscriptEntry(entry) {
          detail.appendChild(
            globalScope.faithLogPanelCommon.renderRecordCard(entry, [
              ["Role", entry.role],
              ["Content", entry.content],
            ]),
          );
        });

        const tasksTitle = document.createElement("h3");
        tasksTitle.className = "faith-log-panel__summary-title";
        tasksTitle.textContent = "Tasks";
        detail.appendChild(tasksTitle);
        state.sessionDetail.tasks.forEach(function appendTask(task) {
          const card = globalScope.faithLogPanelCommon.renderRecordCard(task, [
            ["Task", task.task_id],
            ["Goal", task.goal],
            ["Status", task.status],
            ["Started", task.started_at],
          ]);
          const channels = Object.keys(task.channels || {});
          channels.forEach(function appendChannel(channelName) {
            const openButton = document.createElement("button");
            openButton.type = "button";
            openButton.className = "faith-toolbar__button";
            openButton.textContent = `Open ${channelName}`;
            openButton.addEventListener("click", function onOpenChannelClick() {
              void loadChannelLog(state.selectedSessionId, `${channelName}.log`);
            });
            card.appendChild(openButton);
          });
          detail.appendChild(card);
        });

        if (state.channelLog) {
          const channelTitle = document.createElement("h3");
          channelTitle.className = "faith-log-panel__summary-title";
          channelTitle.textContent = `Channel Log · ${state.channelLog.channel}`;
          detail.appendChild(channelTitle);

          const pre = document.createElement("pre");
          pre.className = "faith-log-panel__channel-log";
          pre.textContent = state.channelLog.content;
          detail.appendChild(pre);
        }

        content.appendChild(detail);
      }

      /**
       * Description:
       *   Load the reverse-chronological session summary list.
       *
       * Requirements:
       *   - Preserve the existing selected session when it still exists.
       *   - Auto-select the newest session when no current selection exists.
       *
       * @returns {Promise<void>} Promise that resolves once the session list is refreshed.
       */
      async function loadSessions() {
        state.loading = true;
        state.error = "";
        render();
        try {
          const response = await globalScope.fetch("/api/logs/sessions");
          if (!response.ok) {
            throw new Error(`Session history failed with status ${response.status}`);
          }
          const payload = await response.json();
          state.sessions = payload.items || [];
          const selectedSessionStillExists = state.sessions.some(function hasSelectedSession(session) {
            return session.session_id === state.selectedSessionId;
          });
          if (!selectedSessionStillExists) {
            state.selectedSessionId = state.sessions.length > 0 ? state.sessions[0].session_id : "";
            state.selectedChannelName = "";
            state.channelLog = null;
            state.sessionDetail = null;
          }
        } catch (error) {
          state.error = String(error.message || error);
        } finally {
          state.loading = false;
          if (!state.destroyed) {
            render();
          }
        }
        if (!state.destroyed && state.selectedSessionId && state.sessionDetail === null) {
          await loadSessionDetail(state.selectedSessionId);
        }
      }

      /**
       * Description:
       *   Load one session detail payload from the backend.
       *
       * Requirements:
       *   - Replace the current detail view with the requested session.
       *
       * @param {string} sessionId Selected persisted session identifier.
       * @returns {Promise<void>} Promise that resolves once the detail payload is loaded.
       */
      async function loadSessionDetail(sessionId) {
        state.loading = true;
        state.error = "";
        state.selectedSessionId = sessionId;
        state.selectedChannelName = "";
        state.channelLog = null;
        render();
        try {
          const response = await globalScope.fetch(`/api/logs/sessions/${encodeURIComponent(sessionId)}`);
          if (!response.ok) {
            throw new Error(`Session detail failed with status ${response.status}`);
          }
          state.sessionDetail = await response.json();
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
       *   Load one persisted task channel log from the backend.
       *
       * Requirements:
       *   - Preserve the current session detail while adding the channel-log content.
       *
       * @param {string} sessionId Selected persisted session identifier.
       * @param {string} channelName Selected channel-log filename.
       * @returns {Promise<void>} Promise that resolves once the channel log is loaded.
       */
      async function loadChannelLog(sessionId, channelName) {
        state.loading = true;
        state.error = "";
        state.selectedChannelName = channelName;
        render();
        try {
          const response = await globalScope.fetch(
            `/api/logs/sessions/${encodeURIComponent(sessionId)}/channels/${encodeURIComponent(channelName)}`,
          );
          if (!response.ok) {
            throw new Error(`Channel log failed with status ${response.status}`);
          }
          state.channelLog = await response.json();
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
       *   Start a fresh Project Agent session through the same-origin API.
       *
       * Requirements:
       *   - Clear the current detail view until the fresh session detail is reloaded.
       *   - Refresh the summary list and select the new active session after creation.
       *
       * @returns {Promise<void>} Promise that resolves once the new session is visible.
       */
      async function startNewSession() {
        state.loading = true;
        state.error = "";
        render();
        try {
          const response = await globalScope.fetch("/api/pa/session/new", {
            method: "POST",
          });
          if (!response.ok) {
            throw new Error(`New session failed with status ${response.status}`);
          }
          const payload = await response.json();
          state.selectedSessionId = payload.session_id || "";
          state.selectedChannelName = "";
          state.channelLog = null;
          state.sessionDetail = null;
          await loadSessions();
          if (!state.destroyed && state.selectedSessionId) {
            await loadSessionDetail(state.selectedSessionId);
          }
          return;
        } catch (error) {
          state.error = String(error.message || error);
        } finally {
          state.loading = false;
          if (!state.destroyed) {
            render();
          }
        }
      }

      newSessionButton.addEventListener("click", function onNewSessionClick() {
        void startNewSession();
      });
      refreshButton.addEventListener("click", function onRefreshClick() {
        void loadSessions();
      });

      void loadSessions();
      const refreshTimerId = globalScope.setInterval(function refreshSessionsInBackground() {
        void loadSessions();
      }, 5000);

      return {
        destroy() {
          state.destroyed = true;
          globalScope.clearInterval(refreshTimerId);
        },
      };
    },
  };
})(window);
