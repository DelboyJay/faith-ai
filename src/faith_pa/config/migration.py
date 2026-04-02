"""Description:
    Detect and apply schema migrations for FAITH configuration files.

Requirements:
    - Inspect framework and project configuration files for schema drift.
    - Create backups before mutating any configuration file.
    - Restore the original file when a migration attempt fails.
"""

from __future__ import annotations

import importlib
import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from faith_shared.compatibility import CURRENT_SCHEMA_VERSION

_MIGRATION_PACKAGE = "faith_pa.config.migrations"
logger = logging.getLogger("faith.config.migration")


@dataclass(slots=True)
class MigrationNeeded:
    """Description:
        Describe one configuration file that requires schema migration.

    Requirements:
        - Preserve both the current and target schema versions.

    :param file_path: Configuration file requiring migration.
    :param current_version: Schema version currently recorded in the file.
    :param target_version: Schema version expected by the runtime.
    """

    file_path: Path
    current_version: str
    target_version: str = CURRENT_SCHEMA_VERSION

    def describe(self) -> str:
        """Description:
            Return a short human-readable summary of the migration need.

        Requirements:
            - Include the filename and version transition.

        :returns: Human-readable migration summary.
        """

        return f"{self.file_path.name}: {self.current_version} -> {self.target_version}"


@dataclass(slots=True)
class MigrationResult:
    """Description:
        Describe the result of attempting one configuration migration.

    Requirements:
        - Preserve whether the migration succeeded and where the backup lives.

    :param file_path: Configuration file that was processed.
    :param success: Whether the migration completed successfully.
    :param backup_path: Backup path created before migration, when available.
    :param message: Human-readable result message.
    """

    file_path: Path
    success: bool
    backup_path: Path | None
    message: str

    def describe(self) -> str:
        """Description:
            Return a short human-readable summary of the migration result.

        Requirements:
            - Include the filename and result message.

        :returns: Human-readable migration result summary.
        """

        return f"{self.file_path.name}: {self.message}"


class MigrationEngine:
    """Description:
        Check FAITH configuration files for schema drift and migrate them when needed.

    Requirements:
        - Cover both framework-level and project-level configuration files.
        - Create versioned backups before mutating a file.
        - Restore the original file if migration fails.

    :param framework_config_dir: Framework configuration directory under ``~/.faith``.
    :param project_faith_dir: Optional project-local ``.faith`` directory.
    """

    def __init__(self, framework_config_dir: Path, project_faith_dir: Path | None = None) -> None:
        """Description:
            Initialise the migration engine.

        Requirements:
            - Normalise the configured directories to ``Path`` instances.

        :param framework_config_dir: Framework configuration directory under ``~/.faith``.
        :param project_faith_dir: Optional project-local ``.faith`` directory.
        """

        self.framework_config_dir = Path(framework_config_dir)
        self.project_faith_dir = Path(project_faith_dir) if project_faith_dir else None

    def iter_config_files(self) -> Iterable[Path]:
        """Description:
            Yield the configuration files managed by the migration engine.

        Requirements:
            - Include framework secrets when present.
            - Include project system, security, tool, and agent config files when present.

        :yields: Configuration file paths covered by migration checks.
        """

        secrets = self.framework_config_dir / "secrets.yaml"
        if secrets.exists():
            yield secrets

        if not self.project_faith_dir or not self.project_faith_dir.exists():
            return

        for filename in ("system.yaml", "security.yaml"):
            path = self.project_faith_dir / filename
            if path.exists():
                yield path

        tools_dir = self.project_faith_dir / "tools"
        if tools_dir.exists():
            yield from sorted(tools_dir.glob("*.yaml"))

        agents_dir = self.project_faith_dir / "agents"
        if agents_dir.exists():
            yield from sorted(agents_dir.glob("*/config.yaml"))

    def _check_file(self, path: Path, results: list[MigrationNeeded]) -> None:
        """Description:
            Append a migration need for one file when its schema version is outdated.

        Requirements:
            - Ignore missing files.
            - Compare the discovered version with the current shared schema version.

        :param path: Configuration file path to inspect.
        :param results: Mutable collection of migration needs to extend.
        """

        if not path.exists():
            return

        current_version = self.read_schema_version(path)
        if current_version != CURRENT_SCHEMA_VERSION:
            results.append(
                MigrationNeeded(
                    file_path=path,
                    current_version=current_version,
                    target_version=CURRENT_SCHEMA_VERSION,
                )
            )

    def read_schema_version(self, path: Path) -> str:
        """Description:
            Read the schema version recorded in one configuration file.

        Requirements:
            - Return ``invalid`` when the file cannot be parsed into a mapping.
            - Return ``unknown`` when the mapping has no schema version field.

        :param path: Configuration file path to inspect.
        :returns: Discovered schema version marker.
        """

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            return "invalid"

        if not isinstance(data, dict):
            return "invalid"

        version = data.get("schema_version")
        if version in (None, ""):
            return "unknown"
        return str(version)

    def check_all(self) -> list[MigrationNeeded]:
        """Description:
            Return the full set of configuration files that need migration.

        Requirements:
            - Inspect every configuration file yielded by ``iter_config_files``.

        :returns: List of migration requirements.
        """

        results: list[MigrationNeeded] = []
        for path in self.iter_config_files():
            self._check_file(path, results)
        return results

    def create_backup(self, path: Path) -> Path:
        """Description:
            Create a timestamped backup of one configuration file.

        Requirements:
            - Include the source schema version in the backup filename.
            - Preserve the original file metadata using ``copy2``.

        :param path: Configuration file to back up.
        :returns: Backup file path.
        """

        version = self.read_schema_version(path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup = path.with_name(f"{path.stem}.bak-v{version}-{stamp}{path.suffix}")
        shutil.copy2(path, backup)
        logger.info("Created backup for %s at %s", path.name, backup)
        return backup

    def _apply_migrations(
        self,
        data: dict,
        from_version: str,
        to_version: str,
        filename: str,
    ) -> dict:
        """Description:
            Apply the matching migration function for one configuration file.

        Requirements:
            - Skip migration when the source version is unknown, invalid, or already current.
            - Accept either a file-specific ``migrate_<stem>`` function or a generic ``migrate``.
            - Require the migration function to return a mapping.

        :param data: Parsed configuration payload.
        :param from_version: Source schema version.
        :param to_version: Target schema version.
        :param filename: Configuration filename being migrated.
        :returns: Migrated configuration mapping.
        :raises TypeError: If the migration function returns a non-mapping payload.
        """

        if from_version in {"unknown", "invalid"} or from_version == to_version:
            return data

        module_name = f"v{from_version.replace('.', '_')}_to_v{to_version.replace('.', '_')}"
        file_stem = Path(filename).stem

        try:
            module = importlib.import_module(f"{_MIGRATION_PACKAGE}.{module_name}")
        except ModuleNotFoundError:
            logger.info("No migration script found for %s", module_name)
            return data

        migrate_fn = getattr(module, f"migrate_{file_stem}", None) or getattr(
            module, "migrate", None
        )
        if migrate_fn is None:
            logger.info("Migration module %s does not define migrate_%s()", module_name, file_stem)
            return data

        migrated = migrate_fn(data)
        if not isinstance(migrated, dict):
            raise TypeError(f"{module_name}.migrate_{file_stem}() must return a dict")
        return migrated

    def migrate_file(self, item: MigrationNeeded) -> MigrationResult:
        """Description:
            Migrate one configuration file to the current schema version.

        Requirements:
            - Create a backup before changing the file.
            - Restore the backup when migration fails.
            - Update the stored schema version after successful migration.

        :param item: Migration requirement to process.
        :returns: Migration result for the processed file.
        """

        backup = self.create_backup(item.file_path)

        try:
            data = yaml.safe_load(item.file_path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError("config root must be a mapping")

            migrated = self._apply_migrations(
                data=data,
                from_version=item.current_version,
                to_version=item.target_version,
                filename=item.file_path.name,
            )
            migrated["schema_version"] = item.target_version
            item.file_path.write_text(
                yaml.safe_dump(migrated, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )
            message = f"Migrated from {item.current_version} to {item.target_version}"
            logger.info("%s", message)
            return MigrationResult(item.file_path, True, backup, message)
        except Exception as exc:
            shutil.copy2(backup, item.file_path)
            message = f"Migration failed: {exc}"
            logger.warning("%s; restored %s from backup", message, item.file_path.name)
            return MigrationResult(item.file_path, False, backup, message)

    def migrate_all(self, items: list[MigrationNeeded]) -> list[MigrationResult]:
        """Description:
            Migrate every configuration file in the supplied worklist.

        Requirements:
            - Preserve the ordering of the supplied migration worklist.

        :param items: Migration requirements to process.
        :returns: Ordered migration results.
        """

        return [self.migrate_file(item) for item in items]

    def migration_guide(self, item: MigrationNeeded) -> str:
        """Description:
            Return plain-language guidance for resolving one migration requirement.

        Requirements:
            - Explain the current and expected schema versions clearly.

        :param item: Migration requirement to describe.
        :returns: Plain-language migration guidance string.
        """

        return (
            f"Config file {item.file_path} uses schema version {item.current_version}. "
            f"Current expected version is {item.target_version}. "
            "Create a backup, update schema_version, then re-run validation."
        )
