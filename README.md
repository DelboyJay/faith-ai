# FAITH

Framework AI Team Hive.

FAITH is a system for running a small team of AI agents that work together on a software project under the control of a single coordinator called the Project Agent. In plain terms, you give FAITH a job like "add login with password reset and update the tests", and it can break that work into parts, assign them to specialist agents, keep track of what each one is doing, ask you for approval before risky actions, and show the whole process in a web UI.

Example: if you ask FAITH to add a new API endpoint, the Project Agent could have one agent inspect the existing code, another update the endpoint implementation, another update tests, and then bring the results back together. If one agent needs to edit files or run a command that should be approved first, FAITH pauses and asks you instead of doing it silently.

## POC stack

The current repository includes a minimal runnable POC stack:

- `redis` with AOF persistence
- `pa` FastAPI service on `http://localhost:8000`
- `web-ui` FastAPI dashboard on `http://localhost:8080`
- `ollama` on `http://localhost:11434`
- `mcp-registry` on `http://localhost:8081`

Start it with:

```powershell
docker compose up --build
```

The Web UI polls the Project Agent and shows Redis/config status from the current `config/` directory.

## CLI

The repo now includes a `faith-cli` bootstrap package that wraps the current POC compose stack.

```powershell
faith init
faith status
faith stop
```

`faith init` creates `~/.faith/`, copies config templates and archetypes there, starts the repo-backed Docker Compose stack, and opens `http://localhost:8080` when the Web UI responds.
