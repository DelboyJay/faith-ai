# FAITH-005 — FAITH CLI (`faith-cli` Package)

**Phase:** 1 — Foundation
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-001, FAITH-002
**FRS Reference:** Section 9.2

---

## Objective

Implement the `faith-cli` Python package — a lightweight CLI that serves as the user's sole entry point to FAITH. The package is installed via `pip install faith-cli` and provides the `faith` command on PATH. It handles prerequisite checks, owns the canonical bootstrap `docker-compose.yml`, performs Docker lifecycle management (`init`, `start`, `stop`, `restart`, `update`), communicates with the running PA via the shared PA API contract, and owns lifecycle management for the optional persistent host worker used for direct host-machine actions. Framework orchestration remains in the PA; host authority stays on the host side under `faith-cli`.

---

## Architecture

```
src/faith_cli/                      # CLI package within the monorepo (published to PyPI as faith-cli)
├── __init__.py             # Version
├── __main__.py             # Entry point: python -m faith_cli
├── cli.py                  # Click command group
├── docker.py               # Docker Compose wrapper
├── http_client.py          # HTTP/WebSocket client for PA
├── paths.py                # ~/.faith/ path resolution
├── checks.py               # Prerequisite checks (Python, Docker, Git)
├── browser.py              # Cross-platform browser opening
├── host_worker.py          # Optional persistent host worker lifecycle
└── bundled/                # Extracted to ~/.faith/ on init
    ├── docker-compose.yml
    ├── .env.template
    ├── secrets.yaml.template
    └── archetypes/
        ├── software-developer.yaml
        ├── test-engineer.yaml
        ├── security-reviewer.yaml
        ├── devops-engineer.yaml
        └── technical-writer.yaml

tests/
└── test_faith_cli.py
```

---

## Files to Create

### 1. `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "faith-cli"
version = "0.1.0"
description = "CLI for FAITH — Framework AI Team Hive"
readme = "README.md"
requires-python = ">=3.10"
license = "AGPL-3.0-or-later"
dependencies = [
    "click>=8.1",
    "requests>=2.31",
    "websocket-client>=1.6",
    "pyyaml>=6.0",
]

[project.scripts]
faith = "faith_cli.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/faith_cli"]
```

### 2. `src/faith_cli/__init__.py`

```python
__version__ = "0.1.0"
```

### 3. `src/faith_cli/__main__.py`

```python
from faith_cli.cli import main

if __name__ == "__main__":
    main()
```

### 4. `src/faith_cli/paths.py`

```python
"""Resolve ~/.faith/ paths and manage the framework home directory."""

from pathlib import Path
import platform


def faith_home() -> Path:
    """Return the FAITH framework home directory (~/.faith/)."""
    return Path.home() / ".faith"


def config_dir() -> Path:
    return faith_home() / "config"


def secrets_file() -> Path:
    return config_dir() / "secrets.yaml"


def env_file() -> Path:
    return config_dir() / ".env"


def compose_file() -> Path:
    return faith_home() / "docker-compose.yml"


def logs_dir() -> Path:
    return faith_home() / "logs"


def data_dir() -> Path:
    return faith_home() / "data"


def archetypes_dir() -> Path:
    return config_dir() / "archetypes"


def is_initialised() -> bool:
    """Check whether faith init has been run (compose file exists)."""
    return compose_file().exists()


def is_first_run() -> bool:
    """Check whether this is a first run (no secrets.yaml yet)."""
    return not secrets_file().exists()
```

### 5. `src/faith_cli/checks.py`

```python
"""Prerequisite checks for Python, Docker, Docker Compose, and Git."""

import shutil
import subprocess
import sys

import click


def check_docker() -> None:
    """Verify Docker and Docker Compose are installed and the daemon is running."""
    if not shutil.which("docker"):
        click.secho("Error: Docker is not installed.", fg="red")
        click.echo("  Install Docker: https://docs.docker.com/get-docker/")
        sys.exit(1)

    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        click.secho("Error: Docker daemon is not running.", fg="red")
        click.echo("  Start Docker Desktop or run: sudo systemctl start docker")
        sys.exit(1)

    result = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        click.secho("Error: Docker Compose v2 is not available.", fg="red")
        click.echo("  Docker Compose v2 is included with Docker Desktop.")
        click.echo("  Install: https://docs.docker.com/compose/install/")
        sys.exit(1)

    click.secho("  Docker and Docker Compose detected", fg="green")


def check_git() -> None:
    """Check for Git (optional — warn if absent)."""
    if shutil.which("git"):
        click.secho(
            "  Git detected (file history will auto-skip git-managed workspaces)",
            fg="green",
        )
    else:
        click.secho(
            "  Git not found — FAITH will use its own file history for all workspaces",
            fg="yellow",
        )


def check_python_version() -> None:
    """Verify Python 3.10+."""
    if sys.version_info < (3, 10):
        click.secho(
            f"Error: Python 3.10+ required (found {sys.version})", fg="red"
        )
        sys.exit(1)
```

### 6. `src/faith_cli/docker.py`

```python
"""Docker Compose wrapper for container lifecycle management."""

import subprocess
import sys
from pathlib import Path

import click

from faith_cli.paths import compose_file, faith_home


def _run_compose(*args: str, stream: bool = False) -> subprocess.CompletedProcess:
    """Run a docker compose command against the FAITH compose file."""
    cmd = ["docker", "compose", "-f", str(compose_file()), *args]

    if stream:
        # Stream output to terminal in real time
        result = subprocess.run(cmd)
    else:
        result = subprocess.run(cmd, capture_output=True, text=True)

    return result


def compose_up() -> None:
    """Start FAITH containers in detached mode."""
    click.echo("  Starting containers...")
    result = _run_compose("up", "-d")
    if result.returncode != 0:
        click.secho("Error: Failed to start containers.", fg="red")
        if result.stderr:
            click.echo(result.stderr)
        sys.exit(1)
    click.secho("  Containers started", fg="green")


def compose_down() -> None:
    """Stop and remove FAITH containers."""
    click.echo("  Stopping containers...")
    result = _run_compose("down")
    if result.returncode != 0:
        click.secho("Warning: docker compose down returned an error.", fg="yellow")
    else:
        click.secho("  Containers stopped", fg="green")


def compose_pull() -> None:
    """Pull latest FAITH images."""
    click.echo("  Pulling latest images...")
    result = _run_compose("pull", stream=True)
    if result.returncode != 0:
        click.secho("Error: Failed to pull images.", fg="red")
        sys.exit(1)
    click.secho("  Images updated", fg="green")


def compose_ps() -> str:
    """Return the output of docker compose ps."""
    result = _run_compose("ps", "--format", "table")
    return result.stdout if result.stdout else ""


def is_running() -> bool:
    """Check whether FAITH containers are currently running."""
    result = _run_compose("ps", "--status", "running", "-q")
    return bool(result.stdout and result.stdout.strip())
```

### 7. `src/faith_cli/browser.py`

```python
"""Cross-platform browser opening."""

import platform
import subprocess
import time

import click
import requests


WEB_UI_URL = "http://localhost:8080"
MAX_WAIT_SECONDS = 30


def wait_and_open_browser() -> None:
    """Wait for the Web UI to respond, then open the browser."""
    click.echo("  Waiting for Web UI", nl=False)
    for _ in range(MAX_WAIT_SECONDS):
        try:
            resp = requests.get(WEB_UI_URL, timeout=1)
            if resp.status_code == 200:
                click.echo("")
                click.secho(f"  Web UI ready at {WEB_UI_URL}", fg="cyan")
                _open_url(WEB_UI_URL)
                return
        except requests.ConnectionError:
            pass
        click.echo(".", nl=False)
        time.sleep(1)

    click.echo("")
    click.secho(
        f"  Web UI not responding — open {WEB_UI_URL} manually", fg="yellow"
    )


def _open_url(url: str) -> None:
    """Open a URL in the default browser (best-effort)."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif system == "Windows":
            subprocess.Popen(["cmd", "/c", "start", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass  # Best-effort — URL already printed to terminal
```

### 8. `src/faith_cli/http_client.py`

```python
"""HTTP and WebSocket client for communicating with the running PA."""

import sys
from typing import Optional

import click
import requests


PA_BASE_URL = "http://localhost:8080"


def pa_is_reachable() -> bool:
    """Check whether the PA's HTTP API is responding."""
    try:
        resp = requests.get(f"{PA_BASE_URL}/ws/status", timeout=3)
        return resp.status_code < 500
    except requests.ConnectionError:
        return False


def get_status() -> Optional[dict]:
    """Fetch system status from the PA."""
    try:
        resp = requests.get(f"{PA_BASE_URL}/api/status", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        return None
    except requests.HTTPError:
        return None


def request_shutdown() -> bool:
    """Send a coordinated shutdown request to the PA.

    The PA will save state.md per agent and stop managed containers
    before the CLI runs docker compose down.
    """
    try:
        resp = requests.post(f"{PA_BASE_URL}/api/shutdown", timeout=30)
        return resp.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False
```

### 9. `src/faith_cli/cli.py`

```python
"""FAITH CLI — main command group and all subcommands."""

import importlib.resources
import shutil
import sys
from pathlib import Path

import click

from faith_cli import __version__
from faith_cli.browser import wait_and_open_browser
from faith_cli.checks import check_docker, check_git
from faith_cli.docker import (
    compose_down,
    compose_ps,
    compose_pull,
    compose_up,
    is_running,
)
from faith_cli.http_client import get_status, pa_is_reachable, request_shutdown
from faith_cli.paths import (
    archetypes_dir,
    compose_file,
    config_dir,
    data_dir,
    env_file,
    faith_home,
    is_first_run,
    is_initialised,
    logs_dir,
    secrets_file,
)

BANNER = """
  FAITH — Framework AI Team Hive
"""


def _print_banner():
    click.secho(BANNER, fg="cyan")


def _extract_bundled_files():
    """Extract bundled templates from the package to ~/.faith/."""
    bundled = importlib.resources.files("faith_cli") / "bundled"

    # docker-compose.yml
    src = bundled / "docker-compose.yml"
    dst = compose_file()
    if not dst.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    # .env.template → .env
    src = bundled / ".env.template"
    dst = env_file()
    if not dst.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    # secrets.yaml.template → secrets.yaml (only template, not filled)
    src = bundled / "secrets.yaml.template"
    dst = secrets_file()
    if not dst.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    # Archetypes
    src_dir = bundled / "archetypes"
    dst_dir = archetypes_dir()
    dst_dir.mkdir(parents=True, exist_ok=True)
    for archetype_file in src_dir.iterdir():
        dst_file = dst_dir / archetype_file.name
        if not dst_file.exists():
            dst_file.write_text(
                archetype_file.read_text(encoding="utf-8"), encoding="utf-8"
            )


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="faith")
@click.pass_context
def main(ctx):
    """FAITH — Framework AI Team Hive.

    Use 'faith init' to set up FAITH for the first time.
    Use 'faith start' to launch the system.
    Use 'faith run' to send tasks to the PA.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
def init():
    """First-time setup: create ~/.faith/, pull images, start containers, open wizard."""
    _print_banner()

    if is_initialised():
        if not click.confirm(
            f"  {faith_home()} already exists. Re-initialise? (existing config will be preserved)"
        ):
            click.echo("  Aborted.")
            return

    # Prerequisite checks
    check_python_version()
    check_docker()
    check_git()

    # Create directory structure
    click.echo("")
    click.echo("  Creating framework home directory...")
    for d in [config_dir(), data_dir(), logs_dir(), archetypes_dir()]:
        d.mkdir(parents=True, exist_ok=True)
    click.secho(f"  Created {faith_home()}", fg="green")

    # Extract bundled files
    _extract_bundled_files()
    click.secho("  Extracted config templates and archetypes", fg="green")

    # Pull images and start
    click.echo("")
    compose_pull()
    compose_up()

    # Open browser to wizard
    click.echo("")
    wait_and_open_browser()

    click.echo("")
    click.secho("  FAITH is running. The setup wizard is waiting in your browser.", fg="green")
    click.echo("  Use 'faith stop' to shut down when you're done.")


@main.command()
def start():
    """Start FAITH containers and open the Web UI."""
    _print_banner()

    if not is_initialised():
        click.secho("  FAITH has not been initialised. Run 'faith init' first.", fg="yellow")
        sys.exit(1)

    if is_running():
        click.secho("  FAITH is already running.", fg="green")
        wait_and_open_browser()
        return

    check_docker()
    compose_up()
    wait_and_open_browser()

    click.echo("")
    click.secho("  FAITH is running.", fg="green")
    click.echo("  Use 'faith stop' to shut down.")


@main.command()
def stop():
    """Graceful shutdown: save agent state, stop containers."""
    _print_banner()

    if not is_running():
        click.secho("  FAITH is not running.", fg="yellow")
        return

    # Request coordinated shutdown from PA
    click.echo("  Requesting coordinated shutdown from PA...")
    if pa_is_reachable():
        if request_shutdown():
            click.secho("  PA saved agent state and stopped managed containers", fg="green")
        else:
            click.secho("  PA shutdown request failed — forcing container stop", fg="yellow")
    else:
        click.secho("  PA not reachable — forcing container stop", fg="yellow")

    compose_down()
    click.echo("")
    click.secho("  FAITH stopped.", fg="green")


@main.command()
def restart():
    """Restart FAITH containers."""
    _print_banner()

    if is_running():
        click.echo("  Stopping...")
        if pa_is_reachable():
            request_shutdown()
        compose_down()

    compose_up()
    wait_and_open_browser()
    click.secho("  FAITH restarted.", fg="green")


@main.command()
def status():
    """Show FAITH system status."""
    if not is_initialised():
        click.secho("  FAITH has not been initialised. Run 'faith init' first.", fg="yellow")
        return

    if not is_running():
        click.secho("  FAITH is not running. Run 'faith start' to launch.", fg="yellow")
        return

    click.secho("  FAITH is running.", fg="green")
    click.echo("")

    # Container status
    ps_output = compose_ps()
    if ps_output:
        click.echo(ps_output)

    # PA status (if reachable)
    if pa_is_reachable():
        status_data = get_status()
        if status_data:
            click.echo("")
            project = status_data.get("active_project", "none")
            agents = status_data.get("agent_count", 0)
            click.echo(f"  Active project: {project}")
            click.echo(f"  Agents running: {agents}")
    else:
        click.secho("  PA not reachable — containers may still be starting", fg="yellow")


@main.command()
def update():
    """Pull latest images, validate config, restart."""
    _print_banner()
    check_docker()

    if is_running():
        click.echo("  Stopping current containers...")
        if pa_is_reachable():
            request_shutdown()
        compose_down()

    compose_pull()
    compose_up()

    click.echo("")
    click.secho("  FAITH updated and restarted.", fg="green")
    click.echo("  If config migration is needed, the PA will guide you on next connection.")
```

### 10. `tests/test_faith_cli.py`

```python
"""Tests for the faith-cli package."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from faith_cli.cli import main
from faith_cli.paths import faith_home, is_first_run, is_initialised


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

class TestPaths:
    def test_faith_home_is_under_user_home(self):
        home = faith_home()
        assert home.parent == Path.home()
        assert home.name == ".faith"

    def test_is_initialised_false_when_no_compose(self, tmp_path, monkeypatch):
        monkeypatch.setattr("faith_cli.paths.faith_home", lambda: tmp_path / ".faith")
        assert not is_initialised()

    def test_is_initialised_true_when_compose_exists(self, tmp_path, monkeypatch):
        fake_home = tmp_path / ".faith"
        fake_home.mkdir()
        (fake_home / "docker-compose.yml").write_text("version: '3'")
        monkeypatch.setattr("faith_cli.paths.faith_home", lambda: fake_home)
        monkeypatch.setattr(
            "faith_cli.paths.compose_file", lambda: fake_home / "docker-compose.yml"
        )
        assert is_initialised()

    def test_is_first_run_true_when_no_secrets(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "faith_cli.paths.secrets_file", lambda: tmp_path / "secrets.yaml"
        )
        assert is_first_run()

    def test_is_first_run_false_when_secrets_exist(self, tmp_path, monkeypatch):
        secrets = tmp_path / "secrets.yaml"
        secrets.write_text("openrouter_api_key: test")
        monkeypatch.setattr("faith_cli.paths.secrets_file", lambda: secrets)
        assert not is_first_run()


# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------

class TestChecks:
    @patch("shutil.which", return_value=None)
    def test_check_docker_exits_when_not_installed(self, mock_which):
        from faith_cli.checks import check_docker

        with pytest.raises(SystemExit):
            check_docker()

    @patch("shutil.which", return_value="/usr/bin/git")
    def test_check_git_passes_when_installed(self, mock_which, capsys):
        from faith_cli.checks import check_git

        check_git()
        captured = capsys.readouterr()
        assert "Git detected" in captured.out


# ---------------------------------------------------------------------------
# CLI commands (using Click test runner)
# ---------------------------------------------------------------------------

class TestCLI:
    def test_help_shows_usage(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "FAITH" in result.output

    def test_version_flag(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    @patch("faith_cli.cli.is_initialised", return_value=False)
    def test_start_fails_if_not_initialised(self, mock_init):
        runner = CliRunner()
        result = runner.invoke(main, ["start"])
        assert result.exit_code != 0
        assert "faith init" in result.output

    @patch("faith_cli.cli.is_running", return_value=False)
    @patch("faith_cli.cli.is_initialised", return_value=True)
    def test_stop_when_not_running(self, mock_init, mock_running):
        runner = CliRunner()
        result = runner.invoke(main, ["stop"])
        assert "not running" in result.output

    @patch("faith_cli.cli.is_initialised", return_value=False)
    def test_status_when_not_initialised(self, mock_init):
        runner = CliRunner()
        result = runner.invoke(main, ["status"])
        assert "faith init" in result.output


# ---------------------------------------------------------------------------
# Docker wrapper
# ---------------------------------------------------------------------------

class TestDocker:
    @patch("subprocess.run")
    def test_is_running_returns_true_when_containers_up(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="abc123\ndef456\n", returncode=0
        )
        from faith_cli.docker import is_running

        assert is_running()

    @patch("subprocess.run")
    def test_is_running_returns_false_when_no_containers(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        from faith_cli.docker import is_running

        assert not is_running()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class TestHTTPClient:
    @patch("requests.get", side_effect=Exception("connection refused"))
    def test_pa_not_reachable(self, mock_get):
        from faith_cli.http_client import pa_is_reachable

        assert not pa_is_reachable()

    @patch("requests.post")
    def test_request_shutdown_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        from faith_cli.http_client import request_shutdown

        assert request_shutdown()

    @patch("requests.post", side_effect=Exception("timeout"))
    def test_request_shutdown_failure(self, mock_post):
        from faith_cli.http_client import request_shutdown

        assert not request_shutdown()


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

class TestBrowser:
    @patch("requests.get")
    @patch("faith_cli.browser._open_url")
    def test_wait_and_open_success(self, mock_open, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        from faith_cli.browser import wait_and_open_browser

        wait_and_open_browser()
        mock_open.assert_called_once()
```

---

## Integration Points

### `faith init` → Docker → Web UI → Wizard (FAITH-049)
```
User runs:  pip install faith-cli && faith init
CLI:        Creates ~/.faith/, extracts templates, pulls images, docker compose up -d
Docker:     PA container starts, detects no secrets.yaml → first-run mode
Web UI:     Wizard opens in browser (FAITH-049)
Wizard:     Writes secrets.yaml, creates .faith/ in project
```

### `faith stop` → PA Shutdown → Docker
```
User runs:  faith stop
CLI:        POST /api/shutdown → PA saves state.md per agent, stops managed containers
CLI:        docker compose down → stops PA, Redis, Web UI
```

### `faith run` → FAITH-054 (not implemented in this task)
The `faith run` command is a stub in this task — the actual `run` subcommand with `POST /api/task` and WebSocket blocking is implemented in FAITH-054.

---

## Acceptance Criteria

1. `pip install faith-cli` installs the `faith` command on PATH.
2. `faith --help` displays all available commands with descriptions.
3. `faith --version` displays the package version.
4. `faith init` creates `~/.faith/` with `config/`, `data/`, `logs/`, `config/archetypes/`.
5. `faith init` extracts bundled `docker-compose.yml`, `.env`, `secrets.yaml` template, and archetype files.
6. `faith init` checks Python 3.10+ first and exits with a clear prerequisite message if Python is too old.
7. `faith init` checks Docker and Docker Compose — exits with clear error and install link if missing.
8. `faith init` checks Git — warns but continues if missing.
9. `faith init` pulls Docker images and runs `docker compose up -d`.
10. `faith init` opens the browser to `http://localhost:8080` once the Web UI responds.
11. `faith init` detects existing `~/.faith/` and asks before re-initialising.
12. `faith start` starts containers if not already running; redirects to `faith init` if not initialised.
13. `faith stop` sends `POST /api/shutdown` to PA for coordinated teardown before `docker compose down`.
14. `faith stop` falls back to direct `docker compose down` if PA is unreachable.
15. `faith restart` stops then starts.
16. `faith status` shows container state, active project, and agent count.
17. `faith update` pulls latest images, stops, and restarts containers.
18. The optional persistent host worker can be started and stopped by `faith-cli` and runs with the same user privileges as the invoking user, not elevated/root privileges.
19. All commands work cross-platform (Windows, Linux, macOS).
20. Tests pass with `pytest`.

---

## Notes for Implementer

1. **This task does NOT implement `faith run`** — that is FAITH-054. This task creates the package structure and lifecycle commands only. Add a placeholder `run` command that prints "Not yet implemented — see FAITH-054."
2. **The bundled `docker-compose.yml`** must match the one created in FAITH-001. Keep them in sync.
3. **`importlib.resources`** is used to access bundled files. For Python 3.10 compatibility, use `importlib.resources.files()` (not the deprecated `importlib.resources.open_text`).
4. **Browser opening is best-effort** — if it fails (headless server, SSH session), the URL is printed to the terminal.
5. **`faith stop` coordinated shutdown** sends `POST /api/shutdown` to give the PA time to save `state.md` per agent. The `/api/shutdown` endpoint is created in FAITH-054. Until then, `faith stop` falls back to direct `docker compose down`.
6. **Do NOT use `docker-compose` (hyphenated, v1)** — use `docker compose` (space, v2) exclusively.
7. **Host worker ownership** belongs in `faith-cli`, not the PA container. The worker is a user-scoped local service/process that `faith-cli` supervises when host execution is enabled.
8. **Host-worker protocol ownership** belongs in the `faith_shared` package (`src/faith_shared/`); `faith-cli` implements and enforces it locally.
9. **No persistent root/admin worker in v1**. If an operation needs elevation later, handle it as an explicit elevated action rather than running the worker with elevated privileges by default.
10. **Compatibility enforcement** during `faith init` / `faith start` / `faith update` should use the version rules published by the `faith_shared` package (`src/faith_shared/`).
11. **The complexity was raised from S to M** because this is now a full Python package with `pyproject.toml`, multiple modules, bundled resources, cross-platform logic, and host-worker lifecycle concerns.
12. **Click** is used for the CLI framework (argument parsing, help generation, coloured output). It's lightweight and well-suited for this use case.

