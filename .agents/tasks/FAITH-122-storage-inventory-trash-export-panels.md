# FAITH-122 — Storage Inventory, Trash, and Export Panels

**Phase:** 19 — Scoped File Storage & Deterministic Excerpt Retrieval
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-121, FAITH-084, FAITH-074
**FRS Reference:** Section 4.16, 6.4.2

---

## Objective

Provide the browser surfaces for managing stored files, deleted files, and file
inclusion during session export.

---

## Scope

- Add a global Storage inventory panel that shows all stored files.
- Show filename, description, SHA-256, scope, and binding information.
- Support sortable columns and filename/description search.
- Support inline scope editing by dropdown, bulk actions, and per-row delete.
- Add a Trash view/panel for deleted stored files.
- Support `Session only` and `Session + linked files` export options.
- Ask the user whether to `Delete` or `Show Me` when trashed files are about to
  be finalised on shutdown/startup.

---

## Notes

- The Storage panel is a global inventory view. Access control remains
  scope-aware underneath that UI.
