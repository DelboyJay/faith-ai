---
name: endpoint-security-check
description: Review one API or web endpoint for security issues, verify the real implementation and tests, and produce a severity-ordered findings report with exact evidence.
---

# Endpoint Security Check

## Purpose
Use this skill when the user wants a focused security review of a single endpoint, route, or action. The review should cover identity checks, permission boundaries, data handling, injection and abuse risk, browser/API protections, observability, and documented behavior drift.

## Required Input
Provide one of these:
- `ENDPOINT_URL`
- `ROUTE_NAME`

Optional inputs:
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

If neither `ENDPOINT_URL` nor `ROUTE_NAME` is supplied:
- ask the user for a single concrete endpoint target
- stop until they answer
- do not guess
- do not broaden the review unless the user explicitly asks for multiple endpoints

## Operating Rules
- Re-check the current workspace on every run. Do not rely on memory from earlier reviews.
- Keep the main path local whenever possible.
- Only delegate bounded side tasks that do not block the next local step.
- Use the smallest capable model tier for each delegated task.
- Keep a single evidence trail with exact file references.
- If sub-agents are used, report the capability tier selected and the concrete model if the runtime exposes it.

## Capability Tiers
### `lightweight`
Use for simple evidence extraction such as:
- route grep after the likely area is already known
- serializer or test-file discovery
- throttle or logging hook lookup
- other low-ambiguity read-only lookups

### `balanced`
Use for moderate synthesis such as:
- reconciling router registrations
- tracing serializers and response shapes
- reviewing test gaps
- assessing throttling, logging, or browser controls when interpretation is needed

### `high_reasoning`
Use for harder judgments such as:
- non-trivial auth/authz flows
- IDOR/BOLA and cross-tenant paths
- injection, SSRF, deserialization, or business-logic analysis across multiple layers
- final severity calls when evidence is ambiguous

## Runtime Mapping
Map the capability tier to the active provider at runtime instead of hardcoding vendor names.
Examples:
- OpenAI: `lightweight` -> mini/fast, `balanced` -> mid-tier reasoning, `high_reasoning` -> flagship reasoning
- Anthropic: `lightweight` -> Haiku-class, `balanced` -> Sonnet-class, `high_reasoning` -> Opus-class

If the runtime hides the exact model name, say so explicitly.

## Delegation Guidance
- Prefer a fully local review when the endpoint is small or concentrated.
- Use at most 2-3 sub-agents unless the user explicitly wants a broader investigation.
- A practical split is:
  - implementation mapping and test discovery
  - auth/authz and entity-boundary review
  - injection, browser controls, and abuse checks
- Do not delegate initial endpoint resolution or the final severity decision unless the user explicitly asks for broader parallel work.

## Review Process
1. Confirm the endpoint target is explicit.
2. Re-read the current codebase state for this run.
3. Resolve the endpoint locally.
4. Decide whether the review can stay local or benefit from bounded side tasks.
5. Identify the owning route, view/viewset/action, serializer, service layer, and existing tests.
6. Determine the authentication paths that can actually reach the endpoint.
7. Determine the authorization model actually enforced by code.
8. Build a concrete auth-path matrix.
9. Review request inputs and trusted identifier sources.
10. Review response outputs and exposure risk.
11. Check abuse and cross-entity access paths.
12. Check browser and API protections that are relevant to the endpoint.
13. Check config, feature-flag, and environment-specific branches.
14. Check side effects, transaction handling, retries, and race risk for write paths.
15. Check framework permission persistence when relevant.
16. Compare intended behavior in the FRS with the actual implementation.
17. Search for existing tests before writing any new ones.
18. Add narrow tests only when runtime proof is needed and suitable coverage is missing.
19. Run the relevant tests whenever they exist or are added.
20. Review logging and audit behavior for sensitive actions and failures.
21. Integrate the evidence locally, resolve conflicts, and assign severity.
22. Save the report in the repository root and present findings ordered by severity.

## Repository-Specific Rules
- Always assume the code may have changed since the last run.
- Re-run route discovery and re-open the current source files every time.
- Revalidate old findings before reusing them.
- For route discovery in this repository, use the host in this order:
  - `ADMIN_MODE=0 poetry run yoyo django show_urls | grep <route_name_or_path_fragment>`
  - if no match, `ADMIN_MODE=1 poetry run yoyo django show_urls | grep <route_name_or_path_fragment>`
  - if still no match, fall back to `rg` across URL registrations, routers, and candidate views
- Do not skip the `ADMIN_MODE=0` check.
- For DRF router endpoints, confirm the generated route names precisely.
- For OAuth2 paths in this repo, trace both token issuance scope validation and endpoint enforcement.
- For admin endpoints, inspect the concrete view/action permission requirements rather than inferring them from documentation alone.
- Treat the FRS as the intended contract, but verify the real registered route and permission stack from code.

## Detailed Check Areas
### Endpoint Resolution
- Resolve route name to path and owning view, or URL to owning route.
- Confirm methods, route names, and actual action handlers.

### Implementation Mapping
Trace:
- route registration
- view or viewset action
- serializer(s)
- permission class(es)
- throttle class(es)
- service, repository, task, or background work
- existing tests

### Test Discipline
- Always look for nearby tests first.
- Extend existing endpoint, viewset, permission, or serializer tests when appropriate.
- Add the smallest possible targeted regression tests when runtime behavior matters.
- Run the exact relevant subset and report the command and result.

### Authentication Review
Check whether the endpoint correctly handles:
- required authentication
- anonymous rejection
- malformed or expired credentials
- unintended auth paths
- session vs token mismatches
- any project-specific auth backend that could still reach the endpoint

### Authorization Review
Check:
- required scopes and scope combinations
- method-specific differences
- ownership and tenant boundaries
- IDOR/BOLA and horizontal escalation
- vertical escalation
- field-level gating
- permission declarations that must exist outside tests
- per-action authorization for viewsets

### Input Review
Check:
- required fields
- unknown fields
- type and boundary validation
- enum and state validation
- query, filter, sort, and pagination constraints
- mass-assignment style exposure
- trust source of identifiers: URL, body, query, token, session, installation, or server-derived

### Output Review
Check:
- returned fields
- leakage of tokens, identifiers, notes, internals, or metadata
- correct omission of gated fields
- safe error payloads
- enumeration through status code or response differences
- behavior across `400`, `403`, `404`, `409`, and `500` where relevant

### Injection and Execution Review
Check whether endpoint-controlled data can reach:
- SQL or ORM filters/orderings
- shell commands
- file paths
- templates
- headers
- redirects
- external URLs
- parsers or deserializers

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
When relevant, assess:
- CSRF
- CORS
- XSS
- clickjacking protection
- sensitive-response caching
- cookie flags for session-authenticated flows

### Abuse and Resilience Review
Check:
- rate limiting and throttling
- enumeration resistance
- replay and duplicate-action risk
- idempotency for state-changing operations
- race conditions
- bulk abuse
- scraping via filters or pagination
- expensive query shapes and fanout behavior

### Logging and Audit Review
Check:
- whether denials and sensitive actions are logged where appropriate
- whether logs leak secrets or tokens
- whether audit trails exist where expected
- whether environment-specific logging behavior is safe

### Config and Environment Review
Check whether behavior changes under:
- `ADMIN_MODE`
- installation config
- app type
- feature flags
- environment settings

### Side Effects and Transaction Review
For write paths, inspect:
- background jobs
- signals
- notifications, emails, webhooks
- audit or event publishing
- retry and duplicate-action controls
- transaction boundaries and race exposure

### FRS Drift Review
Compare code to the FRS for:
- route and methods
- scopes and permissions
- equivalent framework permissions
- response fields
- status codes

Report meaningful drift even when it is not yet a confirmed exploit.

## Report File Naming
Write the report to the repository root using:
- `endpoint_security_report_<route_name>.md` when the input was a route name
- `endpoint_security_report_<sanitized_endpoint>.md` when the input was only a URL/path

Filename normalization:
- lowercase only
- replace `/`, `.`, `:`, `?`, `&`, `=`, `<`, `>`, `{`, `}`, and whitespace with `_`
- collapse repeated `_`
- trim leading and trailing `_`

## Report Structure
Start with `Check Summary` before the findings list.

The summary table must include at least:
- authentication
- authorization and scopes
- IDOR / BOLA
- input validation
- output data exposure
- enumeration risk
- rate limiting
- logging/audit behavior
- auth-path matrix
- framework permission persistence
- FRS drift
- config/feature-flag paths

Add these when the endpoint changes state or performs sensitive actions:
- side effects reviewed
- transactionality / race conditions

Add any relevant optional rows such as:
- CSRF
- CORS
- XSS
- SSRF
- SQL injection
- open redirect
- path traversal
- file upload safety
- business-logic abuse

Use this table format:

| Check | Result | Evidence | Why Skipped |
| --- | --- | --- | --- |
| `authentication` | ✅ | `code or test reference` | `` |
| `CSRF` | ⏭️ | `not applicable to token-only JSON route` | `Browser/session flow not used` |
| `SQL injection` | ❌ | `file or test reference` | `` |

Legend:
- `✅` checked, no issue found
- `❌` checked, problem confirmed
- `⏭️` skipped or not applicable

Do not omit the minimum rows. If a check is skipped, explain why.

## Findings Section
List findings first, ordered by severity. Use one numbering sequence across all severities.

Format:

1. `Severity` - short title
- Risk:
- Evidence:
- File:
- Why it matters:
- Recommended fix:

After findings, include these sections:
- `Assumptions`
- `Reviewed Auth Paths`
- `Checks Performed`
- `FRS Drift`
- `Detailed Check Results`
- `Residual Risks`

Under `Detailed Check Results`, use the same numbered style for each pass, fail, or skipped check:

1. `pass|fail|skipped` - short check title
- Check:
- Result:
- Evidence:
- Why it matters:
- Recommended fix:

Use `Recommended fix: None.` for passing checks.

## Severity Calibration
- `critical`
  - auth bypass
  - authz bypass
  - exploitable injection, SSRF, or code execution
  - severe sensitive data exposure
- `high`
  - reliable IDOR/BOLA
  - missing scope checks on sensitive actions or data
  - meaningful enumeration of protected data
- `medium`
  - partial leakage
  - weak abuse controls on sensitive routes
  - risky validation gaps that depend on context
- `low`
  - audit or logging gaps
  - hardening gaps
  - inconsistent but non-bypass behavior

## Minimum Review Set
For every endpoint, review at least:
- authentication
- authorization and scopes
- IDOR / BOLA
- input validation
- output data exposure
- enumeration risk
- rate limiting
- logging/audit behavior

Review these when relevant:
- CSRF
- CORS
- XSS
- SSRF
- SQL injection
- open redirect
- path traversal
- file upload safety
- business-logic abuse

## Final Notes
- Passing tests are not enough to declare an endpoint safe.
- When runtime behavior matters and can be tested, do not stop at static reading alone.
- Prefer extending nearby tests over creating disconnected test modules.
- Use exact file references instead of vague statements.
- Separate confirmed issues from plausible risks.
- State clearly whether a conclusion comes from static inspection or runtime verification.
- If runtime verification is needed but not performed, provide a concrete targeted test plan instead of guessing.
