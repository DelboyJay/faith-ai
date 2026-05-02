# FAITH-057 — Disposable Sandbox Lifecycle & Scheduling

**Phase:** 4 — PA Core
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-014
**FRS Reference:** Section 2.3, 4.2

---

## Objective

Implement the PA-managed disposable sandbox runtime used for agent execution. A sandbox is a Linux Docker container fully controlled by the PA, not a restricted Unix user inside a shared runtime. Agents executing inside a sandbox have root access inside that container and may install Python packages, install or upgrade OS packages, and freely modify the container filesystem. The safety boundary is the container boundary, approved mounts, network policy, and resource quotas. Sandboxes must never receive the Docker socket, must not run in privileged mode, must not use host networking, and must receive only the minimum required Linux capabilities plus explicitly approved mounts.

The PA must be able to:
- create a fresh sandbox from a known base image
- assign approved project mounts and runtime policy
- reuse a shared sandbox when isolation is unnecessary
- allocate an isolated sandbox for a sub-agent when package/runtime conflicts, destructive experimentation, or risk justify separation
- destroy and recreate polluted or broken sandboxes by default rather than repairing them in place
- enforce CPU, memory, disk, and concurrent sandbox limits
- emit audit/status events for sandbox create, reuse, reset, destroy, and scheduling decisions

---

## Architecture

```
faith/pa/
├── sandbox_manager.py      ← Sandbox lifecycle and scheduling (this task)
├── sandbox_models.py       ← Sandbox state, allocation mode, policy models (this task)
└── container_manager.py    ← Integrates sandbox creation with Docker SDK (FAITH-014)

tests/
├── test_sandbox_manager.py
└── test_sandbox_scheduler.py
```

---

## Requirements

1. Support two allocation modes:
- `shared`: multiple collaborating agents/sub-agents may reuse the same sandbox
- `isolated`: a dedicated sandbox is created for one sub-agent or risky task

2. The PA decides allocation mode based on:
- destructive or risky operations
- conflicting runtime or package requirements
- explicit isolation requirement from the orchestration plan
- current CPU, memory, disk, and concurrent sandbox quotas

3. Each sandbox must have:
- a deterministic identifier
- owning session/task metadata
- approved mounts only, with no framework secrets mounted by default
- explicit network mode/policy (never host networking for the disposable sandbox model)
- resource limits
- lifecycle state (`creating`, `ready`, `busy`, `resetting`, `destroyed`, `failed`)

4. Recovery policy:
- if the PA judges a sandbox broken, polluted, or untrustworthy, the default action is destroy and recreate
- in-place repair is exceptional and must be explicit in code/logging

5. Audit and event integration:
- publish sandbox lifecycle events to `system-events`, including isolation-policy violations or refusal reasons
- write audit log entries for create, reuse, reset, destroy, and scheduling decisions

6. Scheduling constraints:
- prefer sandbox reuse before creating more containers
- enforce a configurable concurrent sandbox limit
- refuse or queue isolated sandbox allocation when quotas are exhausted

---

## Acceptance Criteria

- The PA can create and destroy disposable sandboxes through one manager interface.
- Shared versus isolated sandbox assignment is explicit in runtime state.
- Sandbox reuse and isolated allocation both have test coverage.
- Resource limits and concurrent sandbox caps are enforced.
- Reset logic destroys and recreates containers rather than mutating a dirty sandbox in place.
- Sandbox lifecycle events and audit entries are emitted consistently.


