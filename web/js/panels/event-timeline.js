/**
 * Description:
 *   Register the FAITH Event Timeline panel runtime.
 *
 * Requirements:
 *   - Fetch read-only event entries from `/api/logs/events`.
 *   - Keep results in descending datetime order via the backend contract.
 */

(function initialiseFaithEventTimelinePanel(globalScope) {
  globalScope.faithEventTimelinePanel = globalScope.faithLogPanelCommon.createListPanel({
    title: "Event Timeline",
    endpoint: "/api/logs/events",
    emptyText: "No event entries found.",
    filters: [
      { label: "Event", query: "event", placeholder: "Filter by event" },
      { label: "Source", query: "source", placeholder: "Filter by source" },
      { label: "Search", query: "search", placeholder: "Search event payload" },
    ],
    renderItem(item) {
      return globalScope.faithLogPanelCommon.renderRecordCard(item, [
        ["Event", item.event],
        ["Source", item.source],
        ["Channel", item.channel],
      ]);
    },
  });
})(window);
