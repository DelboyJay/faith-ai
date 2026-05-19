# FAITH-110 — Managed Tools Directory, Trust Badges, Update Notifications, and Rollback Retention

**Phase:** 17 — Managed MCP Tool Acquisition & Governance
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-109
**FRS Reference:** Section 4.11.2.1, 6.4.2

---

## Objective

Persist future third-party tools in a stable host-backed managed tools
directory and make source/trust/version state visible and recoverable.

---

## Scope

- Store acquired third-party MCP tools in a host-backed managed tools
  directory mounted into the PA runtime.
- Surface source badges such as `Built-in`, `GitHub`, and `ZIP`.
- Surface trust/status badges such as `Required`, `Verified Vendor`, and
  `Unverified` where the source metadata supports them.
- Detect when a GitHub-backed tool has an update available and notify the user
  without auto-updating silently.
- On update, preserve prior versions and keep the last three installed versions
  available for rollback.
- Prune older archived versions automatically beyond the retention limit.

---

## Notes

- `Verified Vendor` is a trust signal, not a security guarantee.
- Rollback must remain user-driven and visible from Manage Tools.
