"""Prerequisite checks for the FAITH CLI."""

from __future__ import annotations

import shutil
import subprocess

import click

DOCKER_INSTALL_URL = "https://docs.docker.com/get-docker/"
COMPOSE_INSTALL_URL = "https://docs.docker.com/compose/install/"
PYTHON_INSTALL_URL = "https://www.python.org/downloads/"


def check_python_version() -> None:
    """Retained for CLI flow symmetry; package metadata enforces Python 3.10+."""

    return None


def check_docker() -> None:
    """Verify Docker, Compose, and the daemon are available."""

    if not shutil.which("docker"):
        raise click.ClickException(
            f"Docker is not installed or not on PATH. Install it from {DOCKER_INSTALL_URL}"
        )

    info = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if info.returncode != 0:
        details = info.stderr.strip() or info.stdout.strip() or "Docker daemon is not running."
        raise click.ClickException(details)

    compose = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if compose.returncode != 0:
        raise click.ClickException(f"Docker Compose v2 is not available. See {COMPOSE_INSTALL_URL}")


def check_git() -> None:
    """Warn when Git is unavailable."""

    if shutil.which("git"):
        click.secho("Git detected.", fg="green")
        return
    click.secho("Git not found; FAITH will continue without Git-aware helpers.", fg="yellow")
