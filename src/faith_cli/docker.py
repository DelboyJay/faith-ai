"""Description:
    Provide Docker Compose helpers for the FAITH CLI.

Requirements:
    - Centralise compose command construction for the extracted FAITH stack.
    - Keep lifecycle commands operating against the installed framework home.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

from faith_cli.paths import compose_file, faith_home, is_editable_install

DEFAULT_OLLAMA_MODEL = "llama3:8b"
BOOTSTRAP_CONTAINER_NAMES = (
    "faith-pa",
    "faith-web-ui",
    "faith-redis",
    "faith-ollama",
    "faith-mcp-registry",
    "faith-mcp-registry-db",
)


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
        encoding="utf-8",
        errors="replace",
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


def install_default_ollama_model(
    model: str = DEFAULT_OLLAMA_MODEL,
) -> subprocess.CompletedProcess[str]:
    """Description:
        Pull the default local PA model into the managed Ollama service.

    Requirements:
        - Install the 6GB-GPU baseline model during first-run bootstrap.
        - Use the extracted compose project so the model lands in the FAITH Ollama volume.

    :param model: Ollama model tag to pull.
    :returns: Completed subprocess result from ``ollama pull``.
    """

    result = run_compose("exec", "-T", "ollama", "ollama", "pull", model, capture_output=True)
    if result.returncode == 0:
        return result

    existing = existing_bootstrap_containers()
    if existing.get("faith-ollama") == "running":
        return subprocess.run(
            ["docker", "exec", "faith-ollama", "ollama", "pull", model],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=compose_project_directory(),
        )
    return result


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


def existing_bootstrap_containers() -> dict[str, str]:
    """Description:
        Return any FAITH bootstrap containers that already exist on the host.

    Requirements:
        - Inspect containers by their fixed bootstrap names regardless of compose project labels.
        - Return Docker state strings such as ``running`` or ``exited`` keyed by container name.
        - Ignore names that are not currently present.

    :returns: Mapping of bootstrap container name to Docker state.
    """

    states: dict[str, str] = {}
    for container_name in BOOTSTRAP_CONTAINER_NAMES:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=compose_project_directory(),
        )
        if result.returncode == 0:
            states[container_name] = (result.stdout or "").strip() or "unknown"
    return states


def remove_bootstrap_containers(container_names: Iterable[str]) -> subprocess.CompletedProcess[str]:
    """Description:
        Force-remove one or more existing FAITH bootstrap containers by name.

    Requirements:
        - Be safe to call with an empty iterable.
        - Preserve host-backed data by removing only containers, not volumes.

    :param container_names: Bootstrap container names to remove.
    :returns: Completed subprocess result from the Docker remove command.
    """

    ordered_names = [name for name in BOOTSTRAP_CONTAINER_NAMES if name in set(container_names)]
    if not ordered_names:
        return subprocess.CompletedProcess(["docker", "rm", "-f"], 0, "", "")
    return subprocess.run(
        ["docker", "rm", "-f", *ordered_names],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=compose_project_directory(),
    )
