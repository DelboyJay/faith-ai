"""Resolve paths for the FAITH CLI bootstrap flow."""

from __future__ import annotations

from pathlib import Path


def source_root() -> Path:
    """Return the repository root for the current FAITH PoC."""

    return Path(__file__).resolve().parents[1]


def faith_home() -> Path:
    """Return the user-owned FAITH home directory."""

    return Path.home() / ".faith"


def config_dir() -> Path:
    return faith_home() / "config"


def data_dir() -> Path:
    return faith_home() / "data"


def logs_dir() -> Path:
    return faith_home() / "logs"


def archetypes_dir() -> Path:
    return config_dir() / "archetypes"


def env_file() -> Path:
    return config_dir() / ".env"


def secrets_file() -> Path:
    return config_dir() / "secrets.yaml"


def recent_projects_file() -> Path:
    return config_dir() / "recent-projects.yaml"


def installed_compose_file() -> Path:
    """Reserved for a future packaged compose bundle under ~/.faith/."""

    return faith_home() / "docker-compose.yml"


def source_compose_file() -> Path:
    """The active compose file for the current repository-backed PoC."""

    return source_root() / "docker-compose.yml"


def compose_file() -> Path:
    """Return the compose file used by the current CLI implementation."""

    return source_compose_file()


def is_initialised() -> bool:
    """Return True when the local FAITH home has been bootstrapped."""

    required_paths = [
        faith_home(),
        config_dir(),
        data_dir(),
        logs_dir(),
        archetypes_dir(),
        env_file(),
        secrets_file(),
        recent_projects_file(),
        source_compose_file(),
    ]
    return all(path.exists() for path in required_paths)


def is_first_run() -> bool:
    """Return True while the user has not provided secrets yet."""

    secrets = secrets_file()
    if not secrets.exists():
        return True
    content = secrets.read_text(encoding="utf-8").strip()
    if not content:
        return True
    return "your_" in content.lower() or "changeme" in content.lower()
