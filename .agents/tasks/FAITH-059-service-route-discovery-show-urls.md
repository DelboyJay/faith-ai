# FAITH-059 — Service Route Discovery & `faith show-urls`

**Phase:** 11 — CLI & Skill Execution
**Complexity:** S
**Model:** Sonnet / GPT-5.4
**Dependencies:** FAITH-005, FAITH-036
**FRS Reference:** Section 9.2.2, 9.6.1

---

## Objective

Implement a structured route-discovery contract for FAITH services and expose it through a new `faith show-urls` CLI command.

The CLI must not hard-code the PA or Web UI endpoint inventory. Instead, each relevant HTTP service exposes `GET /api/routes`, returning a machine-readable manifest of its current HTTP and WebSocket endpoints. The CLI aggregates those manifests and renders a readable endpoint listing similar in spirit to Django's `show_urls`, but service-driven rather than framework-introspected.

---

## Requirements

1. The Project Agent service must expose `GET /api/routes`.
2. The Web UI service must expose `GET /api/routes`.
3. The route manifest must include, at minimum:
   - service name
   - service version
   - protocol (`http` or `websocket`)
   - HTTP method where applicable
   - path
   - brief summary/description
   - expected HTTP status codes for HTTP routes
4. `faith show-urls` must call the service manifests rather than embedding route lists in the CLI source.
5. The CLI output must show absolute URLs, not just paths.
6. If one service is unavailable, the CLI should still show manifests from reachable services and report the unavailable service clearly.
7. If no manifests are reachable, the CLI must fail with actionable guidance (`faith init` / `faith start`).
8. Add request-style tests for every new `GET /api/routes` endpoint.
9. Add CLI tests proving `faith show-urls`:
   - prints discovered HTTP and WebSocket URLs
   - fails cleanly when no services are reachable

---

## Implementation Notes

- Keep the manifest contract in `faith_shared` so the PA, Web UI, and CLI all share one schema.
- Prefer a structured JSON contract over a human-formatted `show-urls` API response.
- The manifest is a discovery endpoint for tooling and diagnostics, so it should not depend on Redis health.
- Include WebSocket endpoints in the manifest because they are part of the supported external surface.

---

## Acceptance Criteria

- `GET /api/routes` exists on the PA and returns HTTP 200 with the structured manifest.
- `GET /api/routes` exists on the Web UI and returns HTTP 200 with the structured manifest.
- `faith show-urls` prints currently available service routes without hard-coded endpoint definitions.
- The CLI output includes method/protocol, absolute URL, and short description for each discovered route.
- Reachable services are still shown when another service is unavailable.
- The command returns a clear actionable error when no manifests are available.
- High-level tests cover both route endpoints and the CLI command.
