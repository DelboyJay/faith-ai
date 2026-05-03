/**
 * Description:
 *   Render the FAITH agent output panel inside the browser workspace.
 *
 * Requirements:
 *   - Consume the dedicated agent WebSocket feed.
 *   - Render output, protocol, status, and error events with local panel actions.
 *   - Reconnect automatically with bounded exponential backoff.
 */

(function initialiseFaithAgentPanel(globalScope) {
  const AGENT_WS_PATH_PREFIX = "/ws/agent/";
  const PROJECT_AGENT_TRANSCRIPT_PATH = "/api/pa/transcript";
  const MAX_RECONNECT_DELAY_MS = 8000;
  const INITIAL_RECONNECT_DELAY_MS = 400;

  /**
   * Description:
   *   Build the browser WebSocket URL for one agent output stream.
   *
   * Requirements:
   *   - Preserve the current browser host and scheme.
   *   - Point at the dedicated agent stream path.
   *
   * @param {string} agentId: Agent identifier.
   * @returns {string} Absolute agent WebSocket URL.
   */
  function buildAgentWebSocketUrl(agentId) {
    const base = new URL(globalScope.location.href);
    base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
    base.pathname = `${AGENT_WS_PATH_PREFIX}${encodeURIComponent(agentId)}`;
    base.search = "";
    base.hash = "";
    return base.toString();
  }

  /**
   * Description:
   *   Build the HTTP URL used to restore the saved Project Agent transcript.
   *
   * Requirements:
   *   - Preserve the current browser origin.
   *   - Point at the same-origin transcript rehydration API path.
   *
   * @returns {string} Absolute transcript API URL.
   */
  function buildProjectAgentTranscriptUrl() {
    const base = new URL(globalScope.location.href);
    base.pathname = PROJECT_AGENT_TRANSCRIPT_PATH;
    base.search = "";
    base.hash = "";
    return base.toString();
  }

  /**
   * Description:
   *   Parse one incoming agent WebSocket frame into a message list.
   *
   * Requirements:
   *   - Accept single JSON objects, arrays of JSON objects, and plain text.
   *   - Convert malformed payloads into a non-fatal panel error event.
   *
   * @param {string} rawFrame: Raw WebSocket frame text.
   * @returns {Array<object>} Normalised message list.
   */
  function normaliseAgentMessages(rawFrame) {
    try {
      const parsed = JSON.parse(rawFrame);
      if (Array.isArray(parsed)) {
        return parsed.filter(function keepObjects(item) {
          return item && typeof item === "object";
        });
      }
      if (parsed && typeof parsed === "object") {
        return [parsed];
      }
    } catch (error) {
      return [{ type: "error", message: `Malformed agent payload: ${rawFrame}` }];
    }
    return [{ type: "output", text: String(rawFrame) }];
  }

  /**
   * Description:
   *   Create one terminal adapter for the panel mount element.
   *
   * Requirements:
   *   - Use a visible DOM transcript so PA output remains readable even when
   *     terminal emulators fail to paint inside dynamic layout panels.
   *   - Keep the adapter API small so a richer terminal can be reintroduced later.
   *
   * @param {HTMLElement} host: Terminal host element.
   * @returns {object} Terminal adapter with write, writeLine, clear, copy, resize, and dispose methods.
   */
  function createTerminalAdapter(host, onContentChanged, isPinnedToLatest) {
    const pre = document.createElement("pre");
    pre.className = "faith-agent-panel__fallback-terminal";
    host.appendChild(pre);

    /**
     * Description:
     *   Notify the panel that transcript content changed.
     *
     * Requirements:
     *   - Keep fake DOM runtime tests able to simulate scroll growth.
     *   - Avoid relying on browser-only layout reads inside the adapter.
     */
    function notifyContentChanged() {
      const wasNearBottom = typeof isPinnedToLatest === "function" ? isPinnedToLatest() : true;
      if (typeof host.clientHeight === "number" && typeof host.scrollHeight === "number") {
        host.scrollHeight = Math.max(host.clientHeight, pre.textContent.length + host.clientHeight);
      }
      if (typeof onContentChanged === "function") {
        onContentChanged(wasNearBottom);
      }
    }

    return {
      write(text) {
        pre.textContent += text;
        notifyContentChanged();
      },
      writeLine(text) {
        pre.textContent += `${text}\n`;
        notifyContentChanged();
      },
      clear() {
        pre.textContent = "";
        notifyContentChanged();
      },
      async copy() {
        if (globalScope.navigator && globalScope.navigator.clipboard) {
          await globalScope.navigator.clipboard.writeText(pre.textContent.trim());
        }
      },
      resize() {},
      dispose() {},
    };
  }

  /**
   * Description:
   *   Start a simple DOM-removal watcher for one panel root element.
   *
   * Requirements:
   *   - Trigger cleanup when the panel root leaves the document.
   *   - Degrade safely when `MutationObserver` is unavailable.
   *
   * @param {HTMLElement} target: Mounted panel root element.
   * @param {Function} cleanup: Cleanup callback to trigger once.
   * @returns {Function} Stop-watching callback.
   */
  function watchPanelRemoval(target, cleanup) {
    if (
      typeof globalScope.MutationObserver !== "function" ||
      !(globalScope.document && globalScope.document.body)
    ) {
      return function stopWatching() {};
    }

    const observer = new globalScope.MutationObserver(function onMutation() {
      if (!target.isConnected) {
        observer.disconnect();
        cleanup();
      }
    });
    observer.observe(globalScope.document.body, { childList: true, subtree: true });
    return function stopWatching() {
      observer.disconnect();
    };
  }

  /**
   * Description:
   *   Mount one live agent panel into the supplied DOM element.
   *
   * Requirements:
   *   - Show agent name, status, model, and local panel actions.
   *   - Keep processing paused frames in memory until resumed.
   *   - Clean up the WebSocket and timers on destroy.
   *
   * @param {HTMLElement} target: Panel mount element.
   * @param {object} state: Agent panel component state.
   * @returns {Function} Cleanup callback for the mounted panel.
   */
  function mountPanel(target, state) {
    const panelState = Object.assign(
      {
        agentId: "project-agent",
        displayName: "Project Agent",
        model: "unknown",
      },
      state || {},
    );

    const wrapper = document.createElement("section");
    wrapper.className = "faith-panel faith-panel--agent-output";

    const topBar = document.createElement("div");
    topBar.className = "faith-agent-panel__bar";

    const meta = document.createElement("div");
    meta.className = "faith-agent-panel__meta";

    const agentName = document.createElement("strong");
    agentName.className = "faith-agent-panel__name";
    agentName.textContent = panelState.displayName || panelState.agentId;

    const statusBadge = document.createElement("span");
    statusBadge.className = "faith-agent-panel__status faith-agent-panel__status--disconnected";
    statusBadge.textContent = "disconnected";

    const modelLabel = document.createElement("span");
    modelLabel.className = "faith-agent-panel__model";
    modelLabel.textContent = panelState.model || "unknown model";

    meta.appendChild(agentName);
    meta.appendChild(statusBadge);
    meta.appendChild(modelLabel);

    const actions = document.createElement("div");
    actions.className = "faith-agent-panel__actions";

    const clearButton = document.createElement("button");
    clearButton.type = "button";
    clearButton.textContent = "Clear";

    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.textContent = "Copy";

    const pauseButton = document.createElement("button");
    pauseButton.type = "button";
    pauseButton.textContent = "Pause";

    const pinButton = document.createElement("button");
    pinButton.type = "button";
    pinButton.textContent = "Pin";

    actions.appendChild(clearButton);
    actions.appendChild(copyButton);
    actions.appendChild(pauseButton);
    actions.appendChild(pinButton);

    topBar.appendChild(meta);
    topBar.appendChild(actions);

    const terminalHost = document.createElement("div");
    terminalHost.className = "faith-agent-panel__terminal";

    const thinkingIndicator = document.createElement("div");
    thinkingIndicator.className = "faith-agent-panel__thinking";
    thinkingIndicator.setAttribute("role", "status");
    thinkingIndicator.setAttribute("aria-live", "polite");
    thinkingIndicator.hidden = true;
    thinkingIndicator.textContent = `${panelState.displayName || "Project Agent"} is thinking...`;

    const jumpToLatestButton = document.createElement("button");
    jumpToLatestButton.type = "button";
    jumpToLatestButton.className = "faith-agent-panel__jump";
    jumpToLatestButton.textContent = "Jump to latest";
    jumpToLatestButton.hidden = true;

    wrapper.appendChild(topBar);
    wrapper.appendChild(thinkingIndicator);
    wrapper.appendChild(terminalHost);
    wrapper.appendChild(jumpToLatestButton);
    target.replaceChildren(wrapper);

    const SCROLL_BOTTOM_THRESHOLD_PX = 24;
    let hasUnreadBelow = false;

    /**
     * Description:
     *   Determine whether the transcript is already at or near the bottom.
     *
     * Requirements:
     *   - Tolerate tiny layout differences by using a small threshold.
     *
     * @returns {boolean} True when the newest text is already visible.
     */
    function isNearBottom() {
      const distanceFromBottom =
        terminalHost.scrollHeight - terminalHost.clientHeight - terminalHost.scrollTop;
      return distanceFromBottom <= SCROLL_BOTTOM_THRESHOLD_PX;
    }

    /**
     * Description:
     *   Scroll the transcript to the newest visible content.
     *
     * Requirements:
     *   - Work for both live browser layout and the lightweight runtime harness.
     *
     * @returns {void}
     */
    function scrollTranscriptToLatest() {
      terminalHost.scrollTop = Math.max(0, terminalHost.scrollHeight - terminalHost.clientHeight);
      hasUnreadBelow = false;
      jumpToLatestButton.hidden = true;
    }

    /**
     * Description:
     *   React to transcript content growth after one terminal write.
     *
     * Requirements:
     *   - Keep the latest text visible when the user is already near the bottom.
     *   - Preserve scroll position and expose a jump affordance when the user
     *     is reading earlier content.
     *
     * @returns {void}
     */
    function handleTranscriptContentChanged(wasNearBottom) {
      if (wasNearBottom) {
        scrollTranscriptToLatest();
        return;
      }
      hasUnreadBelow = true;
      jumpToLatestButton.hidden = false;
    }

    const terminal = createTerminalAdapter(
      terminalHost,
      handleTranscriptContentChanged,
      isNearBottom,
    );
    let savedTranscriptMessageCount = 0;
    const queuedMessages = [];
    let isPaused = false;
    let isPinned = false;
    let reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
    let reconnectTimer = null;
    let socket = null;
    let reconnectScheduledForSocket = null;
    let destroyed = false;

    /**
     * Description:
     *   Update the visible status and model labels.
     *
     * Requirements:
     *   - Reflect the latest connection or agent runtime state.
     *   - Surface active LLM calls as a clear thinking state for slow responses.
     *
     * @param {string} status: Current status label.
     * @param {string} model: Current model label.
     */
    function setHeaderState(status, model) {
      const visibleStatus = status === "active" ? "thinking" : status;
      const isThinking = visibleStatus === "thinking";
      statusBadge.textContent = visibleStatus;
      statusBadge.className = `faith-agent-panel__status faith-agent-panel__status--${visibleStatus.replace(/\s+/g, "-")}`;
      modelLabel.textContent = model || panelState.model || "unknown model";
      thinkingIndicator.hidden = !isThinking;
    }

    /**
     * Description:
     *   Render one structured agent message into the terminal.
     *
     * Requirements:
     *   - Dim protocol messages relative to natural language output.
     *   - Surface status and error updates without crashing the panel.
     *
     * @param {object} message: Structured agent message.
     */
    function processMessage(message) {
      if (!message || typeof message !== "object") {
        return;
      }
      if (message.type === "status") {
        panelState.model = message.model || panelState.model;
        setHeaderState(message.status || "running", panelState.model);
        return;
      }
      if (message.type === "protocol") {
        terminal.writeLine(`\x1b[2m${message.text || message.message || ""}\x1b[0m`);
        return;
      }
      if (message.type === "error") {
        terminal.writeLine(`ERROR: ${message.message || message.text || "Unknown agent error"}`);
        setHeaderState("error", panelState.model);
        return;
      }
      if (message.stream) {
        terminal.write(message.text || message.message || "");
        return;
      }
      terminal.writeLine(message.text || message.message || "");
    }

    /**
     * Description:
     *   Render one saved transcript message before the live WebSocket stream starts.
     *
     * Requirements:
     *   - Preserve the current terminal-style transcript labels for existing UI behaviour.
     *   - Ignore malformed restore payload entries safely.
     *
     * @param {object} message: Saved transcript message entry.
     */
    function processSavedTranscriptMessage(message) {
      if (!message || typeof message !== "object") {
        return;
      }
      const role = message.role === "assistant" ? "PA" : "User";
      const content = typeof message.content === "string" ? message.content : "";
      if (!content) {
        return;
      }
      terminal.writeLine(`${role}: ${content}`);
    }

    /**
     * Description:
     *   Reconcile the saved Project Agent transcript with the currently rendered panel.
     *
     * Requirements:
     *   - Use the same-origin transcript endpoint when `fetch` is available.
     *   - Append only transcript entries that have not already been rendered locally.
     *   - Degrade silently when the transcript endpoint is unavailable.
     */
    async function reconcileSavedTranscript() {
      if (typeof globalScope.fetch !== "function") {
        return;
      }
      try {
        const response = await globalScope.fetch(buildProjectAgentTranscriptUrl());
        if (!response || response.ok !== true) {
          return;
        }
        const payload = await response.json();
        if (!payload || !Array.isArray(payload.messages)) {
          return;
        }
        const nextMessages = payload.messages.slice(savedTranscriptMessageCount);
        if (nextMessages.length === 0) {
          return;
        }
        nextMessages.forEach(processSavedTranscriptMessage);
        savedTranscriptMessageCount = payload.messages.length;
        scrollTranscriptToLatest();
      } catch (error) {
        return;
      }
    }

    /**
     * Description:
     *   Process one raw WebSocket frame.
     *
     * Requirements:
     *   - Queue messages while paused.
     *   - Accept multi-message frames encoded as JSON arrays.
     *
     * @param {string} rawFrame: Raw WebSocket frame text.
     */
    function handleFrame(rawFrame) {
      const messages = normaliseAgentMessages(rawFrame);
      if (isPaused) {
        queuedMessages.push.apply(queuedMessages, messages);
        return;
      }
      messages.forEach(processMessage);
    }

    /**
     * Description:
     *   Reconnect the agent WebSocket using bounded exponential backoff.
     *
     * Requirements:
     *   - Stop scheduling reconnects after panel destruction.
     */
    function scheduleReconnect() {
      if (destroyed || reconnectTimer !== null) {
        return;
      }
      let callbackRanSynchronously = false;
      const timerHandle = globalScope.setTimeout(function reconnectLater() {
        callbackRanSynchronously = true;
        reconnectTimer = null;
        reconnectScheduledForSocket = null;
        connect();
      }, reconnectDelay);
      reconnectTimer = callbackRanSynchronously ? null : timerHandle;
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
    }

    /**
     * Description:
     *   Handle one socket failure event and schedule a safe reconnect.
     *
     * Requirements:
     *   - Ignore stale socket events once a newer socket has been created.
     *   - Avoid scheduling duplicate reconnects for the same failed socket.
     *
     * @param {WebSocket} failedSocket: Socket instance that failed.
     * @param {string} visibleStatus: Status label to surface before reconnecting.
     */
    function handleSocketFailure(failedSocket, visibleStatus) {
      if (destroyed || failedSocket !== socket) {
        return;
      }
      setHeaderState(visibleStatus, panelState.model);
      if (reconnectScheduledForSocket === failedSocket) {
        return;
      }
      reconnectScheduledForSocket = failedSocket;
      scheduleReconnect();
    }

    /**
     * Description:
     *   Open the live WebSocket stream for the current agent.
     *
     * Requirements:
     *   - Reset reconnect backoff after a successful open.
     *   - Mark disconnects visibly and retry automatically.
     */
    function connect() {
      if (destroyed) {
        return;
      }
      setHeaderState("connecting", panelState.model);
      const nextSocket = new globalScope.WebSocket(buildAgentWebSocketUrl(panelState.agentId));
      socket = nextSocket;
      nextSocket.addEventListener("open", function onOpen() {
        reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
        reconnectScheduledForSocket = null;
        if (panelState.agentId === "project-agent") {
          void reconcileSavedTranscript();
        }
        setHeaderState("connected", panelState.model);
      });
      nextSocket.addEventListener("message", function onMessage(event) {
        handleFrame(event.data);
      });
      nextSocket.addEventListener("close", function onClose() {
        handleSocketFailure(nextSocket, "disconnected");
      });
      nextSocket.addEventListener("error", function onError() {
        handleSocketFailure(nextSocket, "error");
      });
    }

    clearButton.addEventListener("click", function onClearClick() {
      terminal.clear();
    });

    copyButton.addEventListener("click", function onCopyClick() {
      void terminal.copy();
    });

    pauseButton.addEventListener("click", function onPauseClick() {
      isPaused = !isPaused;
      pauseButton.textContent = isPaused ? "Resume" : "Pause";
      if (!isPaused && queuedMessages.length > 0) {
        queuedMessages.splice(0).forEach(processMessage);
      }
    });

    pinButton.addEventListener("click", function onPinClick() {
      isPinned = !isPinned;
      wrapper.classList.toggle("faith-agent-panel--pinned", isPinned);
      pinButton.textContent = isPinned ? "Unpin" : "Pin";
    });

    terminalHost.addEventListener("scroll", function onTranscriptScroll() {
      if (isNearBottom()) {
        hasUnreadBelow = false;
        jumpToLatestButton.hidden = true;
        return;
      }
      jumpToLatestButton.hidden = !hasUnreadBelow;
    });

    jumpToLatestButton.addEventListener("click", function onJumpToLatestClick() {
      scrollTranscriptToLatest();
    });

    if (panelState.agentId === "project-agent") {
      void reconcileSavedTranscript().finally(connect);
    } else {
      connect();
    }

    const stopWatchingRemoval = watchPanelRemoval(target, cleanup);

    /**
     * Description:
     *   Dispose the mounted panel resources.
     *
     * Requirements:
     *   - Close the WebSocket and clear pending timers once.
     */
    function cleanup() {
      if (destroyed) {
        return;
      }
      destroyed = true;
      stopWatchingRemoval();
      if (reconnectTimer !== null) {
        globalScope.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (socket && typeof socket.close === "function") {
        socket.close();
      }
      terminal.dispose();
    }

    return cleanup;
  }

  globalScope.faithAgentPanel = {
    AGENT_WS_PATH_PREFIX: AGENT_WS_PATH_PREFIX,
    PROJECT_AGENT_TRANSCRIPT_PATH: PROJECT_AGENT_TRANSCRIPT_PATH,
    buildAgentWebSocketUrl: buildAgentWebSocketUrl,
    buildProjectAgentTranscriptUrl: buildProjectAgentTranscriptUrl,
    mountPanel: mountPanel,
    normaliseAgentMessages: normaliseAgentMessages,
  };
})(window);
