# FAITH-001 — Project Directory Structure & Base Scaffolding

**Phase:** 1 — Foundation
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** None
**FRS Reference:** Section 2.4, 9.2

---

## Objective

Create the FAITH monorepo scaffold, framework installation templates, the Docker Compose bootstrap file, and the `.gitignore`. The bootstrap stack must include Ollama enabled by default so the first-run wizard always has a local-model endpoint available, and it must also include the self-hosted MCP Registry service.

FAITH is developed as a **single monorepo** containing all components: CLI, Project Agent, Web UI, shared contracts, and FAITH-owned MCP servers. End users install via `pip install faith-cli`; the CLI's `faith init` command extracts bundled templates to `~/.faith/` on first use. Project-level `.faith/` directories are created by the PA during project setup (FAITH-049), not at install time.

This task produces the monorepo skeleton and bundled template assets that all subsequent tasks build upon. It creates **empty package directories** with `__init__.py` stubs only — no runtime code. Runtime implementation is owned by later tasks.

---

## Monorepo Source Layout

```
faith/                                 # Repository root
├── .agents/                           # Planning docs, task specs, dependency graph (not shipped)
├── .gitignore
├── LICENSE                            # AGPL-3.0
├── README.md                          # Minimal — project name + "see docs"
├── pyproject.toml                     # Monorepo Python package definition
├── docker-compose.yml                 # Bootstrap stack (PA, Redis, Web UI, Ollama, MCP Registry)
│
├── config/                            # Framework-level config templates (bundled by CLI)
│   ├── .env.template                  # Credential placeholders — copied to .env on first run
│   └── archetypes/                    # Role archetype templates for dynamic agent creation
│       ├── software-developer.yaml
│       ├── qa-tester.yaml
│       ├── technical-writer.yaml
│       ├── code-reviewer.yaml
│       └── devops-engineer.yaml
│
├── data/
│   └── model-prices.default.json      # Bundled pricing reference (committed)
│
├── logs/
│   └── .gitkeep
│
├── src/                               # All Python source packages
│   ├── faith_cli/                     # CLI package — faith init/start/stop/run (FAITH-005)
│   │   └── __init__.py
│   │
│   ├── faith_pa/                      # Project Agent — orchestration, sessions, events (FAITH-010+)
│   │   └── __init__.py
│   │
│   ├── faith_web/                     # FastAPI server + WebSocket endpoints (FAITH-036)
│   │   └── __init__.py
│   │
│   ├── faith_shared/                  # Shared models, protocols, schemas, contracts
│   │   └── __init__.py
│   │
│   └── faith_mcp/                     # FAITH-owned MCP servers (one subpackage each)
│       ├── __init__.py
│       ├── filesystem/                # FAITH-022
│       │   └── __init__.py
│       ├── python_exec/               # FAITH-024
│       │   └── __init__.py
│       ├── code_index/                # FAITH-027
│       │   └── __init__.py
│       ├── fulltext_search/           # FAITH-032
│       │   └── __init__.py
│       └── kv_store/                  # FAITH-033
│           └── __init__.py
│
├── web/                               # Frontend source and bundled assets (React + Dockview) (FAITH-074+)
│   └── .gitkeep
│
├── containers/                        # Dockerfiles for each deployable image
│   ├── pa/
│   │   └── Dockerfile
│   ├── web-ui/
│   │   └── Dockerfile
│   └── mcp-runtime/                   # Project-scoped container for external MCP servers
│       └── Dockerfile
│
└── tests/                             # All test code
    └── __init__.py
```

### Design Rationale

- **`src/` layout** — avoids import ambiguity between installed packages and source directories. Standard Python packaging practice.
- **`faith_mcp/` subpackages** — each FAITH-owned MCP server is a subpackage. They share a parent namespace but can be built into independent Docker images via their respective Dockerfiles.
- **`containers/`** — Dockerfiles only, no source code. Each Dockerfile copies from `src/` at build time. Only containers that FAITH builds and ships get a Dockerfile (PA, Web UI, MCP runtime). Tool MCP servers run inside the PA container or the mcp-runtime container.
- **`web/`** — frontend assets built separately and served by the web-ui container.
- **No `containers/` for individual MCP servers** — FAITH-owned MCP servers are Python packages in `src/faith_mcp/`. They run as processes inside the PA container or the project-scoped `mcp-runtime` container, not as separate Docker images per tool.

---

## Extracted Framework Home (`~/.faith/`)

For **end users**, `faith init` extracts bundled templates to `~/.faith/`. This layout is unchanged from the FRS:

```
~/.faith/                              # Framework home (created by `faith init`)
├── config/
│   ├── secrets.yaml                   ← created by first-run wizard, NEVER committed
│   ├── .env                           ← created from .env.template, NEVER committed
│   ├── .env.template                  ← committed, copied to .env on first run
│   ├── recent-projects.yaml           ← framework-level, tracks recent project paths
│   └── archetypes/                    ← role archetype templates
│       ├── software-developer.yaml
│       ├── qa-tester.yaml
│       ├── technical-writer.yaml
│       ├── code-reviewer.yaml
│       └── devops-engineer.yaml
├── data/
│   └── model-prices.default.json
├── logs/
│   └── .gitkeep
├── docker-compose.yml
└── .gitignore
```

### Project-Level `.faith/` Directory (created by PA, NOT by this task)

For reference, the PA creates this structure inside each user project when it is first set up (FAITH-049):

```
~/my-project/                          # The user's project
├── src/                               # User's existing code
├── .faith/                            # Created by PA during project setup
│   ├── system.yaml                    ← project settings (privacy, models, thresholds)
│   ├── security.yaml                  ← approval rules
│   ├── tools/                         ← per-tool config files
│   │   ├── filesystem.yaml
│   │   ├── python.yaml
│   │   ├── database.yaml
│   │   ├── browser.yaml
│   │   └── confluence.yaml
│   ├── agents/                        ← per-agent directories (PA-created)
│   │   └── software-developer/
│   │       ├── config.yaml            ← model, tools, trust, file_watches
│   │       ├── prompt.md              ← agent system prompt
│   │       ├── context.md             ← rolling context summary (gitignored)
│   │       └── state.md               ← agent state for resume (gitignored)
│   ├── skills/                        ← reusable task definitions (markdown + frontmatter)
│   ├── sessions/                      ← session logs (gitignored)
│   └── docs/
│       └── frs.md                     ← living FRS document
```

---

## File Contents

### `docker-compose.yml`

```yaml
version: "3.8"

services:
  pa:
    image: ghcr.io/faith/faith-project-agent:latest
    container_name: faith-pa
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./config:/config:ro                      # Framework-level secrets + archetypes (read-only)
      - ./data:/data
      - ./logs:/logs
      # Project workspace is mounted dynamically by PA at runtime via Docker SDK
      # The PA mounts the user's project root (including .faith/) when a project is opened
    environment:
      - FAITH_CONFIG_DIR=/config
      - FAITH_LOG_DIR=/logs
      - FAITH_REDIS_URL=redis://redis:6379/0
    env_file:
      - ./config/.env
    networks:
      - maf-network
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
      ollama:
        condition: service_started

  redis:
    image: redis:7-alpine
    container_name: faith-redis
    command: redis-server --appendonly yes --appendfsync everysec
    volumes:
      - redis-data:/data
    networks:
      - maf-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3

  web-ui:
    image: ghcr.io/faith/faith-web-ui:latest
    container_name: faith-web-ui
    ports:
      - "8080:8080"
    volumes:
      - ./logs:/logs:ro
    environment:
      - FAITH_REDIS_URL=redis://redis:6379/0
      - FAITH_PA_CHANNEL=pa-input
    networks:
      - maf-network
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy

  ollama:
    image: ollama/ollama:latest
    container_name: faith-ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama-data:/root/.ollama
    networks:
      - maf-network
    restart: unless-stopped

  mcp-registry:
    image: ghcr.io/modelcontextprotocol/registry:latest
    container_name: faith-mcp-registry
    networks:
      - maf-network
    restart: unless-stopped

networks:
  maf-network:
    name: maf-network
    driver: bridge

volumes:
  redis-data:
  ollama-data:
```

### `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "faith-cli"
version = "0.1.0"
description = "FAITH — Fully Autonomous Intelligent Task Handler"
requires-python = ">=3.11"
license = "AGPL-3.0-or-later"
readme = "README.md"

dependencies = [
    "click>=8.1",
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "websockets>=12.0",
]

[project.scripts]
faith = "faith_cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/faith_cli"]
# Other packages (faith_pa, faith_web, etc.) are NOT shipped in the CLI wheel.
# They are built into Docker images separately.

[tool.pytest.ini_options]
testpaths = ["tests"]
```

### `.gitignore`

```gitignore
# Credentials — NEVER commit
config/.env
config/secrets.yaml

# Runtime data
config/recent-projects.yaml
data/model-prices.cache.json
logs/*
!logs/.gitkeep

# Python
__pycache__/
*.pyc
*.pyo
.venv/
*.egg-info/
dist/
build/

# Docker
.docker/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Config backups from migration
config/*.bak-*

# Node (web UI frontend)
web/node_modules/
web/dist/
```

### `.faith/.gitignore` (template — PA writes this into each project's `.faith/` directory)

```gitignore
# Volatile agent state — do not commit
agents/*/context.md
agents/*/state.md

# Session logs
sessions/
```

### `config/.env.template`

```env
# FAITH Environment Variables
# Copy this file to .env and fill in your values.
# This file is NEVER committed to git.
# Secrets are referenced from config/secrets.yaml via ${VAR_NAME} substitution.

# OpenRouter (optional — required for paid models)
OPENROUTER_API_KEY=

# SerpAPI (optional — enhanced web search)
SERPAPI_KEY=
```

### `config/secrets.yaml` (template — created by wizard, NEVER committed)

The wizard (FAITH-049) creates this file. It uses `${VAR}` substitution from `.env`. Tool configs in `.faith/tools/*.yaml` reference these keys via `secret_ref`.

```yaml
# FAITH Secrets — NEVER commit this file
# Values use ${VAR} substitution from .env
# Tool configs reference these keys via secret_ref: <key_name>
schema_version: "1.0"

openrouter_api_key: "${OPENROUTER_API_KEY}"
serpapi_key: "${SERPAPI_KEY}"

# Per-project secrets are added here by the wizard or manually
# Example:
# myproject_db_password: "${MYPROJECT_DB_PASSWORD}"
# confluence_password: "${CONFLUENCE_PASSWORD}"
```

### `config/archetypes/software-developer.yaml` (example archetype)

```yaml
# Role archetype — used by PA as a starting template when creating agents
name: Software Developer
description: Writes, modifies, and debugs code
suggested_tools:
  - filesystem
  - python
  - code-index
  - git
suggested_trust: standard
suggested_tags:
  - code
  - bug
  - feature
  - refactor
```

### `data/model-prices.default.json`

```json
{
  "generated_date": "2026-03-23",
  "source": "openrouter.ai/models",
  "models": {
    "anthropic/claude-opus-4-6": {
      "input_cost_per_token": 0.000015,
      "output_cost_per_token": 0.000075,
      "context_window": 200000,
      "privacy_tier": "internal",
      "training_opt_out": true
    },
    "anthropic/claude-sonnet-4-6": {
      "input_cost_per_token": 0.000003,
      "output_cost_per_token": 0.000015,
      "context_window": 200000,
      "privacy_tier": "internal",
      "training_opt_out": true
    },
    "anthropic/claude-haiku-4-5": {
      "input_cost_per_token": 0.0000008,
      "output_cost_per_token": 0.000004,
      "context_window": 200000,
      "privacy_tier": "internal",
      "training_opt_out": true
    },
    "google/gemini-2.5-pro": {
      "input_cost_per_token": 0.00000125,
      "output_cost_per_token": 0.000005,
      "context_window": 1000000,
      "privacy_tier": "public",
      "training_opt_out": false
    },
    "openai/gpt-4o": {
      "input_cost_per_token": 0.0000025,
      "output_cost_per_token": 0.00001,
      "context_window": 128000,
      "privacy_tier": "internal",
      "training_opt_out": true
    },
    "meta-llama/llama-3-8b-instruct": {
      "input_cost_per_token": 0.0000001,
      "output_cost_per_token": 0.0000001,
      "context_window": 8192,
      "privacy_tier": "public",
      "training_opt_out": false
    },
    "meta-llama/llama-3-70b-instruct": {
      "input_cost_per_token": 0.0000008,
      "output_cost_per_token": 0.0000008,
      "context_window": 8192,
      "privacy_tier": "public",
      "training_opt_out": false
    }
  }
}
```

### `containers/pa/Dockerfile`

```dockerfile
# Placeholder — implementation owned by FAITH-014
FROM python:3.12-slim
WORKDIR /app
COPY src/faith_pa/ ./faith_pa/
COPY src/faith_shared/ ./faith_shared/
# Full Dockerfile defined in FAITH-014
```

### `containers/web-ui/Dockerfile`

```dockerfile
# Placeholder — implementation owned by FAITH-036
FROM python:3.12-slim
WORKDIR /app
COPY src/faith_web/ ./faith_web/
COPY web/dist/ ./static/
# Full Dockerfile defined in FAITH-036
```

### `containers/mcp-runtime/Dockerfile`

```dockerfile
# Placeholder — implementation owned by FAITH-035
# Project-scoped container for running external MCP servers (npm stdio)
FROM node:20-slim
WORKDIR /app
# Full Dockerfile defined in FAITH-035
```

---

## Acceptance Criteria

1. Running `find` from the repository root shows the expected monorepo directory tree with all directories and placeholder files.
2. All `.gitkeep` files exist in empty directories that git would otherwise skip.
3. `docker-compose.yml` passes `docker compose config` validation and includes PA, Redis, Web UI, Ollama, and the self-hosted MCP Registry enabled by default.
4. `.gitignore` covers all entries listed (especially `config/secrets.yaml` and `config/.env`).
5. `config/.env.template` contains credential placeholders.
6. `config/archetypes/` contains at least 5 role archetype YAML files.
7. `data/model-prices.default.json` is valid JSON with the `generated_date` field.
8. `git add -A && git status` shows no files that should be in `.gitignore`.
9. `pyproject.toml` is valid and defines the `faith` CLI entry point.
10. All `src/` packages contain `__init__.py` stubs only — no runtime code.
11. `containers/` contains only Dockerfiles (placeholder stubs) — no source code or requirements.txt.
12. `tests/` directory exists with `__init__.py`.
13. `.agents/` directory is preserved (planning docs, not shipped).

---

## Notes for Implementer

- **This is a monorepo.** All FAITH components live in one repository. There are no separate `faith-cli`, `faith-project-agent`, `faith-web-ui`, or `faith-mcp-*` repos.
- **End users install via `pip install faith-cli`** which ships only the CLI package. PA, Web UI, and MCP servers are deployed as Docker images built from this repo.
- Do NOT create `config/secrets.yaml` or `config/.env` — these are generated by the first-run wizard (FAITH-049). Only `.env.template` and archetype files are committed.
- Do NOT create any `.faith/` project directories — those are created by the PA when a project is first opened.
- Include Ollama in the bootstrap compose stack. The user-facing off switch is handled by the wizard/config flow.
- Include the self-hosted MCP Registry in the bootstrap compose stack.
- Keep `model-prices.default.json` prices approximate — they will be superseded by live scraping.
- Role archetypes in `config/archetypes/` are lightweight templates — they suggest tools, trust levels, and tags but don't define complete agent configs.
- `__init__.py` files in `src/` should contain only a module docstring describing the package's purpose and FRS reference. No imports, no code.
- Dockerfiles in `containers/` are placeholder stubs. Each will be fully defined by its owning task (FAITH-014 for PA, FAITH-036 for Web UI, FAITH-035 for MCP runtime).
- The `web/` directory will hold the React frontend source and bundled browser assets. Early bootstrap can still use a `.gitkeep` until the build pipeline lands.
- The `.agents/` directory is for planning and task management. It is NOT shipped with the CLI or Docker images.
