# Functional Requirements Specification
# FAITH — Framework AI Team Hive

**Version:** 0.1 (Draft)
**Date:** 2026-03-23
**Status:** Sections 1–11 substantially defined. Section 12 (Cloud Deployment) sketched — lowest implementation priority.

---

## Table of Contents

1. [Introduction & Overview](#1-introduction--overview)
2. [System Architecture](#2-system-architecture)
3. [Agent Communication & Orchestration](#3-agent-communication--orchestration)
4. [Tools & MCP Integration](#4-tools--mcp-integration) *(4.1–4.15 defined)*
5. [Security & Approval System](#5-security--approval-system)
6. [Web UI](#6-web-ui)
7. [Configuration Management](#7-configuration-management)
8. [Logging & Observability](#8-logging--observability)
9. [Setup & Deployment](#9-setup--deployment) *(9.1–9.6: prerequisites, launch, wizard, CLI & workflows)*
10. [Licensing](#10-licensing)
11. [Example Use Case: Software Team](#11-example-use-case-software-team)
12. [Cloud Deployment](#12-cloud-deployment) *(lowest priority — architecture sketch only)*

---

## 1. Introduction & Overview

### 1.1 Purpose

This document defines the functional requirements for **FAITH** (Framework AI Team Hive), a Python-based multi-agent AI framework that enables multiple AI agents to collaborate on tasks, communicate with each other, and utilise local tools via MCP (Model Context Protocol). FAITH is designed for personal use, running locally via Docker, with a web-based UI.

### 1.2 Problem Statement

Current AI coding assistants (Codex, Claude Code) operate as single-agent systems with limited tool access and rigid configuration that requires restarts for changes. There is no lightweight, self-hosted framework that allows multiple specialised AI agents to collaborate as a team, each using cost-appropriate LLM models, with granular security controls and seamless tool integration.

### 1.3 Goals

- Enable multi-agent collaboration with a Project Agent (PA) acting as coordinator, moderator, and session manager.
- Support multiple LLM providers (Ollama for free local models, OpenRouter for paid models) with dynamic model selection based on task complexity.
- Provide tool access via MCP with an adapter layer for models that don't natively support MCP.
- Run on Windows 11, Linux, and Mac via Docker with minimal setup effort. Installation is `pip install faith-cli` followed by `faith init`.
- Prioritise user friendliness at every touchpoint — adding a model, a tool, or an agent should feel like a one-line config change or a single conversation with the PA, never an installation task.
- Support both interactive (Web UI) and non-interactive (CLI) operation. Enable headless task execution for cron jobs, CI/CD pipelines, and scripted automation.
- Allow seamless addition of new agents and tools without system restarts.
- Enforce strict security boundaries where agents cannot modify their own permissions.
- Minimise token usage through compact inter-agent communication protocols.
- Enable agents to be self-sufficient in managing their own context and memory.

### 1.4 Scope

**In scope for v1:**

- Project Agent (PA) as central coordinator and session manager.
- Web UI for rich interaction (text, images, documents).
- Four core tool capabilities: Python execution, filesystem access, PostgreSQL database access (read-only by default), and browser automation.
- MCP-based tool communication with adapter layer for simpler models.
- YAML-based configuration with hot-reload.
- Markdown-based agent prompts, context summaries, and conversation logging.
- Docker-based containerisation.
- Granular approval system for agent actions.
- Direct agent-to-agent communication via message bus.
- Token-efficient compact protocol for inter-agent messaging.
- Per-agent rolling context management with disk-based persistence.
- CLI for installation (`faith init`), lifecycle management (`faith start`/`stop`), and headless task execution (`faith run`).
- Skills — markdown-based reusable task definitions (`faith run --skill <name>`) for automated/scheduled execution with unattended approval handling.

**Out of scope for v1:**

- Kubernetes / multi-machine distribution.
- Third-party plugin marketplace.
- Mobile interface.

### 1.5 Target Platforms

- Windows 11
- Linux (Ubuntu/Debian primary)
- macOS

### 1.6 Python Version Requirements

| Component | Minimum Python | Rationale |
|-----------|---------------|-----------|
| `faith-cli` (host) | 3.10 | Maximum compatibility — the CLI is a thin wrapper (Click + requests), needs nothing beyond 3.10 features |
| All Docker containers (PA, agents, tools, web-ui) | 3.13 | FAITH controls the container images. Python 3.13 provides significant performance improvements, better `asyncio` task groups, improved error messages, and the free-threaded build option for future use |

The `faith-cli` package declares `requires-python = ">=3.10"` in `pyproject.toml`. All Dockerfiles use `python:3.13-slim` as their base image.

`faith_shared` publishes the canonical schema and protocol versions consumed by `faith_cli`, `faith_pa`, `faith_web`, and FAITH-owned MCP servers (`faith_mcp`).

---

## 2. System Architecture

### 2.1 High-Level Architecture

The system follows a hub-and-spoke model with the PA at the centre, transitioning to a managed mesh when agents are collaborating directly.

```
User <--> Web UI <--> Project Agent (PA)
                          |
              +-----------+-----------+
              |           |           |
           Agent A     Agent B     Agent C
              |           |           |
           Tools        Tools       Tools
        (via MCP)    (via MCP)   (via MCP)

Direct Agent Communication (PA-managed channels):

PA (Session Manager / Monitor)
 |
 ├── Sets up channels
 ├── Monitors traffic
 ├── Intervenes when needed (loops, drift, escalation)
 └── Can override/halt conversations

Message Bus (Redis)
 |
 ├── Channel: dev+qa+security
 │   ├── Shared context store
 │   └── Compact protocol messages
 └── Channel: architect+fds
     ├── Shared context store
     └── Compact protocol messages
```

### 2.2 Core Components

#### 2.2.1 Project Agent (PA)

- Central coordinator for all agent activity.
- Routes tasks to appropriate agents based on their defined capabilities.
- Moderates multi-agent discussions by controlling turn order.
- Sets up direct communication channels between agents, then monitors rather than relays.
- Can override or halt agent conversations when detecting issues (loops, drift, off-topic).
- Handles MCP translation for models that lack native MCP support (adapter layer).
- Manages approval workflow — surfaces actions requiring user consent to the web UI.
- Monitors config file changes and triggers hot-reload across the system.
- Defaults to a capable LLM (configurable) since it handles orchestration logic.
- Asks the user for permission before using paid LLMs for tasks requiring stronger reasoning.

#### 2.2.2 Specialist Agents

- **No agents are hardcoded or shipped with FAITH.** The PA is the only permanent component. All specialist agents are created dynamically by the PA based on the user's project requirements.
- When a user describes their project (or the PA analyses an existing codebase), the PA determines the optimal agent team — deciding how many agents are needed, their roles, models, tool permissions, and channel structure. The PA creates each agent's directory in `.faith/agents/{id}/`, writes `config.yaml` (machine-readable definition), and generates `prompt.md` (system prompt).
- The user reviews and can adjust the proposed team before the PA starts the agent containers. The user can also request changes at any time during a session (*"add a database expert"*, *"remove the security agent"*) and the PA updates the team accordingly.
- Each agent runs in its own Docker container, built from a shared `agent-base` image.
- Can communicate directly with other agents via PA-managed message bus channels.
- Self-manage their own context window using rolling summary approach (see Section 3.5).
- Can request config changes by instructing the user (never writing configs directly).
- Can trigger opening config files in the user's preferred editor.
- Each agent has a dedicated project folder (created by the PA) containing its markdown system prompt, context summary, and conversation logs.

#### 2.2.3 MCP Tool Servers

- Each tool runs as an MCP server in its own Docker container.
- Tools expose capabilities via the MCP standard.
- PA provides an adapter/proxy layer that translates MCP tool calls into simpler prompt-based instructions for models that don't support MCP natively.
- Each tool has a dedicated log folder recording all commands sent to it.
- Tool configs (e.g. allowed filesystem paths, DB connection strings) are read-only to agents.

#### 2.2.4 Message Bus (Redis)

- Lightweight Redis container for inter-agent pub/sub messaging.
- PA creates and manages channels for agent groups.
- Supports the compact inter-agent protocol (see Section 3.3).
- Shared context stores per channel.

#### 2.2.5 Web UI

- Built with Python FastAPI (backend) + GoldenLayout + Vue 3 (no-build) + xterm.js (frontend).
- Supports text input, image paste/upload, document upload.
- Displays agent conversations, approval requests, and system status.
- Displays Docker runtime state for FAITH-managed containers and images in a dedicated monitoring panel.
- Provides config editing guidance (can open files in user's preferred editor).

### 2.3 Docker Architecture

```
docker-compose.yml (bootstrap only)
├── pa-container            (Project Agent — always on, manages all other containers)
├── redis-container         (Message bus — always on)
├── web-ui-container        (FAITH Web UI backend/frontend client — always on)
├── ollama-container        (Local model service — enabled by default)
└── mcp-registry-container  (Self-hosted MCP Registry — always on)

PA-managed containers (started/stopped by PA via Docker SDK):
├── agent-{role}-container  (... specialist agents, created dynamically by PA per project)
├── sandbox-{task-or-session}  (Disposable root-capable execution sandbox for agent work)
├── tool-python-container   (Python execution MCP server)
├── tool-fs-container       (Filesystem MCP server)
├── tool-code-index-container (Code Index MCP server)
├── tool-search-container   (Full-Text Search MCP server)
├── tool-kv-container       (Key-Value Store MCP server)
└── mcp-runtime-container   (Project-scoped runtime for external registry-backed stdio MCP servers)
```

All containers share a named Docker network (`maf-network`). The PA attaches all managed containers to this network on startup.

**Volume mounts:**
- The FAITH installation's `config/` directory is mounted read-only into the PA container only (for `secrets.yaml` access). It is **never** mounted into agent or tool containers.
- The project workspace (e.g. `~/my-project/`) is mounted into the PA and the project-scoped tool runtimes that require it. The PA mounts the project's `.faith/` directory to read agent configs, tool configs, and project settings. FAITH-owned tool containers and the project-scoped `mcp-runtime` container receive only the specific paths required by the enabled tools and permitted by the project's `.faith/tools/*.yaml`.
- Agent containers receive only their own agent directory (`.faith/agents/{id}/`) and the workspace paths assigned to them.

The PA holds the Docker socket mount and manages the full container lifecycle. This requires root-equivalent access and is disclosed to the user during installation. See Section 4.6 for full orchestration details.

**Disposable sandbox model:**
- Sandbox means a **disposable Linux Docker container** fully controlled by the PA, not a restricted Unix user inside a shared container.
- Agents running inside a sandbox have **root access inside that sandbox container** and may install Python packages, install or upgrade OS packages, modify the container filesystem, and otherwise treat it as a scratch machine.
- The safety boundary is the container boundary, approved mounts, network policy, and resource quotas — not arbitrary in-container permission restrictions.
- Root access inside the sandbox is acceptable only because the sandbox is disposable and isolated. It must not be treated as equivalent to safe host access.
- The Docker socket is never mounted into sandbox containers. Sandboxes must not run in Docker `--privileged` mode, must not use host networking, and must receive only the minimum Linux capabilities required for their workload.
- Sandboxes receive only explicitly approved host mounts. Framework secrets, PA-only config, and unrelated project paths must not be mounted into them by default.
- If a sandbox becomes polluted, broken, or otherwise undesirable, the PA should destroy it and create a fresh sandbox from the base image rather than attempt manual repair by default.
- Specialist agents may share a sandbox when they are collaborating on the same task and do not need environment isolation.
- The PA may allocate a dedicated sandbox to a sub-agent when the work is destructive, requires conflicting runtime/package changes, needs risky experimentation, or benefits from isolated parallel execution.
- Sandbox allocation is a PA scheduling decision subject to CPU, memory, disk, and concurrent-container limits. The PA should prefer reuse/shared sandboxes first and create additional isolated sandboxes only when the isolation benefit justifies the resource cost.

### 2.4 Directory Architecture

FAITH separates two distinct directory trees: the **framework installation** (FAITH's own code and runtime) and the **project workspace** (the user's project). These are never mixed.

#### 2.4.0 Repository Topology

FAITH is implemented as a **monorepo** — all FAITH-owned components live in a single `faith/` repository.

**Source layout:**

```
faith/                                 # Repository root
├── src/
│   ├── faith_cli/                     # CLI package (pip install faith-cli)
│   ├── faith_pa/                      # Project Agent
│   ├── faith_web/                     # FastAPI + WebSocket + Web UI server
│   ├── faith_shared/                  # Shared models, protocols, schemas
│   └── faith_mcp/                     # FAITH-owned MCP servers
│       ├── filesystem/
│       ├── python_exec/
│       ├── code_index/
│       ├── fulltext_search/
│       └── kv_store/
├── web/                               # Frontend assets (Vue 3)
├── containers/                        # Dockerfiles only (pa/, web-ui/, mcp-runtime/)
├── config/                            # Framework-level templates
├── data/
├── logs/
├── tests/
├── docker-compose.yml
├── setup.ps1                       # Windows bootstrap helper
├── setup.sh                        # Linux bootstrap helper
└── pyproject.toml
```

**Primary packages:**

- **`faith_cli`** (`src/faith_cli/`) — lightweight installer/bootstrapper and CLI client. Handles `faith init/start/stop/run`, local environment checks, ownership of the bootstrap `docker-compose.yml`, Docker lifecycle, HTTP/WebSocket communication with the PA, and lifecycle management for the optional host-side worker used for unsandboxed local-machine actions. Published to PyPI as `faith-cli`.
- **`faith_pa`** (`src/faith_pa/`) — the core backend framework. Contains the Project Agent, shared agent runtime, config loading/enforcement, event system, approval logic, session/task orchestration, MCP adapter layer, and the stable backend HTTP/WebSocket API implementation consumed by the Web UI and any alternative clients.
- **`faith_shared`** (`src/faith_shared/`) — shared contracts used across FAITH-owned components: protocol/event models, config schemas, PA API request/response/event contracts, host-worker protocol definitions, common types, compatibility helpers, and shared version/compatibility constants.
- **`faith_mcp`** (`src/faith_mcp/`) — FAITH-owned MCP servers, each as a subpackage (e.g. `faith_mcp.filesystem`, `faith_mcp.python_exec`, `faith_mcp.code_index`). Each subpackage can be independently versioned and updated.
- **`faith_web`** (`src/faith_web/`) — FastAPI + WebSocket backend for the Web UI. Frontend assets (Vue 3) live in `web/`. The Web UI consumes the PA's public API and is only one possible client; other frontends may be implemented independently in any language/framework.

**Design rule:** FAITH-owned MCP servers are separate subpackages under `src/faith_mcp/`, not embedded into the PA codebase. The PA consumes them as MCP servers. Docker images are built separately from the monorepo's `containers/` Dockerfiles (PA, web-ui, mcp-runtime).

**Ownership rules:**

- The bootstrap `docker-compose.yml` is owned and versioned by `faith_cli`.
- Shared config schemas are owned by `faith_shared`.
- The PA API contract is defined in `faith_shared` and implemented by `faith_pa`.
- The host-worker protocol is defined in `faith_shared` and implemented/managed by `faith_cli`.
- Cross-package compatibility rules and version constants live in `faith_shared`; enforcement happens in `faith_cli` during install/update/bootstrap and in `faith_pa` during startup/runtime validation.

#### 2.4.1 Framework Installation

The FAITH framework home directory. Created by `faith init` and shared across all projects. Contains runtime config, Docker definitions, and global secrets.

For **end users**, FAITH is installed via `pip install faith-cli` which places the `faith` command on PATH. The CLI bundles `docker-compose.yml`, config templates, and archetype files. Running `faith init` extracts these to `~/.faith/` and pulls the bootstrap Docker images (PA, Web UI, Redis, Ollama, and the self-hosted MCP registry).

For **developers/contributors**, all work happens in the single `faith` monorepo. The implementation is split by package responsibility under `src/` (see Section 2.4.0). The framework home on disk is still the same.

```
~/.faith/                          # Framework home (created by `faith init`)
├── config/                        # Framework-level config (NOT project-specific)
│   ├── secrets.yaml               # Credentials ONLY — API keys, DB passwords, tokens
│   ├── .env                       # Environment variables (referenced by secrets.yaml)
│   ├── recent-projects.yaml       # List of recent projects for project switcher
│   └── archetypes/                # Role archetype templates (built-in + user-defined)
│       ├── software-developer.yaml
│       ├── test-engineer.yaml
│       └── ...                    # Users may add custom archetypes here
├── data/
│   ├── model-prices.default.json  # Bundled pricing (committed to git)
│   ├── model-prices.cache.json    # Live scraped pricing (gitignored)
│   └── provider-privacy.json      # Provider T&C knowledge base
├── logs/                          # Framework-level logs (gitignored)
├── docker-compose.yml             # Bootstrap: PA, Redis, Web UI, Ollama, MCP Registry
└── .gitignore
```

**`secrets.yaml`** contains all credentials in one place. Tools reference secrets by key name; the PA injects actual values at container startup. **AI agents never have access to this file.** The filesystem MCP server has a hard block preventing any read access to `secrets.yaml` or `.env` (see Section 5).

```yaml
# config/secrets.yaml
openrouter_api_key: ${OPENROUTER_API_KEY}    # resolved from .env
github_token: ${GITHUB_TOKEN}
confluence_password: ${CONFLUENCE_PASSWORD}
databases:
  prod-db:
    host: db.example.com
    port: 5432
    user: readonly_user
    password: ${PROD_DB_PASSWORD}
  test-db:
    host: localhost
    port: 5432
    user: test_user
    password: ${TEST_DB_PASSWORD}
```

**Framework `.gitignore`:**
```
config/secrets.yaml
config/.env
data/model-prices.cache.json
logs/
```

#### 2.4.2 Project Workspace

Each project the user works on contains a `.faith/` directory alongside their existing code. This directory holds all FAITH project-specific configuration, agent definitions, session history, and project documentation. It is safe to commit to the project's git repository — no secrets are stored here.

```
~/my-project/                      # The user's project (the "workspace")
├── src/                           # Their source code
├── tests/
├── cag/                           # User-managed high-value reference docs for CAG loading
│   ├── architecture.md            # Example: stable architecture notes
│   ├── domain-rules.md            # Example: important business/domain constraints
│   └── ...                        # Normal markdown/text files the PA may read, and update on request
├── .faith/                        # FAITH project config (committable to project git)
│   ├── system.yaml                # Project settings: PA model, privacy profile, editor, loop detection
│   ├── security.yaml              # Approval rules, trust levels for this project
│   ├── tools/                     # Per-tool configuration (one file per tool)
│   │   ├── filesystem.yaml        # Mount definitions, permissions, history settings
│   │   ├── python.yaml            # Internet toggle, timeout
│   │   ├── database.yaml          # Connection names, access levels (no secrets — keys reference secrets.yaml)
│   │   ├── browser.yaml           # Headless mode, viewport
│   │   └── confluence.yaml        # Space key, base URL (no passwords)
│   ├── agents/                    # One folder per agent (PA-generated, user-editable)
│   │   ├── software-developer/
│   │   │   ├── config.yaml        # Machine-readable: model, tools, trust, file_watches, listen_tags
│   │   │   ├── prompt.md          # AI-readable: role definition, behavioural instructions
│   │   │   ├── context.md         # AI-readable: rolling conversation summary (gitignored)
│   │   │   └── state.md           # AI-readable: resumable state, written on teardown (gitignored)
│   │   └── test-engineer/
│   │       └── ...
│   ├── skills/                    # Reusable task definitions (markdown + frontmatter)
│   │   └── nightly-qa-tests.md
│   ├── sessions/                  # Session history and logs (gitignored)
│   │   └── sess-NNNN-YYYY-MM-DD/
│   │       ├── session.meta.json
│   │       └── ...
│   └── docs/
│       ├── frs.md                 # Living Functional Requirements Specification
│       ├── architecture.md        # Optional: architecture detail document
│       └── ...                    # Any docs here are auto-indexed by RAG tool
└── ...                            # Rest of the user's project
```

**Project `.gitignore` entries (added by FAITH on project setup):**
```
.faith/agents/*/context.md
.faith/agents/*/state.md
.faith/sessions/
```

**What is committed vs gitignored:**

| Committed to project git | Gitignored |
|---|---|
| `.faith/system.yaml` — project settings | `.faith/agents/*/context.md` — volatile runtime state |
| `.faith/security.yaml` — approval rules | `.faith/agents/*/state.md` — volatile runtime state |
| `.faith/tools/*.yaml` — tool config (no secrets) | `.faith/sessions/` — session logs and history |
| `.faith/agents/*/config.yaml` — agent definitions | |
| `.faith/agents/*/prompt.md` — agent prompts | |
| `.faith/docs/frs.md` — project requirements | |

This means a colleague cloning the project gets the full FAITH configuration, agent team, and project requirements. They supply their own `secrets.yaml` in their FAITH installation and can immediately resume work.

**Secret references in project config:**

Project-level tool configs reference secrets by key name, never by value:

```yaml
# .faith/tools/database.yaml
connections:
  prod-db:
    secret_ref: prod-db          # Resolved from secrets.yaml at runtime
    database: myapp_production
    access: readonly
  test-db:
    secret_ref: test-db          # Resolved from secrets.yaml at runtime
    database: myapp_test
    access: readwrite
```

### 2.5 Multi-Project Support

FAITH manages **one active project at a time**. A project is defined by its directory on the host filesystem — specifically the presence of a `.faith/` subdirectory (created during project setup).

**Switching projects:**
- Via the Web UI: a project switcher dropdown in the toolbar lists recently used projects (stored in the framework's `config/recent-projects.yaml`).
- Via the PA: the user says *"switch to project at ~/projects/foo"* and the PA performs the switch.

**Coordinated teardown (leaving current project):**

1. PA signals all active agents to finish their current LLM call — no new work is accepted.
2. Each agent publishes its final context summary to `context.md`.
3. PA writes `state.md` for each agent — capturing current task, progress, channel assignments, file watch subscriptions, and a plain-English summary of where the agent left off.
4. PA writes `session.meta.json` with session status.
5. PA stops all agent containers.
6. Tool containers remain running (they are project-agnostic; only their config changes).

**Project load (entering target project):**

1. PA mounts the target project directory.
2. If the project has no `.faith/` directory (first visit), the PA runs the project setup flow (see Section 9.3.5) — creating `.faith/`, analysing the codebase, proposing an agent team, and generating config files.
3. If `.faith/` exists (returning to a previous project):
   a. PA reads `.faith/system.yaml` for project settings.
   b. PA reads `.faith/tools/*.yaml` and reconfigures project-scoped tool runtimes.
   c. PA scans `.faith/agents/*/config.yaml` to discover the agent roster.
   d. PA starts agent containers from the existing definitions.
   e. Each agent loads its `prompt.md`, `context.md`, and `state.md` to resume where it left off.
4. RAG tool re-indexes `.faith/docs/`.
5. Code Index Tool re-indexes the project source code.
6. PA confirms to the user that the project is loaded and ready, summarising the resumed state.

**No agent containers survive a project switch** — agents are always torn down and recreated. This ensures a clean boundary between projects. Project-scoped tool runtimes may be reconfigured in place where safe; external MCP subprocesses inside `mcp-runtime` are stopped and re-resolved against the new project's registrations.

### 2.6 Hot-Reload Strategy

- The PA watches config files using filesystem polling (compatible across all OS).
- On change detection, the PA reloads the affected config and propagates updates to relevant agents/tools.
- **No restart required for:** agent model changes, tool permission changes, security policy changes, new agent definitions (PA-generated or user-added), editor preferences, approval rules, agent prompt changes.
- **Restart required for:** Docker volume mount changes (new host folders), port changes, or introducing a new project-scoped container runtime that the PA does not already manage.

---

## 3. Agent Communication & Orchestration

### 3.1 Communication Modes

The framework supports three communication modes:

#### 3.1.1 User-to-PA

- Natural language via the web UI.
- Supports text, images, and document uploads.
- PA responds in natural language.

#### 3.1.2 PA-to-Agent (Task Delegation)

- The PA dynamically creates specialist agents based on the user's project requirements. It determines the optimal team composition — roles, models, tools, and trust levels — and writes each agent's `config.yaml` and `prompt.md` in `.faith/agents/{id}/`. The user reviews and may adjust the proposed team before the PA starts agent containers. Agents can also be added, removed, or reconfigured mid-session at the user's request.
- PA assigns tasks to specific agents based on their capabilities defined in their `config.yaml`.
- PA determines execution order when multiple agents are involved.
- Uses compact protocol for instructions, natural language only when the agent needs to reason about ambiguous requirements.
- Each agent has a reserved direct channel `pa-{agent-id}` (e.g. `pa-software-developer`) created automatically when the agent container starts. The PA uses this channel for initial task assignment and direct queries. Agents listen on their `pa-{agent-id}` channel from startup. All subsequent multi-agent collaboration uses named task channels (e.g. `ch-auth-feature`).

#### 3.1.3 Agent-to-Agent (Direct Collaboration)

- PA sets up a communication channel on the Redis message bus.
- Participating agents are defined, the goal is set, and the PA steps back to a monitoring role.
- Agents communicate directly using the compact inter-agent protocol.
- PA monitors for: circular discussions, off-topic drift, escalation needs, approval triggers.
- PA can intervene, redirect, or halt the conversation at any time.

#### 3.1.4 Dynamic Agent Creation

No specialist agents are shipped with FAITH. The PA creates all agents dynamically based on the user's project requirements. Agent creation is a core PA responsibility and follows a **template-guided but not template-limited** approach.

**Role Archetype Library**

FAITH ships with a library of **role archetypes** stored in `config/archetypes/`. Each archetype is a lightweight YAML file describing when the role is useful, not a full agent definition:

```yaml
# config/archetypes/software-developer.yaml
name: Software Developer
description: "Writes and modifies source code to implement features, fix bugs, and refactor."
suggested_when:
  - "Project involves writing or modifying code"
  - "Implementation work is required"
default_tools: [filesystem, python, code-index]
default_trust: standard
prompt_guidance: |
  Focus on clean, maintainable code. Check existing patterns before writing new code.
  Always verify packages are installed before executing. Batch multiple steps where possible.
```

```yaml
# config/archetypes/test-engineer.yaml
name: Test Engineer
description: "Designs and writes test cases — unit, integration, and end-to-end."
suggested_when:
  - "Automated testing is required"
  - "QA coverage is part of the project scope"
default_tools: [filesystem, python]
default_trust: standard
prompt_guidance: |
  Write tests that verify behaviour, not implementation. Cover edge cases.
  Use file watch events to detect code changes and re-run relevant tests.
```

The PA uses these archetypes as a **starting palette** when analysing the user's project. It selects relevant archetypes, adapts them to the specific project (adjusting tools, models, and prompt content), and may **invent novel roles** that don't match any archetype when the project demands it. For example, a database migration project might result in a `database-migration-specialist` agent that the PA reasons into existence from the requirements.

**User-defined archetypes:** Users can create their own archetype files in `config/archetypes/` to expand the library with roles they use frequently. User-defined archetypes are loaded alongside the built-in set and are available for the PA to select from. The PA treats user-defined and built-in archetypes identically.

**PA reasoning requirement:** When proposing an agent team, the PA must explain its reasoning for each agent — why this role is needed for this project. For example: *"I'm recommending a security-expert because your project handles JWT tokens and password storage."* This ensures the user understands the team composition and can make informed adjustments.

**Agent team evolution mid-project:**

The PA continuously evaluates whether the current agent team matches the project's needs. When requirements change (e.g. new sections added to `frs.md`, user requests a new capability), the PA adjusts the team automatically — it does not wait for the user to request changes.

**Cost-based approval rule:**

- **Free/local model agents (Ollama):** The PA creates, modifies, and removes agents autonomously with no user approval required. The user is notified via a message in the conversation context and a new agent panel appearing (or disappearing) in the Web UI. This is informational, not a request.
- **Paid model agents (OpenRouter):** The PA must inform the user of the cost implication and receive confirmation before creating the agent. Example: *"The security review requires reasoning beyond what local models can handle. I'd like to create a `security-expert` agent using `claude-sonnet-4-6` via OpenRouter. This will incur API costs. Proceed?"* Removal of paid agents is always automatic since stopping them saves money.
- **Removal** is always automatic and silent (with notification). The PA manages team composition as a core responsibility — the user delegates this to the PA. Agent directories, logs, and history are preserved regardless of removal. The agent's `config.yaml` is removed (or the directory is marked inactive) and its container is stopped.

This mirrors the approval philosophy used throughout FAITH: free actions proceed automatically; actions with a cost require user consent.

**Agent lifecycle:**

| Action | Trigger | Approval required? | Effect |
|---|---|---|---|
| Create (free model) | Scope change, PA analysis | No — notify only | PA creates `.faith/agents/{id}/` directory, writes `config.yaml` and generates `prompt.md`, starts container, notifies user |
| Create (paid model) | Scope change, PA analysis | Yes — user confirms | Same as above, but only after user approves the cost |
| Modify agent | PA analysis or user request | No — notify only | PA updates the agent's `config.yaml`, hot-reloads |
| Remove agent | Phase complete, scope change, PA analysis | No — notify only | PA stops container, removes `config.yaml` (directory, prompt.md, logs preserved for history) |
| Restart agent | PA detects crash, or user request | No | PA restarts container from same `config.yaml` definition |

**Prompt generation:**

The PA generates each agent's `prompt.md` system prompt entirely from its own knowledge. No external prompt library is required — the PA's underlying LLM has been trained on extensive prompt engineering content and knows what makes an effective system prompt for any given role.

Each generated `prompt.md` is assembled from two layers:

1. **Base skeleton (fixed template, built into FAITH):** Structural instructions that are identical for all agents — how to use the compact protocol, how to publish events, how to interact with MCP tools, how to manage context, when to publish heartbeats. This is not LLM-generated; it is a tested template embedded in the FAITH codebase.
2. **Role and project section (PA-generated):** The PA writes the role-specific and project-specific content for each agent. It draws on the archetype's `prompt_guidance` for behavioural direction and incorporates project context (technologies in use, coding patterns, FRS requirements, team structure). This section is unique to each agent and each project.

The user can view and edit any agent's `prompt.md` at any time — it is a plain markdown file in `.faith/agents/{agent-id}/prompt.md`. Edits are hot-reloaded (see Section 7.3). The PA does not show generated prompts to the user by default; however, the Web UI agent panel displays a brief summary of each agent's role, tools, and model for at-a-glance understanding.

### 3.2 PA as Session Manager

When setting up an agent collaboration session, the PA:

1. Identifies which agents are needed for the task.
2. Stages agent involvement — agents are brought into a channel when their phase begins, not all at once. For example: architect and FDS agents complete their phase before developer and QA agents are introduced. This keeps channels focused and prevents agents from reacting to work that isn't ready for them.
3. Creates a channel on the message bus.
4. Defines the goal and constraints for the session.
5. Assigns initial turn order.
6. Configures file watch subscriptions for relevant agents via the filesystem tool.
7. Notifies participating agents with their role in the session.
8. **Does not monitor agent channel messages.** The PA subscribes to the `system-events` channel only and reacts to state-change events published by agents and tools. It joins an agent communication channel only when an event signals intervention is needed (see Section 3.7).
9. Intervenes when: `channel:stalled`, `agent:task_blocked`, `channel:loop_detected`, `agent:error`, `approval:requested`, or `agent:model_escalation_requested` events are received.

#### 3.2.1 Channel Size Limit

A soft limit of 5 agents per channel is enforced by default (configurable in `system.yaml`; set to 0 to disable). When a channel exceeds the limit, the PA warns the user and suggests splitting into focused sub-channels. No hard block is applied — the user may override and proceed.

#### 3.2.2 Loop Detection

The PA actively monitors all channels for circular behaviour patterns. Loop detection covers:

- **Direct repetition:** An agent producing output substantively identical to a recent previous message on the channel.
- **Circular dependency loops:** A sequence where Agent A's output causes Agent B to make a change, which causes Agent A to revert or re-change the same thing. Example: a test agent modifies a test to pass → the dev agent changes the code to fix a resulting bug → the test agent changes the test again → repeat.
- **Oscillation:** Any state (file content, decision, task status) that reverts to a previous state within a configurable number of messages.

**Detection mechanism:**
- The PA maintains a rolling hash of key state changes (file modifications, decisions recorded) per channel.
- If the same state hash is seen more than once within a configurable window (default: 10 messages), the PA flags a loop.
- On loop detection: the PA halts the channel, surfaces a summary of the loop to the user via the Web UI, and requests guidance on how to proceed. Agents are not informed of the loop detection — only the user is.

**Configuration in `system.yaml`:**
```yaml
loop_detection:
  enabled: true
  window_messages: 10       # number of recent messages to check
  state_repeat_threshold: 2 # how many times a state can repeat before flagging
```

### 3.3 Compact Inter-Agent Protocol

Agents communicate with each other using a structured, token-efficient protocol rather than natural language prose. This reduces inter-agent token usage by an estimated 60-80%.

**Message format:**

```yaml
from: dev
to: qa
channel: ch-auth-feature
msg_id: 47
type: review_request
tags: [code, auth, testing]
status: complete
files: [auth.py, auth_test.py]
summary: "auth module done, 3 endpoints, JWT httponly cookies"
needs: "test coverage for token expiry edge case"
context_ref: ch-auth-feature/msg-42-46
```

**Field definitions:**

| Field | Required | Description |
|-------|----------|-------------|
| `from` | Yes | Sending agent short name |
| `to` | Yes | Target agent(s), or `all` for broadcast |
| `channel` | Yes | Channel identifier |
| `msg_id` | Yes | Sequential message ID within channel |
| `type` | Yes | Message type: `task`, `review_request`, `feedback`, `question`, `status_update`, `decision`, `escalate` |
| `tags` | Yes | Role-relevance tags for context filtering |
| `status` | No | Current status: `in_progress`, `complete`, `blocked`, `needs_input` |
| `files` | No | Relevant file paths |
| `summary` | Yes | Concise description of content |
| `needs` | No | What is required from the recipient |
| `context_ref` | No | Reference to previous messages instead of repeating content |
| `priority` | No | `low`, `normal`, `high`, `critical` |
| `disposable` | No | `true` — artifact in this message can be purged during compaction once the task it relates to is marked complete. Only the summary line is retained. |

**Protocol rules:**

- Natural language is used only in `summary` and `needs` fields and should be kept concise.
- Context references (`context_ref`) are used instead of repeating previous information.
- Agents switch to natural language only when communicating with the user via the PA.
- Agent system prompts include instructions for using this protocol.
- Messages containing large artifacts (code snippets, documents, file contents) should be marked `disposable: true` when the artifact is only needed for the immediate task (e.g. a code review). During compaction, disposable messages are replaced with a single summary line rather than being included in the rolling context summary. Example retained line: *"Code review of auth.py completed — feedback addressed, token refresh endpoint approved."*

### 3.4 Role-Based Context Filtering

Each agent maintains only context relevant to its role. Messages are filtered using the `tags` field:

**Tag-to-role mapping (configured in the agent's `config.yaml`):**

| Agent | Listens to tags |
|-------|----------------|
| Software Developer | `code`, `architecture`, `bug`, `feature`, `testing` |
| Test Case Engineer | `testing`, `code`, `qa`, `requirements` |
| QA Engineer | `qa`, `testing`, `code`, `bug`, `e2e` |
| Security Expert | `security`, `code`, `auth`, `vulnerability` |
| System Architect | `architecture`, `design`, `requirements`, `integration` |
| FDS Architect | `requirements`, `design`, `architecture`, `fds` |

When a message arrives on a channel:

1. The agent checks if any of the message's `tags` match its configured listen tags.
2. If yes: the message is added to the agent's active context.
3. If no: the message is ignored (not stored in the agent's context).
4. If `to` field explicitly names the agent: always added regardless of tags.

### 3.5 Context Management

Each agent is self-sufficient in managing its own context window. The PA does not manage context for agents.

#### 3.5.1 Message Structure Sent to LLM

For every LLM API call, the agent assembles the following:

```
[System Prompt]       — Full role definition from prompt.md (sent every time)
[Role Reminder]       — 2-3 line reinforcement of role and protocol usage
[Context Summary]     — Loaded from context.md (rolling summary of past work)
[CAG Documents]       — Pre-loaded static reference documents (if configured)
[Recent Messages]     — Last N compact protocol messages from active channels
[Current Task]        — The current message or task to process
```

This structure ensures:

- The agent never forgets its role (system prompt + role reminder on every call).
- Historical context is preserved without filling the context window (context.md).
- Recent conversation flow is available for immediate reasoning.

#### 3.5.2 Rolling Context Summary

Each agent manages a `context.md` file in its project folder. Summarisation is triggered **adaptively by token count**, not by a fixed message count:

1. After each message, the agent estimates the accumulated token count of its recent message window.
2. When the accumulated tokens exceed a configurable threshold (default: 50% of the assigned model's context window), summarisation triggers.
3. A hard maximum message count fallback (default: 50 messages) triggers summarisation regardless of token count, as a safety net.
4. The summary captures: key decisions made, outstanding tasks, important facts, current blockers.
5. The summary is appended to `context.md`.
6. Old raw messages are dropped from active context. Disposable messages (marked `disposable: true` in the compact protocol) are replaced with a single retained summary line rather than being fully summarised.
7. On each new message, the agent loads `context.md` as part of its context assembly.

**Token counting method:** agents use `tiktoken` (OpenAI's open-source tokenizer) as a consistent approximation across all models. For Ollama models without a matching tokenizer encoding, a fallback of 4 characters per token is applied. Token counts are estimates — a 10% margin is applied to the threshold to account for counting imprecision (e.g. a 50% threshold effectively triggers at ~45% of measured token count).

Per-agent overrides are supported in the agent's `config.yaml`:

```yaml
agents:
  fds-architect:
    context:
      summary_threshold_pct: 40   # summarise earlier — works with large documents
      max_messages: 30
  software-developer:
    context:
      summary_threshold_pct: 50   # default
      max_messages: 50
```

**Benefits over compaction (e.g. Claude Code approach):**

- Context window never fills up because summarisation is proactive, not reactive.
- Already-compact protocol messages have minimal benefit from further compaction — the rolling summary handles the natural language reasoning growth instead.
- No risk of session abandonment due to full context.
- Persistent to disk — survives container restarts.

#### 3.5.3 Context.md Format

```markdown
# Context Summary — Software Developer
## Last Updated: 2026-03-23 14:30

### Key Decisions
- Auth module uses JWT with HS256, tokens in httponly cookies
- API follows RESTful conventions, versioned at /api/v1

### Outstanding Tasks
- Implement token refresh endpoint
- Address security feedback on password hashing (see ch-auth-feature/msg-52)

### Important Facts
- Database schema finalised (see schema.sql)
- QA requires all endpoints to have integration tests before review

### Current Blockers
- Waiting for FDS sign-off on rate limiting requirements
```

### 3.6 LLM Model Selection

#### 3.6.1 Default Behaviour

- All specialist agents default to a local Ollama model (free) for routine tasks when local-model execution is enabled.
- **The PA is an exception** — orchestration, session management, loop detection, and approval routing require strong reasoning capability that current Ollama models are unlikely to handle reliably. The PA should default to a capable paid model (e.g. via OpenRouter) unless the user explicitly configures otherwise.
- The PA monitors task complexity and agent performance and may suggest escalating a specialist agent to a paid model when needed.
- Local-model recommendations must be based on measured runtime capability, not static preference order alone. The PA should probe whether Ollama inference is actually working, whether GPU acceleration is available, how much usable VRAM is present, and whether the model can fit comfortably before recommending a local model.

#### 3.6.2 OpenRouter Onboarding

Adding a paid model should require no technical effort from the user. If the system is running in offline/Ollama-only mode and the user tells the PA they want to use OpenRouter:

1. The PA instructs the user to add their OpenRouter API key to `system.yaml` (or offers to open the file in their preferred editor).
2. Once the key is saved, the PA detects the change via hot-reload and immediately makes OpenRouter models available — no restart required.
3. The PA confirms to the user that paid models are now available and suggests which agents would benefit most.

This flow applies equally on first setup and at any point during normal use. The goal is that onboarding a new model provider feels like a one-line config change, not an installation task.

#### 3.6.3 Escalation to Paid Models

The PA identifies escalation candidates based on the following observable signals — not subjective reasoning:

- An agent has produced the same incorrect or incomplete output more than twice on the same task.
- A task involves domains explicitly flagged as requiring stronger reasoning in the agent's `config.yaml` (e.g. `escalate_for: [security, architecture]`).
- An agent publishes `agent:model_escalation_requested` — the agent itself signals it cannot make progress with its current model.
- The PA is about to route a task to an agent on a local model but the task type matches a configured escalation rule in `system.yaml`.

When any signal is detected, the PA asks the user:
> *"This task may benefit from a more capable model via OpenRouter. Approve paid model usage for [agent] on [task]?"*

User can approve or deny via the Web UI. User can also override the default model for any specific agent or task at any time by telling the PA in natural language.

#### 3.6.4 LLM API Failure Handling

All LLM API calls use exponential backoff retry logic:

- **Max retries:** 3
- **Delays:** 2s → 4s → 8s between attempts
- **Retry on:** HTTP 429 (rate limit), 503 (service unavailable), network timeout
- **Do not retry:** HTTP 400 (bad request), 401 (unauthorised), 404 (model not found) — these are permanent errors requiring PA intervention
- **After 3 failures:** PA is notified via `agent:error`. If a `fallback_model` is configured for the agent, the PA switches to it automatically. If no fallback is configured, the user is notified and the task is paused.
- Transient vs permanent error classification is based on HTTP status code alone — no content inspection.

#### 3.6.5 Model Configuration

- Default models per agent defined in the agent's `config.yaml`.
- Global default model defined in `system.yaml`.
- PA model configured separately in `system.yaml` — defaults to `ollama/llama3:8b`,
  selected as the baseline local model for systems with at least a 6GB GPU.
- `faith init` must pull the default Ollama model into the FAITH-managed Ollama
  volume before reporting first-run startup as ready.
- User overrides take precedence over defaults.
- Model changes are hot-reloaded — no restart required.
- Ollama model management is exposed to the PA through a dedicated Ollama MCP
  server. The PA can ask the tool to list installed models, inspect running
  model CPU/GPU placement, pull or delete models after explicit approval, probe
  a model with a tiny inference request, and persist a new PA or specialist
  default model to `.faith/system.yaml`.

```yaml
# system.yaml excerpt
pa:
  model: ollama/llama3:8b

openrouter:
  api_key: sk-or-...                 # set this to enable paid models

default_agent_model: ollama/llama3:8b
```

---

### 3.7 Event System

#### 3.7.1 Overview

FAITH uses a framework-wide event system built on the existing Redis message bus. Rather than the PA monitoring agent conversations to track state, all components — agents, tools, and the filesystem — publish structured state-change events to a dedicated `system-events` channel. The PA subscribes only to this channel and reacts when intervention is needed.

This means:
- The PA never reads inter-agent messages unless an event signals a problem.
- Agents do not need to tell other agents about file changes — file events handle that.
- Agent handoff messages shrink — semantic context only, no file lists or status polling.
- The PA's context window is freed from monitoring noise, dramatically reducing its token usage.

#### 3.7.2 Event Bus

All events are published to the `system-events` Redis channel. This channel is separate from agent communication channels. The PA subscribes to `system-events` at startup and maintains that subscription permanently. Individual agents may also subscribe to `system-events` for events relevant to their role (e.g. the QA agent subscribing to `tool:call_complete` for browser tool events).

#### 3.7.3 Event Format

Events are lightweight JSON messages, deliberately simpler than the full compact protocol:

```json
{
  "event": "agent:task_complete",
  "source": "software-developer",
  "channel": "ch-auth-feature",
  "ts": "2026-03-23T14:32:01Z",
  "data": {
    "task": "Implement JWT token refresh endpoint",
    "msg_id": 47,
    "files_written": 2
  }
}
```

Events do not carry full content — they carry state and references. The PA retrieves detail only if intervention requires it.

#### 3.7.4 Event Catalogue

**Agent state events:**

| Event | Published by | Meaning |
|---|---|---|
| `agent:task_complete` | Agent | Task finished successfully; PA can advance the session |
| `agent:task_blocked` | Agent | Cannot proceed — waiting on another agent, tool, or user |
| `agent:needs_input` | Agent | Waiting for specific information before continuing |
| `agent:error` | Agent | Unrecoverable error; PA investigates and intervenes |
| `agent:heartbeat` | Agent | Liveness ping published every 30s (configurable) |
| `agent:model_escalation_requested` | Agent | Current model insufficient; requesting upgrade |
| `agent:context_summary_triggered` | Agent | Rolling context summary has fired; context is being managed |

**Channel events:**

| Event | Published by | Meaning |
|---|---|---|
| `channel:stalled` | PA | No message activity on an active channel for configured timeout |
| `channel:goal_achieved` | Agent / PA | Session objective complete; PA can close the channel |
| `channel:loop_detected` | PA | Circular behaviour pattern detected (see Section 3.2.2) |

**Tool events:**

| Event | Published by | Meaning |
|---|---|---|
| `tool:call_started` | Tool MCP server | Tool invocation has begun |
| `tool:call_complete` | Tool MCP server | Tool call finished; result available |
| `tool:permission_denied` | Tool MCP server | Action blocked by approval rules; logged to audit |
| `tool:error` | Tool MCP server | Tool call failed |

**File events:**

| Event | Published by | Meaning |
|---|---|---|
| `file:changed` | Filesystem tool | Watched file or glob match has changed (SHA256 delta) |
| `file:created` | Filesystem tool | New file created within a watched path |
| `file:deleted` | Filesystem tool | File deleted within a watched path |

**Approval events:**

| Event | Published by | Meaning |
|---|---|---|
| `approval:requested` | PA | Action requires user decision; surfaced to Web UI |
| `approval:decision` | PA | User has responded; action proceeds or is blocked |

**Resource events:**

| Event | Published by | Meaning |
|---|---|---|
| `resource:token_threshold` | Agent | Token usage has crossed a configured warning level |
| `resource:token_critical` | Agent | Token usage approaching model context limit |

**System events:**

| Event | Published by | Meaning |
|---|---|---|
| `system:config_changed` | PA | Config file change detected and validated |
| `system:container_started` | PA | A managed container has started |
| `system:container_stopped` | PA | A managed container has stopped |
| `system:container_error` | PA | A managed container has crashed or failed to start |

#### 3.7.5 PA Subscription Model

```
PA subscribes to:
  system-events          ← all framework events

PA does NOT subscribe to:
  ch-auth-feature        ← agent communication channels
  ch-design-review
  ch-*

PA joins an agent channel only when:
  channel:stalled        → investigate why activity stopped
  agent:task_blocked     → determine blocker and resolve
  channel:loop_detected  → halt and surface to user
  agent:error            → diagnose and recover
  user requests it       → explicit user instruction
```

When the PA joins a channel to investigate, it reads only the most recent N messages (configurable, default 10) — not the full history. It queries the relevant agent directly for a structured status update rather than inferring state from conversation.

#### 3.7.6 Stall Detection

Stall detection combines two signals:

1. **Heartbeat absence:** if an agent misses 3 consecutive heartbeats (configurable), `agent:error` is published.
2. **Channel inactivity:** the PA maintains an in-memory timestamp of the last message received per active channel, updated on every Redis message. A 60-second background tick checks all active channels — if any channel's last-message timestamp exceeds the configured timeout (default: 5 minutes), the PA publishes `channel:stalled`. Tool call activity (tool:call_started / tool:call_complete events) resets the stall timer for the associated channel — an agent actively using a tool is not considered stalled.

On receiving either, the PA sends a direct structured query to the relevant agent:

```yaml
from: pa
to: software-developer
channel: ch-auth-feature
type: status_request
summary: "No activity detected for 5 minutes. What is your current status?"
needs: "Current task, blocker if any, estimated completion"
```

The agent responds with a structured status event. The PA acts on the status without needing to read the full channel history.

#### 3.7.7 File Watch Subscriptions

Agents declare file watch subscriptions in the agent's `config.yaml` (static) or the PA registers them dynamically at session start (session-scoped, cleared on session end):

```yaml
# agents.yaml — static subscriptions
agents:
  qa-engineer:
    file_watches:
      - pattern: "workspace/tests/**/*.py"
        events: [file:changed, file:created]
      - pattern: "workspace/src/**/*.py"
        events: [file:changed]
```

When a watched file changes, the filesystem tool publishes a `file:changed` event directly to the subscribing agent's channel. The agent acts on the change without waiting for a handoff message from the writing agent. The writing agent's handoff message carries only semantic context — *why* files changed and what is needed — never a list of files the recipient is already watching.

#### 3.7.8 Event Batching (Completion Accumulation)

When an agent (PA or specialist) is waiting on multiple concurrent sub-tasks or events, it **must not** invoke its LLM for each individual completion event. Instead, it accumulates incoming completion events and only triggers an LLM call once **all** pending tasks have reported (or a configurable timeout expires).

**Rationale:** Each LLM call consumes output tokens. If the PA dispatches 4 agents to work in parallel and reacts to each `agent:task_complete` individually, it wastes 3 intermediate LLM calls that serve no purpose — only the final call (when all results are available) produces a meaningful next action.

**Implementation rules:**

1. **Pending set:** When the PA (or any orchestrating agent) dispatches N sub-tasks, it records an expected-completions set of size N.
2. **Accumulate:** On receiving `agent:task_complete`, remove that agent/task from the pending set and buffer the result. Do **not** invoke the LLM.
3. **Act:** When the pending set is empty (all tasks complete), invoke the LLM with all buffered results in a single context. This produces one response covering all outcomes.
4. **Timeout:** If a configurable batch timeout (default: 10 minutes) expires with tasks still pending, act on what is available and treat missing tasks as potentially stalled (trigger stall detection for those).
5. **Partial action:** Some events are urgent and must not be batched — `agent:error`, `channel:stalled`, `channel:loop_detected`, and `approval:requested` always trigger immediate handling regardless of pending batches.

**Configuration in `.faith/system.yaml`:**
```yaml
event_batching:
  enabled: true
  batch_timeout_minutes: 10    # max wait for all completions
  immediate_events:            # never batched, always handled immediately
    - "agent:error"
    - "channel:stalled"
    - "channel:loop_detected"
    - "approval:requested"
```

#### 3.7.9 Token Efficiency Summary

| Old approach | Event-driven approach | Saving |
|---|---|---|
| PA reads all channel messages to track state | PA receives state-change events only | High |
| PA reacts to each task completion individually | PA batches completions, acts once all done | High |
| Agent A tells Agent B which files changed | File events notify Agent B directly | Medium |
| PA polls agents for status | Heartbeat absence triggers stall check | Medium |
| PA detects loops by reading conversation | Loop detection publishes event on trigger | Medium |
| Agent handoff includes full file lists | Handoff carries semantic context only | Low–Medium |

---

### 3.8 Living FRS Document

Each project managed by FAITH maintains a canonical `frs.md` file — a Functional Requirements Specification that evolves continuously as the project progresses. This is the single source of truth for all project requirements, decisions, and scope.

#### 3.8.1 Role in the System

- All specialist agents query `frs.md` via the RAG tool when they need requirements context — they never ask the PA to re-explain requirements.
- The PA updates `frs.md` when the user refines, adds, or corrects requirements through conversation.
- Agents reference specific sections of `frs.md` in their compact protocol messages via `context_ref` — no requirement is ever copied wholesale into a message.
- The file history system (Section 4.3.5) versions `frs.md` automatically — every change is recoverable.

#### 3.8.2 Workflow

The user discusses requirements with the PA in natural language — exactly as one would discuss a project with a business analyst. The PA:

1. Identifies whether the user input is a new requirement, a refinement, a correction, or a question.
2. Updates the relevant section of `frs.md` accordingly.
3. Publishes a `file:changed` event — any agent watching `frs.md` is notified automatically.
4. Determines which active tasks or agents are affected by the change.
5. Issues updated instructions to affected agents or creates new tasks as needed.

This mirrors real Agile practice: the FRS is a living backlog, the user is the product owner, and the PA is the delivery manager translating requirements into agent work.

#### 3.8.3 Structure

`frs.md` follows a consistent structure that all agents understand:

```markdown
# Project FRS — [Project Name]
## Last Updated: [timestamp] by PA

### Requirements
- REQ-001: [requirement text]
- REQ-002: [requirement text]

### Decisions
- DEC-001: [decision and rationale]

### Out of Scope
- [items explicitly excluded]

### Open Questions
- [unresolved items awaiting user input]
```

Requirements and decisions are numbered so agents can reference them precisely (`context_ref: frs/REQ-012`) rather than quoting the full text.

---

## 4. Tools & MCP Integration

### 4.1 MCP Adapter Layer

The PA provides a transparent adapter layer for agents whose assigned model does not natively support MCP.

**Behaviour:**
- Agents always make tool requests in the compact protocol format (`type: tool_call`, `tool: filesystem`, `action: read`, `args: ...`) regardless of their underlying model.
- The PA checks the model capability flag in the agent's `config.yaml` to determine whether the agent's model supports MCP natively.
- If yes: the PA forwards the call directly as an MCP request.
- If no: the PA translates the request into a structured prompt instruction, executes the tool call, and returns the result to the agent in the same compact protocol format.
- The agent never needs to know which path was taken — the translation is fully transparent.

**Design rationale:**
- All adapter logic lives in one place (the PA), not spread across agents.
- Swapping an agent's model requires no changes to agent prompts or behaviour.
- The adapter is stateless and mechanical — it performs format translation only, not reasoning. It does not consume meaningful PA context.
- As Ollama models gain native MCP support over time, the adapter becomes unused automatically — no changes required.

#### 4.1.1 PA Chat MCP Tool-Calling Loop

The interactive Project Agent chat path must expose enabled MCP tools to non-native
models such as local Ollama models. Having a filesystem MCP server running is not
enough by itself; the PA must bridge the browser-chat model turn into the tool
runtime.

**Behaviour:**
- The PA includes a compact MCP tool manifest in the Project Agent system context.
- For non-native models, the manifest describes the exact JSON shape the model must
  emit to request a tool call: `{"type": "tool_call", "tool": "...", "action": "...", "args": {...}}`.
- When the model emits a valid tool-call JSON object, the PA parses it, validates the
  requested tool/action, executes it through the existing MCP adapter/tool execution
  layer, and sends the structured tool result back to the model as the next turn.
- The PA streams visible tool-use progress to the Project Agent panel so the user can
  see that the agent is still working.
- The loop stops when the model returns a normal user-facing answer or when a bounded
  safety iteration limit is reached.
- In v1, the browser-chat loop must expose the filesystem MCP read/list/stat surface
  for Project Agent inspection. Additional tools can be added to the same manifest
  as their execution paths become safe for direct PA chat use.

#### 4.1.2 MCP Inventory Grounding

The PA must answer questions about available FAITH MCP servers and tools from a
canonical runtime inventory, not from general LLM knowledge.

**Behaviour:**
- In FAITH, MCP always means **Model Context Protocol**.
- The Project Agent prompt must explicitly prevent interpreting MCP as Microsoft
  Configuration Manager or any other unrelated acronym.
- The PA must expose a deterministic inventory surface for available chat-time MCP
  tools, including `mcp.list_tools`.
- When the user asks what MCP servers or tools are available to FAITH, the PA must
  answer directly from the canonical inventory without asking the LLM to improvise.
- The inventory answer must name the available tool actions, including the
  filesystem MCP server actions exposed to chat (`filesystem.read`,
  `filesystem.list`, and `filesystem.stat`).
- The answer must not invent placeholder servers such as "MCP Server 1" or tools
  that are not actually available.

---

### 4.2 Python Execution Tool

#### 4.2.1 Container Configuration

- Runs as a dedicated MCP server container.
- Container execution is the default path for Python execution.
- Internet access is **on by default** and can be toggled via `tools.yaml` (Docker network policy).
- Web scraping (BeautifulSoup, requests, etc.) is available whenever internet access is enabled.

#### 4.2.1.1 Host Routing Rule

FAITH is container-first for Python execution, but the PA should route directly to the optional host worker when the action clearly requires host-only context or host-only resources.

- The default execution target is a **disposable sandbox container** that the PA can create, destroy, and recreate at will.
- The executing agent has **root access inside that sandbox container** and may install Python packages, install OS packages, and modify the container freely.
- The PA should not waste time attempting container execution first when the target path, dependency, toolchain, or expected result is clearly host-bound.
- Examples include host-only paths outside the container mount set, host-installed toolchains, or workflows explicitly configured to run via the host worker.
- The routing decision is made by the PA before execution begins and should be reflected in approval/audit metadata.
- Host execution remains subject to host-worker enablement, allowed-path checks, and approval policy.
- Sandbox containers must not receive the Docker socket, must not run in privileged mode, must not use host networking, and must receive only explicitly approved mounts.
- Sandbox recovery defaults to **destroy and recreate**. The PA should prefer replacing a broken sandbox with a fresh instance over repairing an untrusted environment in place.
- When multiple sub-agents are active, the PA may either place them in a shared sandbox or assign isolated sandboxes per sub-agent based on isolation need, expected package/runtime conflicts, risk level, and current resource quotas.

#### 4.2.2 Package Management

- A base set of commonly used packages is pre-installed in the Docker image (standard library plus: numpy, pandas, requests, beautifulsoup4, playwright, and others defined at build time).
- Agents may install additional packages at runtime via `pip install`.
- **Check-before-execute rule** (enforced via agent system prompt): the agent must inspect its code for all required imports before execution, resolve any missing packages in a single `pip install` call, then execute. This prevents wasted tool calls and token usage.

#### 4.2.3 Execution Batching

- Agents are instructed via their system prompt to plan the full execution before invoking the tool.
- All steps that do not require an intermediate human decision or dynamic branch should be combined into a single script.
- Multiple tool calls are only used when the agent genuinely needs to inspect an intermediate result to decide what to do next.
- This minimises token usage and round-trip overhead.

#### 4.2.4 Filesystem Access

The Python execution container accesses only the workspace mounts explicitly assigned to the executing agent in the agent's `config.yaml` — the same permission model as the filesystem tool. No additional path access is granted by virtue of running inside the Python tool.

#### 4.2.5 Output Capture

All execution output is captured and returned to the agent:

- **stdout and stderr** are captured separately and returned in full.
- **Return values** from the executed script are captured if the script uses a conventional `result = ...` pattern.
- **File artefacts** written to an assigned mount are accessible to the agent via the filesystem tool after execution — the Python tool does not return file contents directly.
- **Execution errors** (exceptions, non-zero exit codes) are returned with full traceback to the agent for self-correction.

#### 4.2.6 Execution Timeout

- Default timeout: 60 seconds per execution call.
- Configurable per agent in the agent's `config.yaml` (`python_timeout_seconds`).
- On timeout: the process is killed, a `tool:error` event is published, and the agent is notified with a timeout message.
- Long-running tasks (data processing, model inference) should be split into checkpointed steps rather than relying on extended timeouts.

#### 4.2.7 Security Hardening Requirement

The Python execution tool is a high-risk surface and requires explicit security hardening, testing, and review as part of implementation.

- FAITH must treat the Python execution MCP server as security-sensitive infrastructure, not a convenience utility.
- The implementation must include dedicated security investigation of sandbox escape risks, package-install risks, filesystem boundary enforcement, resource exhaustion, and approval bypass paths.
- The implementation must include explicit security-focused tests in addition to functional tests.
- Dangerous or ambiguous operations must surface through FAITH's approval system rather than being silently permitted.
- Shipping the Python execution tool without a documented hardening/testing pass is not acceptable.

---

### 4.3 Filesystem Tool

The filesystem tool is a **security-first project workspace boundary**, not a general-purpose filesystem platform. It implements only the file operations FAITH needs while prioritising mount isolation, permission enforcement, deny-lists, symlink safety, auditability, and change detection.

#### 4.3.1 Mount Configuration

The user defines named mount points in `.faith/tools/*.yaml`, each mapped to a specific host folder. Agents reference mounts by name, never by raw host path. Docker volume mounts enforce boundaries at the OS level.

```yaml
filesystem:
  mounts:
    workspace:
      host_path: ~/projects/my-project
      access: readwrite
      recursive: true          # default — permissions apply to all subfolders
    outputs:
      host_path: ~/projects/my-project/outputs
      access: readwrite
    project-docs:
      host_path: ~/documents/specs
      access: readonly
      recursive: false         # opt-out — permission applies to this folder only, not children
    workspace/config:          # subfolder override example
      access: readonly         # overrides parent workspace readwrite for this subfolder
```

#### 4.3.2 Permission Resolution Rules

Permissions are resolved in the following order — most restrictive always wins:

1. **Specificity overrides:** Subfolder configuration overrides parent mount configuration (most specific path wins).
2. **Recursive default:** Permissions apply recursively to all child folders unless `recursive: false` is set on the mount.
3. **Agent cap:** An agent's permission for a mount (defined in the agent's `config.yaml`) cannot exceed the mount-level permission. If the mount is `readonly`, no agent can write to it regardless of their assigned permission.
4. **Most restrictive wins:** When mount-level and agent-level permissions both apply, the more restrictive of the two is enforced.

#### 4.3.3 Agent Mount Assignment

Each agent is assigned only the mounts it needs in the agent's `config.yaml`:

```yaml
agents:
  software-developer:
    mounts:
      workspace: readwrite
      outputs: readwrite
  fds-architect:
    mounts:
      project-docs: readonly
```

An agent cannot access any mount not explicitly assigned to it.

#### 4.3.4 Symlink Handling

- Symlinks that resolve to a path **within** the same mount are followed normally.
- Symlinks that resolve to a path **outside** the mount boundary are blocked — the tool returns a permission error. This prevents symlink-based escape from the defined mount scope.
- Broken symlinks are reported as errors, not silently ignored.

#### 4.3.5 File Size Limits

Maximum file size for read and write operations is configurable per mount in `.faith/tools/*.yaml`:

```yaml
filesystem:
  mounts:
    workspace:
      max_file_size_mb: 50    # default: 50MB
```

- Reads exceeding the limit return an error with the file size and limit — agents should use the Code Index Tool or RAG Tool for large files rather than loading them directly.
- Writes exceeding the limit are rejected before writing begins — no partial writes occur.
- Binary files (images, compiled artifacts) count toward the limit the same as text files.

#### 4.3.5 File History

The filesystem tool maintains a lightweight version history for every file it writes to, providing a safety net for recovering from agent errors or unintended modifications.

**File watching and events:**

The filesystem tool is the publisher of all `file:changed`, `file:created`, and `file:deleted` events in the FAITH event system (Section 3.7). It polls all actively subscribed paths every 5 seconds using SHA256 checksums for reliable change detection across all platforms. Only paths with active subscriptions are polled — the tool never watches paths nobody has registered interest in. See Section 3.7.7 for subscription configuration.

**Activation conditions for file history:**

- File history is **optional** and disabled by default.
- Enabled per mount in `.faith/tools/*.yaml` via `history: true`.
- If the mount's `host_path` is detected as being inside a git repository, file history is automatically skipped for that mount — git already provides superior version control and parallel versioning would create noise.

```yaml
filesystem:
  mounts:
    workspace:
      host_path: ~/projects/my-project
      access: readwrite
      history: true          # enable file history for this mount
      history_depth: 10      # number of versions to retain (default: 10, round-robin)
```

**Storage structure:**

Versions are stored under a `history/` folder in the FAITH project root, mirroring the mount path:

```
history/
└── workspace/
    └── src/
        └── auth.py/
            ├── v01.py         ← oldest (overwritten first when depth exceeded)
            ├── v01.meta.json
            ├── v02.py
            ├── v02.meta.json
            ...
            └── v10.py         ← most recent
            └── v10.meta.json
```

Each version has a metadata sidecar (`.meta.json`) recording:

```json
{
  "ts": "2026-03-23T14:32:01Z",
  "agent": "software-developer",
  "channel": "ch-auth-feature",
  "msg_id": 47,
  "audit_id": "aud-00341",
  "summary": "Implemented JWT token refresh endpoint"
}
```

This ties each file version directly back to the audit log entry and the agent task that produced it.

**Restoration:**

- The user asks the PA in natural language: *"restore auth.py to two versions ago"* or *"show me the history of auth.py"*.
- The PA queries the filesystem tool using the `list_history` and `restore_version` MCP commands.
- `list_history(path)` returns all available versions with timestamp, agent, and summary from the metadata sidecar.
- `restore_version(path, version)` copies the specified version back to the original path.
- The PA confirms the restore to the user and logs it as a write operation in the audit log.
- Restoration itself creates a new history entry — the round-robin is never unwound, only appended to.

**Depth and storage:**

- Default depth: 10 versions per file, configurable per mount.
- Round-robin: once depth is reached, the oldest version slot is overwritten.
- Binary files are versioned as-is; no diffing is applied — simplicity over efficiency.

---

### 4.4 Database Tool

#### 4.4.1 Named Connections

Multiple database connections are supported, each defined as a named connection in `.faith/tools/*.yaml`, mirroring the named mounts approach of the filesystem tool. Agents reference connections by name.

```yaml
database:
  connections:
    prod-db:
      host: db.internal
      port: 5432
      database: myapp
      user: agent_readonly
      access: readonly
    test-db:
      host: localhost
      port: 5432
      database: test_myapp
      user: agent_user
      access: readwrite
```

#### 4.4.2 Access Rules

1. **Read-only by default.** All connections default to read-only unless explicitly set to `readwrite` in `.faith/tools/*.yaml`.
2. **No name-based auto-grants.** FAITH never grants write access automatically based on the database name. Test databases may be configured `readwrite`, but they are not upgraded implicitly.
3. **Writes require two conditions.** A mutating query is permitted only when:
   - the connection is explicitly declared `access: readwrite` in `.faith/tools/*.yaml`, and
   - the user approves the mutating action through FAITH's approval system (or a remembered allow rule already covers it).
4. **Declared access remains authoritative.** If a connection is declared `readonly`, FAITH must classify and block mutating SQL before execution even if the external MCP server or underlying DB role would technically allow it.
5. **Agent cap.** An agent's permission for a connection (defined in the agent's `config.yaml`) cannot exceed the connection-level permission — the same layered model as the filesystem tool.

#### 4.4.3 Permission Validation

The MCP tool server runs a permission check on startup and on each new connection:

- It queries the actual database role permissions and compares them against the `tools.yaml` `access` setting for that connection.
- If the actual database permissions are **more permissive** than `tools.yaml` declares (e.g. `tools.yaml` says `readonly` but the DB user can write), the operation is cancelled and the user is alerted via the Web UI approval panel.
- The user may set a `permission_override: true` flag on the connection in `.faith/tools/*.yaml` to acknowledge the mismatch and proceed. This override only acknowledges the role mismatch; it does **not** bypass the declared `access` level and does **not** bypass per-action approval for writes.

```yaml
database:
  connections:
    prod-db:
      host: db.internal
      database: myapp
      user: agent_user        # misconfigured — has write access
      access: readonly
      permission_override: true   # user has acknowledged the mismatch
```

#### 4.4.4 Agent Connection Assignment

Each agent is assigned only the connections it needs in the agent's `config.yaml`:

```yaml
agents:
  software-developer:
    databases:
      test-db: readwrite
  fds-architect:
    databases:
      prod-db: readonly
```

#### 4.4.5 Query Result Limits

To prevent runaway data dumps from consuming agent context windows:

- Default row limit: 1,000 rows per query (configurable per connection in `.faith/tools/*.yaml` via `max_rows`).
- Default data size limit: 5MB per result set (configurable via `max_result_mb`).
- When a result is truncated, the agent receives the truncated result plus a `truncated: true` flag and the total row count — enabling it to refine the query rather than assume the result is complete.

#### 4.4.6 Query Logging

All queries — reads and writes — are logged to the audit log (Section 5.5) with: timestamp, agent, connection name, query text, row count returned, execution time in milliseconds, and whether the result was truncated. No separate query log file; the audit log is the single source of truth for all tool operations.

#### 4.4.7 Database Support

v1 supports PostgreSQL only. MySQL and SQLite are out of scope for v1 but the named connection architecture in `.faith/tools/*.yaml` is designed to accommodate additional drivers in future versions without structural changes.

---

### 4.5 Browser Automation Tool

**Library: Playwright (Python)**

- Async-native, fits FastAPI's async architecture without workarounds.
- Single Python package install; browsers managed via `playwright install` inside the Docker image — no external WebDriver binaries.
- Native screenshot and video capture for QA reporting.
- Chrome DevTools Protocol (CDP) access for network interception — useful for security agent analysis.
- Primary use case: AI-driven QA testing — agents execute test scenarios via the browser, capture screenshots at key steps, and generate a Confluence document with annotated screenshots and pass/fail results.
- No requirement for human-runnable local test scripts — all execution is agent-driven within the container.

**Browser target:** Chromium only in v1. Multi-browser support (Firefox, WebKit) is out of scope — Chromium covers all practical QA and scraping requirements and simplifies the container image significantly.

**Headed vs headless:** headless by default — required for container deployment. Headed mode is not supported in v1; agents interact with pages programmatically via Playwright's API, not visually.

**Confluence report generation:** the QA agent navigates to the target Confluence space using Playwright browser automation — no separate Confluence REST API integration is required. The agent logs in (credentials stored in `.faith/tools/*.yaml` under a `confluence` connection entry), navigates to the project space, creates a new page, and inserts test results and screenshots using Confluence's editor via browser interaction. This approach requires no Confluence API keys and works with any Confluence version (Cloud or Data Center) accessible via a browser.

---

### 4.6 Container Orchestration

#### 4.6.1 PA as Container Orchestrator

The PA is responsible for the lifecycle of all agent and tool containers. It uses the Docker Python SDK, accessed via a mounted Docker socket, to start, stop, and restart containers without user involvement. This is treated as a core PA responsibility, not a privileged operation requiring user approval.

The PA mounts `/var/run/docker.sock` from the host, giving it the ability to manage containers programmatically. **This grants the PA effective root access to the host.** This is explicitly disclosed to the user during the installation process and requires upfront acknowledgement before setup proceeds. The Docker socket is never exposed to specialist agents, MCP tool containers, or disposable sandbox containers — only the PA process interacts with it directly.

Sandbox containers may run as root inside the container, but that is acceptable only under strict isolation controls: no Docker socket mount, no privileged mode, no host networking, minimal required Linux capabilities, approved host mounts only, and no framework secrets mounted by default.

#### 4.6.2 Bootstrap Architecture

`docker compose up` starts a minimal set of always-on containers only:

```yaml
services:
  pa:
    build: ./containers/pa
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./config:/config:ro
    networks:
      - maf-network
  redis:
    image: redis:alpine
    networks:
      - maf-network
  web-ui:
    build: ./containers/web-ui
    networks:
      - maf-network

networks:
  maf-network:
    name: maf-network
```

After project config validation succeeds, the PA scans `.faith/agents/*/config.yaml` to discover the agent roster and reads `.faith/tools/*.yaml` for tool configuration. It then starts the project-scoped agent containers, the FAITH-owned tool containers required by the project, and the project-scoped `mcp-runtime` container when external registry-backed MCP servers are enabled. External MCP servers in v1 are launched as stdio subprocesses inside `mcp-runtime`, not as one-container-per-server services. The `docker-compose.yml` remains minimal and bootstrap-only. All specialist agent containers are built from the shared `agent-base` image — the PA differentiates agents via their `prompt.md`, model assignment, and tool permissions, not via separate Dockerfiles.

#### 4.6.3 Adding New Tools

- **New instance of an existing built-in tool type** (new filesystem mount, new code-index scope, etc.): user edits the relevant `.faith/tools/*.yaml`, and the PA hot-reloads the config into the existing runtime where possible. No container restart is needed unless mounts or ports change.
- **New external MCP server**: user adds a registration under `.faith/tools/` using the registry-based v1 flow. The PA validates the registration, ensures the project-scoped `mcp-runtime` container is available, installs the pinned package version, and launches the server as a stdio subprocess. No manual `docker compose` commands are required.
- **New FAITH-owned tool type**: this is a product/runtime addition, not a per-project ad hoc container definition. New FAITH-owned tool containers are introduced by framework updates, not by users dropping arbitrary Dockerfiles into the project.

#### 4.6.4 PA Restart and Crash Recovery

If the PA container restarts (graceful or crash), all managed agent containers, FAITH-owned tool containers, and the project-scoped `mcp-runtime` container continue running independently — Docker containers have no dependency on the process that started them. External MCP subprocesses inside `mcp-runtime` are re-synchronised from the current registrations when the PA reconnects.

**Redis persistence:** Redis is configured with AOF (Append-Only File) persistence enabled. This ensures the message bus state — channel history, published events — survives a PA crash. Without AOF, a PA crash would lose in-flight channel messages even though agent containers are still running.

**Recovery sequence on PA restart:**
1. PA re-reads all config files and re-validates schemas.
2. PA re-attaches to all running containers via the Docker SDK.
3. PA reads all `session.meta.json` files where `status != "complete"` — these are sessions that were active at crash time.
4. For each recovered session, the PA re-subscribes to `system-events` and the session's active task channels.
5. PA notifies the user in the Web UI: *"FAITH restarted. Session [sess-XXXX] recovered — [N] active tasks resumed. Please confirm you'd like to continue or close the session."*
6. User confirms resume or close for each recovered session.

Agent containers are unaware of the PA restart — they continue operating normally on their channels throughout.

---

### 4.7 Document RAG Tool

#### 4.7.1 Purpose

Prose documents (FDS specs, requirements, architecture documents, meeting notes) are too large to load into agent context wholesale. The RAG tool allows agents to retrieve only the relevant sections by semantic similarity search, dramatically reducing token usage.

#### 4.7.2 Technology

- **Vector store:** ChromaDB — Python-native, runs as a standalone MCP container, no external dependencies.
- **Embeddings:** Sentence-transformers (local, no API call required) — consistent with the framework's offline-capable design.

#### 4.7.3 Behaviour

- All documents in `workspace/docs/` are indexed automatically on startup and re-indexed whenever any file in that folder changes (via the file event system). No manual registration required — any file added to `workspace/docs/` is indexed automatically.
- Standard documents expected in `workspace/docs/`: `frs.md` (required), `architecture.md`, `api-spec.md`, `glossary.md` (all optional). Any additional documents placed here are indexed regardless of filename.
- Agents query the RAG tool with a natural language question or topic; the tool returns the most relevant chunks with source references.
- Agents cite the source chunk reference in their compact protocol messages (`context_ref`) rather than reproducing the content.
- Supported document types: PDF, DOCX, MD, TXT.

#### 4.7.4 Agent Usage Pattern

```yaml
type: tool_call
tool: rag
action: query
args:
  query: "rate limiting requirements for the auth API"
  top_k: 3
```

Response returns ranked chunks with document name, section heading, and page/line reference. The agent uses the reference, not the full text.

---

### 4.8 Code Index Tool

#### 4.8.1 Purpose

Agents should never need to load a full source file to understand codebase structure. The Code Index Tool maintains a live index of all source files, exposing function and class signatures, docstrings, and file paths on demand. This is deterministic lookup, not semantic search — agents get precise, reliable results.

#### 4.8.2 Technology

- **Parser:** `tree-sitter` — AST-based parsing, supports all major languages (Python, JavaScript, TypeScript, Java, Go, etc.), handles real-world code reliably.
- Runs as a dedicated MCP tool container, watching the workspace mount for file changes.

#### 4.8.3 Available Queries

| Query | Returns |
|---|---|
| `list_files` | Full file tree of the workspace |
| `list_symbols [file]` | All functions and classes in a file with signatures and line numbers |
| `list_symbols [module]` | All symbols across a module/directory |
| `get_function [name] [file]` | Full source of a specific function |
| `search_symbol [name]` | Find all occurrences of a symbol across the codebase |
| `describe_symbol [name]` | Signature + docstring only (no full body) |

#### 4.8.4 Agent Usage Pattern

Agents are instructed via their system prompt to:

1. Query the code index **before** writing any new function or class — to check if it already exists.
2. Use `describe_symbol` to get signatures without loading full function bodies.
3. Only use `get_function` when they need to read or modify the actual implementation.

This means a Dev agent reviewing a 2,000-line module pays ~100 tokens for the symbol list rather than ~12,000 tokens for the full file.

#### 4.8.5 Index Freshness

- The index is updated in real time as agents write files via filesystem event watching.
- Agents are aware the index may lag by seconds on rapid file changes — the system prompt instructs them to re-query after a write if symbol freshness is critical.

---

### 4.9 Pricing MCP Tool

#### 4.9.1 Purpose

Provides accurate, up-to-date LLM pricing data to the PA and Web UI for real-time cost tracking, session cost summaries, and proactive cost warnings. Pricing data is fetched from OpenRouter and cached locally rather than bundled statically, ensuring accuracy without manual FAITH updates.

#### 4.9.2 Default Price List

A baseline price list is committed to the FAITH repository at `data/model-prices.default.json`. This file:

- Contains pricing for all major OpenRouter models at time of FAITH release.
- Includes a `generated_date` field so users can see how old the bundled data is.
- Is used as fallback when no local cache exists and scraping is unavailable.
- Is updated by the FAITH maintainers with each release via a PR.

```json
{
  "generated_date": "2026-03-23",
  "source": "openrouter.ai/models",
  "models": {
    "anthropic/claude-sonnet-4-6": {
      "input_cost_per_token": 0.000003,
      "output_cost_per_token": 0.000015,
      "privacy_tier": "internal",
      "training_opt_out": true
    }
  }
}
```

#### 4.9.3 MCP Commands

| Command | Input | Output |
|---|---|---|
| `get_price` | `model_name` | Input/output cost per token, currency, data age |
| `calculate_cost` | `model_name`, `input_tokens`, `output_tokens` | Estimated cost |
| `list_models` | `privacy_tier` (optional) | All models matching tier with pricing |
| `refresh_prices` | — | Triggers fresh scrape; returns success/failure and timestamp |
| `price_age` | — | Timestamp of last successful scrape |
| `write_prices` | `data` (structured pricing JSON) | Validates and writes to local cache |

#### 4.9.4 Scrape and Cache Strategy

- On startup, the tool attempts to scrape OpenRouter's model list using Playwright.
- On success: parsed data is validated (realistic price ranges, minimum model count) and written to `data/model-prices.cache.json`.
- Periodic refresh: every 24 hours by default (configurable in `system.yaml`).
- If privacy profile is **Confidential**: scraping is skipped entirely. Bundled default is used. No outbound connection is made.
- If prices are stale beyond 7 days: PA surfaces a warning in the Web UI. User can trigger a manual refresh.

**Cache priority order:**
1. `data/model-prices.cache.json` (live scraped, if present and valid)
2. `data/model-prices.default.json` (bundled with FAITH release)

#### 4.9.5 PA-as-Fallback-Parser Pattern

If the structured scraper fails (page layout changed, new structure, unexpected content), the tool does not silently fall back — it attempts intelligent recovery via the PA:

1. Tool publishes `tool:error` with `reason: scrape_parse_failed` and `raw_content_available: true`.
2. Tool returns the raw page content to the PA alongside the error.
3. PA receives raw content and uses its LLM to extract pricing data semantically — understanding the page meaning rather than relying on fixed CSS selectors.
4. PA calls `write_prices(data)` with the parsed result.
5. Tool validates the data and writes it to cache. Normal operation resumes.

If the PA's parse also fails (page is fundamentally unrecognisable), the tool falls back to the cached or bundled price list and alerts the user that pricing data may be outdated.

**This pattern — tool fails structurally, PA interprets intelligently, writes result back — is a named resilience pattern in FAITH** applicable to any tool that scrapes or parses external content. It leverages the PA's LLM capability as an intelligent fallback parser rather than requiring brittle scraper maintenance.

---

### 4.10 CAG Store (Cache-Augmented Generation)

#### 4.10.1 Purpose

The RAG tool retrieves relevant chunks on demand — ideal for large, evolving documents. The CAG store complements it for **small-to-medium static reference documents** that agents access on almost every LLM call. Rather than paying retrieval cost repeatedly, the full document is pre-loaded into the agent's context at session start and kept there.

**Use CAG for:**
- Coding standards and style guides (referenced on every code generation call)
- Complete API specifications (needed in full, not in chunks)
- Database schema definitions (all agents need the full picture)
- Security policy documents (security agent needs this constantly)
- Library documentation for a specific dependency central to the project

**Use RAG for:**
- Large FRS documents and architectural specs (too large for context)
- Meeting notes and historical decisions
- Any document that changes frequently

#### 4.10.2 How It Works

CAG documents are pre-loaded into the agent's assembled context at session start, inserted between the Context Summary and Recent Messages:

```
[System Prompt]
[Role Reminder]
[Context Summary]     ← rolling summary from context.md
[CAG Documents]       ← pre-loaded static reference documents (new)
[Recent Messages]     ← last N compact protocol messages
[Current Task]
```

If the underlying model supports prompt caching (Claude API prefix caching, OpenAI cached prompts), the CAG section is stable across calls and the cache hit eliminates its token cost on subsequent calls. If the model does not support caching, the document is still prepended — cheaper than repeated RAG retrieval for frequently accessed documents.

#### 4.10.3 Configuration

CAG documents are declared per agent in the agent's `config.yaml`:

```yaml
agents:
  software-developer:
    cag_documents:
      - workspace/docs/coding-standards.md    # loaded at session start
      - workspace/docs/api-spec.md            # loaded at session start
    cag_max_tokens: 8000                      # total CAG budget per agent (default: 8000)
```

The PA validates at session start that all declared CAG documents exist and that their combined token count does not exceed `cag_max_tokens`. If they exceed the budget, the PA warns the user and suggests moving the largest documents to RAG instead.

#### 4.10.3.1 Project `cag/` Folder Convention

Each project should support an optional top-level `cag/` folder containing user-managed high-value reference documents. These are plain markdown or text files intended for stable facts, architecture notes, domain rules, coding conventions, or other small-to-medium reference material that the PA or specialist agents may need repeatedly.

Rules:

- The `cag/` folder lives at the project root alongside `src/`, `tests/`, and `.faith/`.
- Files in `cag/` remain normal user-owned project files, not a special binary store or database.
- The PA may read files from `cag/` when they are explicitly declared as CAG documents or when the user asks the PA to consult them.
- The PA may create or update files in `cag/` only when the user explicitly asks it to write or maintain reference material there.
- FAITH v1 must not silently rewrite or auto-summarise user-maintained `cag/` documents without an explicit user request.
- The existence of `cag/` does not replace RAG over `.faith/docs/`; it provides a curated, human-managed reference area for documents intended to be loaded directly into agent context.

#### 4.10.3.2 Automatic Project `cag/` Loading

FAITH v1 should treat the project-root `cag/` folder as a first-class default CAG source for the PA and any agent configured to inherit project reference context.

Requirements:

- On project load, the PA should scan the project-root `cag/` folder for supported markdown and text documents.
- Supported `cag/` documents should be included in CAG loading by default without requiring each file to be listed manually in agent config.
- The PA must estimate total token usage for the discovered `cag/` corpus before injecting it into agent context.
- If the discovered `cag/` corpus fits within the effective CAG budget, the PA should load it automatically.
- If the discovered `cag/` corpus exceeds budget, the PA must not silently discard documents. Instead it should report the largest contributors and suggest ways to reduce the footprint while preserving important information.
- Suggested reductions may include: creating a shorter curated summary document, splitting stable high-value rules from bulky background material, and moving lower-value documents to RAG instead of CAG.
- FAITH v1 should not perform lossy compression of user-maintained `cag/` files automatically. Any generated compressed or summarised replacement should require an explicit user request.
- When `cag/` files change, the PA should detect the change through the existing file-event path and reload the affected CAG material on the next relevant call.

#### 4.10.4 Freshness

- CAG documents are loaded once at session start.
- If a CAG document changes during a session (file event detected), the PA notifies the agent and reloads the document into its context on the next call.
- The agent receives a brief notification: *"[filename] has been updated and reloaded into your reference context."*

---

### 4.11 External MCP Server Integration

#### 4.11.1 Philosophy

FAITH does not rebuild integrations that already exist as mature, well-maintained MCP servers. For third-party services — version control, project management, team communication, browser automation, databases, web search, and optional RAG — FAITH connects to community or vendor-provided MCP servers where possible. FAITH only builds its own MCP tools for capabilities that are clearly FAITH-specific or too small/project-aware to justify an external dependency.

**FAITH-owned by default in v1:**

- **Filesystem** — path restriction, deny-lists, file watching, and audit-aware file operations are central to FAITH's security model.
- **Python Execution** — execution safety, approval enforcement, package policy, and sandbox behavior are too central to FAITH's trust model to leave entirely to a third-party tool.
- **Code Index** — project-aware codebase indexing is a core FAITH capability.
- **Key-Value Store** — small session-scoped state tool, tightly coupled to FAITH sessions.
- **Full-Text Search** — lightweight project-aware search utility.

**Not FAITH-owned MCP servers by default in v1:**

- PostgreSQL / database access
- Browser automation / Playwright
- Git
- Web search
- RAG

These may still be wrapped, constrained, or replaced later if external servers do not satisfy FAITH's approval, audit, privacy, or UX requirements.

**Important distinction:** CAG is not an MCP server. It is a `faith_pa` context assembly feature layered on top of file access.

#### 4.11.2 Registering External MCP Servers

For v1, FAITH keeps external MCP onboarding intentionally narrow: it uses a **self-hosted MCP Registry service** in the bootstrap Docker stack and installs servers from **registry references only**. The registry implementation should be the official open-source MCP Registry, run as a container rather than reimplemented in Python. Git URLs, local-path registrations, ZIP imports, and remote `streamable-http` servers are deferred until later phases.

**Supported source type in v1:**

- **`registry`** — a server is identified by MCP Registry metadata (`server.json`) resolved through the self-hosted registry service.

**Supported transport type in v1:**

- **`stdio`** — locally launched MCP servers only.

**Supported package form in v1:**

- **npm package-backed registry entries** — the registry entry resolves to an npm package, and FAITH installs/executes it internally. npm and Node.js are not host prerequisites; FAITH manages this inside its containerized runtime.

**Runtime model in v1:**

- External registry-backed MCP servers run inside a dedicated project-scoped `mcp-runtime` container managed by the PA.
- The PA installs pinned npm packages into that runtime and launches each external server as a stdio subprocess.
- FAITH-owned MCP tools remain dedicated containers where FAITH controls the security boundary directly.
- The PA does not require users to run npm or Node.js on the host.

FAITH stores registrations in project config and resolves them into concrete launch/runtime records. Example:

```yaml
external_mcp:
  github:
    source_type: registry
    registry_name: io.github.modelcontextprotocol/server-github
    package_type: npm
    package_identifier: "@modelcontextprotocol/server-github"
    transport: stdio
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}
    privacy_tier: internal
    agents: [software-developer, qa-engineer, security-expert]

```

**Registration flow:**

1. User adds a server via registry reference.
2. FAITH resolves the server metadata through the self-hosted MCP Registry service.
3. FAITH validates the npm package metadata, stdio launch settings, secrets requirements, and privacy tier.
3. FAITH stores the registration in project config.
4. The PA installs the pinned package into the project-scoped `mcp-runtime` container and launches the server on demand via stdio.

**Install / update policy in v1:**

- FAITH records the resolved registry name, npm package identifier, and an exact package version in project config. Floating `latest` installs are not permitted once a server is registered.
- Installing or updating an external MCP server always requires an explicit user action or an approved PA suggestion flow. The PA must not update external servers silently.
- Updates are manual and version-pinned: the user selects a target version, FAITH resolves it through the registry, validates metadata again, and stores the new pinned version only after confirmation.
- FAITH keeps the previously pinned version available until the replacement server has installed and started successfully, so rollback is possible if the new version fails startup.
- Audit logs must capture install, update, rollback, disable, and uninstall actions with registry name, package identifier, old version, and new version where applicable.
- User trust decisions are stored per external server registration. A blocked package should not be suggested again by the PA unless the user removes the block.

**Web UI configuration requirement:**

- Every registered external MCP server must have a dedicated configuration page in the Web UI.
- The page must show: server identity, registry reference, pinned package version, transport, privacy tier, assigned agents, required secrets/env vars, health, install status, and audit/trust state.
- The page must allow: enable/disable, change assigned agents, adjust privacy tier, update the pinned version, reinstall, rollback to the previously pinned version, and uninstall the server.
- Package installation, update, rollback, and uninstall actions must all surface confirmation and provenance details before execution.

**Deferred beyond v1:** git sources, local-path sources, ZIP imports, non-npm package ecosystems, and `streamable-http`.

**Design rule:** FAITH should align with the MCP standard rather than inventing a FAITH-only plugin protocol. FAITH adds orchestration, approvals, privacy checks, and lifecycle management on top of MCP; it does not replace MCP packaging or transport conventions.

#### 4.11.3 Privacy Tier Enforcement

Each external MCP server declares a minimum `privacy_tier`. If the active FAITH privacy profile is more restrictive than the server's tier, the server is not started and agents cannot access it. For example: a GitHub server marked `internal` is not available when the privacy profile is `confidential`.

#### 4.11.4 Recommended External MCP Servers

| Service | MCP Server | Purpose in FAITH |
|---|---|---|
| **GitHub** | `@modelcontextprotocol/server-github` | Issues, PRs, repo management, code review comments |
| **Atlassian Jira** | `@modelcontextprotocol/server-atlassian` | Ticket creation, sprint management, status updates |
| **Atlassian Confluence** | `@modelcontextprotocol/server-atlassian` | Document creation, page updates (supplements browser-based QA reports) |
| **Slack** | `@modelcontextprotocol/server-slack` | PA posts session summaries, approval notifications, task completions |
| **Linear** | Community MCP server | Alternative to Jira for issue tracking |
| **Notion** | Community MCP server | Alternative to Confluence for documentation |
| **Google Drive** | Community MCP server | Document access for teams using Google Workspace |
| **Sentry** | Community MCP server | Error tracking — security and QA agents can query live errors |

The PA's MCP adapter layer (Section 4.1) works transparently with external MCP servers — models that don't support MCP natively can still use GitHub, Jira, and Slack via the same translation mechanism.

#### 4.11.5 Confluence Note

The browser-based Confluence automation (Section 4.5) remains the primary method for QA report generation — it requires no API tokens and works with any Confluence version. The Atlassian MCP server is an optional complement for structured operations (ticket creation, page listing, status queries) where browser automation would be unnecessarily heavy.

---

### 4.12 Git Integration (External in v1)

#### 4.12.1 v1 Position

Git is **not FAITH-owned in v1**. FAITH should prefer an external/public local Git MCP server registered through the external MCP flow in Section 4.11, launched inside the project-scoped `mcp-runtime` container. The GitHub MCP server is not a substitute for local repository operations, so the selected external Git server must operate against the local repository on disk rather than only against remote hosting APIs.

If the external Git server later proves insufficient for FAITH's approval, audit, or UX requirements, FAITH may add its own local Git MCP server in a future version. That is not part of the v1 baseline.

#### 4.12.2 Expected Approval Policy For Git Operations

| Command | Description | Default approval tier |
|---|---|---|
| `status` | Working tree status | Ask first unless covered by remembered rule |
| `log` | Commit history | Ask first unless covered by remembered rule |
| `diff` | Show unstaged/staged changes | Ask first unless covered by remembered rule |
| `add` | Stage files | Ask first unless covered by remembered rule |
| `commit` | Create a commit | Ask first unless covered by remembered rule |
| `branch` | List / create / delete branches | Ask first unless covered by remembered rule |
| `checkout` | Switch branches | Ask first unless covered by remembered rule |
| `push` | Push to remote | `always_ask` |
| `pull` | Pull from remote | Ask first unless covered by remembered rule |
| `stash` | Stash / pop changes | Ask first unless covered by remembered rule |

Default rules are pre-populated in `security.yaml` at first run. Users can override via the standard remembered regex and path approval rules.

---

### 4.13 Key-Value Store Tool (Built-in)

#### 4.13.1 Purpose

Provides agents with a shared, session-scoped key-value store for fast lookups of established facts, cached decisions, and inter-agent shared state — without consuming context window space.

**Use cases:**
- Storing agreed decisions: *"auth_method = JWT_HS256"* — any agent can retrieve this without asking the PA
- Caching computed values: token counts, file hashes, resolved dependency versions
- Tracking task progress markers across a long session
- Sharing small structured data between agents without sending it through channel messages

#### 4.13.2 Implementation

Built on Redis (already in the stack). Keys are namespaced per session (`sess-{id}:{key}`) to prevent cross-session contamination. Keys expire when the session closes unless explicitly marked as persistent.

#### 4.13.3 Commands

| Command | Description |
|---|---|
| `set(key, value, ttl?)` | Store a value (optional TTL in seconds) |
| `get(key)` | Retrieve a value |
| `delete(key)` | Remove a key |
| `list(prefix?)` | List all keys matching an optional prefix |
| `exists(key)` | Check if a key exists |

---

### 4.14 Full-Text Search Tool (Built-in)

#### 4.14.1 Purpose

The Code Index Tool provides structured symbol lookup. The Full-Text Search Tool complements it with fast exact keyword and regex search across all text content — including comments, string literals, configuration files, and documentation — that the AST-based Code Index does not index.

**Use cases:**
- Finding all occurrences of an error message string in the codebase
- Searching comments for TODO/FIXME markers
- Finding configuration keys across YAML/JSON/TOML files
- Locating test fixtures containing specific data

#### 4.14.2 Implementation

Uses `ripgrep` (via subprocess) as the search engine — it is already available in most container base images, extremely fast, and supports full regex. Results are returned as structured JSON (file path, line number, matched line) rather than raw terminal output.

#### 4.14.3 Commands

| Command | Description |
|---|---|
| `search(pattern, path?, file_glob?)` | Regex search; optional path and file type filter |
| `search_literal(text, path?, file_glob?)` | Exact string search (no regex interpretation) |
| `search_files(filename_pattern)` | Find files by name pattern |

---

### 4.15 Web Search Tool (Built-in)

#### 4.15.1 Purpose

Allows agents to search the web for documentation, library APIs, error message solutions, security advisories, and dependency information — without launching a full browser session.

**Use cases:**
- Looking up a library's API when documentation isn't in `workspace/docs/`
- Searching for solutions to a specific error or exception
- Checking for known CVEs on a dependency
- Retrieving the latest version of a package

#### 4.15.2 Implementation

Uses the DuckDuckGo Instant Answer API (no API key required, privacy-respecting) as the default. An optional SerpAPI key can be configured in `.env` for richer results. Returns structured results (title, URL, snippet) — not full page HTML. For full page content, agents use the Browser tool.

**Privacy:** disabled automatically when the privacy profile is `confidential`. When `internal`, results are fetched but not cached externally.

#### 4.15.3 Commands

| Command | Description |
|---|---|
| `search(query, max_results?)` | Web search; returns titles, URLs, snippets |
| `search_docs(query, site?)` | Search restricted to a specific documentation domain |

---

## 5. Security & Approval System

### 5.1 Approval Rule System

#### 5.1.1 Ask-First Rules

All MCP actions default to **ask the user first** unless a prior user decision already covers the action. Rules in `security.yaml` are used to remember and re-apply prior decisions. Regex/path matching gives flexibility to approve or deny exact actions, directories, files, package identifiers, or broader patterns when the user explicitly chooses to persist that decision.

```yaml
# security.yaml excerpt
approval_rules:
  software-developer:
    always_ask:
      - "^filesystem:.*$"                       # ask by default unless remembered
    always_allow:
      - "^filesystem:read:E:/repos/myproj/docs/.*$"
    always_deny:
      - "^filesystem:write:E:/Windows/.*$"
```

**Decision model:**

| Decision | Behaviour |
|---|---|
| `always_ask` | Always surface to the user, even if a broader allow rule exists |
| `always_allow` | Execute silently and log the rule match |
| `always_deny` | Block silently and log the rule match |
| default | Ask the user first |

#### 5.1.2 PA-Managed Rule Updates

When an action is surfaced for approval, the approval panel offers these options:

- **Allow once** — executes this time only; not persisted as a long-term rule.
- **Approve for this session** — executes and is remembered in session memory until the session ends.
- **Always allow** — PA adds a persisted rule to `security.yaml`.
- **Always ask** — PA adds a persisted rule to `security.yaml` forcing future prompts for matching actions.
- **Deny once** — blocks this attempt only; not persisted as a long-term rule.
- **Deny permanently** — PA adds a persisted deny rule to `security.yaml`.

For persisted decisions, the rule is generated from the action and presented to the user before being written, so they can review or adjust it.

This means `security.yaml` builds itself organically through use, rather than requiring upfront manual configuration. The user can also edit `security.yaml` directly at any time — the PA hot-reloads the file and applies changes immediately.

The PA writes to `security.yaml` only in response to an explicit user approval action — never autonomously.

#### 5.1.3 Rule Precedence

Rules are evaluated in order: `always_ask` is checked first, then `always_deny`, then `always_allow`, then the default ask-first fallback. This ensures that a command matching both an allow and an ask rule always prompts — safety over convenience.

#### 5.1.4 Filesystem Path-Based Memory

Filesystem approvals are path-based and should remember the user's choice against the approved or denied target scope.

- The remembered scope may be an exact file, a directory, or a path/glob-style pattern.
- Each remembered filesystem decision stores both the target scope and the decision type.
- Supported decision types are:
  - allow once
  - approve for session
  - always allow
  - always ask
  - deny once
  - deny forever
- `allow once` and `deny once` are not persisted as long-term rules.
- `approve for session` is stored only in session memory.
- `always allow`, `always ask`, and `deny forever` are persisted and reused on future matching filesystem actions.

---

### 5.2 Config File Immutability

- All files under `config/` are mounted read-only (`:ro`) into agent containers at the Docker level.
- Agents cannot write config files under any circumstances — the filesystem permission enforces this, not just convention.
- When an agent identifies a config change is needed, it instructs the user via the PA (natural language explanation of what to change and why). The PA can open the relevant file in the user's preferred editor. The PA then watches for the change and confirms when it is detected.
- The PA is the only process that may write to `security.yaml`, and only in response to explicit user approval actions (Section 5.1.2).

---

### 5.3 Trust Levels

Each agent is assigned a trust level in the agent's `config.yaml`, but unmatched MCP actions still default to ask-first. Trust level is an orchestration signal, not a security bypass.

Trust level may influence:
- PA team-composition and role recommendations
- whether the PA suggests additional peer review for an agent's output
- UI warning emphasis and explanation wording
- future model-escalation or supervision recommendations

Trust level does **not** influence:
- approval fallback behaviour
- filesystem or database permissions
- host-worker access
- any other security boundary

---

### 5.4 Docker-Level Enforcement

- The FAITH framework `config/` directory (containing `secrets.yaml` and `.env`) is mounted **only into the PA container** and is **never** mounted into agent or tool containers. Agent containers have no filesystem path to secrets.
- **Filesystem MCP hard block:** The filesystem MCP server maintains a hardcoded deny list of paths that can never be read, written, or listed, regardless of any configuration: `config/secrets.yaml`, `config/.env`, and any path matching `**/secrets.yaml` or `**/.env`. This is a defence-in-depth measure — even if a mount misconfiguration were to expose the path, the tool would refuse the operation and log the attempt to the audit trail.
- Project `.faith/` YAML config files are readable by agents (they need tool config awareness), but are mounted read-only — agents cannot modify them.
- Filesystem tool path restrictions enforced at volume mount level — agents cannot access host paths outside defined mounts.
- Database integrations default to read-only. If a connection is explicitly configured `readwrite`, FAITH still requires per-action approval for mutating queries and must classify/block writes when the connection is not declared writable.
- The Docker socket is accessible only to the PA container — no agent container has access.

---

### 5.5 Audit Log

A dedicated audit log records all agent actions separately from conversation logs. The audit log is always on and cannot be disabled — it is a core safety feature, not an optional observability tool.

#### 5.5.1 What Is Logged

Every tool operation is recorded, including:

- All filesystem read and write operations
- All database queries
- All Python execution commands
- All browser automation actions
- All approval decisions (approved, denied, remembered-rule matches, unattended approvals)
- All PA container lifecycle actions (start, stop, restart)
- File history restoration events (see Section 4.3.5)

#### 5.5.2 Log Format

Audit entries are written as JSON lines to `logs/audit.log` — one JSON object per line. Structured format enables easy filtering, grep, and future Web UI surfacing.

`approval_tier` should use canonical values only. In v1 these are:
- `always_allow`
- `approve_session`
- `allow_once`
- `always_ask`
- `always_deny`
- `unattended`
- `unknown`

The `decision` field records the outcome separately as `approved` or `denied`.

```json
{
  "ts": "2026-03-23T14:32:01Z",
  "agent": "software-developer",
  "tool": "filesystem",
  "action": "write",
  "target": "workspace/src/auth.py",
  "approval_tier": "always_allow",
  "rule_matched": "^(write|read) workspace/src/.*$",
  "decision": "approved",
  "channel": "ch-auth-feature",
  "msg_id": 47,
  "audit_id": "aud-00341"
}
```

#### 5.5.3 Access and Retention

- The audit log directory is mounted read-only into all agent containers — agents cannot modify or delete audit entries.
- The PA appends to the audit log; no other process writes to it.
- Log rotation is configurable in `system.yaml` (default: retain 90 days). Older entries are archived, not deleted.
- The audit log is surfaced as a filterable view in the Web UI (Section 6).

---

### 5.6 Approval Response Options

Every approval request surfaced in the Web UI presents the user with six options:

| Option | Behaviour | Persists to `security.yaml`? |
|---|---|---|
| **Allow once** | Action executes once; will prompt again on next occurrence | No |
| **Approve for this session** | Action is remembered for the remainder of the current session only | No |
| **Always allow** | PA writes a learned `always_allow` rule to `security.yaml` | Yes — `always_allow_learned` section |
| **Always ask** | PA writes a learned `always_ask` rule to `security.yaml` so the action always prompts | Yes — `always_ask_learned` section |
| **Deny once** | Action is blocked once; will prompt again on next occurrence | No |
| **Deny permanently** | PA writes a learned `always_deny` rule to `security.yaml` | Yes — `always_deny_learned` section |

**UX rules:**

- The persisted options (`Always allow`, `Always ask`, `Deny permanently`) are visually de-emphasised from the session/one-shot options — they are durable actions and should feel deliberately chosen.
- Before writing any rule to `security.yaml`, the PA displays the exact regex that will be written and asks for confirmation. The user may edit the regex before it is saved.
- Learned rules are written to clearly separated sections in `security.yaml` (`always_allow_learned`, `always_ask_learned`, `always_deny_learned`), distinct from user-authored rules. They can be reviewed and revoked individually from the Web UI or by editing the file directly.

```yaml
# security.yaml — learned rules (PA-managed, user may edit or delete)
always_allow_learned:
  software-developer:
    - "^git commit.*$"          # learned 2026-03-23, always allow
    - "^pytest tests/.*$"       # learned 2026-03-23, always allow

always_ask_learned:
  software-developer:
    - "^git push.*$"            # learned 2026-03-23, always ask

always_deny_learned:
  software-developer:
    - "^rm -rf.*$"              # learned 2026-03-23, denied permanently
```

---

## 6. Web UI

### 6.1 Design Philosophy

The Web UI adopts a **terminal-inspired, multi-panel workspace** aesthetic — similar in feel to a tiling terminal multiplexer (tmux) or a trading terminal, but running in the browser. The guiding principles are:

- Each agent and tool has its own dedicated panel, giving the user direct visibility into what every component is doing simultaneously.
- The user controls their own layout — panels can be dragged, docked, resized, split, and tabbed freely.
- The UI is a thin shell over the backend — all intelligence lives in the agents; the UI only renders, routes input, and surfaces approvals.
- No build step, no npm. The entire frontend is served as static files by FastAPI, using CDN-loaded libraries.

---

### 6.2 Technology Stack

| Layer | Technology | Reason |
|---|---|---|
| Backend | FastAPI (Python) | Native async, WebSocket support, Jinja2 templates, zero-config static file serving |
| Templates | Jinja2 | Server-side HTML rendering for initial page load; no client-side routing needed |
| Panel Management | GoldenLayout (CDN) | IDE-grade dockable, draggable, resizable, tab-grouped panels; no npm required |
| Reactivity | Vue 3 ES Module (CDN) | Per-panel reactive state and WebSocket data binding; no build step in no-build mode |
| Terminal Rendering | xterm.js (CDN) | Full terminal emulator per panel — monospace output, ANSI colour codes, scrollback |
| Styling | Custom CSS | Dark terminal theme; monospace fonts; no UI framework dependency |
| Real-time Comms | FastAPI WebSockets | One WebSocket endpoint per agent/tool; panels subscribe independently |

**No npm. No Node.js. No build pipeline.** The Docker web-ui container runs only Python.

---

### 6.3 FastAPI Backend Architecture

#### 6.3.1 Responsibilities

- Serve the initial HTML shell and all static assets (JS, CSS).
- Expose a WebSocket endpoint per agent and per tool for streaming output.
- Expose a WebSocket endpoint for the approval queue.
- Expose a WebSocket endpoint for the system status feed.
- Handle HTTP endpoints for user input submission, file/image uploads, and approval actions.
- Relay messages from the user to the PA via the Redis message bus.
- Relay approval decisions from the user back to the PA.

#### 6.3.2 Endpoint Overview

| Endpoint | Type | Purpose |
|---|---|---|
| `GET /` | HTTP | Serve initial HTML shell |
| `GET /static/*` | HTTP | Serve JS, CSS, fonts |
| `POST /input` | HTTP | Submit user text message to PA |
| `POST /upload` | HTTP | Upload image or document to PA |
| `WS /ws/agent/{agent_id}` | WebSocket | Stream agent output to its panel |
| `WS /ws/tool/{tool_id}` | WebSocket | Stream tool activity to its panel |
| `WS /ws/approvals` | WebSocket | Push approval requests to UI |
| `WS /ws/status` | WebSocket | Push system status updates to UI |
| `WS /ws/docker` | WebSocket | Push Docker runtime and image updates to the Docker Runtime panel |
| `POST /approve/{request_id}` | HTTP | Submit approval decision |

#### 6.3.3 Message Relay Flow

```
User types message
       │
  POST /input
       │
  FastAPI → Redis pub (user-input channel)
       │
  PA picks up message, processes, delegates
       │
  PA/Agents → Redis pub (agent output channels)
       │
  FastAPI WebSocket workers subscribe to Redis
       │
  WS push to browser panels in real time
```

---

### 6.4 Panel System

#### 6.4.1 GoldenLayout Configuration

GoldenLayout manages the panel workspace. On first load, a default layout is rendered. The user can then freely rearrange panels, and the layout is persisted to `localStorage` so it survives page refreshes.

**Default layout on first load (illustrative):**

```
┌─────────────────────────────────────────────────────┐
│  [Project Agent]                              [+]   │  ← tab bar
├───────────────────────────────┬─────────────────────┤
│                               │                     │
│  Project Agent                │  Input              │
│  (xterm panel)                │  (text + upload)    │
│                               │                     │
├───────────────────────────────┴─────────────────────┤
│  Approvals                    │  System Status      │
│  (action panel)               │  (status panel)     │
└─────────────────────────────────────────────────────┘
```

This is intentionally minimal. Only the Project Agent is shown by default. Specialist agent panels must be created only after the PA has decided those agents are needed and has started them. The default layout must not assume a software-team workflow or pre-create Software Developer, QA, Security, or tool-specific panels before those runtimes exist.

**Implementation task anchors:**
- Minimal first-load layout requirements derive the implementation task for the default Project Agent/Input/Approvals/Status workspace.
- Runtime status card requirements derive the implementation task for replacing raw status JSON with readable FAITH-managed container cards.
- Panel lifecycle requirements derive the implementation task for close/reopen behaviour, singleton deduping, and runtime-identity deduping.
- Snap-grid layout requirements derive the implementation task for tidy dashboard-like movement and resizing behaviour.
- Panel title-bar requirements derive the implementation task for moving names into panel title bars and adding visible close controls.

**Supported user interactions:**
- Drag a panel by its header to a new position — other panels reflow.
- Drag a panel onto another to create a tab group.
- Resize any panel by dragging its border.
- Detach a panel to a floating window.
- Close a panel; re-open it from the `+` button or toolbar.
- Panel titles should live in the panel title bar rather than in a duplicated left-side label strip.
- Closable panels should expose a visible close affordance in the title bar, such as an `×` button in the top-right area.
- Reset layout to default via toolbar button.
- The UI must prevent accidental duplicate singleton panels. At minimum, Input, Approvals, and System Status must not be duplicated by repeated add-panel actions.
- Agent and tool panels must also avoid unbounded duplication for the same runtime identity. Attempting to add a panel that already exists for the same `agent_id` or `tool_id` should focus or reveal the existing panel rather than create a duplicate.
- Panel removal must be intentional and reliable. Closing a panel must remove it cleanly from the layout state, and reopening it through the toolbar must recreate exactly one instance.
- Dragging and resizing should feel structured rather than sloppy. v1 may use GoldenLayout docking alone, but a snap-to-grid style refinement for movement/resizing is preferred where practical so panels settle into tidy dashboard-like arrangements.

#### 6.4.2 Panel Types

**Agent Panel**

One panel per agent. Renders the agent's output as a terminal using xterm.js.

- Panel header shows: agent name, current status badge (`idle` / `active` / `blocked` / `waiting`), assigned model name.
- Output is streamed character-by-character from the agent's WebSocket feed.
- Supports ANSI colour codes — agent output can use colour to distinguish message types (tasks, responses, protocol messages, errors).
- Scrollback buffer retained in-browser (configurable depth).
- Compact protocol messages rendered in a dimmed colour; natural language in full brightness.
- Small toolbar per panel: `Clear`, `Copy`, `Pause stream`, `Pin to top`.

**Tool Panel**

One panel per MCP tool. Shows all commands sent to the tool and the tool's responses.

- Output format mirrors a shell session: command echoed, then output below.
- Commands awaiting approval are shown with a `[PENDING APPROVAL]` indicator.
- Completed commands show exit status (success / failure / timeout).

**Input Panel**

The primary user interaction area.

- Text input field (multi-line, expandable).
- Image paste support: paste from clipboard directly into the input area; image thumbnail shown inline.
- Document upload: drag-and-drop or file picker; accepted types: PDF, DOCX, TXT, MD, images.
- Send button + keyboard shortcut (`Ctrl+Enter`).
- Sent messages are echoed into the PA panel for continuity.

**Approval Panel**

Surfaces all pending approval requests from the PA in a queue.

- Each request card shows: requesting agent, action type, full command/action details, relevant context summary.
- Each request card offers six actions: `Allow once`, `Approve for session`, `Always allow`, `Always ask`, `Deny once`, `Deny permanently`.
- For persisted decisions, the UI previews the generated rule or remembered path/package scope before confirmation.
- Approved/denied cards are moved to a collapsible history section.
- Approval panel flashes / plays a subtle alert when a new request arrives.

**System Status Panel**

Live overview of the framework state.

- Agent status table: name, model, status, token count for current session.
- Active channels: channel name, participating agents, message count.
- Tool status: each MCP tool server — online / offline / error.
- Each MCP server must also have a dedicated configuration page in the Web UI where its project-level `.faith/tools/*.yaml` settings can be viewed and edited.
- Redis connection status.
- Session token usage and estimated cost (if paid models in use).
- Hot-reload indicator: shows when a config change is detected and propagated.
- The default visual presentation must be user-friendly rather than raw JSON. Status data should be rendered as readable cards or similarly compact UI elements.
- FAITH-managed Docker resources should be surfaced as one compact card per container showing at least: container name, role, running state (tick/cross or equivalent), health text when available, and a human-usable URL only when the service exposes one.
- On first load, the panel should prioritise the bootstrap containers that define whether FAITH is usable: Project Agent, Web UI, Redis, Ollama, and MCP Registry. Agent, tool, runtime, and sandbox container cards appear dynamically as those containers exist.

**Docker Runtime Panel**

Dedicated operational overview for FAITH-managed Docker resources.

- Shows running/stopped state for bootstrap containers, FAITH-owned tool containers, agent containers, sandbox containers, and the external MCP runtime container.
- Shows image name plus version tag or digest for each running or recently stopped container.
- Shows container role, health, created time, restart count, and owning session/task/sandbox where applicable.
- Surfaces image inventory relevant to the current project/session so the user can see exactly what versions FAITH is running.
- Updates in real time via a dedicated backend feed and remains read-only in v1.
- The status panel may show a compact subset of this information for quick operational awareness, while the dedicated Docker Runtime Panel remains the detailed operational view.

---

### 6.5 Real-Time Streaming

#### 6.5.1 WebSocket Strategy

Each panel that displays live data maintains its own WebSocket connection to the FastAPI backend. FastAPI subscribes to the corresponding Redis pub/sub channel on behalf of each connection and forwards messages to the browser.

```
Browser Panel (xterm.js)
      │  WebSocket /ws/agent/dev
FastAPI WebSocket Worker
      │  Redis SUBSCRIBE agent:dev:output
Redis
      │  PA / Agent publishes to agent:dev:output
Agent Container
```

This means:
- Panels update independently — the QA panel streaming does not block the Dev panel.
- If a panel is closed, its WebSocket closes and the Redis subscription drops cleanly.
- Reconnection is handled automatically by the Vue 3 component on connection loss.

#### 6.5.2 Streaming Format

Messages sent over the WebSocket are newline-delimited JSON:

```json
{"type": "output", "agent": "dev", "text": "Implementing JWT handler...\n", "ts": "2026-03-23T14:32:01Z"}
{"type": "status", "agent": "dev", "status": "active", "model": "llama3:8b"}
{"type": "approval_required", "request_id": "apr-042", "agent": "dev", "action": "run_command", "detail": "pytest tests/auth/"}
```

The Vue 3 component per panel dispatches each message to the appropriate handler — terminal output goes to xterm.js `write()`, status updates trigger reactive badge changes, approval events are forwarded to the Approval panel.

---

### 6.6 Terminal Aesthetic

The UI is styled to feel like a professional terminal environment:

- **Colour scheme:** Dark background (`#0d1117` or similar), with agent output in light grey. Distinct colours per agent (configurable) for easy visual separation in tab groups. ANSI standard colour palette for agent-generated colour codes.
- **Font:** Monospace throughout — `JetBrains Mono` or `Fira Code` loaded as a web font (self-hosted in the static directory to avoid external CDN dependency).
- **Panel borders:** Subtle, low-contrast dividers. GoldenLayout's default chrome is replaced with a minimal custom theme.
- **Status badges:** Small coloured dots (green = active, amber = waiting, red = blocked, grey = idle) — no text labels needed once the user is familiar.
- **Animations:** Minimal. Cursor blink in xterm panels. Subtle fade-in for new approval cards. No transitions that slow down perceived responsiveness.

---

### 6.7 Configuration Guidance UI

The UI does not provide in-browser config editing (agents are not permitted to write config; users edit config files directly). Instead, the UI surfaces configuration guidance:

- When an agent recommends a config change, the PA surfaces a **Config Guidance card** in the Approval panel area.
- The card describes the recommended change in plain language.
- A `Open in Editor` button triggers a FastAPI endpoint that opens the relevant config file in the user's preferred editor (configured in `system.yaml` — e.g. `code`, `notepad++`, `vim`).
- The PA watches the file for changes and confirms when the update is detected.

---

### 6.8 User Communication Model

The PA is the **sole interface between the user and all agents**. Users never communicate directly with specialist agents, join agent channels, or interact with tool outputs directly. All user input — questions, requirements, feedback, refinements, corrections — goes through the PA.

This applies regardless of session or task state:
- If a task is complete and the user wants to comment or refine, they tell the PA.
- If the user wants to contribute a technical idea to an ongoing task, they tell the PA, which injects it appropriately.
- If an agent needs clarification from the user, the PA relays the question and the answer.

The PA translates between natural language (user) and structured task orchestration (agents) in both directions. This keeps the PA in full control of session state and prevents users from accidentally disrupting active agent workflows by injecting messages mid-task.

---

### 6.9 Project Switcher

A project switcher dropdown in the Web UI toolbar lists recently used projects (paths stored in the framework's `config/recent-projects.yaml`). Selecting a project triggers the project switch flow defined in Section 2.5. The current session is closed gracefully before the new project loads. The user can also switch projects by telling the PA directly in natural language.

---

### 6.10 Layout Persistence

- The user's panel layout is serialised by GoldenLayout and saved to `localStorage` under a key namespaced to the framework (e.g. `faith_layout_v1`).
- On page load, the saved layout is restored automatically.
- If no saved layout exists, the default layout (Section 6.4.1) is rendered.
- A `Reset Layout` button in the toolbar clears `localStorage` and restores the default.
- Future consideration (v2): persist layout server-side per user profile for multi-device support.

---

### 6.9 Serving the UI

The web-ui Docker container runs a single FastAPI process:

```
web-ui-container
├── main.py              (FastAPI app, WebSocket workers, HTTP endpoints)
├── templates/
│   └── index.html       (Jinja2 shell — loads GoldenLayout, Vue 3, xterm.js from CDN or local static)
└── static/
    ├── css/
    │   └── theme.css    (terminal dark theme)
    ├── js/
    │   ├── app.js       (Vue 3 app — panel components, WebSocket logic)
    │   └── layout.js    (GoldenLayout initialisation and panel registration)
    └── fonts/
        └── JetBrainsMono.woff2
```

CDN libraries (`goldenlayout.js`, `vue.esm-browser.js`, `xterm.js`) can be pinned to specific versions and optionally vendored into `static/js/vendor/` for offline/air-gapped deployment. This decision is configurable at setup time.

---

### 6.11 Open Questions (Web UI)

1. **Offline CDN vendoring:** Should CDN libraries always be vendored locally (simpler offline support, slightly larger image) or loaded from CDN by default with a local fallback?
2. **Panel audio alerts:** Should the approval panel support an optional audio notification for new approval requests?
3. **Mobile responsiveness:** Out of scope for v1 (per Section 1.4), but should the layout degrade gracefully on small screens rather than break entirely?
4. **Authentication:** No authentication is planned for v1 (local-only deployment). Should there be an optional simple password gate for users who expose the port beyond localhost?

---

## 7. Configuration Management

### 7.1 Config File Overview

Configuration is split between the **framework installation** (global, shared across projects) and the **project workspace** (per-project, portable).

#### 7.1.1 Framework-Level Config

| File | Purpose | Writable by | Accessible to agents? |
|---|---|---|---|
| `config/secrets.yaml` | All credentials — API keys, DB passwords, tokens | User only | **Never** — hard-blocked by filesystem MCP |
| `config/.env` | Environment variables referenced by `secrets.yaml` | User only | **Never** |
| `config/archetypes/*.yaml` | Role archetype templates for agent creation | User (custom), FAITH (built-in) | No |

#### 7.1.2 Project-Level Config

All project config lives in `.faith/` within the project directory. These files are safe to commit to the project's git repository (no secrets).

| File | Purpose | Writable by |
|---|---|---|
| `.faith/system.yaml` | Project settings — PA model, privacy profile, editor, loop detection | User, PA |
| `.faith/security.yaml` | Approval rules, trust levels, learned rules for this project | User, PA (learned rules only) |
| `.faith/tools/filesystem.yaml` | Filesystem mount definitions, permissions, history | User only |
| `.faith/tools/python.yaml` | Python tool — internet toggle, timeout | User only |
| `.faith/tools/database.yaml` | Database connections — names and access levels (secrets referenced by key) | User only |
| `.faith/tools/browser.yaml` | Browser tool — headless mode, viewport | User only |
| `.faith/tools/{tool}.yaml` | Additional tool configs (Confluence, external MCP servers, etc.) | User only |
| `.faith/agents/{id}/config.yaml` | Agent definition — model, tools, trust, file watches, listen tags | PA (generates), User (may edit) |
| `.faith/agents/{id}/prompt.md` | Agent system prompt — role definition, behavioural instructions | PA (generates), User (may edit) |

The PA is the primary author of agent `config.yaml` and `prompt.md` files. It generates these during project setup and updates them when the team evolves. The user may edit any file directly; the PA hot-reloads changes.

---

### 7.2 Schema Validation

Each YAML config file has a corresponding JSON Schema bundled with the FAITH installation (e.g. `faith/schemas/system.schema.json`, `faith/schemas/agent-config.schema.json`). Validation is enforced by the PA on every config change detection and on startup.

#### 7.2.1 Validation Behaviour

1. PA detects a file change via the config watcher (SHA256 delta on `.faith/**/*.yaml`).
2. PA loads the new file and validates it against its schema.
3. **If valid:** new config is applied. Hot-reload handler for that file fires (see Section 7.3). A `system:config_changed` event is published.
4. **If invalid:** PA does NOT apply the change. Previous valid config remains active. PA reports the error to the user via the Web UI in plain English with the specific field and a suggested fix.

**Example error surfaced to user:**
> *Config error in agent `software-developer` config.yaml: the `trust` field must be one of `high`, `standard`, or `low`. You entered `medium`. Previous configuration remains active.*

Raw schema validation errors are never shown to the user — the PA always translates them.

#### 7.2.2 Startup Validation

On project load, all config files in `.faith/` are validated before any project-scoped runtime is started. If any file is invalid, the PA reports the error in the Web UI with instructions for fixing the issue. The bootstrap control plane may remain running (PA, Web UI, Redis, Ollama, MCP Registry), but no project-scoped agent containers, FAITH-owned tool containers, or external MCP sessions are started until the project config is valid.

---

### 7.3 Hot-Reload Handlers

The PA watches all YAML files in `.faith/` and `config/` using SHA256 polling. Each file type has a dedicated change handler. Changes take effect immediately with no container restarts unless explicitly noted.

#### 7.3.1 Project-Level Config (`.faith/`)

| File changed | Handler actions |
|---|---|
| `.faith/system.yaml` | Reload project settings; update PA model config; reload editor preference; update loop detection params. **If privacy profile changes:** PA checks all active agent model assignments against the new profile. For each non-compliant assignment, PA surfaces a card in the Web UI listing the agent, its current model, and the compliance issue. User chooses per agent: switch to a compliant model now, or acknowledge and continue with override. No automatic forced reassignment — user always decides. Active tasks are not interrupted; model changes take effect on the next LLM call. |
| `.faith/security.yaml` | Reload all approval rules immediately; takes effect on the next approval request; no agent notification required |
| `.faith/tools/*.yaml` | Diff against running state per tool: register new mounts/connections with existing runtimes; start or reconfigure project-scoped tool containers when needed; install/start/stop external MCP registrations inside `mcp-runtime`; update permission rules; toggle internet access on Python tool |
| `.faith/agents/*/config.yaml` | Diff against running state: start containers for new agents; stop containers for removed agents; update model/tool/watch assignments for existing agents in place |
| `.faith/agents/*/prompt.md` | PA detects the change, publishes `system:config_changed`; the updated prompt is automatically loaded on the agent's next LLM call (prompts are read fresh per call). PA surfaces a notification in the Web UI agent panel: *"[Agent name] system prompt updated — will take effect on next message."* |

#### 7.3.2 Framework-Level Config (`config/`)

| File changed | Handler actions |
|---|---|
| `config/secrets.yaml` | Reload all credentials; hot-apply to active tool connections (DB reconnects if credentials changed); PA notifies user in Web UI: *"Credentials updated and applied."* |
| `config/.env` | Reload environment variables; re-resolve `${VAR}` references in `secrets.yaml`; same effect as changing `secrets.yaml` directly |
| `config/archetypes/*.yaml` | Reload archetype library; no effect on existing agents (archetypes are used only at agent creation time) |

**Restart required only for:** Docker volume mount changes (new host folders), port changes, changes to the Docker network configuration.

---

### 7.4 User-Preferred Editor Integration

The user's preferred editor is configured once in `system.yaml`:

```yaml
editor: code          # VS Code
# editor: notepad++
# editor: vim
# editor: nano
```

When the PA needs the user to edit a config file (e.g. following a config guidance card in the Web UI), it calls a FastAPI endpoint that opens the specified file in the configured editor using a platform-appropriate shell command. The PA then watches the file for changes via the event system and confirms in the Web UI when the change is detected and validated.

If no editor is configured, the Web UI displays the file path and the user opens it manually.

---

### 7.5 YAML Schema Reference

Key schema constraints for each config file. Full schemas are maintained in `config/schemas/`.

#### `.faith/system.yaml`

```yaml
privacy_profile: public | internal | confidential   # required
pa:
  model: string                                      # required
  fallback_model: string                             # optional
default_agent_model: string                          # required
editor: string                                       # optional
loop_detection:
  enabled: boolean                                   # default: true
  window_messages: integer                           # default: 10
  state_repeat_threshold: integer                    # default: 2
log_retention_days: integer                          # default: 90
```

#### `config/secrets.yaml` (framework-level)

```yaml
openrouter_api_key: string                           # optional — ${OPENROUTER_API_KEY}
github_token: string                                 # optional
confluence_password: string                          # optional
databases:
  <connection-id>:
    host: string                                     # required
    port: integer                                    # default: 5432
    user: string                                     # required
    password: string                                 # required — ${VAR} reference
```

#### `.faith/agents/{id}/config.yaml`

```yaml
name: string                                         # required
role: string                                         # required — human-readable description
model: string                                        # optional — overrides default_agent_model
trust: high | standard | low                         # default: standard
tools: [string]                                      # list of permitted tool IDs
databases:
  <connection-id>: readonly | readwrite              # references secrets.yaml by key
mounts:
  <mount-id>: readonly | readwrite
file_watches:
  - pattern: string
    events: [file:changed, file:created, file:deleted]
context:
  summary_threshold_pct: integer                     # default: 50
  max_messages: integer                              # default: 50
listen_tags: [string]                                # compact protocol tag filter
```

#### `.faith/tools/filesystem.yaml`

```yaml
mounts:
  <mount-id>:
    host_path: string                                # required
    access: readonly | readwrite                     # required
    recursive: boolean                               # default: true
    history: boolean                                 # default: false
    history_depth: integer                           # default: 10
    <subfolder-path>:
      access: readonly | readwrite                   # subfolder override
```

#### `.faith/tools/python.yaml`

```yaml
internet_access: boolean                             # default: true
timeout_seconds: integer                             # default: 60
```

#### `.faith/tools/database.yaml`

```yaml
connections:
  <connection-id>:
    secret_ref: string                               # required — key in secrets.yaml
    database: string                                 # required
    access: readonly | readwrite                     # required
    permission_override: boolean                     # default: false
    max_rows: integer                                # default: 1000
```

#### `.faith/tools/browser.yaml`

```yaml
headless: boolean                                    # default: true
```

#### `.faith/tools/confluence.yaml`

```yaml
url: string                                          # Confluence base URL
secret_ref: string                                   # key in secrets.yaml for credentials
default_space: string                                # default space key for report creation
```

#### `.faith/security.yaml`

```yaml
approval_rules:
  <agent-id>:
    always_ask: [string]                             # regex list
    always_allow: [string]                           # regex list

trust_overrides:
  <agent-id>: high | standard | low

# PA-managed sections — do not edit manually
always_allow_learned:
  <agent-id>: [string]

always_ask_learned:
  <agent-id>: [string]

always_deny_learned:
  <agent-id>: [string]
```

---

### 7.6 Config Migration

When a new version of FAITH introduces breaking schema changes:

1. The PA detects a schema version mismatch on startup.
2. Before starting any containers, the PA displays a migration guide in the Web UI — listing each changed field, the old format, the new format, and the reason for the change.
3. The user is offered an **Auto-migrate** option: the PA rewrites the affected config files to the new schema, creating backups of the originals (`config/system.yaml.bak-v1.2`).
4. If the user prefers to migrate manually, the PA waits, validating on each save until all files pass the new schema.
5. No migration runs silently — the user always sees what changed and why.

---

## 8. Logging & Observability

### 8.1 Log Types Overview

FAITH maintains four distinct log types, each with a clear purpose, format, and location. No two log types duplicate the same information.

| Log | Location | Format | Purpose |
|---|---|---|---|
| **Audit log** | `logs/audit.log` | JSON lines | Every tool action, approval decision, container event |
| **Event log** | `logs/events.log` | JSON lines | All system events from the `system-events` channel |
| **Session logs** | `logs/sessions/sess-XXXX-YYYY-MM-DD/` | Markdown + JSON | Conversation history, task metadata |
| **Token log** | `logs/tokens.log` | JSON lines | Per-call token counts, cost estimates, model used |

---

### 8.2 Audit Log

Defined in full in Section 5.5. Records every tool operation, approval decision, container lifecycle event, and file history restoration. Always on, append-only, agents have no write access.

---

### 8.3 Event Log

Records all events published to the `system-events` channel (Section 3.7). Complements the audit log — where the audit log records *what agents did*, the event log records *the state changes that drove PA decisions*.

```json
{"ts": "2026-03-23T14:32:01Z", "event": "agent:task_complete", "source": "software-developer", "channel": "ch-auth-feature", "data": {"task": "JWT token refresh endpoint", "msg_id": 47}}
{"ts": "2026-03-23T14:35:10Z", "event": "channel:stalled", "source": "system", "channel": "ch-auth-feature", "data": {"idle_seconds": 312}}
{"ts": "2026-03-23T14:35:15Z", "event": "agent:task_blocked", "source": "qa-engineer", "channel": "ch-auth-feature", "data": {"reason": "Waiting for security review sign-off"}}
```

Retention follows the same policy as the audit log (`log_retention_days` in `system.yaml`).

---

### 8.4 Session Logs

Session logs capture the full human-readable record of all conversations and tasks. Structure defined in Section 3 discussions:

```
logs/sessions/
└── sess-0042-2026-03-23/
    ├── session.meta.json              ← session metadata (JSON — read by PA on restart)
    ├── pa-user.log                    ← PA↔user conversation (markdown, full session)
    └── tasks/
        └── task-001-143201.456/
            ├── task.meta.json         ← goal, agents, channels, start/end timestamps
            ├── ch-auth-feature.log    ← channel conversation, time-ordered (markdown)
            └── pa-software-developer.log  ← direct PA↔agent assignments (markdown)
```

**`session.meta.json` fields:**
```json
{
  "session_id": "sess-0042",
  "started": "2026-03-23T14:30:00Z",
  "ended": "2026-03-23T18:45:00Z",
  "privacy_profile": "internal",
  "task_count": 3,
  "agents_active": ["software-developer", "qa-engineer", "security-expert"],
  "total_input_tokens": 48200,
  "total_output_tokens": 12400,
  "total_estimated_cost": 0.87
}
```

**Channel log format (markdown):**
```markdown
# Channel: ch-auth-feature
# Task: task-001 — Implement JWT auth module
# Started: 2026-03-23T14:32:00Z

---
**[14:32:01] software-developer → qa-engineer**
type: review_request | status: complete
summary: "auth module done, 3 endpoints, JWT httponly cookies"
needs: "test coverage for token expiry edge case"

---
**[14:45:22] qa-engineer → software-developer**
type: feedback | status: in_progress
summary: "token expiry tests written, one edge case found in refresh logic"
```

**Agent cross-reference index** (no content duplication):
```
agents/software-developer/sessions.index.md
```
Lists sessions and tasks the agent participated in with links to the relevant session log directories.

---

### 8.5 Token and Cost Log

Every LLM API call is recorded in `logs/tokens.log` as a JSON line:

```json
{
  "ts": "2026-03-23T14:32:01Z",
  "session_id": "sess-0042",
  "task_id": "task-001-143201.456",
  "agent": "software-developer",
  "model": "ollama/llama3:8b",
  "input_tokens": 1240,
  "output_tokens": 380,
  "estimated_cost": 0.00,
  "price_source": "cache",
  "price_age_days": 1
}
```

For paid models, `estimated_cost` is calculated by the Pricing MCP Tool (Section 4.9) using the current cached price data. `price_source` and `price_age_days` are recorded so cost estimates can be re-evaluated if prices change.

**Real-time status panel** displays for the current session:
- Running token count per agent
- Running estimated cost (paid models only)
- Model currently assigned to each agent

**Proactive cost warning:** when session estimated cost crosses a configurable threshold (default: $1.00 in `system.yaml`), the PA surfaces a warning in the Web UI and suggests which agent is driving the most cost, with the option to switch it to a cheaper model.

---

### 8.6 Log Retention and Rotation

Configured in `system.yaml`:

```yaml
log_retention_days: 90       # audit, event, token logs older than this are archived
session_retention_days: 365  # session logs retained for 1 year by default
```

- Logs older than the retention threshold are moved to `logs/archive/` — never deleted automatically.
- Archive can be manually cleared by the user.
- The PA surfaces a notification when the archive exceeds a configurable size threshold (default: 1GB).

---

### 8.7 Web UI Log Views

The following log views are available in the Web UI:

| View | Source | Features |
|---|---|---|
| **Audit trail** | `audit.log` | Filter by agent, tool, action, date range; search by command |
| **Event timeline** | `events.log` | Chronological event stream; filter by event type or agent |
| **Session history** | `logs/sessions/` | Browse sessions and tasks; open channel logs as read-only panels |
| **Token usage** | `tokens.log` | Per-agent token chart; cumulative cost by model; session comparisons |
| **Approval history** | `audit.log` (filtered) | All approve/deny decisions with full context |

---

## 9. Setup & Deployment

### 9.1 Prerequisites

- Python 3.10+ (required on host — users install this prerequisite themselves so `faith-cli` can run. Containers use Python 3.13 internally.)
- Docker and Docker Compose (required)
- Git (optional — enables automatic file history skip for git-managed workspaces)
- Ollama is included in the FAITH bootstrap stack by default as a Docker service. Advanced users may disable it in the first-run wizard or point FAITH at an existing external Ollama instance instead.
- OpenRouter API key (optional — required for paid models and recommended PA model)
- Source checkouts may use the repository bootstrap helpers before `faith init`:
  - `setup.sh` for Ubuntu/Debian hosts or Linux VMs. Installs Docker Engine, Docker Compose plugin, Python 3, and Git.
  - `setup.ps1` for Windows hosts. Installs Python and attempts Docker Desktop only when a supported backend is available; if WSL/Hyper-V style backends are unavailable, it exits with guidance to use a Linux VM instead.

No other host dependencies. Node.js, npm, and MCP registry infrastructure are encapsulated inside Docker containers.

### 9.2 Installation & CLI

FAITH is installed and managed via the `faith` CLI — a lightweight Python package that handles Docker lifecycle and communicates with the running PA.

#### 9.2.1 Installation

```bash
pip install faith-cli
faith init
```

For source checkouts, host prerequisites may be bootstrapped first with:

```bash
sudo ./setup.sh
```

or on Windows:

```powershell
pwsh -File .\setup.ps1
```

1. `pip install faith-cli` installs the `faith` command on PATH. The package is lightweight (~50KB) and bundles the canonical bootstrap `docker-compose.yml`, config templates, and archetype files. No framework code — just a Docker Compose wrapper and HTTP client.
2. `faith init` performs first-time setup:
   - Verifies Python 3.10+ is available for `faith-cli`. If Python is missing, the user is instructed to install it first; FAITH does not attempt to bootstrap Python itself.
   - Checks Docker and Docker Compose are installed and the daemon is running. If Docker is missing, prints a clear install link and exits. If Docker is installed but the daemon/Desktop is not running, the CLI must detect the host OS and print the most appropriate recovery instruction instead of a generic failure: on Windows and macOS, tell the user to start Docker Desktop; on Linux, show the expected service-start command for common systemd setups and note that distro-specific alternatives may apply. In all cases, instruct the user to rerun `faith init` after Docker is running.
   - The Docker-daemon-not-running behaviour is intentionally manual in v1. FAITH must not try to auto-start Docker Desktop, start system services, or trigger elevation prompts; it should explain the OS-specific recovery step and stop.
   - Checks Git is installed (optional — warns if absent but continues).
   - Creates the framework home directory (`~/.faith/`) with `config/`, `data/`, `logs/`, and extracts bundled files (`docker-compose.yml`, config templates, archetypes).
  - Pulls pre-built Docker images from the container registry (PA, web-ui, Redis, Ollama, MCP Registry, and other bootstrap dependencies).
   - Runs `docker compose up -d`.
   - Opens browser to `http://localhost:8080` → first-run wizard (Section 9.3).
   - Returns when wizard completes, or user can Ctrl+C to background it.

If `~/.faith/` already exists, `faith init` detects this and offers to re-initialise (with backup) or abort.

#### 9.2.2 CLI Commands

| Command | Description |
|---|---|
| `faith init` | First-time setup: create `~/.faith/`, pull images, start containers, open wizard |
| `faith start` | Start containers (if not already running), open browser to Web UI |
| `faith stop` | Coordinated shutdown: PA saves `state.md` per agent, then `docker compose down` |
| `faith restart` | Stop then start |
| `faith run "<prompt>"` | Send an ad-hoc task to the running PA (see Section 9.6) |
| `faith run --skill <name>` | Execute a skill from `.faith/skills/` (see Section 9.6.2) |
| `faith status` | Show running state, active project, agent count, container health |
| `faith show-urls` | Query service route manifests and list currently exposed HTTP/WebSocket endpoints with brief descriptions |
| `faith update` | Pull latest images, validate config compatibility, restart containers |
| `faith help` | Show help and available commands |

**`faith start`** checks whether containers are already running before calling `docker compose up -d`. If this is the first run (no `config/secrets.yaml`), it redirects to `faith init`.

**`faith stop`** sends a graceful shutdown signal to the PA via `POST /api/shutdown`, which triggers coordinated teardown (save `state.md` per agent, stop managed containers), then runs `docker compose down`.

**`faith update`** replaces the previous `run.sh --update` / `run.ps1 --update` approach. It pulls the latest images, validates config/schema/API compatibility using the versions published by `faith_shared`, and restarts. If breaking config changes are detected, the user is guided through migration before restart.

#### 9.2.3 Optional Host Worker

FAITH supports an optional persistent **host worker** for actions that should execute directly on the user's machine rather than inside Docker. This worker is not the default execution path; the default remains containerized execution.

- The host worker is launched and supervised by `faith-cli`, not by the PA container directly.
- The host worker runs with the **same user privileges** as the user who launched `faith-cli`.
- The host worker is **not** root/admin by default. Operations requiring elevation are handled as explicit elevated actions later; the persistent worker itself remains user-scoped.
- The wizard may enable or disable the host worker and configure which host paths are exposed to it.
- All host-worker actions are audited and remain subject to approval policy.
- When both container execution and host execution are available, the PA should choose the correct boundary up front rather than attempting container execution first for obviously host-only work.

### 9.3 First-Run Wizard

On first run, the PA detects no configuration exists and launches an interactive setup wizard delivered through the Web UI. The user answers plain-language questions; the PA builds the full configuration automatically. No manual YAML editing required to get started.

#### 9.3.1 Step 1 — Docker Socket Disclosure

Before any other configuration, FAITH displays a clear disclosure:

> *FAITH requires access to the Docker socket to manage agent, tool, and sandbox containers on your behalf. This grants the PA root-equivalent access to your machine. Sandbox containers may run as root inside the container, but they are disposable Linux sandboxes with only approved mounts and no direct Docker socket access. FAITH uses host-level Docker access only to create, start, stop, and destroy its own containers.*

The user must explicitly acknowledge this before proceeding. Declining exits the wizard and stops the containers.

#### 9.3.2 Step 2 — Privacy & Security Profile

FAITH asks the user to select a privacy profile. This determines which LLM providers and models will be recommended or permitted throughout the system.

> *"How sensitive is the data and code you'll be working with?"*

| Profile | Description | Permitted providers |
|---|---|---|
| **Public** | Open source projects, non-sensitive work | Any provider, any model |
| **Internal** | Business data, proprietary code — no training data usage permitted | Providers with explicit no-training guarantees only |
| **Confidential** | Sensitive IP, regulated data — data must not leave your infrastructure | Ollama (local) only; no external API calls |

The PA maintains an embedded knowledge base of LLM provider privacy policies and Terms of Service, covering whether messages are stored, used for model training, subject to human review, and whether data processing agreements are available. This knowledge base is updated with each FAITH release.

When a privacy profile is selected:
- The PA filters all model recommendations to only those compliant with the profile.
- If the user subsequently attempts to configure a non-compliant model, the PA warns them and requires explicit override acknowledgement.
- The selected profile is stored in the project's `.faith/system.yaml` and enforced throughout the system's lifetime.
- The wizard defaults to local-model support being enabled. The user may disable Ollama entirely or switch to an existing external Ollama instance before completing setup.
- Ollama routing is platform-aware. Linux should prefer the bundled container Ollama when accelerator support is confirmed. Windows should use bundled container Ollama only when Docker Desktop GPU support is confirmed under WSL2; otherwise FAITH should prefer native host Ollama. macOS should default to native host Ollama rather than assuming container GPU support.

#### 9.3.3 Step 3 — PA Model Selection

> *"FAITH's Project Agent needs a capable model for orchestration. Let's set that up."*

- If the privacy profile is **Confidential**: Ollama-only options are shown. The PA recommends the best available local model for orchestration based on measured capability (successful inference probe, working GPU path if available, usable VRAM, and RAM fallback) rather than a fixed model list.
- If **Internal** or **Public**: the PA recommends a capable OpenRouter model that meets the privacy profile (e.g. Claude via Anthropic API which offers no-training guarantees). The user is asked for their OpenRouter API key. If provided, it is validated immediately. If not provided, the best available local model is used based on the same measured capability probe, with a note that orchestration quality may be limited.
- The PA explains the trade-off between local models (free, private, less capable) and paid models (cost, privacy-dependent, more capable) in plain language before the user decides.
- By default, the wizard resolves local-model access through the platform-appropriate Ollama route. An advanced setting allows the user to disable Ollama or override the endpoint to an external host-managed Ollama instance.

#### 9.3.4 Step 4 — Default Agent Model

> *"What model should your specialist agents use by default?"*

- The PA recommends a default based on the privacy profile and measured local capability or available OpenRouter models.
- The user can accept the recommendation or choose differently.
- This sets `default_agent_model` in `.faith/system.yaml`. No agents exist yet — the PA creates them per-project (see Step 5).
- Per-agent model overrides are available after agents are created, via each agent's `config.yaml` or by asking the PA.

#### 9.3.5 Step 5 — First Project Setup

> *"Let's set up your first project. Are you starting a new project from scratch, or do you have an existing codebase you'd like to work with?"*

**New project:**
- User provides a name and directory path for the project.
- PA creates the `.faith/` directory structure inside the project folder (see Section 2.4.2).
- PA also creates an empty `cag/` folder in the project root as the standard location for curated CAG reference documents.
- User describes the project in natural language.
- PA generates an initial `.faith/docs/frs.md` from the description.
- PA creates the `src/` directory ready for development.
- PA writes `.faith/system.yaml` with the settings from Steps 2–4.
- PA generates default tool configs in `.faith/tools/` based on the project structure.
- PA analyses the project requirements and proposes a specialist agent team — determining the number of agents, their roles, models, tool permissions, and trust levels. The proposal is presented to the user for review.

**Existing codebase:**
- User provides the path to their existing project folder.
- PA creates the `.faith/` directory structure inside the project folder.
- If no project-root `cag/` folder exists, the PA offers to create it as the standard location for curated CAG reference documents.
- PA displays a warning: *"FAITH agents will have write access to this folder. Ensure it is git-managed or backed up before proceeding."* User must acknowledge.
- Code Index Tool indexes the existing codebase automatically.
- If `.faith/docs/frs.md` already exists (returning to a FAITH-managed project), the PA reads it and summarises the existing requirements to the user for confirmation.
- If no `frs.md` exists, the PA analyses the codebase structure and generates a draft `.faith/docs/frs.md` from what it finds, asking the user to review and confirm before proceeding.
- PA writes `.faith/system.yaml` and generates tool configs in `.faith/tools/`.
- PA analyses the codebase technologies and project requirements, then proposes an agent team tailored to the project. The proposal is presented to the user for review.

**Agent team proposal (both cases):**

The PA presents the proposed team in the Web UI as a structured summary:

> *"Based on your project, I recommend the following agent team:"*
>
> | Agent | Role | Model | Tools |
> |---|---|---|---|
> | `software-developer` | Implementation — writes and modifies code | ollama/llama3:8b | filesystem, python, code-index |
> | `test-engineer` | Test design and execution | ollama/llama3:8b | filesystem, python |
> | `security-expert` | Security review | openrouter/claude-sonnet-4-6 | filesystem, code-index |
>
> *"Would you like to adjust this team, or shall I proceed?"*

The user can:
- **Accept** the proposed team as-is.
- **Adjust** — add agents, remove agents, change roles, reassign models or tools.
- **Skip** — proceed with no agents; the user can ask the PA to create agents later during the session.

On acceptance, the PA creates each agent's directory in `.faith/agents/{agent-id}/`, writes `config.yaml` (model, tools, trust, file watches), and generates `prompt.md` (role definition, behavioural guidelines, tool usage instructions, and project context). The PA also adds appropriate entries to the project's `.gitignore` for volatile agent files (`context.md`, `state.md`).

#### 9.3.6 Step 6 — Launch

- PA starts all tool containers and the accepted agent containers.
- Wizard closes and the main FAITH workspace opens.
- A brief onboarding overlay explains the panel layout and how to start a task.
- Total time from `pip install faith-cli && faith init` to working system: under 5 minutes for a user with Docker installed.

#### 9.3.7 Reopening the Wizard

The setup wizard is also available after initial setup as a guided edit mode.

- The Web UI exposes a **Reopen Setup Wizard** action from settings/configuration guidance.
- In edit mode, the wizard preloads current framework and project settings.
- The wizard writes changes back through the same config generation and hot-reload pipeline used during first run.
- Existing `.faith/` files are updated in place where safe rather than recreated from scratch.
- High-impact changes require explicit confirmation before apply, including privacy profile changes, Ollama enable/disable, switching between bundled and external Ollama, PA model provider changes, and project path changes.
- Direct YAML editing remains supported, but the wizard is the preferred guided path for revising setup.

### 9.4 Provider Privacy Knowledge Base

The PA embeds a structured privacy reference covering all supported LLM providers. This is not a live web scrape — it is a curated, versioned dataset updated with each FAITH release.

**Fields per provider/model:**

| Field | Description |
|---|---|
| `training_opt_out` | Whether messages are used for training (and if opt-out is available) |
| `human_review` | Whether human operators may review conversations |
| `data_retention` | How long messages are stored |
| `dpa_available` | Whether a Data Processing Agreement is available |
| `compliance` | Notable certifications (SOC2, GDPR, HIPAA, etc.) |
| `faith_privacy_tier` | Minimum FAITH privacy profile required to use this provider |

When a new provider or model is added to `system.yaml`, the PA checks it against this knowledge base and warns the user if it conflicts with the active privacy profile.

### 9.5 Updating FAITH

- Running `faith update` pulls the latest FAITH images and restarts containers. The CLI package itself is updated separately via `pip install --upgrade faith-cli`.
- The PA validates config schema compatibility before applying updates — if a new version introduces breaking config changes, the user is guided through migration before containers restart.
- Agent prompts, project files, and logs are never modified during an update.

### 9.6 CLI & Skill Execution

FAITH supports headless, non-interactive task execution via a `faith` CLI command. This enables cron jobs, CI/CD integration, and scripted automation without the Web UI.

#### 9.6.1 CLI Entry Point

The `faith` CLI is a lightweight client that connects to the already-running PA via the FastAPI server.

All FAITH HTTP services that expose CLI-relevant or user-facing routes must implement `GET /api/routes`. This endpoint returns a structured machine-readable manifest describing the service's public HTTP and WebSocket endpoints, their purpose, and expected HTTP status codes. The `faith show-urls` command uses these manifests to list available endpoints without hard-coding PA or Web UI routes inside the CLI.

```bash
# Ad-hoc task
faith run "Run all tests and generate a QA report for Confluence"

# Run a skill
faith run --skill nightly-qa-tests

# With timeout override
faith run --skill nightly-qa-tests --timeout 30m

# Unattended mode — approvals handled automatically per skill config
faith run --skill nightly-qa-tests --unattended

# Dry run — shows what PA would do without executing
faith run --skill nightly-qa-tests --dry-run
```

**Execution flow:**

1. CLI POSTs the task to `POST /api/task` (dedicated endpoint for non-interactive tasks).
2. PA returns a `task_id`.
3. CLI opens a WebSocket to `/ws/task/{task_id}` and blocks.
4. PA processes the task — agents work normally.
5. On completion, PA publishes the result. CLI receives it and exits with a return code.

**Return codes:**

| Code | Meaning |
|------|---------|
| `0` | Task completed successfully |
| `1` | Task failed (agent error, unresolvable blocker) |
| `2` | Timeout exceeded |
| `3` | PA not running / connection refused |
| `4` | Approval required but blocked in unattended mode, or `unattended_allowed: false` |

#### 9.6.2 Skills

Skills are reusable task definitions stored as markdown files in `.faith/skills/`. A skill is conceptually identical to skills in Claude Code or Codex — a markdown document that tells the PA what to do. Skills support two execution modes: **AI skills** (default) where the PA executes the task using agents and tools, and **script skills** where a shell command or Python script runs directly without AI involvement.

**AI skill example** (`executor: ai`, the default — can be omitted):

```markdown
<!-- .faith/skills/nightly-qa-tests.md -->
---
name: Nightly QA Tests
description: Run full test suite, generate report, publish to Confluence
timeout: 30m
notify_on_complete: true
unattended_allowed: true
unattended_security: safe
---

# Nightly QA Tests

Run the full test suite for this project. Capture all pass/fail/skip results.

Generate a QA report summarising:
- Total tests, pass rate, failure details
- Any new failures since last run
- Test execution time

Publish the report to Confluence under the QA space with today's date as the page title.

If any critical tests fail, flag them prominently at the top of the report.
```

**Script skill example** (`executor: script`):

```markdown
<!-- .faith/skills/convert-reports-to-pdf.md -->
---
name: Convert Reports to PDF
description: Convert all .docx files in reports/ to PDF format
executor: script
command: "python /workspace/scripts/docx_to_pdf.py --input /workspace/reports/ --output /workspace/pdfs/"
timeout: 5m
schedule: "0 6 * * *"
schedule_enabled: true
notify_on_complete: true
unattended_allowed: true
---

# Convert Reports to PDF

Converts all .docx files in the reports folder to PDF format daily at 6am.
The markdown body is documentation only — not sent to the PA.
```

**Executor modes:**

| `executor` | Behaviour |
|------------|-----------|
| **`ai`** (default) | Markdown body is sent to the PA as a prompt. The PA handles everything — agent creation, tool selection, execution planning. |
| **`script`** | The `command` field is executed directly inside the PA container as a subprocess. The PA is not involved. The markdown body serves as human-readable documentation only. |

Script skills get the same benefits as AI skills: audit logging, return codes, scheduler integration, Web UI visibility, `notify_on_complete`, and `--unattended` support. The only difference is that no AI is involved in execution.

**Structure:**
- **YAML frontmatter** — machine-readable settings (timeout, executor, command, unattended config, schedule). Parsed by the CLI and scheduler.
- **Markdown body** — task instructions for `executor: ai`, or documentation for `executor: script`.

**Usage:**
```bash
faith run --skill nightly-qa-tests                       # AI skill
faith run --skill nightly-qa-tests --unattended          # AI skill, unattended
faith run --skill convert-reports-to-pdf                  # script skill
faith run --skill security-scan --timeout 1h
```

For AI skills, the PA receives the skill's markdown body and executes it using its standard capabilities — creating whatever agents it needs, assigning tools, planning execution steps, and tearing down when done. Skills do not specify agents, tools, or execution order — the PA decides all of this, same as interactive mode. For script skills, the `command` is run directly and the exit code determines success (0) or failure (non-zero).

**Creating skills:** Users can write skill files directly, or ask the PA conversationally: *"Create a skill that runs tests every night and publishes to Confluence."* The PA writes `.faith/skills/nightly-qa-tests.md`.

**Web UI Skills panel:** The Web UI shows a Skills panel listing all `.faith/skills/*.md` files with name, description, last run status, and Run/Edit buttons. Running a skill from the Web UI is identical to running it from the CLI.

#### 9.6.3 Unattended Execution & Approval Handling

When the `--unattended` flag is passed, the CLI sends an `unattended: true` context to the PA. The PA then handles approvals differently based on the skill's `unattended_security` frontmatter setting.

**Check flow:**

1. CLI loads `.faith/skills/{name}.md` and parses frontmatter.
2. Checks `unattended_allowed`. If `false`, exits immediately with code 4 and message: *"Unattended execution is not allowed for this skill."*
3. If `executor: script` — runs the `command` directly (no PA involvement, no approval flow). The `--unattended` flag is accepted but has no effect since scripts don't trigger approvals.
4. If `executor: ai` (default) — submits skill body to PA with unattended context and security mode.
5. When an approval is needed during execution, the PA evaluates it against the skill's `unattended_security` mode.

**Security modes:**

| Mode | `always_allow` matched action | Unmatched ask-first action | `always_ask` matched action |
|------|-------------------------------|----------------------------|-----------------------------|
| **`safe`** (default) | Allow | **Block — fail task with code 4** | **Block — fail task with code 4** |
| **`all`** | Allow | Allow | Allow |
| **`explicit`** | Allow | Only if matched by `auto_approve` list | Only if matched by `auto_approve` list |

- **`safe`** — sensible default. Only actions already covered by a durable `always_allow` rule proceed unattended. New ask-first actions and `always_ask` actions remain blocked.
- **`all`** — full trust. The user accepts all risk. Suitable for skills where every possible operation is known and accepted.
- **`explicit`** — most restrictive. Only operations matching the skill's `auto_approve` frontmatter list are permitted beyond the base `always_allow` rules. Everything else fails the task.

**`explicit` mode example frontmatter:**
```yaml
unattended_security: explicit
auto_approve:
  - "git:status"
  - "git:log"
  - "python:execute_python"
  - "browser:confluence_*"
```

**Audit trail:** Every unattended approval is logged with `approval_source: "unattended"`, the skill name, and the security mode. This ensures full traceability of what was approved and why.

#### 9.6.4 Non-Interactive Sessions

CLI-triggered tasks create a standard session (same as Web UI sessions) with an additional `trigger: "cli"` field and `skill: "<name>"` field (if applicable) in `session.meta.json`. The PA processes the task identically — agents, tools, events, and logging all work the same way. The only difference is approval handling (per Section 9.6.3) and that the result is returned to the CLI process rather than displayed in the Web UI.

If `notify_on_complete: true` is set in the skill's frontmatter, the result is also surfaced in the Web UI for any connected user to see.

#### 9.6.5 Built-in Skill Scheduler

FAITH includes a built-in cron-style scheduler running inside the PA container. This eliminates the need for host OS cron jobs and keeps scheduling, execution, and logging in one place.

**Schedule definition:** Skills declare their schedule in frontmatter using standard cron expressions:

```yaml
schedule: "0 2 * * *"        # minute hour day month weekday
schedule_enabled: true        # toggle without removing the schedule (default: true)
```

If a skill has a `schedule` field and `schedule_enabled` is not `false`, the PA registers it with the internal scheduler on startup.

**How it works:**

1. On PA startup, the scheduler loads all `.faith/skills/*.md` and registers skills that have a `schedule` field.
2. The scheduler runs as an `asyncio` background task inside the PA, ticking every 60 seconds.
3. On each tick, it compares the current time against the next-run time (computed via `croniter`) for each registered skill.
4. When a skill is due: for `executor: ai` skills, the PA creates a task internally (identical to `POST /api/task`), with `trigger: "scheduled"` and `skill: "<name>"` in `session.meta.json`. The skill's `unattended_allowed` and `unattended_security` settings apply automatically. For `executor: script` skills, the scheduler runs the `command` directly as a subprocess — no PA task is created.
5. On `.faith/skills/*.md` file change (detected by the config hot-reload system, FAITH-004), the scheduler re-parses frontmatter and updates/adds/removes schedule registrations without restart.

**Scheduler log:** All schedule events are written to `logs/scheduler.log` as JSON lines:

```json
{"ts": "2026-03-25T02:00:00Z", "skill": "nightly-qa-tests", "action": "triggered", "task_id": "task-abc123"}
{"ts": "2026-03-25T02:04:12Z", "skill": "nightly-qa-tests", "action": "completed", "task_id": "task-abc123", "exit_code": 0, "duration_s": 252}
{"ts": "2026-03-25T02:00:01Z", "skill": "data-refresh", "action": "triggered", "task_id": "task-def456"}
{"ts": "2026-03-25T02:01:30Z", "skill": "data-refresh", "action": "failed", "task_id": "task-def456", "exit_code": 1, "duration_s": 89, "error": "approval blocked in unattended mode"}
```

**Web UI — Scheduled Skills panel:**

| Column | Description |
|--------|-------------|
| Skill | Skill name (from frontmatter) |
| Schedule | Human-readable schedule (e.g. "02:00 daily", "Mon 09:00") |
| Next Run | Computed next execution time |
| Last Run | Timestamp of most recent execution |
| Status | Success / Failed / Running / Disabled |
| Duration | How long the last run took |
| Actions | Run Now, Enable/Disable, View Log |

- **Run Now** triggers the skill immediately with the same unattended settings as a scheduled run.
- **Enable/Disable** toggles `schedule_enabled` in the skill's frontmatter (writes the file).
- **View Log** opens the session log for the most recent run. A history dropdown shows all past runs with timestamps and exit codes.

**Concurrency:** If a scheduled skill is still running when its next trigger time arrives, the scheduler skips that trigger and logs a `skipped_still_running` event. No overlapping runs of the same skill.

**Users can still use host OS cron** (`crontab`, Windows Task Scheduler) to call `faith run --skill <name> --unattended` if they prefer. The built-in scheduler is a convenience, not a requirement.

---

## 10. Licensing

### 10.1 Dual Licence Model

FAITH is released under a dual licence:

| Use case | Licence | Cost |
|---|---|---|
| Personal use, education, open source projects | AGPL-3.0 | Free |
| Commercial / business use | FAITH Commercial Licence | Paid (see Section 10.3) |

### 10.2 AGPL-3.0 (Personal & Open Source)

The GNU Affero General Public Licence v3.0 applies to all personal, educational, and open source use of FAITH. Key conditions:

- FAITH may be used freely for any non-commercial purpose.
- Any modifications to FAITH must be released under AGPL-3.0 — the copyleft obligation applies to source code changes.
- **Network use clause:** if FAITH (modified or unmodified) is run as a service accessible over a network, the full source code of that deployment must be made available to users. This prevents companies from running FAITH as a SaaS product without either open sourcing their version or obtaining a commercial licence.
- The AGPL-3.0 licence text is included in the repository as `LICENSE`.

**Why AGPL over MIT/Apache:** more permissive licences (MIT, Apache) would allow commercial entities to take FAITH, modify it, and run it as a commercial service without contributing back or paying. AGPL's network use clause closes this gap while keeping the software genuinely free for personal use.

### 10.3 Commercial Licence

Any use of FAITH within a business or commercial context requires a commercial licence. This includes:

- Running FAITH for internal business operations (e.g. a company's development team using FAITH).
- Running FAITH as a hosted or SaaS service for customers.
- Embedding FAITH components in a commercial product.
- Using FAITH in a cloud deployment serving multiple users or tenants.

The commercial licence:
- Removes the AGPL copyleft obligation — modifications may remain proprietary.
- Permits cloud, multi-tenant, and SaaS deployments.
- Support tiers and exact pricing are to be determined prior to first commercial release. The expected model is per-organisation deployment licence with optional support tiers (community, standard, enterprise).
- Licence enquiries and commercial terms will be managed via the FAITH project website (to be established).

### 10.4 Contributor Licence Agreement

Contributors to the FAITH open source repository must sign a Contributor Licence Agreement (CLA) before their pull requests are merged. The CLA grants the FAITH maintainers the right to distribute contributions under both the AGPL-3.0 and the commercial licence. Without this dual-licence grant, commercial licensing would not be possible.

### 10.5 Third-Party Component Licences

FAITH may include Ollama in the local bootstrap stack by default so the wizard has a ready local-model endpoint, but model downloads remain explicit user actions. Third-party runtime components and models retain their own licences and are not relicensed under FAITH's AGPL-3.0. The user agrees to the relevant model licences at download time.

| Component | Licence | How obtained |
|---|---|---|
| Ollama | MIT | Started by default in the local bootstrap stack; may be disabled in the wizard or replaced with an external instance |
| Llama 3 (Meta) | Meta Llama 3 Community Licence | Downloaded via Ollama if user selects it |
| Mistral | Apache 2.0 | Downloaded via Ollama if user selects it |
| Other Ollama models | Varies per model | Downloaded via Ollama if user selects them |
| OpenRouter API | Terms of Service | User provides their own API key |

**First-run wizard behaviour:** Ollama-backed local models are available by default through the resolved local endpoint, but actual model downloads remain opt-in. When the user opts to download any model, FAITH displays the relevant licence summary and a link to the full licence text, requires explicit acknowledgement before downloading, and executes the pull against the resolved Ollama endpoint selected by the platform-aware routing rules above. This is consistent with standard practice for software that fetches third-party components at runtime.

MIT (Ollama) is fully compatible with AGPL-3.0 in any case — if FAITH were ever to bundle Ollama directly, no licence conflict would arise.

### 10.6 Precedents

This dual-licence model is well-established and understood in the open source community. Notable examples using the same structure: MongoDB (SSPL variant), Elastic (ELv2 + commercial), Ghostwriter, GitLab EE. Users and companies are familiar with the pattern.

---

## 11. Example Use Case: Software Team

This section walks through a complete FAITH session end-to-end, demonstrating the major architectural features in action. The scenario: a user asks FAITH to implement a JWT authentication module for a Python REST API.

---

### 11.1 Agent Team for This Project

> **Note:** The agents below are **not built into FAITH**. They were dynamically created by the PA for this specific project after analysing the user's requirements and the existing codebase. A different project would result in a different agent team. The PA proposed this team and the user accepted it during project setup (see Section 9.3.5).

PA-generated agent team for this project (each agent has a `config.yaml` in `.faith/agents/{id}/`):

| Agent ID | Role | Model | Trust |
|---|---|---|---|
| `fds-architect` | FDS Architect — requirements and functional design | `openrouter/claude-sonnet-4-6` | standard |
| `sys-architect` | System Architect — technical design | `ollama/llama3:8b` | standard |
| `software-developer` | Software Developer — implementation | `ollama/llama3:8b` | standard |
| `test-engineer` | Test Case Engineer — test design | `ollama/llama3:8b` | standard |
| `qa-engineer` | QA Engineer — end-to-end testing | `ollama/llama3:8b` | standard |
| `security-expert` | Security Expert — vulnerability review | `openrouter/claude-sonnet-4-6` | standard |

---

### 11.2 Session Initialisation

**User types in the FAITH Input panel:**
> *"I need to add JWT authentication to the API. Users should log in with email and password, receive a token, and use it to access protected endpoints. Tokens should expire after 24 hours with a refresh mechanism."*

**PA receives the message and:**

1. Updates `frs.md` with new requirements:
```markdown
### Requirements
- REQ-011: Users authenticate via email and password
- REQ-012: Successful login returns a JWT access token (24h expiry) and refresh token (7d expiry)
- REQ-013: Protected endpoints validate the JWT on every request
- REQ-014: A token refresh endpoint accepts a valid refresh token and returns a new access token
- REQ-015: Passwords stored using bcrypt hashing — no plaintext storage

### Open Questions
- OQ-001: Should refresh tokens be single-use (rotated on each refresh)?
- OQ-002: Token storage strategy on the client — httponly cookie vs Authorization header?
```

2. Publishes `file:changed` on `frs.md` — all agents watching it are notified.

3. Surfaces the open questions to the user:
> *"I've added the requirements. Two questions before we start: Should refresh tokens rotate on each use (more secure but more complex)? And should tokens be stored in httponly cookies or returned in the response body for the client to manage?"*

**User responds:**
> *"Yes rotate refresh tokens. Use httponly cookies — we don't want tokens in localStorage."*

4. PA updates `frs.md`, closes both open questions, creates session `sess-0047-2026-03-23`, and publishes `system:config_changed`.

5. PA responds to user:
> *"Requirements confirmed. I'll start with the FDS Architect and System Architect to design the module, then hand off to the developer. I'll keep you informed at key milestones."*

---

### 11.3 Phase 1 — Requirements & Architecture

**PA stages Phase 1 agents:** starts `fds-architect` and `sys-architect` containers, creates channel `ch-auth-design`, registers file watch on `frs.md` for both agents, creates `task-001-143201.456`.

**PA sends task to `fds-architect`:**
```yaml
from: pa
to: fds-architect
channel: ch-auth-design
msg_id: 1
type: task
tags: [requirements, design, auth]
summary: "Design functional spec for JWT auth module per frs.md REQ-011 to REQ-015"
needs: "Functional design covering login flow, token lifecycle, refresh rotation, cookie strategy"
context_ref: frs/REQ-011,REQ-012,REQ-013,REQ-014,REQ-015
```

**`fds-architect` queries RAG tool** for relevant sections of `frs.md` and any existing API design documents, then responds:

```yaml
from: fds-architect
to: sys-architect
channel: ch-auth-design
msg_id: 2
type: task
tags: [design, architecture, auth]
status: complete
summary: "Auth flow designed: POST /auth/login → set httponly cookies (access+refresh), POST /auth/refresh → rotate refresh token, POST /auth/logout → clear cookies. Refresh tokens single-use, stored server-side with hash."
needs: "Technical design: token storage backend, middleware approach, password hashing library"
context_ref: frs/REQ-011,REQ-015
```

**`sys-architect` responds:**
```yaml
from: sys-architect
to: fds-architect
channel: ch-auth-design
msg_id: 3
type: decision
tags: [architecture, auth, design]
status: complete
summary: "Stack decisions: PyJWT for tokens, bcrypt via passlib, refresh tokens in PostgreSQL (hashed), FastAPI middleware for JWT validation, redis for token blacklist on logout"
needs: "FDS sign-off on refresh token DB schema"
```

**`fds-architect` signs off:**
```yaml
from: fds-architect
to: pa
channel: ch-auth-design
msg_id: 4
type: status_update
tags: [design]
status: complete
summary: "Phase 1 complete. Architecture agreed. Ready for implementation."
```

**PA receives `agent:task_complete` event** from `ch-auth-design`. It does not read the channel — it queries `fds-architect` directly for a handoff summary, then informs the user:
> *"Design phase complete. The FDS and System Architects have agreed on the approach: PyJWT + bcrypt, httponly cookies, refresh tokens stored (hashed) in PostgreSQL, Redis blacklist for logout. Moving to implementation."*

**PA closes Phase 1:** stops `fds-architect` container (no longer needed), stages Phase 2.

---

### 11.4 Phase 2 — Implementation

**PA starts** `software-developer` and `test-engineer` containers, creates channel `ch-auth-impl`, registers file watches on `workspace/src/auth/**` for `test-engineer` and `workspace/tests/auth/**` for `software-developer`.

**PA delegates to `software-developer`:**
```yaml
from: pa
to: software-developer
channel: ch-auth-impl
msg_id: 1
type: task
tags: [code, auth, feature]
summary: "Implement JWT auth module per Phase 1 design"
needs: "auth.py, middleware.py, models.py — all endpoints from frs.md REQ-011 to REQ-015"
context_ref: ch-auth-design/msg-2,msg-3
```

**`software-developer` checks Code Index Tool** — queries `list_symbols workspace/src/` to understand existing codebase structure before writing. Confirms no existing auth module. Checks `pip` for required packages, installs in one call: `pip install pyjwt passlib[bcrypt] redis`.

**Agent writes `auth.py`, `middleware.py`, `models.py`.** The filesystem tool detects changes and publishes `file:changed` events:
- `file:changed` → `workspace/src/auth/auth.py` → notifies `test-engineer`
- `file:changed` → `workspace/src/auth/middleware.py` → notifies `test-engineer`

**`test-engineer` receives file events and begins writing tests without waiting for a handoff message.** `software-developer` sends only semantic context:

```yaml
from: software-developer
to: test-engineer
channel: ch-auth-impl
msg_id: 8
type: review_request
tags: [code, testing, auth]
status: complete
disposable: true
summary: "Auth module implemented: 3 endpoints, JWT HS256, httponly cookies, bcrypt hashing, Redis blacklist, refresh token rotation with DB storage"
needs: "Test coverage for: token expiry, refresh rotation (single-use enforcement), logout blacklist, invalid password rejection"
```

Note: no `files` field — `test-engineer` already knows which files changed via file events.

**`test-engineer` writes `test_auth.py`.** File event notifies `software-developer`.

```yaml
from: test-engineer
to: software-developer
channel: ch-auth-impl
msg_id: 12
type: feedback
tags: [testing, code, auth]
status: needs_input
disposable: true
summary: "Tests written for all cases. One issue: refresh endpoint returns 200 on expired refresh token instead of 401"
needs: "Fix expiry check in POST /auth/refresh before I can complete the test suite"
```

**`software-developer` fixes the bug**, files change, `test-engineer` receives `file:changed`, re-runs test mentally and confirms:

```yaml
from: test-engineer
to: pa
channel: ch-auth-impl
msg_id: 15
type: status_update
tags: [testing]
status: complete
summary: "All test cases written and validated. Ready for Phase 3."
```

**PA receives `agent:task_complete`**, informs user, closes Phase 2.

---

### 11.5 Approval Request Example

Before `software-developer` runs the test suite, it requests approval:

**`software-developer` attempts:** `pytest tests/auth/ -v`

This command has no matching remembered rule — the default is to ask the user first.

**Approval card appears in Web UI:**
```
┌──────────────────────────────────────────────────┐
│ ⚡ Approval Required                             │
│ Agent: software-developer                        │
│ Action: pytest tests/auth/ -v                    │
│ Context: Running auth module test suite          │
│                                                  │
│ [Approve once] [Approve session] [All sessions]  │
│ [Deny]         [Deny permanently]                │
│                                                  │
│ Rule if approved for all sessions:               │
│ ^pytest tests/.*$                                │
└──────────────────────────────────────────────────┘
```

**User clicks "Always allow".** PA writes rule `^pytest tests/.*$` to `security.yaml` `always_allow_learned`. Tests run.

---

### 11.6 Loop Detection in Action

During Phase 3, `qa-engineer` and `software-developer` hit a circular dependency:

- `qa-engineer` modifies `test_refresh.py` to assert single-use enforcement more strictly
- `file:changed` notifies `software-developer` → it updates `auth.py` to pass the new assertion
- `file:changed` notifies `qa-engineer` → it modifies `test_refresh.py` again to tighten the assertion
- Cycle repeats

**PA's loop detection** sees the same SHA256 hashes for `auth.py` and `test_refresh.py` oscillating across 8 messages — threshold of 2 repetitions exceeded.

**PA publishes `channel:loop_detected`**, halts channel `ch-auth-phase3`, surfaces to Web UI:

```
┌──────────────────────────────────────────────────────┐
│ ⚠ Loop Detected — ch-auth-phase3                    │
│                                                      │
│ software-developer and qa-engineer are in a circular │
│ cycle modifying auth.py ↔ test_refresh.py.           │
│                                                      │
│ Last 3 states of auth.py have alternated between     │
│ two versions. Channel halted.                        │
│                                                      │
│ Suggested action: clarify the single-use refresh     │
│ token behaviour expected by both agents.             │
│                                                      │
│ [Resume with guidance]  [Review channel log]         │
└──────────────────────────────────────────────────────┘
```

**User clicks "Resume with guidance"** and types:
> *"The refresh token should be invalidated immediately on use — the new token is returned in the same response. One DB write per refresh."*

PA injects this as a requirement update to `frs.md` (REQ-014 clarified), notifies both agents, resumes the channel. The loop does not recur.

---

### 11.7 Phase 3 — Security Review & QA

**PA starts** `security-expert` and `qa-engineer` containers, creates `ch-auth-review`.

**`security-expert` queries Code Index Tool:**
```yaml
type: tool_call
tool: code-index
action: list_symbols
args:
  module: workspace/src/auth
```

Returns all function signatures without loading full file bodies — ~80 tokens vs ~4,000 for the full module.

**`security-expert` flags one issue:**
```yaml
from: security-expert
to: software-developer
channel: ch-auth-review
msg_id: 6
type: feedback
tags: [security, auth, code]
status: needs_input
summary: "JWT secret loaded from environment variable with no validation — empty string accepted silently. Must enforce minimum 32-char secret on startup."
needs: "Add startup validation: raise if JWT_SECRET < 32 chars or not set"
context_ref: code-index/auth.py:generate_token
```

**`software-developer` fixes and responds.** Security expert signs off.

---

### 11.8 QA Report Generation

**`qa-engineer` uses Playwright** to run end-to-end tests against the running API:

1. `POST /auth/login` with valid credentials → asserts 200, cookies set
2. `GET /api/protected` with valid token → asserts 200
3. `GET /api/protected` with expired token → asserts 401
4. `POST /auth/refresh` → asserts new token, old token invalidated
5. `POST /auth/logout` → asserts cookies cleared, blacklist entry created

Playwright captures screenshots at each step.

**`qa-engineer` generates a Confluence report** via the browser tool — navigates to the project Confluence space, creates a new page "Auth Module QA Report — 2026-03-23", inserts a structured table of test results, and embeds screenshots inline.

```yaml
from: qa-engineer
to: pa
channel: ch-auth-review
msg_id: 14
type: status_update
tags: [qa, testing]
status: complete
summary: "All 12 QA test cases passed. Confluence report created with screenshots. Auth module approved."
```

**PA receives `agent:task_complete`**, informs user:
> *"All phases complete. Security review passed with one fix (JWT secret validation added). QA has approved the auth module — 12/12 tests passed. Confluence report is available in your project space."*

---

### 11.9 Context Summary Evolution

After Phase 2, `software-developer`'s accumulated token count crosses 50% of its context window. Rolling summary fires and appends to `context.md`:

```markdown
# Context Summary — Software Developer
## Last Updated: 2026-03-23 16:45

### Key Decisions
- JWT auth uses HS256, tokens delivered via httponly cookies (access 24h, refresh 7d)
- Refresh tokens single-use, rotated on each call, stored hashed in PostgreSQL
- Redis blacklist used for immediate token invalidation on logout
- bcrypt via passlib for password hashing

### Completed Work
- auth.py: login, refresh, logout endpoints implemented
- middleware.py: JWT validation middleware for protected routes
- models.py: User and RefreshToken DB models

### Outstanding Tasks
- Awaiting security expert sign-off (JWT secret validation fix applied)

### Important Facts
- test_refresh.py required REQ-014 clarification (see frs.md) — loop resolved by PA
- pytest tests/auth/ always allowed (`always_allow_learned`)
```

Raw Phase 2 messages — including the disposable code review content — are dropped from context. The summary is ~200 tokens vs ~8,000 for the full message history.

---

### 11.10 Session Summary

| Metric | Value |
|---|---|
| Session duration | 2h 14m |
| Phases | 3 (design, implementation, review) |
| Agents active | 5 (fds-architect, sys-architect, software-developer, test-engineer, security-expert, qa-engineer) |
| Channels created | 3 (ch-auth-design, ch-auth-impl, ch-auth-review) |
| Loop detections | 1 (resolved via requirement clarification) |
| Approval requests | 1 (pytest — approved for all sessions) |
| Files written | 6 (auth.py, middleware.py, models.py, test_auth.py, test_refresh.py, test_middleware.py) |
| Total input tokens | 41,200 |
| Total output tokens | 9,800 |
| Estimated cost | $0.43 (PA + security-expert on paid models; other agents on Ollama) |
| QA tests passed | 12 / 12 |
| Confluence report | Created |

---

## 12. Cloud Deployment

> **Implementation priority: Lowest.** This section is defined for future planning purposes. Cloud deployment will be implemented after all local deployment features are complete and stable.

### 12.1 Overview

Cloud deployments serve commercial organisations running FAITH for multiple users across multiple projects. The local Docker model is retained for single-user deployments. Cloud introduces a thin additional layer to handle multi-tenancy, authentication, and enterprise-grade orchestration.

### 12.2 Architecture Differences from Local

| Concern | Local deployment | Cloud deployment |
|---|---|---|
| Orchestration | PA manages containers via Docker socket | PA issues requests to Kubernetes API via scoped service account |
| Authentication | None (local access only) | OAuth2 / SSO via control plane |
| Multi-tenancy | Single user, single project | Isolated namespaces per project |
| Storage | Local filesystem | S3-compatible object storage, per-project isolation |
| Web UI layout | `localStorage` per browser | Server-side per user profile |
| Redis | Single instance | Per-project or shared (configurable) |
| Root access | Docker socket mount | Removed — Kubernetes RBAC replaces it |

### 12.3 Control Plane

A FAITH Management Service sits above the PA layer and handles:
- User authentication and SSO integration
- Project creation and namespace isolation
- Billing and commercial licence enforcement
- Provisioning PA and agent containers per project via Kubernetes

### 12.4 Shared Codebase

The PA, agent, and tool container code is identical between local and cloud deployments. Only the orchestration and infrastructure layers differ. Features built for local use are automatically available in cloud deployments.

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| PA | Project Agent — the central coordinating agent and sole user interface |
| MCP | Model Context Protocol — Anthropic's open standard for tool integration |
| Compact Protocol | The structured, token-efficient message format used for inter-agent communication |
| Hot-Reload | Updating configuration without requiring a system restart |
| Rolling Summary | Proactive context management where agents periodically summarise and offload working memory to disk |
| Context.md | Per-agent file containing the agent's rolling context summary |
| Channel | A message bus topic connecting two or more agents for direct communication |
| Adapter Layer | PA component that translates MCP tool calls into simpler prompts for non-MCP-capable models |
| FAITH | Framework AI Team Hive — the name of this system |
| FRS | Functional Requirements Specification — the living project requirements document managed by the PA |
| Event System | The Redis pub/sub mechanism for framework-wide state-change notifications |
| system-events | The dedicated Redis channel for all FAITH framework events |
| Disposable Message | A compact protocol message marked for purging during context compaction once its task is complete |
| Session | A continuous user work period; contains one or more tasks |
| Task | A discrete goal within a session, with its own channels and logs |
| File History | Per-file version history maintained by the filesystem tool (round-robin, configurable depth) |
| Code Index Tool | MCP tool providing deterministic AST-based lookup of codebase symbols without loading full files |
| RAG Tool | Retrieval-Augmented Generation tool using ChromaDB for semantic search over prose documents |
| Pricing Tool | MCP tool for fetching and querying LLM model pricing data from OpenRouter |
| Loop Detection | PA mechanism for identifying circular agent behaviour patterns and halting affected channels |
| Privacy Profile | User-selected data sensitivity level (Public / Internal / Confidential) governing permitted LLM providers |
| PA-as-Fallback-Parser | Resilience pattern where the PA's LLM interprets raw content when a tool's structured parser fails |
| CAG | Cache-Augmented Generation — pre-loading static reference documents into agent context at session start for zero-retrieval-cost access |
| CAG Store | FAITH component managing pre-loaded reference documents per agent session |
| External MCP Server | A third-party MCP server (GitHub, Jira, Slack, etc.) registered in `.faith/tools/*.yaml` and started as a subprocess by the PA |
| pa-{agent-id} | Reserved direct channel between the PA and a single agent, created on agent container startup |
| Staged Agent Involvement | PA practice of introducing agents to a channel only when their phase begins, not all at once |
| always_ask | Approval tier for commands that always require user confirmation regardless of history |
| always_allow | Approval tier for commands that execute without prompting because they match a remembered or configured allow rule |

---

## Appendix C: Competitive Landscape

The following open source projects occupy adjacent space to FAITH. None combines all of FAITH's capabilities, but each is worth studying for implementation inspiration.

| Project | Licence | Similarity to FAITH | Key differences |
|---|---|---|---|
| **MetaGPT** | MIT | Highest — simulates a software company with PM, Architect, Engineer, QA roles collaborating on tasks | No Docker-per-agent isolation, no MCP, no Redis message bus, no web UI, no privacy controls, no token efficiency design |
| **AutoGen** (Microsoft) | MIT | Multi-agent with human-in-the-loop, agent collaboration, flexible communication patterns | Library not framework — no Docker, no MCP, no built-in tools, no UI, no security model |
| **CrewAI** | MIT | Role-based agents, sequential and parallel task workflows | No Docker, no MCP, minimal UI, no token efficiency design, no privacy model |
| **OpenDevin / All-Hands** | MIT | Docker sandboxing + web UI + code execution + browser automation | Single agent only, not a multi-agent team architecture |
| **AgentScope** (Alibaba) | Apache 2.0 | Message bus agent communication, most architecturally similar to FAITH's Redis design | No MCP, no Docker-per-agent, no privacy model, no event-driven PA |
| **LangGraph** (LangChain) | MIT | Graph-based agent orchestration, flexible topology | Complex graph model, no built-in tooling, no UI, requires significant custom code for equivalent capability |
| **Plandex** | AGPL-3.0 | Multi-step AI coding engine, handles large complex tasks | CLI-only, single-agent execution model, no multi-agent collaboration |
| **Aider** | Apache 2.0 | AI pair programmer, strong git integration, good code context management | Terminal-only, single agent, no MCP, no multi-agent |

**FAITH's differentiators not found in combination in any of the above:**

1. Docker-per-agent container isolation with PA-managed lifecycle
2. MCP as the universal tool interface with transparent adapter layer for non-MCP models
3. Event-driven PA that never reads agent conversations unless intervention is required
4. File event system that eliminates token-expensive file-list notifications between agents
5. Privacy profile enforcement at model selection with provider T&C knowledge base
6. Living FRS (`workspace/docs/frs.md`) as the project driver updated through PA conversation
7. CAG + RAG + Code Index three-tier token efficiency approach
8. Per-file version history with metadata linked to audit log
9. First-run wizard with UX-first design and one-command startup
10. Dual AGPL-3.0 / commercial licence model

**MetaGPT** is the most relevant project to study for agent role design and inter-agent workflow patterns. **AutoGen** is useful for human-in-the-loop interaction patterns. **OpenDevin** is useful for Docker sandboxing and web UI implementation reference.

---

## Appendix B: Open Questions

1. **Frontend framework:** ~~React (richer, heavier) vs HTMX (simpler, lighter) for the web UI?~~ **Decided:** GoldenLayout + Vue 3 (no-build, CDN) + xterm.js. No npm, no build step — see Section 6.
2. **Browser automation library:** ~~Playwright (recommended, modern) vs Selenium for the browser tool?~~ **Decided:** Playwright. See Section 4.5.
3. **Project name:** ~~Working title TBD.~~ **Decided: FAITH** — Framework AI Team Hive. Verify trademark availability before finalising.
4. **Max agents per channel:** ~~Should there be a limit to prevent noisy channels?~~ **Decided:** Soft limit of 5 (configurable in `system.yaml`, 0 = disabled). PA warns and suggests splitting channels. No hard block. PA stages agent involvement rather than running all agents simultaneously. See loop detection below.
5. **Context summary frequency:** ~~What is the optimal N (messages before summarisation triggers)? Should this be adaptive based on message size?~~ **Decided:** Adaptive by token count (default: 50% of model context window), with a hard 50-message fallback. Per-agent overrides in the agent's `config.yaml`. Disposable message flag added to compact protocol for artifact purging on compaction. RAG (ChromaDB) for document retrieval and Code Index Tool (tree-sitter) for codebase lookup — both reduce token load significantly. See Sections 3.3, 3.5.2, 4.7, 4.8.
6. **Offline mode:** ~~Should the system be fully functional with only Ollama (no internet) for air-gapped environments?~~ **Decided:** Full offline support is a first-class design goal. All dependencies vendored at install time. Offline is the secure default; internet and paid models are opt-in. See Section 9 (Setup) for first-run wizard and OpenRouter onboarding flow.
7. **Cloud deployment:** How should FAITH be architected for commercial companies running it in the cloud? Multi-tenancy, authentication, scalability, and managed hosting considerations. **Decided in principle** — see Section 12. **Implementation priority: lowest — after all other features are complete.**
8. **Licensing:** ~~Open source for personal use; commercial licence required for business use. Appropriate licence model to be determined.~~ **Decided:** Dual licence — AGPL-3.0 for personal/open source use; FAITH Commercial Licence for business use. CLA required for contributors. See Section 10.
9. **Dynamic agent creation — PA reasoning:** ~~How should the PA decide what agents to create for a given project? Should it use a fixed heuristic or reason freely? How much latitude for novel roles?~~ **Decided:** Hybrid — template-guided but not template-limited. FAITH ships with a role archetype library (`config/archetypes/`) that the PA uses as a starting palette. The PA can invent novel roles beyond the archetypes when project requirements demand it. Users can add custom archetype files to expand the library. PA must explain its reasoning for each proposed agent. See Section 3.1.4.
10. **Agent team evolution mid-project:** ~~Should the PA proactively add/remove agents, or wait for the user to ask?~~ **Decided:** PA manages team composition autonomously. Free/local model agents are created and removed silently (user notified but not asked). Paid model agents require user confirmation before creation (cost consent). Removal is always automatic. The user delegates team management to the PA — they care about outcomes, not agent orchestration. See Section 3.1.4.
11. **Agent prompt generation quality:** ~~Should the PA use a prompt template library or generate prompts from scratch? Show to user by default?~~ **Decided:** PA generates all prompts from its own knowledge — no external prompt library needed. A fixed base skeleton (protocol, events, tools) is combined with PA-generated role/project content informed by archetype `prompt_guidance`. User can view/edit `prompt.md` files at any time but is not shown them by default. See Section 3.1.4.
12. **Project switch and agent persistence:** ~~Should the PA tear down agents on project switch or preserve them globally?~~ **Decided:** Each project has its own `.faith/agents/` directory with per-agent `config.yaml`, `prompt.md`, `context.md`, and `state.md`. On project switch, the PA performs a coordinated teardown (saves state.md for each agent, stops containers). On resume, the PA reads the target project's `.faith/agents/` to reconstruct the team. No global `agents.yaml` exists — agent definitions are per-project. `config/secrets.yaml` replaces the old global config for credentials. See Sections 2.4, 2.5.
13. **Wizard specification:** The first-run wizard (Section 9.3) has a dedicated specification and implementation track in the epic. Remaining work is implementation and ongoing alignment, not task-definition.
14. **CLI & Skill execution:** ~~Should FAITH support headless, non-interactive task execution for cron jobs and automation?~~ **Decided:** Yes. A `faith` CLI command connects to the running PA via the FastAPI server, submits tasks, and blocks until completion with a bash return code. Skills are markdown files with YAML frontmatter in `.faith/skills/` — conceptually identical to skills in Claude Code/Codex. The PA reads the skill body as a prompt and handles agent creation, tool selection, and execution autonomously. Unattended execution uses a three-tier security model (`safe`/`all`/`explicit`) with a master `unattended_allowed` gate per skill. See Section 9.6.
15. **Git ownership in v1:** ~~The current FRS still contains both an external-first reading of Git under External MCP integration and a built-in Git MCP tool definition. This needs one final decision: either Git remains external-first for v1, or FAITH formally owns and ships a built-in Git MCP server.~~ **Decided:** Git is external-first in v1. FAITH should use an external/public local Git MCP server via the external MCP flow. A FAITH-owned Git MCP server is future fallback only if external options fail FAITH requirements. See Section 4.12.
16. **Tool runtime model:** ~~The document still mixes multiple execution models for tools: “each tool runs in its own Docker container,” PA-managed tool containers started from project config, and v1 external MCP servers launched as npm-backed `stdio` processes. The runtime model should be made explicit per tool class so the PA/container lifecycle rules are unambiguous.~~ **Decided:** Bootstrap services remain in `docker-compose.yml`; FAITH-owned security-sensitive tools run as dedicated project-scoped containers; external v1 MCP servers run as stdio subprocesses inside a project-scoped `mcp-runtime` container managed by the PA. See Sections 2.3, 4.6, 4.11.
17. **Database write policy:** ~~Section 4.4 currently says production write access is never permitted, but the permission-validation flow also allows `permission_override: true` to proceed after a mismatch. It needs to be clarified whether the override is only for mismatched read-only declarations, or whether non-test write access is ever allowed under any override.~~ **Decided:** Database access is read-only by default. Writes are allowed only when the connection is explicitly declared `readwrite` and the user approves the mutating action. `permission_override` acknowledges role mismatches only; it does not bypass declared access or per-action approval. See Section 4.4.
18. **Startup behaviour:** ~~Startup validation says “No partial startup occurs,” while also stating that tool containers may still start even when agent config is invalid. The intended startup mode should be clarified: fully blocked startup, control-plane-only startup, or tool-partial startup.~~ **Decided:** Control-plane-only startup. Bootstrap services may run, but no project-scoped agent containers, FAITH-owned tool containers, or external MCP sessions start until project config validates. See Section 7.2.2.
19. **Trust level effect:** ~~Trust levels are now documented as influencing recommendations and UI presentation without changing the ask-first approval fallback. That makes trust largely advisory unless more concrete effects are defined. Decide whether trust remains informational or gains explicit behaviour elsewhere in the product.~~ **Decided:** Trust remains an orchestration signal only. It may influence PA recommendations, review/escalation guidance, and UI emphasis, but it does not alter approval, access, or any security boundary. See Section 5.3.
20. **Audit terminology cleanup:** ~~The main approval model has been updated to `always_allow` / ask-first / `always_ask` / `always_deny`, but any remaining audit/reporting language should be reviewed to ensure legacy “auto-approved” wording does not re-enter implementation or analytics assumptions.~~ **Decided:** Audit terminology uses canonical v1 approval values only. `approval_tier` and `decision` are defined separately, and legacy `auto-approved` wording should not be used in new implementation. See Section 5.5.2.








