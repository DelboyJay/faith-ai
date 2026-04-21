# FAITH-026 - Browser Automation MCP Server (External Playwright)

**Phase:** 6 - MCP Tool Servers
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-003, FAITH-008, FAITH-035
**FRS Reference:** Section 4.5

---

## Objective

Integrate browser automation through the external official Playwright MCP server
instead of building a FAITH-owned browser automation server by default.

FAITH should use the external Playwright MCP package as the v1 browser
automation provider, running inside FAITH's controlled MCP/tool container
boundary rather than directly on the host. The Project Agent has access to this
tool by default so it can perform small QA checks directly, and it may delegate
larger QA workflows to QA/security agents with the same tool available.

---

## Runtime Model

- Use the official external Playwright MCP package (`@playwright/mcp`) via the
  external MCP lifecycle implemented in FAITH-035.
- Run the external server inside FAITH's controlled MCP runtime/container layer,
  not as a host process.
- Use explicit `--headless` and `--isolated` command arguments by default.
- Keep browser state, cookies, downloads, screenshots, traces, and temporary
  profiles inside bounded runtime artefact locations.
- Do not give the browser runtime Docker socket access, privileged mode, or
  broad host mounts.
- Do not place browser automation inside the same disposable Python execution
  sandbox by default; browser automation has different dependencies, state, and
  security boundaries.

---

## PA Access Model

- The PA must be able to see and use every enabled MCP server by default, subject
  to privacy, approval, and explicit user configuration.
- Specialist agents receive narrower tool assignments. For Playwright, the
  default specialist assignments are `qa-engineer` and `security-expert`.
- The PA can use Playwright directly for small browser checks.
- For larger website QA tasks, the PA may delegate to QA/security agents while
  preserving PA orchestration and approval control.

---

## Required Capabilities

At minimum, the Playwright MCP integration must support QA-oriented browser
workflows:

- Navigate to a URL.
- Inspect visible text and DOM state.
- Click buttons and links.
- Fill forms.
- Capture screenshots.
- Report console/page errors when supported by the external MCP server.
- Return structured findings suitable for the PA panel and future QA reports.

Network capture, video recording, browser traces, and Confluence report
generation are useful later enhancements but are not required for the first
working QA POC unless the selected external MCP server exposes them safely.

---

## Acceptance Criteria

1. FAITH provides a default external Playwright MCP registration template using
   the official `@playwright/mcp` package and a pinned version.
2. The default registration launches with explicit `--headless` and
   `--isolated` arguments.
3. The default registration assigns Playwright to QA and security specialist
   agents.
4. The PA can access Playwright regardless of the specialist assignment list,
   provided the registration is enabled and privacy-allowed.
5. The external MCP lifecycle starts Playwright through the FAITH-managed runtime
   container path, not directly on the host.
6. The implementation includes tests proving PA default access, specialist
   assignment behaviour, and the Playwright registration template.
7. Browser automation is blocked or unavailable when privacy or user config
   disables the external server.
8. The implementation is compared against FRS Section 4.5 before the task is
   marked `DONE`.

---

## Out of Scope

- Building a FAITH-owned Playwright FastAPI/JSON-RPC server in v1.
- Running Playwright directly on the host by default.
- Mixing browser automation into Python execution sandbox containers by default.
- Confluence report generation as part of the initial QA POC.

---

## Notes

- If the external Playwright MCP server later fails FAITH's privacy, artefact,
  audit, or QA reporting requirements, FAITH may add a dedicated `tool-browser`
  container as a fallback implementation.
- The external integration should remain replaceable behind the MCP interface so
  FAITH can switch provider without changing PA/agent tool-call semantics.
