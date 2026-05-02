# FAITH-006 — Config Migration System

**Phase:** 1 — Foundation
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-003
**FRS Reference:** Section 7.6

---

## Objective

Implement a config schema migration system that detects version mismatches on startup, guides the user through migration (auto or manual), and creates backups before making changes. This ensures FAITH upgrades never silently break existing configurations.

---

## Architecture

```
faith/config/
├── migration.py      ← MigrationEngine (this task)
├── migrations/       ← migration scripts, one per version bump
│   ├── __init__.py
│   └── v1_0_to_v1_1.py   ← example future migration
├── loader.py         ← (FAITH-003)
├── models.py         ← (FAITH-003)
└── watcher.py        ← (FAITH-004)
```

---

## Files to Create

### 1. `faith/config/migration.py`

```python
"""FAITH config migration engine.

Detects schema version mismatches on startup and provides
auto-migration with backup, or manual migration guidance.
"""

from __future__ import annotations

import importlib
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("faith.config.migration")

# Current schema version — must match the schema_version in models.py defaults
CURRENT_SCHEMA_VERSION = "1.0"


class MigrationEngine:
    """Handles config schema version detection and migration.

    Checks config files in both locations:
    - Framework-level: ~/.faith/config/secrets.yaml
    - Project-level: <project>/.faith/system.yaml, <project>/.faith/security.yaml,
      <project>/.faith/tools/*.yaml, <project>/.faith/agents/*/config.yaml

    Attributes:
        framework_config_dir: Path to ~/.faith/config/ directory (framework home).
        faith_dir: Path to the project's .faith/ directory (optional).
    """

    def __init__(self, framework_config_dir: Path, faith_dir: Optional[Path] = None):
        self.framework_config_dir = framework_config_dir
        self.faith_dir = faith_dir

    def check_all(self) -> list[MigrationNeeded]:
        """Check all config files for version mismatches.

        Returns:
            List of MigrationNeeded objects for files that need migration.
            Empty list if all files are current.
        """
        results = []

        # Framework-level: config/secrets.yaml
        self._check_file(self.framework_config_dir / "secrets.yaml", results)

        # Project-level (if a project is open)
        if self.faith_dir and self.faith_dir.exists():
            # Direct config files
            self._check_file(self.faith_dir / "system.yaml", results)
            self._check_file(self.faith_dir / "security.yaml", results)

            # Per-tool configs
            tools_dir = self.faith_dir / "tools"
            if tools_dir.exists():
                for tool_file in tools_dir.glob("*.yaml"):
                    self._check_file(tool_file, results)

            # Per-agent configs
            agents_dir = self.faith_dir / "agents"
            if agents_dir.exists():
                for agent_dir in agents_dir.iterdir():
                    if agent_dir.is_dir():
                        self._check_file(agent_dir / "config.yaml", results)

        return results

    def _check_file(self, file_path: Path, results: list[MigrationNeeded]) -> None:
        """Check a single file for version mismatch."""
        if not file_path.exists():
            return

        file_version = self._read_schema_version(file_path)
        if file_version is None:
            results.append(MigrationNeeded(
                file_path=file_path,
                current_version="unknown",
                target_version=CURRENT_SCHEMA_VERSION,
            ))
        elif file_version != CURRENT_SCHEMA_VERSION:
            results.append(MigrationNeeded(
                file_path=file_path,
                current_version=file_version,
                target_version=CURRENT_SCHEMA_VERSION,
            ))

    def migrate_file(self, migration: MigrationNeeded) -> MigrationResult:
        """Auto-migrate a single config file.

        1. Creates a backup of the original.
        2. Attempts to load and apply the migration script.
        3. Writes the migrated config with updated schema_version.

        Args:
            migration: The MigrationNeeded object describing what to migrate.

        Returns:
            MigrationResult with success/failure and details.
        """
        # Create backup
        backup_path = self._create_backup(migration.file_path)

        try:
            # Load current data
            raw_text = migration.file_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw_text) or {}

            # Find and apply migration function
            migrated = self._apply_migrations(
                data,
                migration.current_version,
                migration.target_version,
                migration.file_path.name,
            )

            # Update schema version
            migrated["schema_version"] = migration.target_version

            # Write migrated config
            migration.file_path.write_text(
                yaml.dump(migrated, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )

            logger.info(
                f"Migrated {migration.file_path.name}: "
                f"{migration.current_version} → {migration.target_version}"
            )

            return MigrationResult(
                file_path=migration.file_path,
                success=True,
                backup_path=backup_path,
                message=f"Migrated from {migration.current_version} to {migration.target_version}",
            )

        except Exception as e:
            # Restore from backup on failure
            if backup_path.exists():
                shutil.copy2(backup_path, migration.file_path)
                logger.info(f"Restored {migration.file_path.name} from backup")

            logger.error(f"Migration failed for {migration.file_path.name}: {e}")

            return MigrationResult(
                file_path=migration.file_path,
                success=False,
                backup_path=backup_path,
                message=f"Migration failed: {e}",
            )

    def migrate_all(self, migrations: list[MigrationNeeded]) -> list[MigrationResult]:
        """Auto-migrate all files that need migration.

        Args:
            migrations: List of MigrationNeeded from check_all().

        Returns:
            List of MigrationResult for each file.
        """
        return [self.migrate_file(m) for m in migrations]

    def _create_backup(self, file_path: Path) -> Path:
        """Create a timestamped backup of a config file.

        Returns:
            Path to the backup file.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        version = self._read_schema_version(file_path) or "unknown"
        backup_name = f"{file_path.stem}.bak-v{version}-{timestamp}{file_path.suffix}"
        backup_path = file_path.parent / backup_name
        shutil.copy2(file_path, backup_path)
        logger.info(f"Backup created: {backup_path}")
        return backup_path

    def _apply_migrations(
        self,
        data: dict,
        from_version: str,
        to_version: str,
        filename: str,
    ) -> dict:
        """Apply migration scripts sequentially from from_version to to_version.

        Migration scripts are in faith/config/migrations/ and named:
        v{from}_to_v{to}.py with a migrate_{filename_stem}(data) function.

        If no migration script exists for a version pair, the data is returned
        unchanged with only the schema_version bumped. This handles the case
        where a version bump adds only new optional fields (which Pydantic
        fills with defaults).
        """
        current = from_version
        file_stem = filename.replace(".yaml", "")

        # For now, with only v1.0, no migration scripts exist.
        # When v1.1 is released, a migration script will be added:
        # faith/config/migrations/v1_0_to_v1_1.py
        #
        # The migration chain would be:
        # v1.0 → v1.1 → v1.2 → ... → target
        #
        # Each step tries to import and call:
        # migrations.v1_0_to_v1_1.migrate_system(data) -> data
        # migrations.v1_0_to_v1_1.migrate_agents(data) -> data
        # etc.

        migration_module_name = f"v{current.replace('.', '_')}_to_v{to_version.replace('.', '_')}"

        try:
            module = importlib.import_module(
                f"faith.config.migrations.{migration_module_name}"
            )
            migrate_fn = getattr(module, f"migrate_{file_stem}", None)
            if migrate_fn:
                data = migrate_fn(data)
                logger.info(
                    f"Applied migration {migration_module_name}.migrate_{file_stem}"
                )
        except ModuleNotFoundError:
            # No migration script — only schema_version bump needed
            logger.info(
                f"No migration script for {migration_module_name} — "
                f"applying default (schema_version bump only)"
            )

        return data

    @staticmethod
    def _read_schema_version(file_path: Path) -> Optional[str]:
        """Read the schema_version field from a YAML config file.

        Returns:
            The schema_version string, or None if not present.
        """
        try:
            data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get("schema_version")
        except Exception:
            pass
        return None


class MigrationNeeded:
    """Describes a config file that needs migration."""

    def __init__(self, file_path: Path, current_version: str, target_version: str):
        self.file_path = file_path
        self.current_version = current_version
        self.target_version = target_version

    def describe(self) -> str:
        """Human-readable description for the Web UI."""
        return (
            f"`{self.file_path.name}`: version {self.current_version} → "
            f"{self.target_version}"
        )


class MigrationResult:
    """Result of a migration attempt."""

    def __init__(
        self,
        file_path: Path,
        success: bool,
        backup_path: Path,
        message: str,
    ):
        self.file_path = file_path
        self.success = success
        self.backup_path = backup_path
        self.message = message
```

### 2. `faith/config/migrations/__init__.py`

```python
"""FAITH config migration scripts.

Each migration is a module named v{from}_to_v{to}.py
containing migrate_{config_name}(data: dict) -> dict functions.
"""
```

### 3. `tests/test_config_migration.py`

```python
"""Tests for the config migration system."""

from pathlib import Path

import pytest
import yaml

from faith.config.migration import (
    MigrationEngine,
    CURRENT_SCHEMA_VERSION,
)


@pytest.fixture
def framework_config_dir(tmp_path):
    d = tmp_path / "config"
    d.mkdir()
    return d


@pytest.fixture
def faith_dir(tmp_path):
    d = tmp_path / ".faith"
    d.mkdir()
    (d / "tools").mkdir()
    (d / "agents").mkdir()
    return d


@pytest.fixture
def engine(framework_config_dir, faith_dir):
    return MigrationEngine(framework_config_dir, faith_dir)


def write_config(directory: Path, filename: str, data: dict) -> Path:
    path = directory / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))
    return path


def test_no_migration_needed(faith_dir, engine):
    """Current-version configs should not need migration."""
    write_config(faith_dir, "system.yaml", {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "privacy_profile": "internal",
        "pa": {"model": "test"},
        "default_agent_model": "test",
    })
    results = engine.check_all()
    assert len(results) == 0


def test_detects_old_version(faith_dir, engine):
    """Older schema_version should be flagged for migration."""
    write_config(faith_dir, "system.yaml", {
        "schema_version": "0.9",
        "privacy_profile": "internal",
        "pa": {"model": "test"},
        "default_agent_model": "test",
    })
    results = engine.check_all()
    assert len(results) == 1
    assert results[0].current_version == "0.9"
    assert results[0].target_version == CURRENT_SCHEMA_VERSION


def test_detects_missing_version(faith_dir, engine):
    """Config with no schema_version should be flagged."""
    write_config(faith_dir, "system.yaml", {
        "privacy_profile": "internal",
        "pa": {"model": "test"},
        "default_agent_model": "test",
    })
    results = engine.check_all()
    assert len(results) == 1
    assert results[0].current_version == "unknown"


def test_creates_backup(faith_dir, engine):
    """Migration should create a backup before modifying."""
    write_config(faith_dir, "system.yaml", {
        "schema_version": "0.9",
        "privacy_profile": "internal",
        "pa": {"model": "test"},
        "default_agent_model": "test",
    })
    migrations = engine.check_all()
    results = engine.migrate_all(migrations)

    assert results[0].success is True
    assert results[0].backup_path.exists()
    assert "bak-v0.9" in results[0].backup_path.name


def test_updates_schema_version(faith_dir, engine):
    """Migration should update the schema_version field."""
    write_config(faith_dir, "system.yaml", {
        "schema_version": "0.9",
        "privacy_profile": "internal",
        "pa": {"model": "test"},
        "default_agent_model": "test",
    })
    migrations = engine.check_all()
    engine.migrate_all(migrations)

    data = yaml.safe_load((faith_dir / "system.yaml").read_text())
    assert data["schema_version"] == CURRENT_SCHEMA_VERSION


def test_preserves_existing_data(faith_dir, engine):
    """Migration should not lose existing config values."""
    write_config(faith_dir, "system.yaml", {
        "schema_version": "0.9",
        "privacy_profile": "confidential",
        "pa": {"model": "my-custom-model"},
        "default_agent_model": "ollama/llama3:8b",
        "editor": "vim",
    })
    migrations = engine.check_all()
    engine.migrate_all(migrations)

    data = yaml.safe_load((faith_dir / "system.yaml").read_text())
    assert data["privacy_profile"] == "confidential"
    assert data["pa"]["model"] == "my-custom-model"
    assert data["editor"] == "vim"


def test_skips_missing_files(framework_config_dir):
    """Missing config files should not cause errors."""
    engine = MigrationEngine(framework_config_dir, None)
    results = engine.check_all()
    assert len(results) == 0


def test_checks_per_agent_config(faith_dir, engine):
    """Migration should check per-agent config.yaml files."""
    agent_dir = faith_dir / "agents" / "software-developer"
    agent_dir.mkdir(parents=True)
    write_config(agent_dir, "config.yaml", {
        "schema_version": "0.8",
        "name": "Software Developer",
        "role": "Writes code",
    })
    migrations = engine.check_all()
    assert len(migrations) == 1
    assert "config.yaml" in migrations[0].describe()
    assert "0.8" in migrations[0].describe()


def test_checks_per_tool_config(faith_dir, engine):
    """Migration should check per-tool config files."""
    write_config(faith_dir / "tools", "database.yaml", {
        "schema_version": "0.7",
        "connections": {},
    })
    migrations = engine.check_all()
    assert len(migrations) == 1
    assert "database.yaml" in migrations[0].describe()


def test_checks_framework_secrets(framework_config_dir, engine):
    """Migration should check framework-level secrets.yaml."""
    write_config(framework_config_dir, "secrets.yaml", {
        "schema_version": "0.5",
    })
    migrations = engine.check_all()
    assert len(migrations) == 1
    assert "secrets.yaml" in migrations[0].describe()
```

---

## Integration with PA Startup

The migration check runs **before** `load_all_configs()` and before any containers are started. The flow in the PA's `main()`:

```python
async def startup():
    # framework_config_dir points to ~/.faith/config/ (set during faith init)
    framework_config_dir = Path.home() / ".faith" / "config"
    # faith_dir is determined after project is opened — None at first
    faith_dir = None  # Set later when project workspace is mounted

    # Step 1: Check framework-level migrations (secrets.yaml)
    engine = MigrationEngine(framework_config_dir, faith_dir)
    migrations_needed = engine.check_all()

    if migrations_needed:
        for m in migrations_needed:
            logger.warning(f"Migration needed: {m.describe()}")

        results = engine.migrate_all(migrations_needed)
        for r in results:
            if not r.success:
                logger.error(f"Migration failed: {r.message}")
                raise SystemExit(f"Config migration failed: {r.file_path.name}")

    # Step 2: Load framework-level secrets (FAITH-003)
    secrets = load_secrets(framework_config_dir)

    # Step 3: Open project workspace, then check project-level migrations
    # faith_dir = workspace_path / ".faith"
    # engine = MigrationEngine(framework_config_dir, faith_dir)
    # ... check and migrate project configs ...

    # Step 4: Load project configs (FAITH-003)
    # configs = load_project_configs(faith_dir)

    # Step 5: Continue startup...
```

---

## Acceptance Criteria

1. `MigrationEngine.check_all()` correctly detects config files with a `schema_version` older than `CURRENT_SCHEMA_VERSION`.
2. `MigrationEngine.check_all()` flags config files with no `schema_version` field as needing migration.
3. `MigrationEngine.check_all()` returns an empty list when all files are current.
4. `migrate_file()` creates a timestamped backup before modifying any file. Backup filename includes the old version number.
5. `migrate_file()` updates `schema_version` to the current version.
6. `migrate_file()` preserves all existing config data — no fields are lost.
7. On migration failure, the original file is restored from backup.
8. Migration scripts (when they exist) are dynamically imported and applied.
9. All tests in `tests/test_config_migration.py` pass.

---

## Notes for Implementer

- There are NO migration scripts yet (v1.0 is the first version). The `faith/config/migrations/` directory is created with just an `__init__.py`. The migration infrastructure is built now so it's ready when v1.1 is released.
- When a future version adds a new required field, the migration script adds it with a sensible default. Example for a hypothetical v1.0 → v1.1 migration:

```python
# faith/config/migrations/v1_0_to_v1_1.py
def migrate_system(data: dict) -> dict:
    """Add new 'telemetry_opt_in' field (default: false)."""
    data.setdefault("telemetry_opt_in", False)
    return data
```

- Backups accumulate alongside the original files (in `.faith/` for project configs, in `~/.faith/config/` for framework secrets). They are small YAML files — no cleanup is needed unless the user explicitly removes them.
- Migration runs in two phases: framework-level (secrets.yaml) runs at PA startup, project-level runs when a project workspace is opened. The PA startup sequence is implemented in FAITH-014/FAITH-016. This task only creates the `MigrationEngine` class and its tests.
