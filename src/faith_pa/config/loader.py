"""Description:
    Load and validate FAITH configuration files.

Requirements:
    - Use the canonical shared config models from `faith_shared`.
    - Support `.env` substitution only for `secrets.yaml`.
    - Resolve `*_secret_ref` and `env_secret_refs` values against loaded secrets.
    - Raise human-readable validation errors instead of raw Pydantic tracebacks.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TypeVar, cast

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ValidationError

from faith_shared.config.models import (
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
    """Description:
        Represent a generic FAITH config loading error.

    Requirements:
        - Preserve a human-readable message for CLI and API callers.
    """


class ConfigValidationError(ConfigLoadError):
    """Description:
        Represent a human-readable config validation failure.

    Requirements:
        - Preserve the source path for diagnostics.
        - Expose a `human_message` property that callers can surface directly.

    :param source: Path to the invalid config file.
    :param human_message: Human-readable validation message.
    """

    def __init__(self, source: Path, human_message: str) -> None:
        self.source = source
        self.human_message = human_message
        super().__init__(human_message)


class StartupValidationError(RuntimeError):
    """Description:
        Represent one or more startup validation failures.

    Requirements:
        - Aggregate human-readable config failures for startup reporting.
    """


def config_dir() -> Path:
    """Description:
        Return the framework config directory.

    Requirements:
        - Respect the `FAITH_CONFIG_DIR` environment override.

    :returns: Framework config directory path.
    """

    return Path(os.environ.get("FAITH_CONFIG_DIR", "config")).resolve()


def logs_dir() -> Path:
    """Description:
        Return the framework logs directory.

    Requirements:
        - Respect the `FAITH_LOG_DIR` environment override.

    :returns: Framework logs directory path.
    """

    return Path(os.environ.get("FAITH_LOG_DIR", "logs")).resolve()


def data_dir() -> Path:
    """Description:
        Return the framework data directory.

    Requirements:
        - Respect the `FAITH_DATA_DIR` environment override.

    :returns: Framework data directory path.
    """

    return Path(os.environ.get("FAITH_DATA_DIR", "data")).resolve()


def project_root() -> Path:
    """Description:
        Return the current project root.

    Requirements:
        - Respect the `FAITH_PROJECT_ROOT` environment override.

    :returns: Project root path.
    """

    return Path(os.environ.get("FAITH_PROJECT_ROOT", os.getcwd())).resolve()


def project_config_dir(root: Path | None = None) -> Path:
    """Description:
        Return the `.faith` project config directory.

    Requirements:
        - Resolve against the provided root when supplied.

    :param root: Optional project root override.
    :returns: Project `.faith` directory path.
    """

    return (root or project_root()) / PROJECT_CONFIG_DIRNAME


def env_file() -> Path:
    """Description:
        Return the framework `.env` file path.

    Requirements:
        - Keep the environment file under the framework config directory.

    :returns: Framework `.env` file path.
    """

    return config_dir() / ".env"


def secrets_file() -> Path:
    """Description:
        Return the framework secrets file path.

    Requirements:
        - Keep the secrets file under the framework config directory.

    :returns: Framework secrets file path.
    """

    return config_dir() / "secrets.yaml"


def recent_projects_file() -> Path:
    """Description:
        Return the recent-projects file path.

    Requirements:
        - Keep the recent-projects file under the framework config directory.

    :returns: Recent-projects file path.
    """

    return config_dir() / "recent-projects.yaml"


def archetypes_dir() -> Path:
    """Description:
        Return the archetypes directory path.

    Requirements:
        - Keep archetypes under the framework config directory.

    :returns: Archetypes directory path.
    """

    return config_dir() / "archetypes"


def _read_yaml(path: Path) -> Any:
    """Description:
        Read one YAML file and return the parsed payload.

    Requirements:
        - Raise a human-readable load error when the file is missing.
        - Return an empty mapping when the YAML file is empty.

    :param path: YAML file path.
    :returns: Parsed YAML payload.
    :raises ConfigLoadError: If the file cannot be found or parsed.
    """

    if not path.exists():
        raise ConfigLoadError(f"Config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Invalid YAML in {path}: {exc}") from exc
    return data if data is not None else {}


def load_env_values() -> dict[str, str]:
    """Description:
        Load environment values used for secrets substitution.

    Requirements:
        - Load the framework `.env` file when present.
        - Let process environment variables override `.env` values.

    :returns: Combined environment variable mapping.
    """

    values: dict[str, str] = {}
    if env_file().exists():
        values.update(
            {key: value for key, value in dotenv_values(env_file()).items() if value is not None}
        )
    values.update({key: value for key, value in os.environ.items()})
    return values


def _substitute_env_vars(value: Any, env_values: dict[str, str]) -> Any:
    """Description:
        Recursively substitute `${VAR}` placeholders in one loaded payload.

    Requirements:
        - Preserve non-string values unchanged.
        - Leave unresolved placeholders intact for visibility.

    :param value: Loaded config payload or sub-value.
    :param env_values: Environment values available for substitution.
    :returns: Payload with substitutions applied.
    """

    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: env_values.get(match.group(1), match.group(0)), value)
    if isinstance(value, list):
        return [_substitute_env_vars(item, env_values) for item in value]
    if isinstance(value, dict):
        return {key: _substitute_env_vars(item, env_values) for key, item in value.items()}
    return value


def resolve_secret_ref(secret_ref: str, secrets: SecretsConfig) -> str | None:
    """Description:
        Resolve one named secret reference from a loaded secrets config.

    Requirements:
        - Return `None` when the requested secret key is absent.

    :param secret_ref: Secret key name.
    :param secrets: Loaded secrets config.
    :returns: Resolved secret value when available.
    """

    return secrets.secrets.get(secret_ref)


def _resolve_secret_refs(value: Any, secrets: dict[str, str]) -> Any:
    """Description:
        Resolve secret references embedded in tool config payloads.

    Requirements:
        - Expand `*_secret_ref` values into sibling runtime fields.
        - Expand `env_secret_refs` into the tool environment mapping.
        - Raise a human-readable load error when a referenced secret is missing.

    :param value: Loaded tool config payload.
    :param secrets: Secret mapping loaded from `secrets.yaml`.
    :returns: Payload with secret references resolved.
    :raises ConfigLoadError: If a referenced secret key is missing.
    """

    if isinstance(value, list):
        return [_resolve_secret_refs(item, secrets) for item in value]
    if not isinstance(value, dict):
        return value

    resolved: dict[str, Any] = {}
    for key, item in value.items():
        if key == "env_secret_refs" and isinstance(item, dict):
            env_values = dict(resolved.get("env", {}))
            for env_key, secret_key in item.items():
                secret_value = secrets.get(secret_key)
                if secret_value is None:
                    raise ConfigLoadError(f"Unknown secret_ref '{secret_key}' in env_secret_refs")
                env_values[env_key] = secret_value
            resolved["env"] = env_values
            resolved[key] = item
            continue

        resolved[key] = _resolve_secret_refs(item, secrets)
        if key.endswith("_secret_ref") and isinstance(item, str):
            secret_value = secrets.get(item)
            if secret_value is None:
                raise ConfigLoadError(f"Unknown secret_ref '{item}'")
            resolved[key[:-11]] = secret_value

    return resolved


def _format_validation_error(source: Path, error: ValidationError) -> str:
    """Description:
        Convert a Pydantic validation error into one readable message.

    Requirements:
        - Include the failing file path.
        - Include the field location and message for each error item.

    :param source: Config file path being validated.
    :param error: Pydantic validation error.
    :returns: Human-readable validation error text.
    """

    parts = [f"Validation failed for {source}:"]
    for issue in error.errors():
        location = ".".join(str(part) for part in issue.get("loc", [])) or "<root>"
        message = issue.get("msg", "Invalid value")
        parts.append(f"- {location}: {message}")
    return "\n".join(parts)


def _validate_model(data: Any, model_type: type[T], source: Path) -> T:
    """Description:
        Validate one loaded payload against a shared Pydantic model.

    Requirements:
        - Raise a human-readable validation error rather than a raw traceback.

    :param data: Parsed config payload.
    :param model_type: Pydantic model to validate against.
    :param source: Config file path.
    :returns: Validated model instance.
    :raises ConfigValidationError: If validation fails.
    """

    try:
        return model_type.model_validate(data)
    except ValidationError as exc:
        raise ConfigValidationError(source, _format_validation_error(source, exc)) from exc


def _infer_model_from_path(path: Path) -> type[BaseModel]:
    """Description:
        Infer the config model type from one known FAITH config file path.

    Requirements:
        - Support framework secrets, project system/security, tool, agent, and archetype files.
        - Raise a load error for unsupported config paths.

    :param path: Config file path.
    :returns: Shared Pydantic model type for the path.
    :raises ConfigLoadError: If the config path is not recognised.
    """

    name = path.name
    if name == "secrets.yaml":
        return SecretsConfig
    if name == "system.yaml":
        return SystemConfig
    if name == "security.yaml":
        return SecurityConfig
    if name == "config.yaml" and path.parent.parent.name == "agents":
        return AgentConfig
    if path.parent.name == "tools":
        if name.startswith("external-"):
            return ExternalMCPToolConfig
        model_type = TOOL_CONFIG_MAP.get(name)
        if model_type is None:
            raise ConfigLoadError(f"Unsupported tool config: {name}")
        return model_type
    if path.parent == archetypes_dir():
        return ArchetypeConfig
    raise ConfigLoadError(f"Unsupported config path: {path}")


def load_config(
    path: Path,
    model_type: type[T] | None = None,
    *,
    use_env_substitution: bool = False,
) -> T:
    """Description:
        Load and validate one config file.

    Requirements:
        - Infer the model type from the file path when none is supplied.
        - Support optional environment substitution for secrets loading.
        - Raise human-readable validation errors on failure.

    :param path: Config file path.
    :param model_type: Optional shared Pydantic model used for validation.
    :param use_env_substitution: Whether to apply `${VAR}` substitution first.
    :returns: Validated model instance.
    """

    resolved_model_type = model_type or cast(type[T], _infer_model_from_path(path))
    data = _read_yaml(path)
    if use_env_substitution:
        data = _substitute_env_vars(data, load_env_values())
    return _validate_model(data, resolved_model_type, path)


def list_archetypes() -> list[str]:
    """Description:
        Return the available framework archetype filenames.

    Requirements:
        - Return an empty list when no archetypes are available.

    :returns: Sorted archetype file names.
    """

    path = archetypes_dir()
    if not path.exists():
        return []
    return sorted(item.name for item in path.glob("*.yaml"))


def load_archetype(name: str) -> ArchetypeConfig:
    """Description:
        Load one archetype config file.

    Requirements:
        - Validate the archetype against the shared config contract.

    :param name: Archetype file name.
    :returns: Validated archetype config.
    """

    path = archetypes_dir() / name
    return load_config(path, ArchetypeConfig)


def load_recent_projects() -> list[str]:
    """Description:
        Load the recent-projects list from disk.

    Requirements:
        - Support either a mapping with `projects` or a bare list payload.

    :returns: Recent project path strings.
    """

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
    """Description:
        Load framework-level secrets with `.env` substitution.

    Requirements:
        - Apply environment substitution only to `secrets.yaml`.

    :returns: Validated secrets config.
    """

    path = secrets_file()
    return load_config(path, SecretsConfig, use_env_substitution=True)


def load_system_config(root: Path | None = None) -> SystemConfig:
    """Description:
        Load the project `system.yaml` config.

    Requirements:
        - Validate the payload against the shared system model.

    :param root: Optional project root override.
    :returns: Validated system config.
    """

    path = project_config_dir(root) / "system.yaml"
    return load_config(path, SystemConfig)


def load_security_config(root: Path | None = None) -> SecurityConfig:
    """Description:
        Load the project `security.yaml` config.

    Requirements:
        - Validate the payload against the shared security model.

    :param root: Optional project root override.
    :returns: Validated security config.
    """

    path = project_config_dir(root) / "security.yaml"
    return load_config(path, SecurityConfig)


def load_tool_config(
    filename: str, root: Path | None = None, *, resolve_secrets: bool = True
) -> BaseModel:
    """Description:
        Load one project tool config file.

    Requirements:
        - Resolve the tool model from the shared config registry.
        - Apply secret reference resolution when requested.
        - Support external MCP configs via the `external-*.yaml` naming pattern.

    :param filename: Tool config file name.
    :param root: Optional project root override.
    :param resolve_secrets: Whether to resolve configured secret references.
    :returns: Validated tool config model instance.
    :raises ConfigLoadError: If the tool file or secret references are invalid.
    """

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
            secret_map = load_secrets().secrets
        except ConfigLoadError:
            secret_map = {}
        data = _resolve_secret_refs(data, secret_map)

    return _validate_model(data, cast(type[BaseModel], model_type), path)


def load_all_tool_configs(
    root: Path | None = None, *, resolve_secrets: bool = True
) -> dict[str, BaseModel]:
    """Description:
        Load all project tool config files.

    Requirements:
        - Return an empty mapping when the tools directory is absent.

    :param root: Optional project root override.
    :param resolve_secrets: Whether to resolve configured secret references.
    :returns: Mapping of file name to validated tool config model.
    """

    tools_dir = project_config_dir(root) / "tools"
    if not tools_dir.exists():
        return {}
    configs: dict[str, BaseModel] = {}
    for path in sorted(tools_dir.glob("*.yaml")):
        configs[path.name] = load_tool_config(path.name, root=root, resolve_secrets=resolve_secrets)
    return configs


def load_agent_config(agent_id: str, root: Path | None = None) -> AgentConfig:
    """Description:
        Load one project agent config file.

    Requirements:
        - Validate the payload against the shared agent model.

    :param agent_id: Agent identifier.
    :param root: Optional project root override.
    :returns: Validated agent config.
    """

    path = project_config_dir(root) / "agents" / agent_id / "config.yaml"
    return load_config(path, AgentConfig)


def load_all_agent_configs(root: Path | None = None) -> dict[str, AgentConfig]:
    """Description:
        Load all project agent config files.

    Requirements:
        - Return an empty mapping when the agents directory is absent.

    :param root: Optional project root override.
    :returns: Mapping of agent id to validated config.
    """

    agents_dir = project_config_dir(root) / "agents"
    if not agents_dir.exists():
        return {}
    configs: dict[str, AgentConfig] = {}
    for path in sorted(agents_dir.glob("*/config.yaml")):
        configs[path.parent.name] = load_config(path, AgentConfig)
    return configs


def validate_startup_config(root: Path | None = None) -> None:
    """Description:
        Validate all startup-critical FAITH config files.

    Requirements:
        - Aggregate all discovered failures into one readable exception.

    :param root: Optional project root override.
    :raises StartupValidationError: If one or more config files are invalid.
    """

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
    """Description:
        Build the redacted config summary exposed by the PA status API.

    Requirements:
        - Redact secret values while preserving configured keys.
        - Include environment key names without exposing their values.

    :returns: Redacted config summary payload.
    """

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
