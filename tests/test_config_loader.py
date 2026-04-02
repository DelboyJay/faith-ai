import textwrap
from pathlib import Path

import pytest

from faith_pa.config.loader import (
    ConfigLoadError,
    StartupValidationError,
    build_config_summary,
    load_secrets,
    load_tool_config,
    validate_startup_config,
)


def write_file(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).strip() + "\n", encoding="utf-8")


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    project_root = tmp_path / "project"
    faith_dir = project_root / ".faith"
    monkeypatch.setenv("FAITH_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))
    return config_dir, faith_dir


def test_load_secrets_with_env_substitution(config_env, monkeypatch):
    config_dir, _ = config_env
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    write_file(
        config_dir / "secrets.yaml",
        """
        schema_version: "1.0"
        secrets:
          openrouter_api_key: ${OPENROUTER_API_KEY}
        """,
    )

    secrets = load_secrets()
    assert secrets.secrets["openrouter_api_key"] == "test-key"


def test_load_tool_config_resolves_secret_refs(config_env):
    config_dir, faith_dir = config_env
    write_file(
        config_dir / "secrets.yaml",
        """
        schema_version: "1.0"
        secrets:
          db_password: super-secret
        """,
    )
    write_file(
        faith_dir / "tools" / "database.yaml",
        """
        schema_version: "1.0"
        connections:
          app:
            host: localhost
            database: appdb
            user: app
            password_secret_ref: db_password
        """,
    )

    config = load_tool_config("database.yaml", root=faith_dir.parent)
    connection = config.connections["app"]
    assert connection.password == "super-secret"


def test_validate_startup_config_reports_missing_project_files(config_env):
    config_dir, faith_dir = config_env
    write_file(
        config_dir / "secrets.yaml",
        """
        schema_version: "1.0"
        secrets: {}
        """,
    )

    with pytest.raises(StartupValidationError):
        validate_startup_config(root=faith_dir.parent)


def test_build_config_summary_redacts_secrets(config_env):
    config_dir, _ = config_env
    write_file(
        config_dir / ".env",
        "OPENROUTER_API_KEY=test-key\n",
    )
    write_file(
        config_dir / "secrets.yaml",
        """
        schema_version: "1.0"
        secrets:
          openrouter_api_key: ${OPENROUTER_API_KEY}
        """,
    )

    summary = build_config_summary()
    assert summary.secrets["openrouter_api_key"] == "***configured***"
    assert "OPENROUTER_API_KEY" in summary.env_keys


def test_load_tool_config_raises_on_unknown_secret_ref(config_env):
    _, faith_dir = config_env
    write_file(
        faith_dir / "tools" / "database.yaml",
        """
        schema_version: "1.0"
        connections:
          app:
            host: localhost
            database: appdb
            user: app
            password_secret_ref: missing_secret
        """,
    )

    with pytest.raises(ConfigLoadError):
        load_tool_config("database.yaml", root=faith_dir.parent)

