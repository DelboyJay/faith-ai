/**
 * Description:
 *   Register the FAITH Approval History panel runtime.
 *
 * Requirements:
 *   - Fetch read-only approval history from `/api/logs/approvals`.
 *   - Keep results in descending datetime order via the backend contract.
 */

(function initialiseFaithApprovalHistoryPanel(globalScope) {
  globalScope.faithApprovalHistoryPanel = globalScope.faithLogPanelCommon.createListPanel({
    title: "Approval History",
    endpoint: "/api/logs/approvals",
    emptyText: "No approval decisions found.",
    filters: [
      { label: "Agent", query: "agent", placeholder: "Filter by agent" },
      { label: "Tool", query: "tool", placeholder: "Filter by tool" },
      { label: "Decision", query: "decision", placeholder: "Filter by decision" },
      { label: "Search", query: "search", placeholder: "Search approval history" },
    ],
    renderItem(item) {
      return globalScope.faithLogPanelCommon.renderRecordCard(item, [
        ["Agent", item.agent],
        ["Tool", item.tool],
        ["Action", item.action],
        ["Decision", item.decision],
        ["Approval tier", item.approval_tier],
        ["Target", item.target],
      ]);
    },
  });
})(window);
