"""Configuration loading, validation, and secret resolution for FAITH."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TypeVar

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ValidationError

from faith_pa.config.models import (
    TOOL_CONFIG_MAP,
    AgentConfig,
    ArchetypeConfig,
    ConfigFileStatus,
    ConfigSummary,
    ExternalMCPToolConfig,
    SecretsConfig,
    SecurityConfig,
    SystemConfig,
)

T = TypeVar("T", bound=BaseModel)

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")
PROJECT_CONFIG_DIRNAME = ".faith"


class ConfigLoadError(RuntimeError):
    """Raised when a config file cannot be loaded or validated."""


class StartupValidationError(RuntimeError):
    """Raised when startup validation fails for one or more config files."""


def config_dir() -> Path:
    return Path(os.environ.get("FAITH_CONFIG_DIR", "config")).resolve()


def logs_dir() -> Path:
    return Path(os.environ.get("FAITH_LOG_DIR", "logs")).resolve()


def data_dir() -> Path:
    return Path(os.environ.get("FAITH_DATA_DIR", "data")).resolve()


def project_root() -> Path:
    return Path(os.environ.get("FAITH_PROJECT_ROOT", os.getcwd())).resolve()


def project_config_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / PROJECT_CONFIG_DIRNAME


def env_file() -> Path:
    return config_dir() / ".env"


def env_template_file() -> Path:
    return config_dir() / ".env.template"


def secrets_file() -> Path:
    return config_dir() / "secrets.yaml"


def secrets_template_file() -> Path:
    return config_dir() / "secrets.yaml.template"


def recent_projects_file() -> Path:
    return config_dir() / "recent-projects.yaml"


def archetypes_dir() -> Path:
    return config_dir() / "archetypes"


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise ConfigLoadError(f"Config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Invalid YAML in {path}: {exc}") from exc
    return data if data is not None else {}


def load_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file().exists():
        values.update(
            {key: value for key, value in dotenv_values(env_file()).items() if value is not None}
        )
    values.update({key: value for key, value in os.environ.items()})
    return values


def _substitute_env_vars(value: Any, env_values: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: env_values.get(match.group(1), match.group(0)), value)
    if isinstance(value, list):
        return [_substitute_env_vars(item, env_values) for item in value]
    if isinstance(value, dict):
        return {key: _substitute_env_vars(item, env_values) for key, item in value.items()}
    return value


def _resolve_secret_refs(value: Any, secrets: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_resolve_secret_refs(item, secrets) for item in value]
    if not isinstance(value, dict):
        return value

    resolved: dict[str, Any] = {}
    for key, item in value.items():
        if key == "env_secret_refs" and isinstance(item, dict):
            env_values = dict(resolved.get("env", {}))
            for env_key, secret_key in item.items():
                if secret_key not in secrets:
                    raise ConfigLoadError(f"Unknown secret_ref '{secret_key}' in env_secret_refs")
                env_values[env_key] = secrets[secret_key]
            resolved["env"] = env_values
            resolved[key] = item
            continue

        resolved[key] = _resolve_secret_refs(item, secrets)

        if key.endswith("_secret_ref") and isinstance(item, str):
            if item not in secrets:
                raise ConfigLoadError(f"Unknown secret_ref '{item}'")
            resolved[key[:-11]] = secrets[item]

    return resolved


def _validate_model(data: Any, model_type: type[T], source: Path) -> T:
    try:
        return model_type.model_validate(data)
    except ValidationError as exc:
        raise ConfigLoadError(f"Validation failed for {source}: {exc}") from exc


def list_archetypes() -> list[str]:
    path = archetypes_dir()
    if not path.exists():
        return []
    return sorted(item.name for item in path.glob("*.yaml"))


def load_archetype(name: str) -> ArchetypeConfig:
    path = archetypes_dir() / name
    return _validate_model(_read_yaml(path), ArchetypeConfig, path)


def load_recent_projects() -> list[str]:
    path = recent_projects_file()
    if not path.exists():
        return []
    data = _read_yaml(path)
    if isinstance(data, dict):
        projects = data.get("projects", [])
    elif isinstance(data, list):
        projects = data
    else:
        projects = []
    return [str(project) for project in projects]


def load_secrets() -> SecretsConfig:
    path = secrets_file()
    data = _read_yaml(path)
    substituted = _substitute_env_vars(data, load_env_values())
    return _validate_model(substituted, SecretsConfig, path)


def load_system_config(root: Path | None = None) -> SystemConfig:
    path = project_config_dir(root) / "system.yaml"
    return _validate_model(_read_yaml(path), SystemConfig, path)


def load_security_config(root: Path | None = None) -> SecurityConfig:
    path = project_config_dir(root) / "security.yaml"
    return _validate_model(_read_yaml(path), SecurityConfig, path)


def load_tool_config(
    filename: str, root: Path | None = None, *, resolve_secrets: bool = True
) -> BaseModel:
    path = project_config_dir(root) / "tools" / filename
    data = _read_yaml(path)

    if filename.startswith("external-"):
        model_type: type[BaseModel] = ExternalMCPToolConfig
    else:
        model_type = TOOL_CONFIG_MAP.get(filename)
        if model_type is None:
            raise ConfigLoadError(f"Unsupported tool config: {filename}")

    if resolve_secrets:
        try:
            secrets = load_secrets().secrets
        except ConfigLoadError:
            secrets = {}
        data = _resolve_secret_refs(data, secrets)

    return _validate_model(data, model_type, path)


def load_all_tool_configs(
    root: Path | None = None, *, resolve_secrets: bool = True
) -> dict[str, BaseModel]:
    tools_dir = project_config_dir(root) / "tools"
    if not tools_dir.exists():
        return {}
    configs: dict[str, BaseModel] = {}
    for path in sorted(tools_dir.glob("*.yaml")):
        configs[path.name] = load_tool_config(path.name, root=root, resolve_secrets=resolve_secrets)
    return configs


def load_agent_config(agent_id: str, root: Path | None = None) -> AgentConfig:
    path = project_config_dir(root) / "agents" / agent_id / "config.yaml"
    return _validate_model(_read_yaml(path), AgentConfig, path)


def load_all_agent_configs(root: Path | None = None) -> dict[str, AgentConfig]:
    agents_dir = project_config_dir(root) / "agents"
    if not agents_dir.exists():
        return {}
    configs: dict[str, AgentConfig] = {}
    for path in sorted(agents_dir.glob("*/config.yaml")):
        configs[path.parent.name] = _validate_model(_read_yaml(path), AgentConfig, path)
    return configs


def validate_startup_config(root: Path | None = None) -> None:
    errors: list[str] = []

    try:
        load_secrets()
    except ConfigLoadError as exc:
        errors.append(str(exc))

    for loader in (load_system_config, load_security_config):
        try:
            loader(root)
        except ConfigLoadError as exc:
            errors.append(str(exc))

    try:
        load_all_tool_configs(root)
    except ConfigLoadError as exc:
        errors.append(str(exc))

    try:
        load_all_agent_configs(root)
    except ConfigLoadError as exc:
        errors.append(str(exc))

    if errors:
        raise StartupValidationError("\n".join(errors))


def build_config_summary() -> ConfigSummary:
    resolved_config_dir = config_dir()
    env_values = load_env_values()

    secrets_exists = secrets_file().exists()
    secrets: dict[str, str] = {}
    if secrets_exists:
        try:
            secrets = {
                key: "***configured***" for key, value in load_secrets().secrets.items() if value
            }
        except ConfigLoadError:
            secrets = {}

    return ConfigSummary(
        config_dir=str(resolved_config_dir),
        env_file=ConfigFileStatus(path=str(env_file()), exists=env_file().exists()),
        secrets_file=ConfigFileStatus(path=str(secrets_file()), exists=secrets_exists),
        recent_projects_file=ConfigFileStatus(
            path=str(recent_projects_file()), exists=recent_projects_file().exists()
        ),
        archetypes=list_archetypes(),
        recent_projects=load_recent_projects(),
        env_keys=sorted(key for key in env_values if key.isupper()),
        secrets=secrets,
    )

