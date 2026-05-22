/**
 * Description:
 *   Provide shared read-only log-view panel helpers for the FAITH Web UI.
 *
 * Requirements:
 *   - Reuse one consistent filter, pagination, scroll, and refresh shell across log panels.
 *   - Keep all log views read-only and fetch-driven.
 */

(function initialiseFaithLogPanelCommon(globalScope) {
  const DEFAULT_PAGE_SIZE = 50;

  /**
   * Description:
   *   Create one labelled filter control row.
   *
   * Requirements:
   *   - Render a label and attached control together for consistent layout.
   *
   * @param {string} labelText User-facing filter label.
   * @param {HTMLElement} control Input or select element.
   * @returns {HTMLElement} Filter field wrapper.
   */
  function buildField(labelText, control) {
    const wrapper = document.createElement("label");
    wrapper.className = "faith-log-panel__field";
    const label = document.createElement("span");
    label.className = "faith-log-panel__field-label";
    label.textContent = labelText;
    wrapper.appendChild(label);
    wrapper.appendChild(control);
    return wrapper;
  }

  /**
   * Description:
   *   Build one plain text input for a log filter row.
   *
   * Requirements:
   *   - Attach the supplied placeholder for discoverability.
   *
   * @param {string} placeholder Placeholder text for the input.
   * @returns {HTMLInputElement} Configured text input element.
   */
  function buildTextInput(placeholder) {
    const input = document.createElement("input");
    input.className = "faith-log-panel__input";
    input.type = "text";
    input.placeholder = placeholder;
    return input;
  }

  /**
   * Description:
   *   Build one simple select control from the supplied option list.
   *
   * Requirements:
   *   - Include an `All` option when requested.
   *
   * @param {Array<object>} options Available option descriptors.
   * @returns {HTMLSelectElement} Configured select element.
   */
  function buildSelect(options) {
    const select = document.createElement("select");
    select.className = "faith-log-panel__input";
    options.forEach(function appendOption(option) {
      const element = document.createElement("option");
      element.value = option.value;
      element.textContent = option.label;
      select.appendChild(element);
    });
    return select;
  }

  /**
   * Description:
   *   Build the canonical query string for one log-view request.
   *
   * Requirements:
   *   - Omit empty values.
   *   - Include paging fields on every request.
   *
   * @param {object} state Filter and pagination state.
   * @returns {string} URL query string.
   */
  function buildQuery(state) {
    const params = new URLSearchParams();
    Object.entries(state.filters || {}).forEach(function appendFilter(entry) {
      if (entry[1]) {
        params.set(entry[0], String(entry[1]));
      }
    });
    params.set("page", String(state.page || 1));
    params.set("page_size", String(state.pageSize || DEFAULT_PAGE_SIZE));
    return params.toString();
  }

  /**
   * Description:
   *   Return one human-readable timestamp for a log record.
   *
   * Requirements:
   *   - Prefer `ts`, then session/task timestamp fields when present.
   *
   * @param {object} item Structured log or session record.
   * @returns {string} Human-readable timestamp text.
   */
  function getTimestamp(item) {
    return (
      item.ts ||
      item.started_at ||
      item.updated_at ||
      item.ended_at ||
      item.timestamp ||
      "—"
    );
  }

  /**
   * Description:
   *   Create one generic read-only log panel runtime.
   *
   * Requirements:
   *   - Render filters, a scrollable result list, summary content, and paging controls.
   *   - Keep all interactions read-only and HTTP GET based.
   *
   * @param {object} config Log panel configuration payload.
   * @returns {object} Mounted panel namespace exposing `mountPanel`.
   */
  function createListPanel(config) {
    return {
      /**
       * Description:
       *   Mount one generic log panel into the supplied target element.
       *
       * Requirements:
       *   - Fetch data immediately on mount.
       *   - Keep results in descending datetime order as returned by the backend.
       *
       * @param {HTMLElement} target Panel mount element.
       * @returns {object} Cleanup handle for the mounted panel.
       */
      mountPanel(target) {
        const state = {
          page: 1,
          pageSize: DEFAULT_PAGE_SIZE,
          filters: {},
          payload: { items: [], total: 0, page: 1, page_size: DEFAULT_PAGE_SIZE, summary: null },
          loading: false,
          error: "",
          destroyed: false,
        };

        const wrapper = document.createElement("section");
        wrapper.className = "faith-panel faith-log-panel";

        const filters = document.createElement("div");
        filters.className = "faith-log-panel__filters";

        const controls = [];
        (config.filters || []).forEach(function appendConfiguredFilter(filterConfig) {
          const control =
            filterConfig.type === "select"
              ? buildSelect(filterConfig.options || [{ label: "All", value: "" }])
              : buildTextInput(filterConfig.placeholder || "");
          control.addEventListener("change", function onFilterChange() {
            state.filters[filterConfig.query] = control.value;
            state.page = 1;
            void refresh();
          });
          if (filterConfig.type !== "select") {
            control.addEventListener("input", function onFilterInput() {
              state.filters[filterConfig.query] = control.value;
              state.page = 1;
              void refresh();
            });
          }
          controls.push({ config: filterConfig, control: control });
          filters.appendChild(buildField(filterConfig.label, control));
        });

        const errorBanner = document.createElement("p");
        errorBanner.className = "faith-log-panel__error";
        errorBanner.hidden = true;

        const summary = document.createElement("div");
        summary.className = "faith-log-panel__summary";
        summary.hidden = true;

        const content = document.createElement("div");
        content.className = "faith-log-panel__content";

        const footer = document.createElement("div");
        footer.className = "faith-log-panel__footer";

        const pageLabel = document.createElement("span");
        pageLabel.className = "faith-log-panel__page";

        const previousButton = document.createElement("button");
        previousButton.type = "button";
        previousButton.className = "faith-toolbar__button";
        previousButton.textContent = "Previous";
        previousButton.addEventListener("click", function onPreviousPageClick() {
          if (state.page <= 1) {
            return;
          }
          state.page -= 1;
          void refresh();
        });

        const nextButton = document.createElement("button");
        nextButton.type = "button";
        nextButton.className = "faith-toolbar__button";
        nextButton.textContent = "Next";
        nextButton.addEventListener("click", function onNextPageClick() {
          const maxPage = Math.max(1, Math.ceil((state.payload.total || 0) / state.pageSize));
          if (state.page >= maxPage) {
            return;
          }
          state.page += 1;
          void refresh();
        });

        footer.appendChild(previousButton);
        footer.appendChild(pageLabel);
        footer.appendChild(nextButton);

        wrapper.appendChild(filters);
        wrapper.appendChild(errorBanner);
        wrapper.appendChild(summary);
        wrapper.appendChild(content);
        wrapper.appendChild(footer);
        target.replaceChildren(wrapper);

        /**
         * Description:
         *   Render the current state into the panel DOM.
         *
         * Requirements:
         *   - Keep the results list scrollable within the current panel height.
         *   - Preserve the descending order provided by the backend.
         */
        function render() {
          errorBanner.hidden = !state.error;
          errorBanner.textContent = state.error;

          summary.replaceChildren();
          if (typeof config.renderSummary === "function" && state.payload.summary) {
            const renderedSummary = config.renderSummary(state.payload.summary);
            if (renderedSummary) {
              summary.hidden = false;
              summary.appendChild(renderedSummary);
            } else {
              summary.hidden = true;
            }
          } else {
            summary.hidden = true;
          }

          content.replaceChildren();
          if (state.loading) {
            const loading = document.createElement("p");
            loading.className = "faith-log-panel__empty";
            loading.textContent = "Loading…";
            content.appendChild(loading);
          } else if ((state.payload.items || []).length === 0) {
            const empty = document.createElement("p");
            empty.className = "faith-log-panel__empty";
            empty.textContent = config.emptyText || "No log entries found.";
            content.appendChild(empty);
          } else {
            state.payload.items.forEach(function appendItem(item) {
              content.appendChild(config.renderItem(item));
            });
          }

          const maxPage = Math.max(1, Math.ceil((state.payload.total || 0) / state.pageSize));
          pageLabel.textContent = `Page ${state.page} of ${maxPage} · ${state.payload.total || 0} total`;
          previousButton.disabled = state.page <= 1 || state.loading;
          nextButton.disabled = state.page >= maxPage || state.loading;
        }

        /**
         * Description:
         *   Refresh the active log view from its backend endpoint.
         *
         * Requirements:
         *   - Preserve the current filters and page number.
         *   - Surface request failures through the in-panel error banner.
         *
         * @returns {Promise<void>} Promise that resolves when the panel refresh finishes.
         */
        async function refresh() {
          state.loading = true;
          state.error = "";
          render();
          try {
            const query = buildQuery(state);
            const response = await globalScope.fetch(`${config.endpoint}?${query}`);
            if (!response.ok) {
              throw new Error(`${config.title} failed with status ${response.status}`);
            }
            state.payload = await response.json();
          } catch (error) {
            state.error = String(error.message || error);
          } finally {
            state.loading = false;
            if (!state.destroyed) {
              render();
            }
          }
        }

        void refresh();

        return {
          destroy() {
            state.destroyed = true;
          },
        };
      },
    };
  }

  /**
   * Description:
   *   Create one standard log entry card element.
   *
   * Requirements:
   *   - Keep the timestamp visible at the top of the card.
   *   - Render key/value metadata rows for the supplied fields.
   *
   * @param {object} item Structured log entry.
   * @param {Array<Array<string>>} rows Ordered metadata rows.
   * @returns {HTMLElement} Rendered log entry card.
   */
  function renderRecordCard(item, rows) {
    const card = document.createElement("article");
    card.className = "faith-log-panel__record";

    const timestamp = document.createElement("strong");
    timestamp.className = "faith-log-panel__timestamp";
    timestamp.textContent = getTimestamp(item);
    card.appendChild(timestamp);

    rows.forEach(function appendRow(entry) {
      const row = document.createElement("p");
      row.className = "faith-log-panel__row";
      row.textContent = `${entry[0]}: ${entry[1] || "—"}`;
      card.appendChild(row);
    });

    return card;
  }

  /**
   * Description:
   *   Format one aggregate token summary entry into readable plain text.
   *
   * Requirements:
   *   - Keep context/input, inference/output, cache, and effective-context diagnostics together.
   *   - Preserve optional context-file attribution when the backend supplies it.
   *
   * @param {string} label Summary item label.
   * @param {object} value Structured aggregate value from the backend.
   * @returns {string} Human-readable summary line.
   */
  function formatTokenSummaryLine(label, value) {
    const contextInput = value.context_input_tokens ?? value.input_tokens ?? 0;
    const inferenceOutput = value.inference_output_tokens ?? value.output_tokens ?? 0;
    const total = value.total_tokens ?? contextInput + inferenceOutput;
    const windowPct =
      value.context_window_percentage === null || value.context_window_percentage === undefined
        ? "unknown"
        : `${value.context_window_percentage}%`;
    const snapshotId = value.effective_context_snapshot_id || "—";
    const turnId = value.effective_context_turn_id || "—";
    const cacheSummary =
      value.cache_hit === null || value.cache_hit === undefined
        ? "cache unknown"
        : value.cache_hit
          ? `${value.cached_input_tokens || 0} cached input`
          : "cache miss";
    const contextFiles = Array.isArray(value.context_files)
      ? value.context_files
          .map(function mapContextFile(fileEntry) {
            return `${fileEntry.path || "unknown"} (${fileEntry.tokens || 0})`;
          })
          .join(", ")
      : "";
    const calls = value.calls ? `${value.calls} calls, ` : "";
    return `${label}: ${calls}${contextInput} context/input, ${inferenceOutput} inference/output, ${total} total, ${windowPct}, snapshot ${snapshotId}, turn ${turnId}, ${cacheSummary}${contextFiles ? `, files ${contextFiles}` : ""}`;
  }

  /**
   * Description:
   *   Render one summary column for a mapping-style token aggregate section.
   *
   * Requirements:
   *   - Keep each aggregate section visually grouped under a heading.
   *   - Surface a friendly empty state when no summary entries are available.
   *
   * @param {string} title Section heading.
   * @param {object} entries Mapping of summary keys to aggregate values.
   * @returns {HTMLElement} Rendered summary column.
   */
  function renderTokenSummaryColumn(title, entries) {
    const column = document.createElement("section");
    column.className = "faith-log-panel__summary-column";
    const heading = document.createElement("h3");
    heading.className = "faith-log-panel__summary-title";
    heading.textContent = title;
    column.appendChild(heading);

    const summaryEntries = Object.entries(entries || {});
    if (summaryEntries.length === 0) {
      const emptyState = document.createElement("p");
      emptyState.className = "faith-log-panel__row faith-log-panel__row--muted";
      emptyState.textContent = "No summary data yet.";
      column.appendChild(emptyState);
      return column;
    }

    summaryEntries.forEach(function appendSummaryItem(summaryEntry) {
      const item = document.createElement("p");
      item.className = "faith-log-panel__row";
      item.textContent = formatTokenSummaryLine(summaryEntry[0], summaryEntry[1]);
      column.appendChild(item);
    });
    return column;
  }

  /**
   * Description:
   *   Render a lightweight per-agent token usage chart from aggregate totals.
   *
   * Requirements:
   *   - Compare agents by total token usage without requiring a third-party chart library.
   *   - Fall back gracefully when no agent totals are available.
   *
   * @param {object} summaryPayload Aggregate token summary payload from the backend.
   * @returns {HTMLElement} Rendered chart section.
   */
  function renderAgentUsageChart(summaryPayload) {
    const section = document.createElement("section");
    section.className = "faith-log-panel__summary-column";
    const title = document.createElement("h3");
    title.className = "faith-log-panel__summary-title";
    title.textContent = summaryPayload.agent_chart_title || "Agent usage chart";
    section.appendChild(title);

    const agentEntries = Object.entries(summaryPayload.by_agent || {});
    if (agentEntries.length === 0) {
      const emptyState = document.createElement("p");
      emptyState.className = "faith-log-panel__row faith-log-panel__row--muted";
      emptyState.textContent = "No agent usage yet.";
      section.appendChild(emptyState);
      return section;
    }

    const maxTotal = Math.max(
      ...agentEntries.map(function mapAgentTotal(entry) {
        return Number(entry[1] && entry[1].total_tokens ? entry[1].total_tokens : 0);
      }),
      1,
    );
    agentEntries
      .sort(function compareAgentTotals(left, right) {
        return Number(right[1].total_tokens || 0) - Number(left[1].total_tokens || 0);
      })
      .forEach(function appendAgentChartRow(entry) {
        const row = document.createElement("div");
        row.className = "faith-log-panel__chart-row";

        const label = document.createElement("div");
        label.className = "faith-log-panel__chart-label";
        label.textContent = `${entry[0]} — ${entry[1].total_tokens || 0} total`;
        row.appendChild(label);

        const bar = document.createElement("div");
        bar.className = "faith-log-panel__chart-bar";
        const fill = document.createElement("div");
        fill.className = "faith-log-panel__chart-fill";
        fill.style.width = `${Math.max(
          8,
          Math.round((Number(entry[1].total_tokens || 0) / maxTotal) * 100),
        )}%`;
        bar.appendChild(fill);
        row.appendChild(bar);
        section.appendChild(row);
      });

    return section;
  }

  /**
   * Description:
   *   Render a compact session-comparison section from token aggregates.
   *
   * Requirements:
   *   - Show the heaviest or most relevant sessions in a readable ranked list.
   *   - Keep the section useful even when only one session is available.
   *
   * @param {object} summaryPayload Aggregate token summary payload from the backend.
   * @returns {HTMLElement} Rendered session-comparison section.
   */
  function renderSessionComparisons(summaryPayload) {
    const section = document.createElement("section");
    section.className = "faith-log-panel__summary-column";
    const title = document.createElement("h3");
    title.className = "faith-log-panel__summary-title";
    title.textContent = summaryPayload.session_comparison_title || "Session comparisons";
    section.appendChild(title);

    const comparisons = Array.isArray(summaryPayload.session_comparisons)
      ? summaryPayload.session_comparisons
      : [];
    if (comparisons.length === 0) {
      const emptyState = document.createElement("p");
      emptyState.className = "faith-log-panel__row faith-log-panel__row--muted";
      emptyState.textContent = "No session comparisons yet.";
      section.appendChild(emptyState);
      return section;
    }

    comparisons.slice(0, 5).forEach(function appendComparison(entry, index) {
      const row = document.createElement("p");
      row.className = "faith-log-panel__row";
      row.textContent = `${index + 1}. ${entry.session_id || "unknown"} — ${entry.total_tokens || 0} total (${entry.context_input_tokens || 0} context/input, ${entry.inference_output_tokens || 0} inference/output)`;
      section.appendChild(row);
    });
    return section;
  }

  /**
   * Description:
   *   Create one compact summary block for token-usage aggregates.
   *
   * Requirements:
   *   - Render model and agent totals in a readable two-column layout.
   *
   * @param {object} summaryPayload Aggregate summary payload from the backend.
   * @returns {HTMLElement} Rendered summary block.
   */
  function renderTokenSummary(summaryPayload) {
    const block = document.createElement("div");
    block.className = "faith-log-panel__summary-grid";

    [
      renderTokenSummaryColumn("By model", summaryPayload.by_model || {}),
      renderTokenSummaryColumn("By agent", summaryPayload.by_agent || {}),
      renderTokenSummaryColumn("Session total", { session: summaryPayload.session || {} }),
      renderTokenSummaryColumn("Last message", { latest: summaryPayload.last_message || {} }),
      renderAgentUsageChart(summaryPayload),
      renderSessionComparisons(summaryPayload),
    ].forEach(function appendSummaryColumn(column) {
      block.appendChild(column);
    });

    return block;
  }

  globalScope.faithLogPanelCommon = {
    createListPanel: createListPanel,
    renderRecordCard: renderRecordCard,
    renderTokenSummary: renderTokenSummary,
  };
})(window);
