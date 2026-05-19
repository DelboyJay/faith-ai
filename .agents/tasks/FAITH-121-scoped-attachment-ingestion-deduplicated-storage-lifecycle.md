# FAITH-121 — Scoped Attachment Ingestion and Deduplicated Storage Lifecycle

**Phase:** 19 — Scoped File Storage & Deterministic Excerpt Retrieval
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-099, FAITH-118
**FRS Reference:** Section 4.16, 8.4

---

## Objective

Implement the file-ingestion and storage-lifecycle core so dragged or uploaded
files can be reused safely across inference turns and sessions without storing
duplicate content.

---

## Scope

- Support drag-and-drop ingestion from the Input panel and the Storage panel.
- Persist the original stored file on the host-backed runtime volume.
- Use SHA-256 of file content as the canonical file identifier.
- Prevent duplicate physical storage of identical content.
- Support Global, Scoped, Session, and One-time storage scopes.
- Prompt the user to resolve metadata or scope conflicts when identical content
  is uploaded again with different filename, description, or scope intent.
- Remove One-time files automatically once the inference round finishes and
  control returns to the user.

---

## Notes

- This task owns the storage model and lifecycle rules, not the full inventory
  or trash-management UI.
