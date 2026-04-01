"""Docker Compose helpers for the FAITH CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

from faith_cli.paths import compose_file, source_root


def compose_project_directory() -> Path:
    """Return the project directory used for compose path resolution."""

    return source_root()


def compose_command(*args: str) -> list[str]:
    """Build the docker compose command for the current repo-backed PoC."""

    return [
        "docker",
        "compose",
        "--project-name",
        "faith",
        "--project-directory",
        str(compose_project_directory()),
        "-f",
        str(compose_file()),
        *args,
    ]


def run_compose(*args: str, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a docker compose command against the FAITH stack."""

    return subprocess.run(
        compose_command(*args),
        capture_output=capture_output,
        text=True,
        cwd=compose_project_directory(),
    )


def compose_up() -> subprocess.CompletedProcess[str]:
    return run_compose("up", "-d", "--remove-orphans")


def compose_down() -> subprocess.CompletedProcess[str]:
    return run_compose("down", "--remove-orphans")


def compose_status() -> subprocess.CompletedProcess[str]:
    return run_compose("ps", capture_output=True)


def compose_pull() -> subprocess.CompletedProcess[str]:
    return run_compose("pull")


def is_running() -> bool:
    result = run_compose("ps", "--status", "running", "-q", capture_output=True)
    return result.returncode == 0 and bool((result.stdout or "").strip())
