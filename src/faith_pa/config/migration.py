"""Migration helpers for FAITH configuration files."""

from __future__ import annotations

import importlib
import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

CURRENT_SCHEMA_VERSION = "1.0"
_MIGRATION_PACKAGE = "faith.config.migrations"

logger = logging.getLogger("faith.config.migration")


@dataclass(slots=True)
class MigrationNeeded:
    file_path: Path
    current_version: str
    target_version: str = CURRENT_SCHEMA_VERSION

    def describe(self) -> str:
        return f"{self.file_path.name}: {self.current_version} -> {self.target_version}"


@dataclass(slots=True)
class MigrationResult:
    file_path: Path
    success: bool
    backup_path: Path | None
    message: str

    def describe(self) -> str:
        return f"{self.file_path.name}: {self.message}"


class MigrationEngine:
    def __init__(self, framework_config_dir: Path, project_faith_dir: Path | None = None) -> None:
        self.framework_config_dir = Path(framework_config_dir)
        self.project_faith_dir = Path(project_faith_dir) if project_faith_dir else None

    def iter_config_files(self) -> Iterable[Path]:
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
        results: list[MigrationNeeded] = []
        for path in self.iter_config_files():
            self._check_file(path, results)
        return results

    def create_backup(self, path: Path) -> Path:
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
        return [self.migrate_file(item) for item in items]

    def migration_guide(self, item: MigrationNeeded) -> str:
        return (
            f"Config file {item.file_path} uses schema version {item.current_version}. "
            f"Current expected version is {item.target_version}. "
            "Create a backup, update schema_version, then re-run validation."
        )
