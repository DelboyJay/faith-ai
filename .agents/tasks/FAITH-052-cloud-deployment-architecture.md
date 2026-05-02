# FAITH-052 — Cloud Deployment Architecture

**Phase:** 12 — Cloud Deployment
**Complexity:** XL
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** All previous phases
**FRS Reference:** Section 12

---

## Objective

Adapt FAITH for cloud deployment without forking the product into a separate runtime. The PA, agent, and MCP tool code must remain shared with local deployment; only the orchestration, infrastructure, tenancy, authentication, and persistence layers differ. This task defines and implements the cloud control plane, Kubernetes-based orchestration path, multi-tenant project isolation, server-side user state, and commercial deployment concerns such as billing and licence enforcement.

The result is a deployable cloud architecture suitable for multiple users and multiple projects, while preserving the local-first development model and ensuring that features implemented for local FAITH remain automatically available in cloud FAITH.

---

## Context

1. FRS Section 12 is explicitly lowest priority and should only be implemented once local deployment is complete and stable.
2. Local FAITH uses the PA as Docker orchestrator via Docker socket access. Cloud FAITH must remove Docker socket access and replace it with Kubernetes API calls scoped by RBAC.
3. Cloud introduces a new layer, the FAITH Management Service, responsible for auth, project lifecycle, provisioning, billing, and licence enforcement.
4. Cloud deployments are multi-tenant. Project isolation is a first-class requirement and must be enforced at the namespace, storage, Redis, and secret boundaries.
5. The existing Web UI stores layout in browser `localStorage`. Cloud requires server-side layout persistence per user profile.
6. Local code paths should remain the default for development and personal use. Cloud support must be an additive architecture, not a rewrite.
7. Authentication is absent in local mode; cloud mode requires OAuth2 / SSO support and user-to-project access control.
8. Storage moves from local filesystem mounts to object storage and persistent volumes as appropriate. The user-facing project abstraction must remain stable.
9. Existing session/task logs, audit logs, FRS docs, context summaries, and tool outputs must remain available in cloud mode with project isolation preserved.
10. The cloud design should support both per-project Redis instances and a shared Redis deployment, selected by configuration.

---

## Architecture

### Target Topology

```text
User Browser
    |
    v
FAITH Management Service
    |
    +--> Auth / SSO
    +--> Project API
    +--> Billing / Licence Enforcement
    +--> User Layout Persistence
    +--> Provisioning Service
             |
             v
       Kubernetes API
             |
             +--> Namespace: project-a
             |      +--> pa deployment
             |      +--> agent deployments
             |      +--> tool deployments
             |      +--> redis (per-project or shared ref)
             |      +--> secrets/configmaps/pvc bindings
             |
             +--> Namespace: project-b
                    +--> ...
```

### Core Design Rules

1. The PA runtime must depend on an orchestration interface, not directly on Docker SDK calls.
2. Local mode provides a Docker-backed implementation of that interface.
3. Cloud mode provides a Kubernetes-backed implementation of that interface.
4. Authentication and project access control live above the PA in the Management Service.
5. Every cloud project receives isolated runtime resources, either entirely dedicated or logically partitioned with equivalent guarantees.
6. Secrets are never exposed to agent containers except through existing `secret_ref` injection patterns adapted for Kubernetes secrets.

---

## Files to Create / Modify

### 1. `docs/cloud-architecture.md`

Create the authoritative architecture document for cloud mode. It should describe:

- control plane responsibilities
- runtime plane responsibilities
- namespace isolation model
- per-project resource model
- Redis deployment options
- storage mapping from local filesystem concepts to cloud primitives
- auth and identity flow
- billing and licence enforcement flow
- failure domains and recovery expectations
- deployment modes: single-cluster MVP and future multi-cluster extension

Include at least:

- a component diagram
- a request flow for user login -> project open -> PA attach
- a provisioning flow for project creation
- a teardown flow for project deletion

### 2. `faith/orchestration/base.py`

Create a shared orchestration abstraction used by the PA:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RuntimeContainerSpec:
    name: str
    image: str
    env: dict[str, str]
    command: list[str]
    mounts: list[Any]
    network_identity: str | None = None
    labels: dict[str, str] | None = None


class RuntimeOrchestrator(ABC):
    @abstractmethod
    async def start_runtime(self, spec: RuntimeContainerSpec) -> str: ...

    @abstractmethod
    async def stop_runtime(self, runtime_id: str) -> None: ...

    @abstractmethod
    async def restart_runtime(self, runtime_id: str) -> None: ...

    @abstractmethod
    async def list_runtimes(self, project_id: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_logs(self, runtime_id: str, tail: int = 200) -> str: ...

    @abstractmethod
    async def ensure_project_scope(self, project_id: str) -> None: ...
```

Refactor the PA to depend on this interface rather than directly on Docker SDK types.

### 3. `faith/orchestration/docker_runtime.py`

Move or adapt the existing local Docker orchestration logic from FAITH-014 into a concrete `DockerRuntimeOrchestrator` implementing the shared interface. This preserves backward compatibility for local deployments and serves as the reference implementation.

### 4. `faith/orchestration/kubernetes_runtime.py`

Create `KubernetesRuntimeOrchestrator` for cloud mode. Responsibilities:

- connect to Kubernetes using in-cluster config or kubeconfig
- create or reconcile namespaces per project
- create deployments/jobs/pods for PA, agents, and tools
- create services where required
- apply labels and annotations for project, agent, component type, and session tracking
- attach configmaps and secrets
- support log retrieval
- stop and restart runtimes by Kubernetes resource operations
- avoid cluster-admin assumptions; all operations must fit scoped RBAC

Use the official Kubernetes Python client.

### 5. `faith/cloud/management_service/`

Create the Management Service package. Minimum modules:

- `app.py` — FastAPI app entrypoint
- `auth.py` — OAuth2 / OIDC integration and session validation
- `projects.py` — project CRUD and access checks
- `provisioning.py` — namespace and runtime provisioning
- `billing.py` — licence and billing enforcement stubs and interfaces
- `layouts.py` — server-side layout persistence
- `schemas.py` — request/response models

### 6. `faith/cloud/management_service/app.py`

Implement a FastAPI service with endpoints such as:

- `POST /auth/login`
- `POST /auth/callback`
- `POST /auth/logout`
- `GET /me`
- `GET /projects`
- `POST /projects`
- `GET /projects/{project_id}`
- `DELETE /projects/{project_id}`
- `POST /projects/{project_id}/provision`
- `POST /projects/{project_id}/suspend`
- `POST /projects/{project_id}/resume`
- `GET /projects/{project_id}/layout`
- `PUT /projects/{project_id}/layout`

The exact auth callback path may vary by provider, but the module should be structured so adding Azure AD, Google Workspace, and Okta is straightforward.

### 7. `faith/cloud/schemas/`

Add cloud-specific config and API schemas, for example:

- `cloud_system.schema.json`
- `project_tenant.schema.json`
- `management_service.schema.json`

These should cover:

- cluster connection settings
- Redis strategy (`per_project` or `shared`)
- object storage settings
- auth provider settings
- licence tier settings
- project quota settings

### 8. `faith/config/models/cloud.py`

Add Pydantic models for cloud configuration. Suggested models:

- `CloudSystemConfig`
- `AuthProviderConfig`
- `ObjectStorageConfig`
- `RedisCloudConfig`
- `ProjectQuotaConfig`
- `LicenceConfig`

These models should integrate with the existing config loading system from FAITH-003 without breaking local-only installs.

### 9. `faith/storage/object_store.py`

Create an object-store abstraction for cloud persistence of:

- session logs
- task logs
- audit logs archives
- `context.md`
- `state.md`
- project FRS
- uploaded documents
- exported reports

Support an S3-compatible backend first. Keep the interface narrow and testable.

### 10. `faith/web/`

Modify the Web UI and backend integration to support server-side persisted layout and authenticated cloud sessions:

- replace cloud layout reads/writes to `localStorage` with backend API calls when cloud mode is enabled
- keep `localStorage` for local mode
- include project switch and authenticated user context in the shell
- preserve existing panel structure and message flows

### 11. `containers/management-service/Dockerfile`

Create a container image definition for the Management Service.

### 12. `deploy/k8s/`

Create Kubernetes manifests or Helm templates for:

- management service deployment
- ingress/service
- RBAC roles and role bindings
- namespace template or provisioning examples
- secret/config examples
- optional per-project Redis deployment

Do not hardcode production secrets or vendor-specific ingress annotations.

### 13. `tests/cloud/`

Create test coverage for:

- project provisioning
- namespace isolation rules
- runtime abstraction parity between Docker and Kubernetes backends
- auth gate enforcement
- server-side layout persistence
- object storage path isolation
- Redis strategy selection

---

## Implementation Requirements

### A. Orchestration Abstraction

- Refactor the PA so container lifecycle operations go through `RuntimeOrchestrator`.
- Existing local behaviour must remain unchanged after the refactor.
- Cloud mode must not require Docker socket access.
- Runtime metadata should expose enough information for status views, audit logging, and troubleshooting.

### B. Kubernetes Provisioning

- Each project must have a deterministic Kubernetes namespace name derived from project ID.
- Namespace labels should include at least `faith/project-id`, `faith/environment`, and `faith/owner`.
- PA, agents, and tools must be deployed in a way that supports restart and log collection.
- Tool and agent identity should still map to existing FAITH IDs used in logs, events, and UI.
- Kubernetes secrets must replace direct host-mounted secrets in cloud mode.

### C. Multi-Tenancy and Isolation

- Project data must be isolated by namespace, storage path/prefix, and credentials.
- Shared Redis mode must namespace keys/channels to prevent cross-project bleed.
- Per-project Redis mode must provision or attach a dedicated Redis instance per project.
- Access control must ensure users cannot open or manage projects they are not assigned to.

### D. Authentication and Authorisation

- Support OAuth2 / OIDC login.
- User identity must be available to the Management Service and propagated to audit events where appropriate.
- Management Service endpoints must reject unauthenticated requests by default.
- Authorisation must be project-scoped rather than global-admin-only.

### E. Storage

- Object storage keys must be prefixed by tenant/project identifiers.
- Existing FAITH document conventions should remain intact inside those prefixes so tooling can stay as unchanged as possible.
- Temporary workspace requirements for tools should be satisfied through persistent volumes or ephemeral working directories with explicit sync rules.

### F. Server-Side Layout Persistence

- Cloud mode should persist UI layouts per user and per project.
- Local mode must continue using browser `localStorage`.
- The frontend should switch behaviour based on a backend capability flag, not hardcoded environment assumptions.

### G. Billing and Licence Enforcement

- Introduce interface boundaries for billing and licence enforcement even if the first implementation uses stubs or a simple fixed-tier model.
- Project creation/provisioning must check licence entitlement before allocating resources.
- The design should support future seat counts, usage metering, and trial expiration.

### H. Shared Codebase Guarantee

- Avoid copying PA, agent, or tool runtime logic into cloud-specific packages.
- Differences between local and cloud must be represented through injected infrastructure adapters and configuration.
- Any new cloud-only branch inside shared runtime code must be justified and documented.

---

## Suggested Execution Order

1. Introduce the orchestration abstraction and migrate local Docker code behind it.
2. Implement cloud configuration models and feature flags.
3. Build the Kubernetes orchestrator MVP with namespace provisioning and PA runtime launch.
4. Build the Management Service auth and project APIs.
5. Add project provisioning, suspend/resume, and teardown flows.
6. Add object-store support and server-side layout persistence.
7. Add billing/licence enforcement interfaces.
8. Add deployment manifests and cloud integration tests.
9. Validate local/cloud parity for core user journeys.

---

## Acceptance Criteria

- A shared orchestration interface exists and local FAITH still works through the Docker-backed implementation.
- A Kubernetes-backed orchestrator can provision and manage a project-scoped PA runtime without Docker socket access.
- A Management Service exists with authenticated project CRUD and provisioning endpoints.
- Cloud project resources are isolated by namespace and storage boundaries.
- Server-side layout persistence works per user/project in cloud mode.
- Object storage is used for persistent cloud artifacts with project-safe key prefixes.
- Shared Redis and per-project Redis strategies are both supported by configuration.
- Billing/licence enforcement hooks are present in the provisioning flow.
- Documentation explains the control plane, runtime plane, and isolation model clearly enough for deployment work to proceed.
- Tests cover the critical cloud-specific safety boundaries.

---

## Notes / Constraints

- This task is architecture-heavy and should be treated as a dedicated cloud adaptation stream, not mixed casually into local deployment tasks.
- Prefer interfaces and capability flags over sprawling `if cloud_mode:` branches.
- Keep vendor lock-in low where possible. S3-compatible object storage and standard OIDC providers are preferred abstractions.
- Do not weaken any local security guarantees when adding cloud mode. Cloud must improve isolation, not trade it away.
