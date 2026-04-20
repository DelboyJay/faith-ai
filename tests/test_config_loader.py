"""Description:
    Exercise the FAITH config loader against the shared config contract.

Requirements:
    - Verify secrets substitution, secret-ref resolution, startup validation,
      schema export, and summary redaction.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from faith_pa.config.loader import (
    ConfigLoadError,
    ConfigValidationError,
    StartupValidationError,
    build_config_summary,
    load_config,
    load_secrets,
    load_system_config,
    load_tool_config,
    resolve_secret_ref,
    validate_startup_config,
)
from faith_shared.config.schema_export import export_schemas


def write_file(path: Path, contents: str) -> None:
    """Description:
        Write one UTF-8 text file for config-loader tests.

    Requirements:
        - Create parent directories automatically.
        - Normalise indentation in multiline YAML fixtures.

    :param path: Target file path.
    :param contents: Text content to write.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents).strip() + "\n", encoding="utf-8")


@pytest.fixture
def config_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Description:
        Create an isolated framework and project config environment.

    Requirements:
        - Redirect framework config and project root environment variables.

    :param tmp_path: Temporary test directory.
    :param monkeypatch: Pytest monkeypatch fixture.
    :returns: Tuple containing the config directory and `.faith` directory.
    """

    config_dir = tmp_path / "config"
    project_root = tmp_path / "project"
    faith_dir = project_root / ".faith"
    monkeypatch.setenv("FAITH_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("FAITH_PROJECT_ROOT", str(project_root))
    return config_dir, faith_dir


def test_load_secrets_with_env_substitution(
    config_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Description:
        Verify secrets loading substitutes environment placeholders.

    Requirements:
        - This test is needed to prove `secrets.yaml` resolves `${VAR}` values.
        - Verify the substituted secret value matches the environment input.

    :param config_env: Isolated config and project directories.
    :param monkeypatch: Pytest monkeypatch fixture.
    """

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


def test_load_tool_config_resolves_secret_refs(config_env: tuple[Path, Path]) -> None:
    """Description:
        Verify tool config loading resolves secret references from framework secrets.

    Requirements:
        - This test is needed to prove tool configs can reference `secrets.yaml`.
        - Verify the resolved database password is injected into the validated tool config.

    :param config_env: Isolated config and project directories.
    """

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


def test_validate_startup_config_reports_missing_project_files(
    config_env: tuple[Path, Path],
) -> None:
    """Description:
        Verify startup validation fails when project config files are missing.

    Requirements:
        - This test is needed to prove startup validation blocks incomplete projects.
        - Verify a startup validation error is raised when only framework secrets exist.

    :param config_env: Isolated config and project directories.
    """

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


def test_build_config_summary_redacts_secrets(config_env: tuple[Path, Path]) -> None:
    """Description:
        Verify the config summary redacts secret values.

    Requirements:
        - This test is needed to prove the PA status API does not leak raw secrets.
        - Verify configured secret keys are preserved while the values are redacted.

    :param config_env: Isolated config and project directories.
    """

    config_dir, _ = config_env
    write_file(config_dir / ".env", "OPENROUTER_API_KEY=test-key\n")
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


def test_load_tool_config_raises_on_unknown_secret_ref(config_env: tuple[Path, Path]) -> None:
    """Description:
        Verify tool loading rejects unknown secret references.

    Requirements:
        - This test is needed to prove tool configs cannot silently reference missing secrets.
        - Verify a config load error is raised for an unknown secret reference.

    :param config_env: Isolated config and project directories.
    """

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


def test_validate_startup_config_surfaces_human_readable_field_errors(
    config_env: tuple[Path, Path],
) -> None:
    """Description:
        Verify startup validation surfaces readable field-level validation failures.

    Requirements:
        - This test is needed to prove invalid configs do not leak raw Pydantic tracebacks.
        - Verify the failing field name appears in the aggregated startup message.

    :param config_env: Isolated config and project directories.
    """

    config_dir, faith_dir = config_env
    write_file(config_dir / "secrets.yaml", 'schema_version: "1.0"\nsecrets: {}\n')
    write_file(
        faith_dir / "system.yaml",
        """
        schema_version: "1.0"
        privacy_profile: internal
        default_agent_model: gpt-5.4
        """,
    )
    write_file(faith_dir / "security.yaml", 'schema_version: "1.0"\napproval_rules: {}\n')

    with pytest.raises(StartupValidationError) as exc_info:
        validate_startup_config(root=faith_dir.parent)

    assert "pa" in str(exc_info.value)
    assert "Validation failed" in str(exc_info.value)


def test_load_system_config_raises_config_validation_error(config_env: tuple[Path, Path]) -> None:
    """Description:
        Verify invalid config payloads raise the dedicated validation error type.

    Requirements:
        - This test is needed to prove callers can distinguish parse failures from validation failures.
        - Verify invalid system config raises `ConfigValidationError`.

    :param config_env: Isolated config and project directories.
    """

    config_dir, faith_dir = config_env
    write_file(config_dir / "secrets.yaml", 'schema_version: "1.0"\nsecrets: {}\n')
    write_file(
        faith_dir / "system.yaml",
        """
        schema_version: "1.0"
        default_agent_model: gpt-5.4
        """,
    )

    with pytest.raises(ConfigValidationError):
        load_system_config(root=faith_dir.parent)


def test_export_schemas_writes_shared_schema_files(tmp_path: Path) -> None:
    """Description:
        Verify shared schema export writes the canonical JSON schema files.

    Requirements:
        - This test is needed to prove shared config schemas are generated from the canonical models.
        - Verify all expected schema files are written to the target directory.

    :param tmp_path: Temporary output directory.
    """

    output_dir = tmp_path / "schemas"
    written = export_schemas(output_dir)

    assert {path.name for path in written} == {
        "agent-config.schema.json",
        "secrets.schema.json",
        "security.schema.json",
        "system.schema.json",
        "tool-config.schema.json",
    }
    assert all(path.exists() for path in written)


def test_load_config_infers_model_from_known_path(config_env: tuple[Path, Path]) -> None:
    """Description:
        Verify the generic loader infers the correct config model from a known file path.

    Requirements:
        - This test is needed to prove the public `load_config()` helper matches the task contract.
        - Verify loading `system.yaml` without an explicit model returns a validated system config.

    :param config_env: Isolated config and project directories.
    """

    config_dir, faith_dir = config_env
    write_file(config_dir / "secrets.yaml", 'schema_version: "1.0"\nsecrets: {}\n')
    system_path = faith_dir / "system.yaml"
    write_file(
        system_path,
        """
        schema_version: "1.0"
        privacy_profile: internal
        pa:
          model: gpt-5.4
        default_agent_model: gpt-5.4-mini
        """,
    )

    config = load_config(system_path)
    assert config.default_agent_model == "gpt-5.4-mini"


def test_resolve_secret_ref_returns_secret_value(config_env: tuple[Path, Path]) -> None:
    """Description:
        Verify the public secret-ref helper resolves known keys and tolerates unknown keys.

    Requirements:
        - This test is needed to prove callers can resolve one secret value directly from loaded secrets.
        - Verify known keys resolve and unknown keys return `None`.

    :param config_env: Isolated config and project directories.
    """

    config_dir, _ = config_env
    write_file(
        config_dir / "secrets.yaml",
        """
        schema_version: "1.0"
        secrets:
          db_password: super-secret
        """,
    )

    secrets = load_secrets()
    assert resolve_secret_ref("db_password", secrets) == "super-secret"
    assert resolve_secret_ref("missing", secrets) is None
