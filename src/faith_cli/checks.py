"""Description:
    Run prerequisite checks for the FAITH CLI.

Requirements:
    - Validate that required host tools are available before bootstrap commands run.
    - Raise actionable CLI errors when a prerequisite is missing or unhealthy.
"""

from __future__ import annotations

import shutil
import subprocess

import click

DOCKER_INSTALL_URL = "https://docs.docker.com/get-docker/"
COMPOSE_INSTALL_URL = "https://docs.docker.com/compose/install/"
PYTHON_INSTALL_URL = "https://www.python.org/downloads/"
DOCKER_DESKTOP_NOT_RUNNING_GUIDANCE = (
    "Docker Desktop does not appear to be running on your system. Please run it first, "
    "wait until the engine is running, and then run `faith init` or `faith start` again."
)
DOCKER_TIMEOUT_GUIDANCE = (
    f"Docker did not respond within 10 seconds. {DOCKER_DESKTOP_NOT_RUNNING_GUIDANCE}"
)


def check_python_version() -> None:
    """Description:
        Preserve an explicit Python prerequisite check hook for CLI flows.

    Requirements:
        - Keep the check callable even though package metadata currently enforces the minimum version.
        - Avoid changing command flow while the repository still expects this hook.
    """

    return None


def check_docker() -> None:
    """Description:
        Verify Docker, Docker Compose, and the Docker daemon are available.

    Requirements:
        - Fail fast when the Docker executable is missing from PATH.
        - Verify the daemon responds before any CLI command tries to use compose.
        - Verify Docker Compose v2 is installed and callable.

    :raises click.ClickException: If Docker, Docker Compose, or the daemon is unavailable.
    """

    if not shutil.which("docker"):
        raise click.ClickException(
            f"Docker is not installed or not on PATH. Install it from {DOCKER_INSTALL_URL}"
        )

    try:
        info = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException(DOCKER_TIMEOUT_GUIDANCE) from exc
    if info.returncode != 0:
        details = _normalise_docker_daemon_error(
            info.stderr.strip() or info.stdout.strip() or "Docker daemon is not running."
        )
        raise click.ClickException(details)

    try:
        compose = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException(DOCKER_TIMEOUT_GUIDANCE) from exc
    if compose.returncode != 0:
        raise click.ClickException(f"Docker Compose v2 is not available. See {COMPOSE_INSTALL_URL}")


def _normalise_docker_daemon_error(details: str) -> str:
    """Description:
        Convert low-level Docker daemon errors into user-friendly CLI guidance.

    Requirements:
        - Detect the Windows Docker Desktop named-pipe failure and map it to one standard message.
        - Preserve the original error text for unknown daemon failures so useful diagnostics are not lost.

    :param details: Raw stderr or stdout text returned by the Docker CLI.
    :returns: User-facing Docker daemon guidance.
    """

    lowered = details.lower()
    if (
        "dockerdesktoplinuxengine" in lowered
        and "the system cannot find the file specified" in lowered
    ):
        return DOCKER_DESKTOP_NOT_RUNNING_GUIDANCE
    return details


def check_git() -> None:
    """Description:
        Warn the user when Git is unavailable on the host.

    Requirements:
        - Report a positive signal when Git is present.
        - Avoid blocking FAITH startup when Git is absent.
    """

    if shutil.which("git"):
        click.secho("Git detected.", fg="green")
        return
    click.secho("Git not found; FAITH will continue without Git-aware helpers.", fg="yellow")
