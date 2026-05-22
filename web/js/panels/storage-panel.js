/**
 * Description:
 *   Register the FAITH Storage and Trash inventory panel runtimes.
 *
 * Requirements:
 *   - Render browser-side inventory tables for stored files and trashed files.
 *   - Keep search, sorting, scope-editing intent, and action affordances visible without faking persistence.
 *   - Surface missing backend actions through disabled controls or explicit errors.
 */

(function initialiseFaithStoragePanels(globalScope) {
  const DEFAULT_SORT_KEY = "filename";
  const DEFAULT_SORT_DIRECTION = "asc";
  const SEARCH_PLACEHOLDER = "Search filename or description";
  const SCOPE_OPTIONS = Object.freeze([
    { value: "global", label: "Global" },
    { value: "scoped", label: "Scoped" },
    { value: "session", label: "Session" },
    { value: "one-time", label: "One-time" },
  ]);

  /**
   * Description:
   *   Return one stable item identifier for selection tracking.
   *
   * Requirements:
   *   - Prefer a dedicated file identifier and fall back to the hash or filename.
   *
   * @param {object} item Inventory record.
   * @returns {string} Stable item identifier.
   */
  function getItemId(item) {
    return String((item && (item.file_id || item.id || item.sha256 || item.filename)) || "");
  }

  /**
   * Description:
   *   Resolve one user-visible file label.
   *
   * Requirements:
   *   - Prefer the filename and keep the display stable when the description is missing.
   *
   * @param {object} item Inventory record.
   * @returns {string} Display filename.
   */
  function getItemFilename(item) {
    return String((item && (item.filename || item.name || item.title)) || "Untitled file");
  }

  /**
   * Description:
   *   Resolve one user-visible file description.
   *
   * Requirements:
   *   - Prefer the explicit description and keep the UI readable when it is omitted.
   *
   * @param {object} item Inventory record.
   * @returns {string} Display description.
   */
  function getItemDescription(item) {
    return String((item && (item.description || item.summary || item.note)) || "—");
  }

  /**
   * Description:
   *   Resolve the current storage scope for one inventory item.
   *
   * Requirements:
   *   - Prefer the backend-provided scope and fall back to the default session-oriented scope.
   *
   * @param {object} item Inventory record.
   * @returns {string} Storage scope label.
   */
  function getItemScope(item) {
    return String((item && item.scope) || "session");
  }

  /**
   * Description:
   *   Resolve the current binding label for one inventory item.
   *
   * Requirements:
   *   - Show the session or global binding information if the backend supplies it.
   *
   * @param {object} item Inventory record.
   * @returns {string} Binding label.
   */
  function getItemBinding(item) {
    return String((item && (item.binding || item.binding_label || item.bound_to)) || "—");
  }

  /**
   * Description:
   *   Return the available action URL map for one inventory item.
   *
   * Requirements:
   *   - Let the UI disable missing backend routes instead of inventing local persistence.
   *
   * @param {object} item Inventory record.
   * @returns {object} Action URL map.
   */
  function getItemActions(item) {
    return (item && (item.actions || item.action_urls || item.available_actions)) || {};
  }

  /**
   * Description:
   *   Build one inventory panel runtime for the supplied backend contract.
   *
   * Requirements:
   *   - Support server-driven refreshes, search, sorting, selection, and action hooks.
   *
   * @param {object} config Panel configuration.
   * @returns {object} Mounted panel namespace.
   */
  function createInventoryPanel(config) {
    return {
      /**
       * Description:
       *   Mount one inventory panel into the supplied target element.
       *
       * Requirements:
       *   - Fetch the inventory immediately and render a visible empty/error state when needed.
       *   - Keep the table searchable, sortable, and action-driven without pretending persistence.
       *
       * @param {HTMLElement} target Panel mount element.
       * @returns {object} Cleanup handle for the mounted panel.
       */
      mountPanel(target) {
        const state = {
          items: [],
          searchTerm: "",
          sortKey: DEFAULT_SORT_KEY,
          sortDirection: DEFAULT_SORT_DIRECTION,
          selectedIds: new Set(),
          loading: false,
          error: "",
          destroyed: false,
        };

        const wrapper = document.createElement("section");
        wrapper.className = `faith-panel ${config.panelClassName}`;

        const header = document.createElement("div");
        header.className = `${config.panelClassName}__header`;

        const title = document.createElement("h2");
        title.className = `${config.panelClassName}__title`;
        title.textContent = config.title;
        header.appendChild(title);

        const toolbar = document.createElement("div");
        toolbar.className = `${config.panelClassName}__toolbar`;

        const searchInput = document.createElement("input");
        searchInput.type = "search";
        searchInput.className = "faith-log-panel__input";
        searchInput.placeholder = SEARCH_PLACEHOLDER;
        searchInput.addEventListener("input", function onSearchInput() {
          state.searchTerm = searchInput.value || "";
          render();
        });
        toolbar.appendChild(searchInput);

        const bulkActions = document.createElement("div");
        bulkActions.className = `${config.panelClassName}__bulk-actions`;
        toolbar.appendChild(bulkActions);

        const uploadControls = document.createElement("div");
        uploadControls.className = `${config.panelClassName}__upload`;
        if (config.allowUpload) {
          const scopeSelect = document.createElement("select");
          scopeSelect.className = "faith-log-panel__input";
          SCOPE_OPTIONS.forEach(function appendUploadScope(option) {
            const scopeOption = document.createElement("option");
            scopeOption.value = option.value;
            scopeOption.textContent = option.label;
            scopeSelect.appendChild(scopeOption);
          });
          scopeSelect.value = config.uploadDefaultScope || "global";
          uploadControls.appendChild(scopeSelect);

          const uploadInput = document.createElement("input");
          uploadInput.type = "file";
          uploadInput.hidden = true;

          const uploadButton = document.createElement("button");
          uploadButton.type = "button";
          uploadButton.className = "faith-toolbar__button";
          uploadButton.textContent = "Upload";
          uploadButton.addEventListener("click", function onUploadButtonClick() {
            if (typeof uploadInput.click === "function") {
              uploadInput.click();
            }
          });
          uploadControls.appendChild(uploadButton);
          uploadControls.appendChild(uploadInput);

          const dropZone = document.createElement("div");
          dropZone.className = `${config.panelClassName}__dropzone`;
          dropZone.textContent = "Drop files here";
          uploadControls.appendChild(dropZone);

          async function submitUpload(file) {
            const formData = new globalScope.FormData();
            formData.append("file", file);
            formData.append("scope", scopeSelect.value || (config.uploadDefaultScope || "global"));
            formData.append("description", "");
            formData.append("session_bindings", "[]");
            const response = await globalScope.fetch("/api/storage/files", {
              method: "POST",
              body: formData,
            });
            if (!response.ok) {
              throw new Error(`${config.title} upload failed with status ${response.status}`);
            }
            await refresh();
          }

          uploadInput.addEventListener("change", function onUploadInputChange() {
            const files = Array.from(uploadInput.files || []);
            files.forEach(function uploadSelectedFile(file) {
              void submitUpload(file).catch(function onUploadError(error) {
                setError(String(error.message || error));
                render();
              });
            });
          });

          dropZone.addEventListener("drop", function onDrop(event) {
            const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []);
            files.forEach(function uploadDroppedFile(file) {
              void submitUpload(file).catch(function onUploadError(error) {
                setError(String(error.message || error));
                render();
              });
            });
          });
        }
        toolbar.appendChild(uploadControls);

        const errorBanner = document.createElement("p");
        errorBanner.className = `${config.panelClassName}__error`;
        errorBanner.hidden = true;

        const content = document.createElement("div");
        content.className = `${config.panelClassName}__content`;

        wrapper.appendChild(header);
        wrapper.appendChild(toolbar);
        wrapper.appendChild(errorBanner);
        wrapper.appendChild(content);
        target.replaceChildren(wrapper);

        /**
         * Description:
         *   Show or clear the visible panel error.
         *
         * Requirements:
         *   - Keep transport and action failures readable inside the panel.
         *
         * @param {string} message Error message to display.
         */
        function setError(message) {
          errorBanner.hidden = !message;
          errorBanner.textContent = message || "";
        }

        /**
         * Description:
         *   Return the subset of items that matches the current search text and sort order.
         *
         * Requirements:
         *   - Search filename and description.
         *   - Sort by the active column and direction.
         *
         * @returns {Array<object>} Filtered and sorted inventory rows.
         */
        function getVisibleItems() {
          const query = state.searchTerm.trim().toLowerCase();
          const filtered = (state.items || []).filter(function keepMatchingItem(item) {
            if (!query) {
              return true;
            }
            const haystack = [
              getItemFilename(item),
              getItemDescription(item),
              item.sha256 || "",
              getItemScope(item),
              getItemBinding(item),
            ]
              .join(" ")
              .toLowerCase();
            return haystack.includes(query);
          });

          filtered.sort(function compareItems(left, right) {
            const leftValue = String(
              left && left[state.sortKey] !== undefined
                ? left[state.sortKey]
                : state.sortKey === "binding"
                  ? getItemBinding(left)
                  : state.sortKey === "scope"
                    ? getItemScope(left)
                    : state.sortKey === "description"
                      ? getItemDescription(left)
                      : getItemFilename(left),
            ).toLowerCase();
            const rightValue = String(
              right && right[state.sortKey] !== undefined
                ? right[state.sortKey]
                : state.sortKey === "binding"
                  ? getItemBinding(right)
                  : state.sortKey === "scope"
                    ? getItemScope(right)
                    : state.sortKey === "description"
                      ? getItemDescription(right)
                      : getItemFilename(right),
            ).toLowerCase();
            if (leftValue === rightValue) {
              return 0;
            }
            const comparison = leftValue.localeCompare(rightValue);
            return state.sortDirection === "asc" ? comparison : -comparison;
          });

          return filtered;
        }

        /**
         * Description:
         *   Update the selected row identifiers from the current checkbox state.
         *
         * Requirements:
         *   - Let bulk actions target only the rows the user selected.
         *
         * @param {string} itemId Selected item identifier.
         * @param {boolean} selected Whether the row is selected.
         */
        function setSelected(itemId, selected) {
          if (selected) {
            state.selectedIds.add(itemId);
          } else {
            state.selectedIds.delete(itemId);
          }
        }

        /**
         * Description:
         *   Return whether one action should be enabled for the supplied item.
         *
         * Requirements:
         *   - Disable buttons when the backend does not expose the required endpoint.
         *
         * @param {object} item Inventory record.
         * @param {string} actionKey Action URL key.
         * @returns {boolean} True when the action is available.
         */
        function hasAction(item, actionKey) {
          const actions = getItemActions(item);
          return Boolean(actions && actions[actionKey]);
        }

        /**
         * Description:
         *   Submit one row-level action to the backend.
         *
         * Requirements:
         *   - Use the backend-provided endpoint and surface request errors visibly.
         *
         * @param {object} item Inventory record.
         * @param {string} actionKey Action URL key.
         * @param {object|undefined} bodyPayload Optional JSON payload.
         * @param {string} method HTTP method.
         * @returns {Promise<void>} Promise that resolves after the request completes.
         */
        async function submitRowAction(item, actionKey, bodyPayload, method) {
          const actions = getItemActions(item);
          const actionUrl = actions[actionKey];
          if (!actionUrl) {
            throw new Error(`Missing backend action for ${actionKey}.`);
          }
          const fetchOptions = { method: method || "POST" };
          if (bodyPayload !== undefined) {
            fetchOptions.headers = { "Content-Type": "application/json" };
            fetchOptions.body = JSON.stringify(bodyPayload);
          }
          const response = await globalScope.fetch(actionUrl, fetchOptions);
          if (!response.ok) {
            throw new Error(`${config.title} action failed with status ${response.status}`);
          }
        }

        /**
         * Description:
         *   Apply a bulk action to the current selection.
         *
         * Requirements:
         *   - Use the selected rows rather than a fake local delete list.
         *
         * @param {object} action Action descriptor.
         * @returns {Promise<void>} Promise that resolves when the action completes.
         */
        async function submitBulkAction(action) {
          const selectedItems = getVisibleItems().filter(function keepSelectedItem(item) {
            return state.selectedIds.has(getItemId(item));
          });
          if (selectedItems.length === 0) {
            return;
          }
          if (typeof action.prepare === "function") {
            await action.prepare(selectedItems);
          }
          if (typeof action.endpoint === "function") {
            const response = await globalScope.fetch(action.endpoint(selectedItems), {
              method: action.method || "POST",
            });
            if (!response.ok) {
              throw new Error(`${config.title} bulk action failed with status ${response.status}`);
            }
          } else if (action.endpoint) {
            const response = await globalScope.fetch(action.endpoint, {
              method: action.method || "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(action.buildBody ? action.buildBody(selectedItems) : {}),
            });
            if (!response.ok) {
              throw new Error(`${config.title} bulk action failed with status ${response.status}`);
            }
          }
          await refresh();
        }

        /**
         * Description:
         *   Render the inventory table body for one item.
         *
         * Requirements:
         *   - Expose per-row actions and inline scope changes where the backend allows them.
         *
         * @param {object} item Inventory record.
         * @returns {HTMLElement} Table row element.
         */
        function buildRow(item) {
          const row = document.createElement("tr");
          row.className = `${config.panelClassName}__row`;

          const selectCell = document.createElement("td");
          const selectCheckbox = document.createElement("input");
          selectCheckbox.type = "checkbox";
          selectCheckbox.checked = state.selectedIds.has(getItemId(item));
          selectCheckbox.addEventListener("change", function onRowSelect() {
            setSelected(getItemId(item), selectCheckbox.checked);
          });
          selectCell.appendChild(selectCheckbox);
          row.appendChild(selectCell);

          const filenameCell = document.createElement("td");
          filenameCell.textContent = getItemFilename(item);
          row.appendChild(filenameCell);

          const descriptionCell = document.createElement("td");
          descriptionCell.textContent = getItemDescription(item);
          row.appendChild(descriptionCell);

          const shaCell = document.createElement("td");
          shaCell.textContent = item.sha256 || "—";
          row.appendChild(shaCell);

          const scopeCell = document.createElement("td");
          if (config.allowScopeEdit) {
            const scopeSelect = document.createElement("select");
            scopeSelect.className = "faith-log-panel__input";
            SCOPE_OPTIONS.forEach(function appendScopeOption(option) {
              const scopeOption = document.createElement("option");
              scopeOption.value = option.value;
              scopeOption.textContent = option.label;
              scopeSelect.appendChild(scopeOption);
            });
            scopeSelect.value = getItemScope(item);
            scopeSelect.disabled = !hasAction(item, "scope");
            scopeSelect.addEventListener("change", function onScopeChange() {
              void submitRowAction(item, "scope", { scope: scopeSelect.value }, "PUT").catch(function onScopeError(error) {
                setError(String(error.message || error));
                render();
              });
            });
            scopeCell.appendChild(scopeSelect);
          } else {
            scopeCell.textContent = getItemScope(item);
          }
          row.appendChild(scopeCell);

          const bindingCell = document.createElement("td");
          bindingCell.textContent = getItemBinding(item);
          row.appendChild(bindingCell);

          const actionsCell = document.createElement("td");
          const rowActions = (typeof config.getRowActions === "function" ? config.getRowActions(item, state) : []).filter(Boolean);
          rowActions.forEach(function appendRowAction(action) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "faith-toolbar__button";
            button.textContent = action.label;
            button.disabled = action.disabled || !hasAction(item, action.actionKey);
            button.addEventListener("click", function onRowActionClick() {
              if (button.disabled) {
                return;
              }
              void submitRowAction(item, action.actionKey, action.body || undefined, action.method || "POST")
                .then(function onRowActionSuccess() {
                  return refresh();
                })
                .catch(function onRowActionError(error) {
                  setError(String(error.message || error));
                  render();
                });
            });
            actionsCell.appendChild(button);
          });
          row.appendChild(actionsCell);

          return row;
        }

        /**
         * Description:
         *   Render the current inventory state into the panel DOM.
         *
         * Requirements:
         *   - Keep the search, sort, and bulk-action affordances synchronized with the loaded items.
         */
        function render() {
          errorBanner.hidden = !state.error;
          errorBanner.textContent = state.error;

          bulkActions.replaceChildren();
          (config.bulkActions || []).forEach(function appendBulkAction(action) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "faith-toolbar__button";
            button.textContent = action.label;
            const selectedCount = state.selectedIds.size;
            button.disabled = state.loading || selectedCount === 0 || Boolean(action.disabled);
            button.addEventListener("click", function onBulkClick() {
              void submitBulkAction(action).catch(function onBulkActionError(error) {
                setError(String(error.message || error));
                render();
              });
            });
            bulkActions.appendChild(button);
          });

          content.replaceChildren();
          if (state.loading && state.items.length === 0) {
            const loading = document.createElement("p");
            loading.className = `${config.panelClassName}__empty`;
            loading.textContent = "Loading…";
            content.appendChild(loading);
            return;
          }

          const visibleItems = getVisibleItems();
          if (visibleItems.length === 0) {
            const empty = document.createElement("p");
            empty.className = `${config.panelClassName}__empty`;
            empty.textContent = state.searchTerm ? "No matching files found." : config.emptyText;
            content.appendChild(empty);
            return;
          }

          const table = document.createElement("table");
          table.className = `${config.panelClassName}__table`;

          const thead = document.createElement("thead");
          const headerRow = document.createElement("tr");

          const selectHeader = document.createElement("th");
          selectHeader.textContent = "Select";
          headerRow.appendChild(selectHeader);

          const columns = [
            { key: "filename", label: "Filename" },
            { key: "description", label: "Description" },
            { key: "sha256", label: "SHA-256" },
            { key: "scope", label: "Scope" },
            { key: "binding", label: "Binding" },
          ];

          columns.forEach(function appendHeader(column) {
            const headerCell = document.createElement("th");
            const sortButton = document.createElement("button");
            sortButton.type = "button";
            sortButton.className = `${config.panelClassName}__sort-button`;
            sortButton.textContent = column.label;
            sortButton.dataset.sortDirection =
              state.sortKey === column.key ? state.sortDirection : "";
            sortButton.addEventListener("click", function onSortClick() {
              if (state.sortKey === column.key) {
                state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
              } else {
                state.sortKey = column.key;
                state.sortDirection = DEFAULT_SORT_DIRECTION;
              }
              render();
            });
            headerCell.appendChild(sortButton);
            headerRow.appendChild(headerCell);
          });

          const actionsHeader = document.createElement("th");
          actionsHeader.textContent = "Actions";
          headerRow.appendChild(actionsHeader);

          thead.appendChild(headerRow);
          table.appendChild(thead);

          const tbody = document.createElement("tbody");
          visibleItems.forEach(function appendItem(item) {
            tbody.appendChild(buildRow(item));
          });
          table.appendChild(tbody);
          content.appendChild(table);
        }

        /**
         * Description:
         *   Fetch the inventory snapshot from the backend.
         *
         * Requirements:
         *   - Surface HTTP failures visibly instead of faking local persistence.
         *
         * @returns {Promise<void>} Promise that resolves after the refresh completes.
         */
        async function refresh() {
          state.loading = true;
          state.error = "";
          render();
          try {
            const response = await globalScope.fetch(config.endpoint);
            if (!response.ok) {
              throw new Error(`${config.title} failed with status ${response.status}`);
            }
            const payload = await response.json();
            state.items = payload.items || [];
            state.selectedIds = new Set(
              Array.from(state.selectedIds).filter(function keepVisibleSelection(itemId) {
                return state.items.some(function hasItem(item) {
                  return getItemId(item) === itemId;
                });
              }),
            );
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
          getState() {
            return {
              error: state.error,
              loading: state.loading,
              itemCount: state.items.length,
              selectedCount: state.selectedIds.size,
              searchTerm: state.searchTerm,
              sortKey: state.sortKey,
              sortDirection: state.sortDirection,
            };
          },
        };
      },
    };
  }

  globalScope.faithStoragePanel = createInventoryPanel({
    allowUpload: true,
    allowScopeEdit: true,
    bulkActions: [
      {
        label: "Delete selected",
        endpoint: "/api/storage/files/bulk-delete",
        method: "POST",
        buildBody(selectedItems) {
          return {
            file_ids: selectedItems.map(function mapItem(item) {
              return getItemId(item);
            }),
          };
        },
      },
      {
        label: "Export selected",
        endpoint: "/api/storage/files/bulk-export",
        method: "POST",
        buildBody(selectedItems) {
          return {
            file_ids: selectedItems.map(function mapItem(item) {
              return getItemId(item);
            }),
            export_scope: "session+linked",
          };
        },
      },
    ],
    emptyText: "No stored files found.",
    endpoint: "/api/storage/files",
    getRowActions(item) {
      return [
        {
          actionKey: "delete",
          label: "Delete",
          method: "DELETE",
        },
      ];
    },
    panelClassName: "faith-storage-panel",
    title: "Storage Inventory",
    uploadDefaultScope: "global",
  });

  globalScope.faithTrashPanel = createInventoryPanel({
    allowScopeEdit: false,
    bulkActions: [
      {
        label: "Restore selected",
        endpoint: "/api/storage/trash/bulk-restore",
        method: "POST",
        buildBody(selectedItems) {
          return {
            file_ids: selectedItems.map(function mapItem(item) {
              return getItemId(item);
            }),
          };
        },
      },
      {
        label: "Delete selected",
        endpoint: "/api/storage/trash/bulk-delete",
        method: "DELETE",
        buildBody(selectedItems) {
          return {
            file_ids: selectedItems.map(function mapItem(item) {
              return getItemId(item);
            }),
          };
        },
      },
    ],
    emptyText: "Trash is empty.",
    endpoint: "/api/storage/trash",
    getRowActions(item) {
      return [
        {
          actionKey: "restore",
          label: "Restore",
          method: "POST",
        },
        {
          actionKey: "delete",
          label: "Delete permanently",
          method: "DELETE",
        },
      ];
    },
    panelClassName: "faith-trash-panel",
    title: "Trash",
  });
})(window);
