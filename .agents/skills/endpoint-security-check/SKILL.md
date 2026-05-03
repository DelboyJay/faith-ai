---
name: endpoint-security-check
description: Examine one API or browser-facing endpoint for security weaknesses, confirm the live code and tests, and return evidence-backed findings sorted by severity.
---

# Endpoint Security Check

## Purpose
Apply this skill for a narrow security assessment of one endpoint, route, controller action, or equivalent handler. The review must consider authentication, authorization, object and property boundaries, data validation and exposure, injection paths, resource consumption, misuse or abuse cases, browser and API safeguards, session behavior, error handling, cryptography where relevant, third-party API consumption, logging, and differences between documented intent and implemented behavior.

## Required Input
The user must identify exactly one target using either:
- `ENDPOINT_URL`
- `ROUTE_NAME`

Optional context:
- `HTTP_METHODS`
- `AUTH_MODE`
  - `anonymous`
  - `oauth2`
  - `session`
  - `mixed`
- `FOCUS_AREAS`
  - `auth`
  - `authz`
  - `idor`
  - `data-exposure`
  - `injection`
  - `browser`
  - `business-logic`
  - `rate-limit`
  - `logging`
  - `session`
  - `error-handling`
  - `crypto`
  - `third-party-api`
  - `inventory`

When no `ENDPOINT_URL` or `ROUTE_NAME` is provided:
- ask the user to name one specific endpoint
- wait for their answer before continuing
- do not infer the target
- do not widen the scope to multiple endpoints unless the user clearly requests that

## Operating Rules
- Inspect the current workspace state during every run; do not reuse earlier code knowledge as fact.
- Keep the investigation local unless a bounded parallel lookup is genuinely useful.
- Delegate only independent side work that will not block the next local step.
- Select the least powerful capable model tier for delegated work.
- Maintain one clear evidence record with precise file references.
- If sub-agents are used, state the capability tier chosen and the concrete model name when the runtime exposes it.

## Capability Tiers
### `lightweight`
Use for straightforward evidence gathering, for example:
- grepping for a route after the likely module is known
- locating request or response schemas
- finding nearby tests
- checking for logging or throttling hooks
- other simple read-only lookups with little ambiguity

### `balanced`
Use when moderate interpretation is required, such as:
- connecting FastAPI route registration to dependency wiring
- following request and response shapes across layers
- evaluating whether tests cover the important behavior
- reviewing throttling, logging, CORS, CSRF, or other framework protections

### `high_reasoning`
Use for deeper judgment calls, including:
- complex authentication or authorization paths
- IDOR/BOLA risks and tenant or ownership boundaries
- injection, SSRF, deserialization, or business-logic issues spanning more than one layer
- final severity decisions when evidence can reasonably support more than one rating

## Runtime Mapping
Translate capability tiers to the active provider at runtime. Do not hardcode one vendor as the only option.

Examples:
- OpenAI: `lightweight` maps to mini or fast models, `balanced` maps to mid-tier reasoning models, and `high_reasoning` maps to flagship reasoning models.
- Anthropic: `lightweight` maps to Haiku-class models, `balanced` maps to Sonnet-class models, and `high_reasoning` maps to Opus-class models.

If the runtime does not reveal the exact model, say that explicitly.

## Delegation Guidance
- Prefer a single local review when the endpoint is small or the relevant code is concentrated.
- Use no more than 2-3 sub-agents unless the user asks for a wider investigation.
- Useful delegated slices include:
  - endpoint implementation mapping and test discovery
  - authentication, authorization, and ownership-boundary analysis
  - injection, browser protection, and abuse-resilience review
- Keep endpoint resolution and final severity assignment local unless the user explicitly requests broader parallel work.

## Review Process
1. Verify that the endpoint target is specific.
2. Read the current codebase state for this run.
3. Resolve the endpoint to its concrete implementation.
4. Decide whether local review is enough or whether bounded side tasks would help.
5. Identify route registration, handler function, request and response models, service layer, and existing tests.
6. Determine which authentication mechanisms can actually reach the endpoint.
7. Determine the authorization checks enforced by the current code.
8. Produce an auth-path matrix based on real reachable paths.
9. Inspect request inputs and where trusted identifiers come from.
10. Inspect response data and possible exposure.
11. Evaluate abuse cases and cross-object or cross-tenant access.
12. Review object property-level authorization for readable and writable fields.
13. Review browser and API protections that apply to this endpoint.
14. Check behavior controlled by config, feature flags, API versioning, deployment mode, or environment-specific branches.
15. Review resource consumption, payload limits, quotas, timeouts, expensive queries, and paid downstream operations.
16. Review third-party or outbound API consumption when endpoint data can influence it.
17. For write paths, review side effects, transactions, retries, idempotency, and race exposure.
18. Check framework-level dependency, authentication, and authorization wiring when relevant.
19. Compare implementation behavior with the FRS.
20. Search for existing tests before adding any new tests.
21. Add small targeted tests only when runtime evidence is needed and coverage is missing.
22. Run relevant tests whenever they already exist or were added.
23. Review logging and audit behavior for sensitive actions and failures.
24. Consolidate evidence, resolve contradictory signals, and assign severity.
25. Save the report under `.agents/security/` and present findings from highest to lowest severity.

## Repository-Specific Rules
- Treat every run as fresh; the code may have changed.
- Repeat route discovery and reopen the current source files each time.
- Recheck any prior finding before referencing it.
- Use the FRS as the expected contract, while still verifying the registered route and permission stack from code.
- If a report already exists for the same endpoint, update that report instead of creating a disconnected duplicate.
- When updating an existing report, preserve the old finding context that is still useful, revalidate it against current code, and make fixed items obvious to the reader.

## Detailed Check Areas
### Endpoint Resolution
- Map a route name to its path and owner, or map a URL/path to the registered route.
- Confirm supported HTTP methods, route names, and the real action handler.

### Implementation Mapping
Trace:
- route registration
- endpoint function or handler
- request and response model definitions
- dependency-driven authentication and authorization
- throttling or rate-limit classes
- payload size, pagination, timeout, and quota controls
- service, repository, task, job, or background side effects
- outbound API, webhook, callback, or third-party client calls
- existing tests

### Test Discipline
- Look for nearby tests before creating new files.
- Extend existing endpoint, dependency, auth, or schema tests when that fits.
- Add the smallest focused regression test needed to prove behavior.
- Run the relevant subset and report both the command and result.

### Authentication Review
Check that the endpoint handles:
- required login or token presence
- anonymous access attempts
- malformed, missing, revoked, or expired credentials
- unexpected authentication routes
- session and token mode mismatches
- project-specific authentication backends that might still reach the handler

### Authorization Review
Check:
- required scopes and scope combinations
- method-specific permission differences
- ownership and tenant boundaries
- IDOR/BOLA and horizontal escalation
- vertical privilege escalation
- field-level access control
- property-level read/write authorization
- authorization dependencies that must exist outside tests
- shared handler or router behavior that varies by method

### Input Review
Check:
- required fields
- extra or unknown fields
- type checks and boundary validation
- enum and workflow-state validation
- query, filter, sort, and pagination constraints
- mass-assignment exposure
- object property updates that bypass field-level authorization
- which identifier source is trusted: path, body, query, token, session, installation, or server-derived state

### Output Review
Check:
- returned fields
- exposure of tokens, identifiers, notes, internals, metadata, or other sensitive values
- correct removal of gated fields
- object property leakage through nested data, serializer defaults, or debug metadata
- safe error responses
- enumeration through status codes, timing, or response-body differences
- behavior for `400`, `403`, `404`, `409`, and `500` where those cases matter

### Injection and Execution Review
Check whether endpoint-controlled values can flow into:
- SQL or ORM filters and ordering
- shell commands
- filesystem paths
- templates
- headers
- redirects
- outbound URLs
- parsers, decoders, or deserializers

Look for:
- SQL injection
- command injection
- path traversal
- template injection
- header injection
- open redirect
- SSRF
- unsafe deserialization

### Browser and API Review
When applicable, evaluate:
- CSRF
- CORS
- XSS
- clickjacking defenses
- caching of sensitive responses
- cookie flags in session-authenticated flows
- DOM-based risks, client-side redirects, client-side authorization assumptions, and unsafe rendering of server data

### Session Management Review
When the endpoint can be reached through a browser or session-backed flow, check:
- session fixation and session rotation after login or privilege change
- logout and invalidation behavior
- cookie scope, expiry, `HttpOnly`, `Secure`, and `SameSite`
- privilege changes that leave stale session permissions active
- mixed session/token behavior that can bypass expected controls

### Abuse and Resilience Review
Check:
- rate limits and throttles
- resistance to enumeration
- payload size and body parsing limits
- timeout, pagination, and maximum-result limits
- CPU, memory, storage, email/SMS, payment, or other cost-amplifying resource use
- replay or duplicate-action behavior
- idempotency for state-changing operations
- race conditions
- bulk abuse
- scraping through filters or pagination
- expensive query shapes, fanout, or amplification
- automation of sensitive business flows such as signup, checkout, invitations, exports, messaging, or quota-consuming operations

### Error Handling Review
Check:
- whether validation, authorization, and server errors avoid exposing internals
- whether stack traces, dependency errors, SQL fragments, filesystem paths, tokens, or configuration values can leak
- whether error differences allow user, object, tenant, or state enumeration
- whether client-facing errors remain consistent with the FRS and tests

### Cryptography and Secret Handling Review
When the endpoint handles credentials, tokens, signed values, encrypted data, or secrets, check:
- use of approved algorithms and libraries rather than custom cryptography
- token expiry, audience, issuer, signature, and replay protection
- password, key, and secret handling
- safe comparison for secret values where timing matters
- absence of secrets in responses, logs, URLs, redirects, analytics, or client-visible state

### Logging and Audit Review
Check:
- whether denials and sensitive operations are logged when appropriate
- whether logs reveal secrets, tokens, or sensitive payloads
- whether required audit trails exist
- whether environment-specific logging remains safe

### Config and Environment Review
Check for behavior differences caused by:
- `ADMIN_MODE`
- installation configuration
- app type
- feature flags
- environment settings
- debug mode, permissive CORS, disabled auth, development-only routes, or test-only bypasses
- deployed API versions, deprecated routes, alternate hosts, and undocumented aliases

### Inventory and Versioning Review
Check:
- whether the endpoint exists in documented inventory, route maps, or the FRS as expected
- whether older versions, aliases, hidden routes, or compatibility paths expose weaker controls
- whether debug, health, admin, or generated documentation endpoints disclose sensitive information
- whether stale tests or task documents describe behavior that no longer matches the registered route

### Third-Party API Consumption Review
When the endpoint calls external services or accepts webhook/callback data, check:
- validation and normalization of data returned by upstream services
- timeout, retry, circuit-breaker, and failure behavior
- trust boundaries for webhook signatures, callback URLs, redirects, and upstream identifiers
- whether upstream errors or payloads can become XSS, SSRF, injection, log injection, or data exposure
- whether sensitive data is sent to third parties unnecessarily

### Side Effects and Transaction Review
For endpoints that change state, inspect:
- background jobs
- signals or event hooks
- notifications, emails, and webhooks
- audit or event publishing
- retry behavior and duplicate-action controls
- transaction boundaries and race exposure

### FRS Drift Review
Compare implementation against the FRS for:
- route path and methods
- scopes and permissions
- equivalent FastAPI dependency and auth wiring
- response fields
- status codes

Report meaningful drift even when no exploit is confirmed.

## Report File Naming
Write the report to `.agents/security/` and create that folder if needed.

Use:
- `endpoint_security_report_<route_name>.md` when the target was supplied as a route name
- `endpoint_security_report_<sanitized_endpoint>.md` when the target was supplied only as a URL or path

If the normalized filename already exists, treat it as the current report for that endpoint and update it in place.

Filename normalization:
- use lowercase
- replace `/`, `.`, `:`, `?`, `&`, `=`, `<`, `>`, `{`, `}`, and whitespace with `_`
- collapse repeated `_`
- remove leading or trailing `_`

## Report Structure
Begin the report with `Check Summary`, before the findings.

The summary table must include at least:
- authentication
- authorization and scopes
- IDOR / BOLA
- object property-level authorization
- input validation
- output data exposure
- enumeration risk
- resource consumption
- rate limiting
- logging/audit behavior
- auth-path matrix
- framework permission persistence
- error handling
- session management when applicable
- FRS drift
- config/feature-flag paths
- inventory/versioning
- third-party API consumption when applicable

For state-changing or sensitive-action endpoints, also include:
- side effects reviewed
- transactionality / race conditions
- sensitive business-flow abuse

Add optional rows when relevant, including:
- CSRF
- CORS
- XSS
- SSRF
- SQL injection
- open redirect
- path traversal
- file upload safety
- business-logic abuse
- weak cryptography
- webhook/callback validation

Use this table format:

| Check | Result | Evidence | Notes |
| --- | --- | --- | --- |
| `authentication` | pass | `code or test reference` | `Token dependency rejects missing and invalid credentials.` |
| `CSRF` | skipped | `not applicable to token-only JSON route` | `Browser/session flow is not used by this endpoint.` |
| `SQL injection` | fail | `file or test reference` | `User-controlled sort value reaches raw SQL without an allowlist.` |

Legend:
- `pass` means the check was performed and no problem was found
- `pass (fixed)` means a prior failed check or finding was re-tested and is now resolved
- `pass (by design)` means the reviewed behavior is intentionally safe because of an explicit documented or code-enforced design choice
- `pass (not relevant)` means the risk class was considered and does not apply to this endpoint
- `fail` means the check was performed and a problem was confirmed
- `skipped` means the check did not apply or could not be performed

Do not remove the required summary rows. The `Notes` cell must always explain why the check passed, failed, or was skipped.

When using `pass (fixed)`, the `Notes` cell must identify the previous issue and the current evidence proving it is fixed.

## Findings Section
List findings before supporting sections, ordered from highest to lowest severity. Use one continuous numbering sequence.

Format:

1. `Severity` - short title
- Risk:
- Evidence:
- File:
- Why it matters:
- Recommended fix:

After the findings, include:
- `Assumptions`
- `Reviewed Auth Paths`
- `Previously Reported Issues`
- `Checks Performed`
- `FRS Drift`
- `Detailed Check Results`
- `Residual Risks`

In `Previously Reported Issues`, list each issue from the earlier report as:

1. `fixed|still failing|not reproducible|superseded|not rechecked` - previous title
- Previous result:
- Current result:
- Evidence:
- Notes:

Do not silently remove earlier failures. If they are fixed, mark them fixed and point to the code or test evidence. If they remain valid, keep them in the findings list with updated evidence and severity.

Under `Detailed Check Results`, use the same numbered format for every pass, fail, and skipped item. Use expanded pass labels such as `pass (fixed)`, `pass (by design)`, or `pass (not relevant)` when they describe the result more accurately than plain `pass`:

1. `pass|pass (fixed)|pass (by design)|pass (not relevant)|fail|skipped` - short check title
- Check:
- Result:
- Evidence:
- Why it matters:
- Recommended fix:

For passing checks, write `Recommended fix: None.`

## Severity Calibration
- `critical`
  - authentication bypass
  - authorization bypass
  - exploitable injection, SSRF, or code execution
  - major sensitive data exposure
  - unsafe cryptography or secret handling that enables account or system compromise
- `high`
  - reliable IDOR/BOLA
  - missing scope checks protecting sensitive actions or data
  - meaningful enumeration of protected data
  - unbounded resource or cost-amplifying operation that can materially disrupt service
- `medium`
  - limited data leakage
  - weak abuse controls on sensitive endpoints
  - validation gaps whose impact depends on context
  - unsafe third-party API consumption with plausible but bounded impact
- `low`
  - missing or weak audit/logging
  - hardening opportunities
  - inconsistent behavior that does not create a bypass

## Minimum Review Set
Every endpoint review must include:
- authentication
- authorization and scopes
- IDOR / BOLA
- object property-level authorization
- input validation
- output data exposure
- enumeration risk
- resource consumption
- rate limiting
- error handling
- logging/audit behavior
- inventory/versioning

Add these when they apply:
- CSRF
- CORS
- XSS
- SSRF
- SQL injection
- open redirect
- path traversal
- file upload safety
- business-logic abuse
- session management
- weak cryptography
- third-party API consumption
- webhook/callback validation

## Final Notes
- Passing tests alone do not prove an endpoint is secure.
- When behavior needs runtime confirmation and can be tested, do not rely only on static reading.
- Prefer extending nearby tests instead of creating isolated test modules.
- Use exact file references rather than broad claims.
- Keep confirmed issues separate from plausible but unproven risks.
- State whether each conclusion comes from static inspection, runtime verification, or both.
- If runtime verification is needed but not performed, provide a focused test plan instead of guessing.
