/**
 * Description:
 *   Render the FAITH approval queue panel inside the browser workspace.
 *
 * Requirements:
 *   - Subscribe to `/ws/approvals` and render pending approval requests.
 *   - Submit the six canonical approval decisions to `/approve/{request_id}`.
 *   - Require editable rule confirmation before persisted decisions are posted.
 */

(function initialiseFaithApprovalPanel(globalScope) {
  const APPROVAL_WS_PATH = "/ws/approvals";
  const APPROVAL_POST_PREFIX = "/approve/";
  const RECONNECT_DELAY_MS = 1500;
  const HISTORY_LIMIT = 12;
  const PERSISTED_DECISIONS = new Set(["always_allow", "always_ask", "deny_permanently"]);
  const DECISIONS = Object.freeze([
    { value: "allow_once", label: "Allow once", persisted: false },
    { value: "approve_session", label: "Approve session", persisted: false },
    { value: "always_allow", label: "Always allow", persisted: true },
    { value: "always_ask", label: "Always ask", persisted: true },
    { value: "deny_once", label: "Deny once", persisted: false },
    { value: "deny_permanently", label: "Deny permanently", persisted: true },
  ]);

  /**
   * Description:
   *   Build the WebSocket URL used by the approval panel.
   *
   * Requirements:
   *   - Match the current page host.
   *   - Use secure WebSocket protocol when the page is served over HTTPS.
   *
   * @returns {string} Absolute WebSocket URL for approval events.
   */
  function buildApprovalWebSocketUrl() {
    const protocol = globalScope.location && globalScope.location.protocol === "https:" ? "wss:" : "ws:";
    const host = globalScope.location && globalScope.location.host ? globalScope.location.host : "localhost";
    return `${protocol}//${host}${APPROVAL_WS_PATH}`;
  }

  /**
   * Description:
   *   Convert one raw WebSocket payload into one or more approval messages.
   *
   * Requirements:
   *   - Accept object, array, and JSON text frames.
   *   - Return an error message wrapper for malformed frames instead of throwing.
   *
   * @param {string|object|Array<object>} rawMessage: Raw browser WebSocket message payload.
   * @returns {Array<object>} Normalised approval message objects.
   */
  function normaliseApprovalMessages(rawMessage) {
    try {
      const parsed = typeof rawMessage === "string" ? JSON.parse(rawMessage) : rawMessage;
      if (Array.isArray(parsed)) {
        return parsed.filter(function keepObjects(item) {
          return item && typeof item === "object";
        });
      }
      if (parsed && typeof parsed === "object") {
        return [parsed];
      }
      return [];
    } catch (error) {
      return [{ type: "panel_error", message: `Invalid approval payload: ${error.message || error}` }];
    }
  }

  /**
   * Description:
   *   Return the stable request identifier for one approval payload.
   *
   * Requirements:
   *   - Support both `request_id` and `id` while preserving the FRS canonical field.
   *
   * @param {object} message: Approval message from the WebSocket.
   * @returns {string} Request identifier, or an empty string when absent.
   */
  function getRequestId(message) {
    return String((message && (message.request_id || message.id)) || "");
  }

  /**
   * Description:
   *   Return the most useful detail text for one approval request.
   *
   * Requirements:
   *   - Prefer explicit detail text and fall back to target text.
   *
   * @param {object} request: Approval request payload.
   * @returns {string} Human-readable target or detail text.
   */
  function getRequestDetail(request) {
    return String(request.detail || request.target || "No target supplied");
  }

  /**
   * Description:
   *   Generate a default remembered-rule preview for persisted approval decisions.
   *
   * Requirements:
   *   - Use backend-supplied suggested rules when available.
   *   - Include tool, action, and target/detail values when generating a local preview.
   *
   * @param {object} request: Approval request payload.
   * @returns {string} Editable rule preview text.
   */
  function buildRulePreview(request) {
    if (request.suggested_rule) {
      return String(request.suggested_rule);
    }
    const tool = request.tool || "tool";
    const action = request.action || "action";
    const target = request.target || request.detail || "*";
    return `${tool}:${action}:${target}`;
  }

  /**
   * Description:
   *   Append a labelled metadata row to one approval card.
   *
   * Requirements:
   *   - Keep approval request fields readable without duplicating layout title chrome.
   *
   * @param {HTMLElement} list: Description-list element receiving the row.
   * @param {string} label: Metadata label.
   * @param {string} value: Metadata value.
   */
  function appendMetaRow(list, label, value) {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value || "-";
    list.appendChild(term);
    list.appendChild(description);
  }

  /**
   * Description:
   *   Mount one live approval panel into the supplied DOM element.
   *
   * Requirements:
   *   - Render pending approval cards and recently resolved history.
   *   - Ignore duplicate request identifiers.
   *   - Reconnect the WebSocket after disconnects until the panel is destroyed.
   *
   * @param {HTMLElement} target: Panel mount element.
   * @returns {object} Mounted panel controls and state helpers.
   */
  function mountPanel(target) {
    const wrapper = document.createElement("section");
    wrapper.className = "faith-panel faith-panel--approval";
    const header = document.createElement("div");
    header.className = "faith-approval-panel__header";

    const statusBadge = document.createElement("span");
    statusBadge.className = "faith-approval-panel__status";
    header.appendChild(statusBadge);

    const errorBanner = document.createElement("p");
    errorBanner.className = "faith-approval-panel__error";
    errorBanner.hidden = true;

    const pendingList = document.createElement("div");
    pendingList.className = "faith-approval-panel__queue";

    const historyTitle = document.createElement("h3");
    historyTitle.className = "faith-approval-panel__section-title";
    historyTitle.textContent = "Recent decisions";

    const historyList = document.createElement("div");
    historyList.className = "faith-approval-panel__history";

    wrapper.appendChild(header);
    wrapper.appendChild(errorBanner);
    wrapper.appendChild(pendingList);
    wrapper.appendChild(historyTitle);
    wrapper.appendChild(historyList);
    target.replaceChildren(wrapper);

    const pendingById = new Map();
    const history = [];
    let connectionStatus = "connecting";
    let socket = null;
    let reconnectTimer = null;
    let destroyed = false;
    let pendingRule = null;

    /**
     * Description:
     *   Show or clear the current panel error banner.
     *
     * Requirements:
     *   - Keep transport and submission failures visible without crashing the panel.
     *
     * @param {string} message: Error message to display.
     */
    function setError(message) {
      errorBanner.hidden = !message;
      errorBanner.textContent = message || "";
    }

    /**
     * Description:
     *   Add one resolved approval item to the history list.
     *
     * Requirements:
     *   - Keep only a small recent-history buffer in the browser.
     *
     * @param {object} request: Approval request payload that was resolved.
     * @param {string} decision: Approval decision value.
     */
    function addHistory(request, decision) {
      history.unshift({
        request: request,
        decision: decision,
        timestamp: new Date().toISOString(),
      });
      if (history.length > HISTORY_LIMIT) {
        history.splice(HISTORY_LIMIT);
      }
    }

    /**
     * Description:
     *   Move one approval request out of the pending queue when it is resolved elsewhere.
     *
     * Requirements:
     *   - Treat external resolution messages as authoritative.
     *
     * @param {object} message: Resolution message from the WebSocket.
     */
    function resolveExternalRequest(message) {
      const requestId = getRequestId(message);
      if (!requestId || !pendingById.has(requestId)) {
        return;
      }
      const request = pendingById.get(requestId);
      pendingById.delete(requestId);
      addHistory(request, message.decision || "resolved");
      if (pendingRule && pendingRule.requestId === requestId) {
        pendingRule = null;
      }
      render();
    }

    /**
     * Description:
     *   Submit one approval decision to the Web UI backend.
     *
     * Requirements:
     *   - Preserve the request ID in the route.
     *   - Include edited rule text for persisted decisions.
     *
     * @param {object} request: Approval request being decided.
     * @param {string} decision: Canonical approval decision value.
     * @param {string|null} patternOverride: Edited rule text for persisted decisions.
     * @returns {Promise<void>} Promise that resolves after submission completes.
     */
    async function submitDecision(request, decision, patternOverride) {
      const requestId = getRequestId(request);
      const body = {
        decision: decision,
        scope: PERSISTED_DECISIONS.has(decision) ? "pattern" : "once",
        reason: "",
        pattern_override: patternOverride || null,
      };
      const response = await globalScope.fetch(`${APPROVAL_POST_PREFIX}${encodeURIComponent(requestId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(`Approval failed with status ${response.status}`);
      }
      pendingById.delete(requestId);
      pendingRule = null;
      addHistory(request, decision);
      setError("");
      render();
    }

    /**
     * Description:
     *   Render the editable rule confirmation step for one persisted decision.
     *
     * Requirements:
     *   - Let the user review and edit the rule before submission.
     *
     * @param {HTMLElement} card: Approval card receiving the rule editor.
     * @param {object} request: Approval request being decided.
     */
    function renderRuleEditor(card, request) {
      if (!pendingRule || pendingRule.requestId !== getRequestId(request)) {
        return;
      }
      const editor = document.createElement("div");
      editor.className = "faith-approval-panel__rule-editor";

      const label = document.createElement("label");
      label.textContent = "Rule preview";

      const textarea = document.createElement("textarea");
      textarea.value = pendingRule.rule;
      textarea.rows = 3;

      const actions = document.createElement("div");
      actions.className = "faith-approval-panel__rule-actions";

      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.textContent = "Confirm rule";
      confirmButton.addEventListener("click", function onConfirmRuleClick() {
        void submitDecision(request, pendingRule.decision, textarea.value).catch(function onSubmitError(error) {
          setError(String(error.message || error));
        });
      });

      const cancelButton = document.createElement("button");
      cancelButton.type = "button";
      cancelButton.textContent = "Cancel";
      cancelButton.addEventListener("click", function onCancelRuleClick() {
        pendingRule = null;
        render();
      });

      actions.appendChild(confirmButton);
      actions.appendChild(cancelButton);
      editor.appendChild(label);
      editor.appendChild(textarea);
      editor.appendChild(actions);
      card.appendChild(editor);
    }

    /**
     * Description:
     *   Render one pending approval request card.
     *
     * Requirements:
     *   - Show agent, tool, action, detail, timestamp, and optional context summary.
     *   - Render all six canonical decision actions.
     *
     * @param {object} request: Approval request payload.
     * @returns {HTMLElement} Approval card element.
     */
    function renderPendingCard(request) {
      const card = document.createElement("article");
      card.className = "faith-approval-card";

      const meta = document.createElement("dl");
      meta.className = "faith-approval-card__meta";
      appendMetaRow(meta, "Agent", request.agent || "unknown");
      appendMetaRow(meta, "Tool", request.tool || "unknown");
      appendMetaRow(meta, "Action", request.action || "unknown");
      appendMetaRow(meta, "Detail", getRequestDetail(request));
      appendMetaRow(meta, "Time", request.timestamp || request.ts || "-");
      if (request.context_summary) {
        appendMetaRow(meta, "Context", request.context_summary);
      }

      const actions = document.createElement("div");
      actions.className = "faith-approval-card__actions";
      DECISIONS.forEach(function appendDecisionButton(decision) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = decision.label;
        button.dataset.decision = decision.value;
        if (decision.persisted) {
          button.className = "faith-approval-card__action faith-approval-card__action--persisted";
        } else {
          button.className = "faith-approval-card__action";
        }
        button.addEventListener("click", function onDecisionClick() {
          if (decision.persisted) {
            pendingRule = {
              requestId: getRequestId(request),
              decision: decision.value,
              rule: buildRulePreview(request),
            };
            render();
            return;
          }
          void submitDecision(request, decision.value, null).catch(function onSubmitError(error) {
            setError(String(error.message || error));
          });
        });
        actions.appendChild(button);
      });

      card.appendChild(meta);
      card.appendChild(actions);
      renderRuleEditor(card, request);
      return card;
    }

    /**
     * Description:
     *   Render one resolved approval history row.
     *
     * Requirements:
     *   - Keep the decision and original action visible after cards leave the pending queue.
     *
     * @param {object} entry: History entry containing request and decision.
     * @returns {HTMLElement} History row element.
     */
    function renderHistoryEntry(entry) {
      const row = document.createElement("article");
      row.className = "faith-approval-history__item";
      const title = document.createElement("strong");
      title.textContent = entry.decision;
      const detail = document.createElement("span");
      detail.textContent = `${entry.request.action || "action"} · ${getRequestDetail(entry.request)}`;
      row.appendChild(title);
      row.appendChild(detail);
      return row;
    }

    /**
     * Description:
     *   Refresh the full approval panel DOM from current state.
     *
     * Requirements:
     *   - Keep status, queue, history, and empty states in sync after every event.
     */
    function render() {
      const statusLabel = connectionStatus.charAt(0).toUpperCase() + connectionStatus.slice(1);
      statusBadge.textContent = statusLabel;
      statusBadge.className = `faith-approval-panel__status faith-approval-panel__status--${connectionStatus}`;

      pendingList.replaceChildren();
      if (pendingById.size === 0) {
        const empty = document.createElement("p");
        empty.className = "faith-approval-panel__empty";
        empty.textContent = "No approvals pending.";
        pendingList.appendChild(empty);
      } else {
        pendingById.forEach(function appendPending(request) {
          pendingList.appendChild(renderPendingCard(request));
        });
      }

      historyList.replaceChildren();
      if (history.length === 0) {
        const emptyHistory = document.createElement("p");
        emptyHistory.className = "faith-approval-panel__empty";
        emptyHistory.textContent = "No recent decisions.";
        historyList.appendChild(emptyHistory);
      } else {
        history.forEach(function appendHistory(entry) {
          historyList.appendChild(renderHistoryEntry(entry));
        });
      }
    }

    /**
     * Description:
     *   Handle one normalised approval WebSocket message.
     *
     * Requirements:
     *   - Add new approval requests to the queue.
     *   - Ignore duplicate request IDs.
     *   - Apply external resolution messages to pending cards.
     *
     * @param {object} message: Normalised approval message.
     */
    function handleApprovalMessage(message) {
      if (message.type === "panel_error") {
        setError(message.message);
        return;
      }
      if (
        message.type === "approval_resolved" ||
        message.type === "approval:resolved" ||
        message.type === "approval_decision"
      ) {
        resolveExternalRequest(message);
        return;
      }
      const requestId = getRequestId(message);
      if (!requestId || pendingById.has(requestId) || history.some((entry) => getRequestId(entry.request) === requestId)) {
        return;
      }
      pendingById.set(requestId, message);
      wrapper.classList.add("faith-approval-panel--alert");
      render();
    }

    /**
     * Description:
     *   Schedule a reconnect attempt after the socket closes unexpectedly.
     *
     * Requirements:
     *   - Do not reconnect after the panel has been destroyed.
     */
    function scheduleReconnect() {
      if (destroyed || reconnectTimer) {
        return;
      }
      connectionStatus = "reconnecting";
      render();
      reconnectTimer = globalScope.setTimeout(function onReconnectTimer() {
        reconnectTimer = null;
        connect();
      }, RECONNECT_DELAY_MS);
    }

    /**
     * Description:
     *   Open the approval WebSocket and bind lifecycle handlers.
     *
     * Requirements:
     *   - Reflect connected, disconnected, and reconnecting state visibly.
     *   - Feed all message frames through the normalisation path.
     */
    function connect() {
      if (destroyed || typeof globalScope.WebSocket !== "function") {
        connectionStatus = "disconnected";
        render();
        return;
      }
      connectionStatus = "connecting";
      render();
      socket = new globalScope.WebSocket(buildApprovalWebSocketUrl());
      socket.onopen = function onSocketOpen() {
        connectionStatus = "connected";
        render();
      };
      socket.onmessage = function onSocketMessage(event) {
        normaliseApprovalMessages(event.data).forEach(handleApprovalMessage);
      };
      socket.onerror = function onSocketError() {
        connectionStatus = "disconnected";
        setError("Approval stream disconnected.");
        render();
      };
      socket.onclose = function onSocketClose() {
        if (!destroyed) {
          scheduleReconnect();
        }
      };
    }

    /**
     * Description:
     *   Destroy the mounted panel and stop background connection work.
     *
     * Requirements:
     *   - Close the active WebSocket and cancel any reconnect timer.
     */
    function destroy() {
      destroyed = true;
      if (reconnectTimer) {
        globalScope.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (socket && typeof socket.close === "function") {
        socket.close();
      }
    }

    render();
    connect();

    return {
      destroy: destroy,
      getState() {
        return {
          connectionStatus: connectionStatus,
          pendingCount: pendingById.size,
          historyCount: history.length,
        };
      },
    };
  }

  globalScope.faithApprovalPanel = {
    APPROVAL_POST_PREFIX: APPROVAL_POST_PREFIX,
    APPROVAL_WS_PATH: APPROVAL_WS_PATH,
    DECISIONS: DECISIONS,
    PERSISTED_DECISIONS: PERSISTED_DECISIONS,
    buildApprovalWebSocketUrl: buildApprovalWebSocketUrl,
    buildRulePreview: buildRulePreview,
    mountPanel: mountPanel,
    normaliseApprovalMessages: normaliseApprovalMessages,
  };
})(window);
