"""Description:
    Provide Docker Compose helpers for the FAITH CLI.

Requirements:
    - Centralise compose command construction for the extracted FAITH stack.
    - Keep lifecycle commands operating against the installed framework home.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from faith_cli.paths import compose_file, faith_home, is_editable_install


def compose_project_directory() -> Path:
    """Description:
        Return the project directory used for compose path resolution.

    Requirements:
        - Point Docker Compose at the extracted FAITH home rather than the repo root.

    :returns: Installed FAITH home directory.
    """

    return faith_home()


def compose_command(*args: str) -> list[str]:
    """Description:
        Build the Docker Compose command for the extracted FAITH stack.

    Requirements:
        - Use the installed compose file from the framework home.
        - Keep one stable project name for all local FAITH services.

    :param args: Additional docker compose arguments to append.
    :returns: Full subprocess argument vector for Docker Compose.
    """

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
    """Description:
        Run one Docker Compose command against the FAITH stack.

    Requirements:
        - Execute compose from the installed FAITH home for stable path resolution.
        - Allow callers to request captured output for status-style commands.

    :param args: Additional docker compose arguments to append.
    :param capture_output: Whether stdout and stderr should be captured.
    :returns: Completed subprocess result from the compose invocation.
    """

    return subprocess.run(
        compose_command(*args),
        capture_output=capture_output,
        text=True,
        cwd=compose_project_directory(),
    )


def compose_up() -> subprocess.CompletedProcess[str]:
    """Description:
        Start the extracted FAITH bootstrap stack.

    Requirements:
        - Use ``--build`` for editable installs so local code changes are reflected.
        - Remove orphaned containers from older stack revisions.

    :returns: Completed subprocess result from ``docker compose up``.
    """

    if is_editable_install():
        return run_compose("up", "-d", "--remove-orphans", "--build")
    return run_compose("up", "-d", "--remove-orphans")


def compose_down() -> subprocess.CompletedProcess[str]:
    """Description:
        Stop the extracted FAITH bootstrap stack.

    Requirements:
        - Remove orphaned containers while tearing down the stack.

    :returns: Completed subprocess result from ``docker compose down``.
    """

    return run_compose("down", "--remove-orphans")


def compose_status() -> subprocess.CompletedProcess[str]:
    """Description:
        Return the current Docker Compose service status output.

    Requirements:
        - Capture stdout so CLI status commands can render the service table.

    :returns: Completed subprocess result from ``docker compose ps``.
    """

    return run_compose("ps", capture_output=True)


def compose_pull() -> subprocess.CompletedProcess[str]:
    """Description:
        Pull the bootstrap images referenced by the extracted compose file.

    Requirements:
        - Use the installed compose file rather than any repository-local copy.

    :returns: Completed subprocess result from ``docker compose pull``.
    """

    return run_compose("pull")


def is_running() -> bool:
    """Description:
        Return whether the extracted FAITH stack currently has running services.

    Requirements:
        - Query only running services.
        - Return ``False`` when Docker Compose fails or when no running services are found.

    :returns: ``True`` when at least one FAITH service is running, otherwise ``False``.
    """

    result = run_compose("ps", "--status", "running", "-q", capture_output=True)
    return result.returncode == 0 and bool((result.stdout or "").strip())
