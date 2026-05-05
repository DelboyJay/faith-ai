/**
 * Description:
 *   Register the FAITH Token Usage panel runtime.
 *
 * Requirements:
 *   - Fetch read-only token entries from `/api/logs/tokens`.
 *   - Render aggregate per-model and per-agent summaries above the scrollable log list.
 */

(function initialiseFaithTokenUsagePanel(globalScope) {
  globalScope.faithTokenUsagePanel = globalScope.faithLogPanelCommon.createListPanel({
    title: "Token Usage",
    endpoint: "/api/logs/tokens",
    emptyText: "No token-usage entries found.",
    filters: [
      { label: "Agent", query: "agent", placeholder: "Filter by agent" },
      { label: "Model", query: "model", placeholder: "Filter by model" },
      { label: "Session", query: "session_id", placeholder: "Filter by session" },
      { label: "Search", query: "search", placeholder: "Search token entries" },
    ],
    renderSummary(summary) {
      return globalScope.faithLogPanelCommon.renderTokenSummary(summary);
    },
    renderItem(item) {
      return globalScope.faithLogPanelCommon.renderRecordCard(item, [
        ["Agent", item.agent],
        ["Model", item.model],
        ["Session", item.session_id],
        ["Task", item.task_id],
        ["Input tokens", String(item.input_tokens || 0)],
        ["Output tokens", String(item.output_tokens || 0)],
        ["Estimated cost", String(item.estimated_cost ?? 0)],
      ]);
    },
  });
})(window);
