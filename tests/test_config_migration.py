"""Tests for config migration helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from faith.config.migration import CURRENT_SCHEMA_VERSION, MigrationEngine


def write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_check_all_detects_mismatched_versions(tmp_path):
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


def test_migrate_file_uses_dynamic_migration_script_and_creates_backup(tmp_path, monkeypatch):
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
        assert module_name == "faith.config.migrations.v0_9_to_v1_0"
        return SimpleNamespace(
            migrate_secrets=lambda data: {
                **data,
                "migrated_by": "fake-script",
            }
        )

    monkeypatch.setattr("faith.config.migration.importlib.import_module", fake_import)

    result = engine.migrate_file(item)

    assert result.success is True
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert "bak-v0.9" in result.backup_path.name
    migrated = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    assert migrated["migrated_by"] == "fake-script"
    assert migrated["openrouter_api_key"] == "sk-test"


def test_migrate_file_restores_backup_on_failure(tmp_path, monkeypatch):
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


def test_migration_guide_mentions_versions(tmp_path):
    config_dir = tmp_path / "config"
    write_yaml(config_dir / "secrets.yaml", {"schema_version": "0.9", "secrets": {}})

    engine = MigrationEngine(config_dir)
    item = engine.check_all()[0]
    guide = engine.migration_guide(item)

    assert "schema version" in guide
    assert item.current_version in guide
    assert CURRENT_SCHEMA_VERSION in guide
