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
  function createTerminalAdapter(host) {
    const pre = document.createElement("pre");
    pre.className = "faith-agent-panel__fallback-terminal";
    host.appendChild(pre);
    return {
      write(text) {
        pre.textContent += text;
      },
      writeLine(text) {
        pre.textContent += `${text}\n`;
      },
      clear() {
        pre.textContent = "";
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

    wrapper.appendChild(topBar);
    wrapper.appendChild(thinkingIndicator);
    wrapper.appendChild(terminalHost);
    target.replaceChildren(wrapper);

    const terminal = createTerminalAdapter(terminalHost);
    const queuedMessages = [];
    let isPaused = false;
    let isPinned = false;
    let reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
    let reconnectTimer = null;
    let socket = null;
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
      reconnectTimer = globalScope.setTimeout(function reconnectLater() {
        reconnectTimer = null;
        connect();
      }, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
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
      socket = new globalScope.WebSocket(buildAgentWebSocketUrl(panelState.agentId));
      socket.addEventListener("open", function onOpen() {
        reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
        setHeaderState("connected", panelState.model);
      });
      socket.addEventListener("message", function onMessage(event) {
        handleFrame(event.data);
      });
      socket.addEventListener("close", function onClose() {
        setHeaderState("disconnected", panelState.model);
        scheduleReconnect();
      });
      socket.addEventListener("error", function onError() {
        setHeaderState("error", panelState.model);
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

    connect();

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
    buildAgentWebSocketUrl: buildAgentWebSocketUrl,
    mountPanel: mountPanel,
    normaliseAgentMessages: normaliseAgentMessages,
  };
})(window);
