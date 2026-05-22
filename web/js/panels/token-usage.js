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
      return globalScope.faithLogPanelCommon.renderTokenSummary(
        Object.assign({}, summary, {
          agent_chart_title: "Agent usage chart",
          session_comparison_title: "Session comparisons",
        }),
      );
    },
    renderItem(item) {
      return globalScope.faithLogPanelCommon.renderRecordCard(item, [
        ["Agent", item.agent],
        ["Model", item.model],
        ["Session", item.session_id],
        ["Task", item.task_id],
        ["Context/input tokens", String(item.input_tokens || 0)],
        ["Inference/output tokens", String(item.output_tokens || 0)],
        ["Total tokens", String((item.input_tokens || 0) + (item.output_tokens || 0))],
        ["Context window", item.context_window_percentage == null ? "unknown" : `${item.context_window_percentage}%`],
        ["Effective-context snapshot", item.effective_context_snapshot_id || "—"],
        ["Effective-context turn", item.effective_context_turn_id || "—"],
        ["Cache", item.cache_hit == null ? "unknown" : item.cache_hit ? "hit" : "miss"],
        ["Cached input tokens", String(item.cached_input_tokens ?? 0)],
        [
          "Context files",
          Array.isArray(item.context_files) && item.context_files.length > 0
            ? item.context_files
                .map(function mapContextFile(fileEntry) {
                  return `${fileEntry.path || "unknown"} (${fileEntry.tokens || 0})`;
                })
                .join(", ")
            : "—",
        ],
        ["Estimated cost", String(item.estimated_cost ?? 0)],
      ]);
    },
  });
})(window);
