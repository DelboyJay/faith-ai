"""Description:
    Exercise FAITH config migration helpers.

Requirements:
    - Verify schema mismatch detection, backup creation, migration execution,
      rollback on failure, and human guidance.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from faith_pa.config.migration import CURRENT_SCHEMA_VERSION, MigrationEngine


def write_yaml(path: Path, data: dict) -> Path:
    """Description:
        Write a YAML fixture file for migration tests.

    Requirements:
        - Create parent directories automatically.

    :param path: Target file path.
    :param data: YAML payload to serialise.
    :returns: Written file path.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_check_all_detects_mismatched_versions(tmp_path: Path) -> None:
    """Description:
        Verify migration discovery finds every config file using the wrong schema version.

    Requirements:
        - This test is needed to prove config migration detects framework and project drift.
        - Verify all mismatched files are reported by `check_all()`.

    :param tmp_path: Temporary workspace root.
    """

    config_dir = tmp_path / "config"
    faith_dir = tmp_path / "project" / ".faith"

    write_yaml(config_dir / "secrets.yaml", {"schema_version": "0.9", "secrets": {}})
    write_yaml(
        faith_dir / "system.yaml",
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "privacy_profile": "internal",
            "pa": {"model": "x"},
            "default_agent_model": "y",
        },
    )
    write_yaml(faith_dir / "security.yaml", {"approval_rules": {}})
    write_yaml(faith_dir / "tools" / "database.yaml", {"schema_version": "0.8", "connections": {}})
    write_yaml(
        faith_dir / "agents" / "dev" / "config.yaml",
        {"schema_version": "0.7", "name": "Dev", "role": "Writes code"},
    )

    engine = MigrationEngine(config_dir, faith_dir)
    found = engine.check_all()

    assert len(found) == 4
    assert {item.file_path.name for item in found} == {
        "secrets.yaml",
        "security.yaml",
        "database.yaml",
        "config.yaml",
    }
    assert {item.current_version for item in found} == {"0.9", "unknown", "0.8", "0.7"}


def test_migrate_file_uses_dynamic_migration_script_and_creates_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Description:
        Verify migration execution imports the expected migration module and creates a backup.

    Requirements:
        - This test is needed to prove automatic migrations preserve the original file first.
        - Verify the migrated file receives the shared current schema version.

    :param tmp_path: Temporary workspace root.
    :param monkeypatch: Pytest monkeypatch fixture.
    """

    config_dir = tmp_path / "config"
    target = write_yaml(
        config_dir / "secrets.yaml",
        {
            "schema_version": "0.9",
            "secrets": {},
            "openrouter_api_key": "sk-test",
        },
    )

    engine = MigrationEngine(config_dir)
    item = engine.check_all()[0]

    def fake_import(module_name: str):
        return SimpleNamespace(
            migrate_secrets=lambda data: {
                **data,
                "migrated_by": module_name,
            }
        )

    monkeypatch.setattr("faith_pa.config.migration.importlib.import_module", fake_import)

    result = engine.migrate_file(item)

    assert result.success is True
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert "bak-v0.9" in result.backup_path.name
    migrated = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert migrated["migrated_by"] == "faith_pa.config.migrations.v0_9_to_v1_0"
    assert migrated["openrouter_api_key"] == "sk-test"


def test_migrate_file_restores_backup_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Description:
        Verify migration failure restores the original file contents.

    Requirements:
        - This test is needed to prove failed migrations do not leave configs half-written.
        - Verify the original file contents are restored after migration failure.

    :param tmp_path: Temporary workspace root.
    :param monkeypatch: Pytest monkeypatch fixture.
    """

    config_dir = tmp_path / "config"
    target = write_yaml(
        config_dir / "secrets.yaml",
        {"schema_version": "0.9", "secrets": {}, "token": "original"},
    )
    original_text = target.read_text(encoding="utf-8")

    engine = MigrationEngine(config_dir)
    item = engine.check_all()[0]

    def boom(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(MigrationEngine, "_apply_migrations", boom)

    result = engine.migrate_file(item)

    assert result.success is False
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert target.read_text(encoding="utf-8") == original_text


def test_migration_guide_mentions_versions(tmp_path: Path) -> None:
    """Description:
        Verify the migration guide names the observed and expected schema versions.

    Requirements:
        - This test is needed to prove CLI guidance is actionable when migration is required.
        - Verify the generated guidance includes both version identifiers.

    :param tmp_path: Temporary workspace root.
    """

    config_dir = tmp_path / "config"
    write_yaml(config_dir / "secrets.yaml", {"schema_version": "0.9", "secrets": {}})

    engine = MigrationEngine(config_dir)
    item = engine.check_all()[0]
    guide = engine.migration_guide(item)

    assert "schema version" in guide
    assert item.current_version in guide
    assert CURRENT_SCHEMA_VERSION in guide
