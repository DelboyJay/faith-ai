# FAITH

Framework AI Team Hive.

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
