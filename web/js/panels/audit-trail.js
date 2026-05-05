/**
 * Description:
 *   Register the FAITH Audit Trail panel runtime.
 *
 * Requirements:
 *   - Fetch read-only audit entries from `/api/logs/audit`.
 *   - Keep results in descending datetime order via the backend contract.
 */

(function initialiseFaithAuditTrailPanel(globalScope) {
  globalScope.faithAuditTrailPanel = globalScope.faithLogPanelCommon.createListPanel({
    title: "Audit Trail",
    endpoint: "/api/logs/audit",
    emptyText: "No audit entries found.",
    filters: [
      { label: "Agent", query: "agent", placeholder: "Filter by agent" },
      { label: "Tool", query: "tool", placeholder: "Filter by tool" },
      { label: "Action", query: "action", placeholder: "Filter by action" },
      { label: "Search", query: "search", placeholder: "Search command or target" },
    ],
    renderItem(item) {
      return globalScope.faithLogPanelCommon.renderRecordCard(item, [
        ["Agent", item.agent],
        ["Tool", item.tool],
        ["Action", item.action],
        ["Target", item.target],
        ["Decision", item.decision],
      ]);
    },
  });
})(window);
