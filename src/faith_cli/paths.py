"""Resolve paths for the FAITH CLI bootstrap flow."""

from __future__ import annotations

from pathlib import Path


def package_root() -> Path:
    """Return the installed CLI package root."""

    return Path(__file__).resolve().parent


def package_resources_root() -> Path:
    """Return the bundled CLI resource directory."""

    return package_root() / "resources"


def bundled_config_dir() -> Path:
    """Return the bundled framework config template directory."""

    return package_resources_root() / "config"


def bundled_data_dir() -> Path:
    """Return the bundled framework data template directory."""

    return package_resources_root() / "data"


def bundled_compose_file() -> Path:
    """Return the bundled bootstrap compose file shipped with the CLI."""

    return package_resources_root() / "docker-compose.yml"


def source_root() -> Path:
    """Return the repository root for local development workflows."""

    return Path(__file__).resolve().parents[1]


def source_compose_file() -> Path:
    """Return the repository-backed compose file used for development."""

    return source_root() / "docker-compose.yml"


def faith_home() -> Path:
    """Return the user-owned FAITH home directory."""

    return Path.home() / ".faith"


def config_dir() -> Path:
    """Return the extracted framework config directory."""

    return faith_home() / "config"


def data_dir() -> Path:
    """Return the extracted framework data directory."""

    return faith_home() / "data"


def logs_dir() -> Path:
    """Return the extracted framework log directory."""

    return faith_home() / "logs"


def archetypes_dir() -> Path:
    """Return the extracted archetype directory."""

    return config_dir() / "archetypes"


def env_file() -> Path:
    """Return the extracted environment template path."""

    return config_dir() / ".env"


def secrets_file() -> Path:
    """Return the extracted secrets template path."""

    return config_dir() / "secrets.yaml"


def recent_projects_file() -> Path:
    """Return the framework recent-projects file path."""

    return config_dir() / "recent-projects.yaml"


def installed_compose_file() -> Path:
    """Return the extracted bootstrap compose file path."""

    return faith_home() / "docker-compose.yml"


def compose_file() -> Path:
    """Return the compose file used by the CLI runtime."""

    return installed_compose_file()


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
        installed_compose_file(),
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
