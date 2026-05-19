# FAITH-109 — GitHub and ZIP MCP Tool Acquisition Review Flow

**Phase:** 17 — Managed MCP Tool Acquisition & Governance
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-035, FAITH-108
**FRS Reference:** Section 4.11.2.1

---

## Objective

Allow users to add future third-party MCP tools from GitHub repository URLs or
local ZIP uploads through a staged install-review flow that stays safe and
understandable.

---

## Scope

- Accept a GitHub repository URL as a candidate tool source.
- Accept a local ZIP file via upload/drag-drop as a candidate tool source.
- Stage the candidate in a temporary review area before installation.
- Infer runtime and entrypoint details where possible.
- If inference is incomplete or ambiguous, explain why and collect or confirm
  the missing details rather than silently guessing.
- Run deterministic review checks plus a local Ollama advisory summary covering
  `High`, `Medium`, and `Low` concerns.
- Present a concise install summary and require explicit user approval before
  the tool is installed.

---

## Notes

- The review summary is advisory only and must not claim the tool is safe.
- This task introduces the future GitHub/ZIP source path beyond the registry-only
  v1 onboarding flow already covered by FAITH-035.
