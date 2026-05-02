# FAITH-054 — `faith run` Command & Task API

**Phase:** 11 — CLI & Skill Execution
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-005, FAITH-036, FAITH-015
**FRS Reference:** Section 9.6.1, 9.6.4

---

## Objective

Implement FAITH's non-interactive execution path so users can submit tasks from the command line without using the Web UI. This task adds the `faith run` CLI command, the backend task-submission API, the task result WebSocket stream, and the supporting PA/session plumbing needed for CLI-triggered work to behave like a standard FAITH session.

The CLI must support ad-hoc prompts, timeout overrides, dry-run mode, and robust return codes. Backend behaviour must remain consistent with interactive execution: the PA still creates sessions, tasks, agents, channels, logs, and events in the normal way. The only difference is that the result is streamed back to the CLI process rather than being primarily driven by the browser UI.

---

## Context

1. FRS Section 9.6.1 defines the `faith run` execution flow: CLI POSTs to `POST /api/task`, receives a `task_id`, then connects to `/ws/task/{task_id}` and blocks until completion.
2. FRS Section 9.6.4 requires CLI-triggered work to create standard sessions and tasks, with `trigger: "cli"` and optional `skill: "<name>"` metadata in `session.meta.json`.
3. FAITH-005 provides the CLI package and existing command structure. `faith run` should be added there rather than as a standalone script.
4. FAITH-036 provides the FastAPI server and WebSocket foundation. `POST /api/task`, `POST /api/shutdown`, and `/ws/task/{task_id}` extend that service.
5. FAITH-015 provides session and task management. CLI-triggered tasks should reuse that system rather than inventing a parallel execution path.
6. Return codes are user-facing contract and must match the FRS exactly:
   - `0` success
   - `1` task failure
   - `2` timeout
   - `3` PA not running / connection refused
   - `4` approval blocked or unattended disallowed
7. `--dry-run` should validate and describe what would happen without actually dispatching the task for execution.
8. `notify_on_complete` must allow CLI-triggered results to surface in the Web UI for any connected user.
9. This task intentionally covers raw prompt execution; skill loading and unattended policy logic belong primarily to FAITH-055.

---

## Architecture

```text
faith run "<prompt>"
    |
    v
faith-cli command handler
    |
    +--> POST /api/task
    |       |
    |       v
    |    FastAPI task router
    |       |
    |       v
    |    PA task submission service
    |       |
    |       v
    |   Session/task creation (trigger="cli")
    |       |
    |       v
    |    Normal PA execution flow
    |
    +--> WS /ws/task/{task_id}
            |
            v
       streamed task status/result
            |
            v
       CLI exit code + output
```

### High-Level Flow

1. User runs `faith run "<prompt>"`.
2. CLI validates arguments and submits a request to `POST /api/task`.
3. API creates a new non-interactive task record through the PA.
4. API returns a `task_id`.
5. CLI opens `/ws/task/{task_id}`.
6. PA streams status changes and final completion payload.
7. CLI prints result summary and exits with the correct return code.

---

## Files to Create / Modify

### 1. `faith/cli/commands/run.py`

Create a dedicated CLI module for `faith run`.

Responsibilities:

- parse positional prompt input
- support `--skill <name>` placeholder wiring if provided by future FAITH-055 integration
- support `--timeout`
- support `--dry-run`
- call the FastAPI backend
- connect to task WebSocket
- render progress/result for terminal use
- map backend outcomes to shell exit codes

Suggested interface:

```python
def run_command(
    prompt: str | None,
    skill: str | None = None,
    timeout: str | None = None,
    dry_run: bool = False,
) -> int:
    ...
```

### 2. `faith/cli/client.py`

Add or extend a lightweight HTTP/WebSocket client abstraction used by CLI commands.

Suggested responsibilities:

- resolve base URL for the local FAITH API
- POST JSON to task endpoints
- connect to WebSocket endpoints
- apply connection timeout handling
- convert connection failures into a consistent `ConnectionRefusedError`-style domain exception

### 3. `faith/cli/__main__.py` or existing CLI registration module

Register the new `faith run` command in the CLI entrypoint created by FAITH-005.

Example usage expected to work:

```bash
faith run "Run all tests and generate a QA report"
faith run --timeout 30m "Summarise open TODOs in the repo"
faith run --dry-run "Review the auth architecture"
```

### 4. `faith/web/routes/task_api.py`

Create a FastAPI router dedicated to non-interactive task execution.

Endpoints:

- `POST /api/task`
- `POST /api/shutdown`
- `GET /api/task/{task_id}` optional status endpoint if useful for debugging

`POST /api/task` request payload should include:

- `prompt`
- `trigger` defaulting to `"cli"`
- `skill` optional
- `timeout_seconds` optional
- `dry_run` optional
- `notify_on_complete` optional

Response payload should include at minimum:

- `task_id`
- `status`
- `dry_run_plan` optional

### 5. `faith/web/routes/ws_task.py`

Create or extend a WebSocket route for `/ws/task/{task_id}`.

Responsibilities:

- authenticate/validate task visibility if needed
- subscribe to task lifecycle events
- stream status updates to the CLI client
- emit a final message with result, summary, and exit classification
- close cleanly after terminal state

### 6. `faith/pa/task_submission.py`

Create a PA-internal service for non-interactive task intake.

Responsibilities:

- validate task submission payload
- create task/session metadata via FAITH-015 APIs
- flag session as `trigger: "cli"`
- attach optional `skill` metadata
- record dry-run requests without launching execution
- enqueue work into the standard PA orchestration flow

### 7. `faith/pa/task_result_stream.py`

Create a small service or helper for bridging PA task lifecycle changes to WebSocket consumers.

Possible responsibilities:

- maintain in-memory subscribers by `task_id`
- publish status updates such as `queued`, `running`, `waiting_for_agents`, `completed`, `failed`, `timed_out`, `approval_blocked`
- provide final result payloads

### 8. `faith/models/task_api.py`

Add request/response models for the task API.

Suggested models:

- `TaskSubmitRequest`
- `TaskSubmitResponse`
- `TaskStatusUpdate`
- `TaskCompletionPayload`
- `ShutdownResponse`

### 9. `tests/test_cli_run_command.py`

Add tests for:

- successful prompt submission
- `--dry-run`
- timeout parsing and handling
- API unavailable -> exit code `3`
- approval-blocked result -> exit code `4`
- failed task -> exit code `1`

### 10. `tests/test_task_api.py`

Add backend tests for:

- `POST /api/task` success
- malformed request validation
- dry-run response behaviour
- task result streaming contract
- shutdown endpoint behaviour

### 11. `tests/test_task_result_stream.py`

Add focused tests for task status publication and terminal-state delivery to subscribers.

---

## API Contract

### `POST /api/task`

Example request:

```json
{
  "prompt": "Run all tests and summarise failures",
  "trigger": "cli",
  "skill": null,
  "timeout_seconds": 1800,
  "dry_run": false,
  "notify_on_complete": false
}
```

Example success response:

```json
{
  "task_id": "task-20260327-183015-482",
  "status": "accepted"
}
```

Example dry-run response:

```json
{
  "task_id": "dry-run-task-20260327-183015-482",
  "status": "dry_run",
  "dry_run_plan": [
    "Create a standard CLI-triggered session",
    "Ask PA to decompose the request",
    "Dispatch relevant agents and tools"
  ]
}
```

### `/ws/task/{task_id}`

Suggested event envelope:

```json
{
  "task_id": "task-20260327-183015-482",
  "status": "running",
  "message": "PA has delegated work to 2 agents",
  "final": false
}
```

Final payload example:

```json
{
  "task_id": "task-20260327-183015-482",
  "status": "completed",
  "message": "Task completed successfully",
  "final": true,
  "result": {
    "summary": "All tests passed. QA report generated.",
    "artifacts": ["reports/qa-report.md"]
  }
}
```

---

## Implementation Requirements

### A. CLI Behaviour

- `faith run "<prompt>"` must work when the PA is already running.
- If the API is unreachable, exit with code `3`.
- CLI output should be concise and terminal-friendly, not browser-oriented.
- Timeout values should accept a user-friendly duration format if FAITH-005 already establishes one; otherwise support seconds plus a simple parser for `30m`, `1h`, etc.
- `--dry-run` must not create live agent work.

### B. Session and Task Integration

- CLI-triggered tasks must go through standard FAITH session/task creation.
- `session.meta.json` must include `trigger: "cli"` and `skill` when relevant.
- Event logging, task logging, and audit behaviour must remain consistent with interactive execution.

### C. Result Streaming

- The CLI should receive progress updates until a terminal state is reached.
- Terminal states must be unambiguous: `completed`, `failed`, `timed_out`, `approval_blocked`.
- WebSocket closure should not happen before a final message is sent.

### D. Return Code Mapping

- `completed` -> `0`
- `failed` -> `1`
- `timed_out` -> `2`
- `connection_error` -> `3`
- `approval_blocked` or unattended disallowed -> `4`

The mapping must be enforced centrally so CLI behaviour stays consistent as features expand.

### E. `POST /api/shutdown`

- Add a coordinated shutdown endpoint used by `faith stop`.
- Shutdown should ask the PA to stop active work cleanly before containers are torn down by CLI lifecycle commands.
- The endpoint should return quickly with an acknowledgement and perform cleanup asynchronously if needed.

### F. Notify-on-Complete

- If `notify_on_complete` is true, final task results should also be published into the existing Web UI notification/status flow.
- CLI execution should not depend on a browser being connected.

### G. Dry Run

- Dry-run mode should validate request structure and produce a human-readable execution plan.
- It must not create agent containers, execute tools, or mutate project files.
- The dry-run response may use a lightweight PA planning pass, but no real task execution is allowed.

---

## Suggested Execution Order

1. Add task API request/response models.
2. Implement `POST /api/task` and a stub PA submission path.
3. Add `/ws/task/{task_id}` result streaming.
4. Implement the CLI `faith run` command using the HTTP/WebSocket client.
5. Wire CLI submissions into standard session/task management.
6. Add dry-run support.
7. Add `POST /api/shutdown`.
8. Add notify-on-complete integration.
9. Add test coverage for both CLI and backend flows.

---

## Acceptance Criteria

- `faith run "<prompt>"` submits a non-interactive task and waits for completion.
- Backend returns a `task_id` from `POST /api/task`.
- CLI receives task progress and final state over `/ws/task/{task_id}`.
- Return codes match the FRS contract exactly.
- CLI-triggered tasks create normal FAITH sessions/tasks with `trigger: "cli"` metadata.
- `--timeout` and `--dry-run` both work as documented.
- `POST /api/shutdown` exists and supports coordinated shutdown for `faith stop`.
- `notify_on_complete` can surface CLI results in the Web UI without affecting CLI correctness.
- Tests cover success, failure, timeout, approval-blocked, and connection-refused cases.

---

## Notes / Constraints

- Keep this task focused on raw non-interactive task execution and plumbing.
- Skill parsing, unattended policy semantics, and scheduler behaviour are expanded in FAITH-055 and FAITH-056.
- Avoid introducing a second execution pipeline for CLI tasks; reuse the PA's existing orchestration path wherever possible.
