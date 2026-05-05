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
      ["By model", summaryPayload.by_model || {}],
      ["By agent", summaryPayload.by_agent || {}],
    ].forEach(function appendSummaryColumn(entry) {
      const column = document.createElement("section");
      column.className = "faith-log-panel__summary-column";
      const title = document.createElement("h3");
      title.className = "faith-log-panel__summary-title";
      title.textContent = entry[0];
      column.appendChild(title);

      Object.entries(entry[1]).forEach(function appendSummaryItem(summaryEntry) {
        const item = document.createElement("p");
        item.className = "faith-log-panel__row";
        item.textContent = `${summaryEntry[0]}: ${summaryEntry[1].calls} calls, ${summaryEntry[1].input_tokens} in, ${summaryEntry[1].output_tokens} out`;
        column.appendChild(item);
      });
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
