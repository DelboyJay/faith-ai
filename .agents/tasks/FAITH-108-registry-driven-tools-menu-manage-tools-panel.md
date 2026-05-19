# FAITH-108 — Registry-Driven Tools Menu & Manage Tools Panel

**Phase:** 17 — Managed MCP Tool Acquisition & Governance
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-044, FAITH-081, FAITH-084, FAITH-074
**FRS Reference:** Section 6.4.2

---

## Objective

Replace the current tool-id-oriented tool UI with a registry-driven user
experience where installed/enabled tools are discoverable from the Panels menu
and all tool governance actions live under a dedicated Manage Tools surface.

---

## Scope

- Populate `Panels -> Tools` from the canonical MCP registry rather than from
  user-entered tool identifiers.
- List only installed and enabled MCP tools in the initial `Panels -> Tools`
  submenu.
- Add `Panels -> Tools -> Manage` as the entry point for tool administration.
- In Manage Tools, show built-in tools, installed tools, disabled tools, and
  explicitly known available tools without pretending FAITH has a complete
  marketplace when it does not.
- Make each tool row show tool name, source badge, trust/status badges,
  install state, overall health, and the full path to the tool's main folder.

---

## Notes

- Built-in required tools must be clearly marked and must not be removable or
  disableable.
- This task is primarily about discoverability and UI structure, not the future
  GitHub/ZIP acquisition flow itself.
