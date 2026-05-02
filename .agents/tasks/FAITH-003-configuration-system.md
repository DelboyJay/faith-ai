# FAITH-003 — Configuration System: YAML Loading & Pydantic Models

**Phase:** 1 — Foundation
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-001
**FRS Reference:** Section 7.1, 7.2, 7.5

---

## Objective

Define Pydantic models for all FAITH config files across both the framework installation and project workspace. This includes: framework-level `config/secrets.yaml`, project-level `.faith/system.yaml`, `.faith/security.yaml`, per-tool `.faith/tools/*.yaml`, and per-agent `.faith/agents/*/config.yaml`. The canonical models and exported JSON Schemas are owned by `src/faith_shared/`; this task implements their loading/validation consumption in the relevant runtime. Implement YAML loading with schema validation, create JSON Schema files for external tooling, implement startup validation with human-readable error messages, and implement `secret_ref` resolution for tool configs referencing `secrets.yaml` keys. Include `.env` variable substitution support for `secrets.yaml`.

---

## Architecture

```
src/faith_shared/
├── config_models.py    ← canonical Pydantic models for config files
└── schemas/
    ├── system.schema.json
    ├── security.schema.json
    ├── secrets.schema.json
    ├── agent-config.schema.json
    └── tool-config.schema.json

src/faith_pa/
└── faith/config/
    ├── __init__.py
    ├── loader.py       ← load_config(), validate, .env substitution, secret_ref resolution
    └── schema_export.py
```

Config files live in two locations:
- **Framework-level** (`~/.faith/config/`): `secrets.yaml`, `.env` — credentials and framework settings (created by `faith init` after `pip install faith-cli`)
- **Project-level** (`.faith/`): `system.yaml`, `security.yaml`, `tools/*.yaml`, `agents/*/config.yaml`, `skills/*.md` — project-specific config

The Pydantic models in `src/faith_shared/` are the single source of truth. JSON Schema files are generated FROM those models (not manually maintained) using `schema_export.py`.

---

## Files to Create

### 1. `faith/config/models.py`

All Pydantic model definitions for FAITH configuration.

```python
"""Pydantic models for all FAITH configuration files."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Enums ---

class PrivacyProfile(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


class AccessLevel(str, Enum):
    READONLY = "readonly"
    READWRITE = "readwrite"


class TrustLevel(str, Enum):
    HIGH = "high"
    STANDARD = "standard"
    LOW = "low"


class FileEventType(str, Enum):
    CHANGED = "file:changed"
    CREATED = "file:created"
    DELETED = "file:deleted"


# =============================================
# Framework-level: config/secrets.yaml
# =============================================

class SecretsConfig(BaseModel):
    """Root model for config/secrets.yaml.

    Contains credentials only. Values use ${VAR} substitution from .env.
    Tool configs reference these keys via secret_ref.
    This file is NEVER mounted into agent containers.
    """
    schema_version: str = "1.0"
    # All other fields are dynamic key-value pairs
    # Use model_extra to allow arbitrary secret keys
    model_config = {"extra": "allow"}


# =============================================
# Project-level: .faith/system.yaml
# =============================================

class PAConfig(BaseModel):
    model: str
    fallback_model: Optional[str] = None


class LoopDetectionConfig(BaseModel):
    enabled: bool = True
    window_messages: int = Field(default=10, ge=1)
    state_repeat_threshold: int = Field(default=2, ge=1)


class CostWarningConfig(BaseModel):
    threshold_usd: float = Field(default=1.0, ge=0)


class SystemConfig(BaseModel):
    """Root model for .faith/system.yaml (project-level)."""
    schema_version: str = "1.0"
    privacy_profile: PrivacyProfile
    pa: PAConfig
    default_agent_model: str
    editor: Optional[str] = None
    loop_detection: LoopDetectionConfig = LoopDetectionConfig()
    cost_warning: CostWarningConfig = CostWarningConfig()
    log_retention_days: int = Field(default=90, ge=1)
    session_retention_days: int = Field(default=365, ge=1)
    stall_timeout_seconds: int = Field(default=300, ge=30)
    heartbeat_interval_seconds: int = Field(default=30, ge=5)
    heartbeat_miss_threshold: int = Field(default=3, ge=1)
    channel_agent_limit: int = Field(default=5, ge=0)
    price_refresh_interval_hours: int = Field(default=24, ge=1)
    price_stale_warning_days: int = Field(default=7, ge=1)


# =============================================
# Project-level: .faith/agents/{id}/config.yaml
# =============================================

class FileWatchConfig(BaseModel):
    pattern: str
    events: list[FileEventType] = Field(
        default_factory=lambda: [FileEventType.CHANGED]
    )


class AgentContextConfig(BaseModel):
    summary_threshold_pct: int = Field(default=50, ge=10, le=90)
    max_messages: int = Field(default=50, ge=5)


class AgentConfig(BaseModel):
    """Root model for .faith/agents/{id}/config.yaml (per-agent).

    Written by the PA when it creates or modifies an agent.
    Machine-readable config — the AI-readable prompt is in prompt.md.
    """
    schema_version: str = "1.0"
    name: str
    role: str
    model: Optional[str] = None              # None = use system default
    trust: TrustLevel = TrustLevel.STANDARD
    tools: list[str] = Field(default_factory=list)
    databases: dict[str, AccessLevel] = Field(default_factory=dict)
    mounts: dict[str, AccessLevel] = Field(default_factory=dict)
    file_watches: list[FileWatchConfig] = Field(default_factory=list)
    context: AgentContextConfig = AgentContextConfig()
    listen_tags: list[str] = Field(default_factory=list)
    cag_documents: list[str] = Field(default_factory=list)
    cag_max_tokens: int = Field(default=8000, ge=0)
    escalate_for: list[str] = Field(default_factory=list)
    python_timeout_seconds: int = Field(default=60, ge=5)
    mcp_native: bool = True                  # Model supports native MCP


# =============================================
# Project-level: .faith/tools/*.yaml (per-tool)
# =============================================

class SubfolderOverride(BaseModel):
    access: AccessLevel


class MountConfig(BaseModel):
    host_path: str
    access: AccessLevel
    recursive: bool = True
    history: bool = False
    history_depth: int = Field(default=10, ge=1)
    max_file_size_mb: int = Field(default=50, ge=1)
    subfolder_overrides: dict[str, SubfolderOverride] = Field(default_factory=dict)


class FilesystemToolConfig(BaseModel):
    """Root model for .faith/tools/filesystem.yaml."""
    schema_version: str = "1.0"
    mounts: dict[str, MountConfig] = Field(default_factory=dict)


class PythonToolConfig(BaseModel):
    """Root model for .faith/tools/python.yaml."""
    schema_version: str = "1.0"
    internet_access: bool = True


class DatabaseConnectionConfig(BaseModel):
    host: str
    port: int = 5432
    database: str
    user: str
    password_secret_ref: Optional[str] = None  # References key in secrets.yaml
    access: AccessLevel = AccessLevel.READONLY
    permission_override: bool = False
    max_rows: int = Field(default=1000, ge=1)
    max_result_mb: int = Field(default=5, ge=1)


class DatabaseToolConfig(BaseModel):
    """Root model for .faith/tools/database.yaml."""
    schema_version: str = "1.0"
    connections: dict[str, DatabaseConnectionConfig] = Field(default_factory=dict)


class BrowserToolConfig(BaseModel):
    """Root model for .faith/tools/browser.yaml."""
    schema_version: str = "1.0"
    headless: bool = True


class ConfluenceToolConfig(BaseModel):
    """Root model for .faith/tools/confluence.yaml."""
    schema_version: str = "1.0"
    url: Optional[str] = None
    username: Optional[str] = None
    password_secret_ref: Optional[str] = None  # References key in secrets.yaml
    default_space: Optional[str] = None


class ExternalMCPToolConfig(BaseModel):
    """Root model for .faith/tools/external-*.yaml (external MCP servers)."""
    schema_version: str = "1.0"
    server: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_secret_refs: dict[str, str] = Field(default_factory=dict)  # env var -> secrets.yaml key
    privacy_tier: PrivacyProfile = PrivacyProfile.INTERNAL
    agents: list[str] = Field(default_factory=list)


# Map of tool config filenames to their Pydantic models
TOOL_CONFIG_MAP: dict[str, type[BaseModel]] = {
    "filesystem.yaml": FilesystemToolConfig,
    "python.yaml": PythonToolConfig,
    "database.yaml": DatabaseToolConfig,
    "browser.yaml": BrowserToolConfig,
    "confluence.yaml": ConfluenceToolConfig,
}


# =============================================
# Project-level: .faith/security.yaml
# =============================================

class AgentApprovalRules(BaseModel):
    always_ask: list[str] = Field(default_factory=list)
    always_allow: list[str] = Field(default_factory=list)


class SecurityConfig(BaseModel):
    """Root model for .faith/security.yaml."""
    schema_version: str = "1.0"
    approval_rules: dict[str, AgentApprovalRules] = Field(default_factory=dict)
    trust_overrides: dict[str, TrustLevel] = Field(default_factory=dict)
    always_allow_learned: dict[str, list[str]] = Field(default_factory=dict)
    always_ask_learned: dict[str, list[str]] = Field(default_factory=dict)
    always_deny_learned: dict[str, list[str]] = Field(default_factory=dict)
```

### 2. `faith/config/loader.py`

Config file loading with `.env` substitution, validation, and `secret_ref` resolution.

```python
"""FAITH configuration loader — YAML loading, .env substitution, validation, secret_ref resolution."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, TypeVar, Type

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from faith.config.models import (
    SystemConfig,
    SecretsConfig,
    SecurityConfig,
    AgentConfig,
    FilesystemToolConfig,
    PythonToolConfig,
    DatabaseToolConfig,
    BrowserToolConfig,
    ConfluenceToolConfig,
    ExternalMCPToolConfig,
    TOOL_CONFIG_MAP,
)


T = TypeVar("T", bound=BaseModel)

# Map well-known project-level config filenames to their Pydantic model
PROJECT_CONFIG_MAP: dict[str, Type[BaseModel]] = {
    "system.yaml": SystemConfig,
    "security.yaml": SecurityConfig,
}

# Regex for ${VAR_NAME} substitution
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def substitute_env_vars(text: str) -> str:
    """Replace ${VAR_NAME} placeholders with environment variable values.

    Args:
        text: Raw YAML text with potential ${VAR} placeholders.

    Returns:
        Text with all ${VAR} placeholders replaced. Unresolved vars
        are replaced with empty string.
    """
    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, "")

    return ENV_VAR_PATTERN.sub(_replace, text)


def load_yaml_with_env(file_path: Path, use_env_substitution: bool = False) -> dict:
    """Load a YAML file, optionally with environment variable substitution.

    Args:
        file_path: Path to the YAML file.
        use_env_substitution: If True, apply ${VAR} substitution.
            Only used for secrets.yaml (framework-level).

    Returns:
        Parsed YAML as a dict.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
    """
    raw_text = file_path.read_text(encoding="utf-8")
    if use_env_substitution:
        raw_text = substitute_env_vars(raw_text)
    return yaml.safe_load(raw_text) or {}


def load_config(
    file_path: Path,
    model_class: Optional[Type[T]] = None,
    use_env_substitution: bool = False,
) -> T:
    """Load and validate a FAITH config file.

    Args:
        file_path: Path to the YAML config file.
        model_class: Pydantic model to validate against. If None,
            inferred from filename and parent directory.
        use_env_substitution: If True, apply ${VAR} substitution before parsing.

    Returns:
        Validated Pydantic model instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the model_class cannot be inferred.
        ConfigValidationError: If the config fails validation.
    """
    if model_class is None:
        model_class = _infer_model_class(file_path)

    raw_data = load_yaml_with_env(file_path, use_env_substitution)

    try:
        return model_class.model_validate(raw_data)
    except ValidationError as e:
        raise ConfigValidationError(file_path, e) from e


def _infer_model_class(file_path: Path) -> Type[BaseModel]:
    """Infer the Pydantic model class from a config file's path.

    Handles:
    - .faith/system.yaml -> SystemConfig
    - .faith/security.yaml -> SecurityConfig
    - .faith/agents/*/config.yaml -> AgentConfig
    - .faith/tools/*.yaml -> tool-specific model from TOOL_CONFIG_MAP
    - config/secrets.yaml -> SecretsConfig
    - .faith/tools/external-*.yaml -> ExternalMCPToolConfig
    """
    filename = file_path.name
    parent_name = file_path.parent.name

    # Framework-level
    if filename == "secrets.yaml":
        return SecretsConfig

    # Project-level direct configs
    if filename in PROJECT_CONFIG_MAP:
        return PROJECT_CONFIG_MAP[filename]

    # Per-agent config
    if filename == "config.yaml" and file_path.parent.parent.name == "agents":
        return AgentConfig

    # Per-tool config
    if parent_name == "tools":
        if filename.startswith("external-"):
            return ExternalMCPToolConfig
        tool_model = TOOL_CONFIG_MAP.get(filename)
        if tool_model:
            return tool_model

    raise ValueError(
        f"Cannot infer config model for '{file_path}'. "
        f"Provide model_class explicitly."
    )


def load_secrets(config_dir: Path) -> SecretsConfig:
    """Load framework-level secrets.yaml with .env substitution.

    Loads .env first, then loads secrets.yaml with ${VAR} substitution.

    Args:
        config_dir: Path to the framework config/ directory.

    Returns:
        Validated SecretsConfig instance.
    """
    env_path = config_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)

    secrets_path = config_dir / "secrets.yaml"
    if not secrets_path.exists():
        raise FileNotFoundError(
            f"Secrets file not found: {secrets_path}\n"
            f"Run the first-run wizard to create it."
        )
    return load_config(secrets_path, SecretsConfig, use_env_substitution=True)


def resolve_secret_ref(secret_ref: str, secrets: SecretsConfig) -> Optional[str]:
    """Resolve a secret_ref key to its value from secrets.yaml.

    Tool configs use secret_ref fields to reference credentials stored
    in secrets.yaml. The PA resolves these at container startup — agents
    never see secrets.yaml directly.

    Args:
        secret_ref: The key name in secrets.yaml.
        secrets: The loaded SecretsConfig.

    Returns:
        The resolved secret value, or None if key not found.
    """
    # SecretsConfig uses extra="allow", so dynamic keys are in model_extra
    extras = secrets.model_extra or {}
    return extras.get(secret_ref)


def load_project_configs(faith_dir: Path) -> dict[str, BaseModel]:
    """Load all project-level configs from a .faith/ directory.

    Args:
        faith_dir: Path to the project's .faith/ directory.

    Returns:
        Dict mapping relative path to validated Pydantic model.
        Keys: "system.yaml", "security.yaml", "tools/filesystem.yaml",
              "agents/software-developer/config.yaml", etc.

    Raises:
        ConfigValidationError: If any config file fails validation.
        FileNotFoundError: If required files (system.yaml) are missing.
    """
    configs = {}

    # Required: system.yaml
    system_path = faith_dir / "system.yaml"
    if not system_path.exists():
        raise FileNotFoundError(f"Required config not found: {system_path}")
    configs["system.yaml"] = load_config(system_path)

    # Optional: security.yaml
    security_path = faith_dir / "security.yaml"
    if security_path.exists():
        configs["security.yaml"] = load_config(security_path)

    # Per-tool configs
    tools_dir = faith_dir / "tools"
    if tools_dir.exists():
        for tool_file in tools_dir.glob("*.yaml"):
            key = f"tools/{tool_file.name}"
            configs[key] = load_config(tool_file)

    # Per-agent configs
    agents_dir = faith_dir / "agents"
    if agents_dir.exists():
        for agent_dir in agents_dir.iterdir():
            if agent_dir.is_dir():
                config_path = agent_dir / "config.yaml"
                if config_path.exists():
                    key = f"agents/{agent_dir.name}/config.yaml"
                    configs[key] = load_config(config_path)

    return configs


class ConfigValidationError(Exception):
    """Raised when a config file fails Pydantic validation.

    Attributes:
        file_path: The config file that failed.
        pydantic_error: The original Pydantic ValidationError.
        human_message: A human-readable error message for the Web UI.
    """

    def __init__(self, file_path: Path, pydantic_error: ValidationError):
        self.file_path = file_path
        self.pydantic_error = pydantic_error
        self.human_message = self._build_human_message()
        super().__init__(self.human_message)

    def _build_human_message(self) -> str:
        """Convert Pydantic errors to plain English."""
        lines = [f"Config error in `{self.file_path}`:"]
        for error in self.pydantic_error.errors():
            location = " → ".join(str(loc) for loc in error["loc"])
            msg = error["msg"]
            lines.append(f"  - Field `{location}`: {msg}")
        return "\n".join(lines)
```

### 3. `faith/config/schema_export.py`

Generate JSON Schema files from Pydantic models.

```python
"""Export JSON Schema files from Pydantic models.

Run directly: python -m faith.config.schema_export
Outputs to faith/schemas/
"""

from __future__ import annotations

import json
from pathlib import Path

from faith.config.models import (
    SystemConfig,
    SecretsConfig,
    SecurityConfig,
    AgentConfig,
    FilesystemToolConfig,
    PythonToolConfig,
    DatabaseToolConfig,
    BrowserToolConfig,
    ConfluenceToolConfig,
    ExternalMCPToolConfig,
)

SCHEMA_MAP = {
    "system.schema.json": SystemConfig,
    "secrets.schema.json": SecretsConfig,
    "security.schema.json": SecurityConfig,
    "agent-config.schema.json": AgentConfig,
    "tool-filesystem.schema.json": FilesystemToolConfig,
    "tool-python.schema.json": PythonToolConfig,
    "tool-database.schema.json": DatabaseToolConfig,
    "tool-browser.schema.json": BrowserToolConfig,
    "tool-confluence.schema.json": ConfluenceToolConfig,
    "tool-external-mcp.schema.json": ExternalMCPToolConfig,
}


def export_schemas(output_dir: Path) -> None:
    """Write JSON Schema files for all config models.

    Args:
        output_dir: Directory to write schema files to.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, model_class in SCHEMA_MAP.items():
        schema = model_class.model_json_schema()
        output_path = output_dir / filename
        output_path.write_text(
            json.dumps(schema, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"  Written: {output_path}")


if __name__ == "__main__":
    schemas_dir = Path("faith/schemas")
    print("Exporting FAITH config schemas...")
    export_schemas(schemas_dir)
    print("Done.")
```

### 4. `faith/config/__init__.py`

```python
"""FAITH configuration system — YAML loading, validation, secret_ref resolution, and schema export."""

from faith.config.loader import (
    load_config,
    load_secrets,
    load_project_configs,
    resolve_secret_ref,
    ConfigValidationError,
)
from faith.config.models import (
    SystemConfig,
    SecretsConfig,
    SecurityConfig,
    AgentConfig,
    FilesystemToolConfig,
    PythonToolConfig,
    DatabaseToolConfig,
    BrowserToolConfig,
    ConfluenceToolConfig,
    ExternalMCPToolConfig,
    PrivacyProfile,
    AccessLevel,
    TrustLevel,
)

__all__ = [
    "load_config",
    "load_secrets",
    "load_project_configs",
    "resolve_secret_ref",
    "ConfigValidationError",
    "SystemConfig",
    "SecretsConfig",
    "SecurityConfig",
    "AgentConfig",
    "FilesystemToolConfig",
    "PythonToolConfig",
    "DatabaseToolConfig",
    "BrowserToolConfig",
    "ConfluenceToolConfig",
    "ExternalMCPToolConfig",
    "PrivacyProfile",
    "AccessLevel",
    "TrustLevel",
]
```

### 5. `tests/test_config_loader.py`

```python
"""Tests for the FAITH configuration system."""

from pathlib import Path
import pytest
import yaml
from faith.config.loader import (
    load_config,
    load_secrets,
    load_project_configs,
    resolve_secret_ref,
    substitute_env_vars,
    ConfigValidationError,
)
from faith.config.models import (
    SystemConfig,
    SecretsConfig,
    SecurityConfig,
    AgentConfig,
    FilesystemToolConfig,
    DatabaseToolConfig,
    PrivacyProfile,
    TrustLevel,
)


# --- Fixtures: .faith/ project directory ---

@pytest.fixture
def faith_dir(tmp_path):
    """Create a minimal .faith/ project directory."""
    faith = tmp_path / ".faith"
    faith.mkdir()
    (faith / "tools").mkdir()
    (faith / "agents").mkdir()
    return faith


@pytest.fixture
def valid_system_yaml(faith_dir):
    data = {
        "schema_version": "1.0",
        "privacy_profile": "internal",
        "pa": {"model": "openrouter/anthropic/claude-sonnet-4-6"},
        "default_agent_model": "ollama/llama3:8b",
    }
    path = faith_dir / "system.yaml"
    path.write_text(yaml.dump(data))
    return path


@pytest.fixture
def valid_agent_config(faith_dir):
    agent_dir = faith_dir / "agents" / "software-developer"
    agent_dir.mkdir(parents=True)
    data = {
        "schema_version": "1.0",
        "name": "Software Developer",
        "role": "Writes code",
        "tools": ["filesystem", "python"],
        "mounts": {"workspace": "readwrite"},
        "listen_tags": ["code", "bug"],
    }
    path = agent_dir / "config.yaml"
    path.write_text(yaml.dump(data))
    return path


@pytest.fixture
def valid_filesystem_tool(faith_dir):
    data = {
        "schema_version": "1.0",
        "mounts": {
            "workspace": {
                "host_path": "~/projects/test",
                "access": "readwrite",
            }
        },
    }
    path = faith_dir / "tools" / "filesystem.yaml"
    path.write_text(yaml.dump(data))
    return path


@pytest.fixture
def valid_security_yaml(faith_dir):
    data = {
        "schema_version": "1.0",
        "approval_rules": {
            "software-developer": {
                "always_allow": ["^pytest.*$"],
                "always_ask": ["^git push.*$"],
            }
        },
    }
    path = faith_dir / "security.yaml"
    path.write_text(yaml.dump(data))
    return path


# --- Tests ---

def test_load_system_config(valid_system_yaml):
    config = load_config(valid_system_yaml)
    assert isinstance(config, SystemConfig)
    assert config.privacy_profile == PrivacyProfile.INTERNAL
    assert config.pa.model == "openrouter/anthropic/claude-sonnet-4-6"
    assert config.loop_detection.enabled is True  # default


def test_load_agent_config(valid_agent_config):
    config = load_config(valid_agent_config)
    assert isinstance(config, AgentConfig)
    assert config.name == "Software Developer"
    assert config.trust == TrustLevel.STANDARD  # default
    assert "code" in config.listen_tags


def test_load_filesystem_tool_config(valid_filesystem_tool):
    config = load_config(valid_filesystem_tool)
    assert isinstance(config, FilesystemToolConfig)
    assert "workspace" in config.mounts


def test_load_security_config(valid_security_yaml):
    config = load_config(valid_security_yaml)
    assert isinstance(config, SecurityConfig)
    rules = config.approval_rules["software-developer"]
    assert "^pytest.*$" in rules.always_allow


def test_load_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    secrets_data = {
        "schema_version": "1.0",
        "openrouter_api_key": "${OPENROUTER_API_KEY}",
    }
    (config_dir / "secrets.yaml").write_text(yaml.dump(secrets_data))
    secrets = load_secrets(config_dir)
    assert isinstance(secrets, SecretsConfig)
    assert secrets.model_extra["openrouter_api_key"] == "sk-test-123"


def test_resolve_secret_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PASS", "mypassword")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "secrets.yaml").write_text(yaml.dump({
        "schema_version": "1.0",
        "myproject_db_password": "${DB_PASS}",
    }))
    secrets = load_secrets(config_dir)
    assert resolve_secret_ref("myproject_db_password", secrets) == "mypassword"
    assert resolve_secret_ref("nonexistent_key", secrets) is None


def test_invalid_privacy_profile(faith_dir):
    data = {
        "schema_version": "1.0",
        "privacy_profile": "ultra_secret",
        "pa": {"model": "test"},
        "default_agent_model": "test",
    }
    path = faith_dir / "system.yaml"
    path.write_text(yaml.dump(data))
    with pytest.raises(ConfigValidationError) as exc_info:
        load_config(path)
    assert "privacy_profile" in exc_info.value.human_message


def test_env_var_substitution(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "s3cr3t")
    result = substitute_env_vars("key: ${MY_SECRET}")
    assert result == "key: s3cr3t"


def test_env_var_missing_becomes_empty():
    result = substitute_env_vars("key: ${NONEXISTENT_VAR_XYZ}")
    assert result == "key: "


def test_load_project_configs(
    faith_dir,
    valid_system_yaml,
    valid_agent_config,
    valid_filesystem_tool,
    valid_security_yaml,
):
    configs = load_project_configs(faith_dir)
    assert isinstance(configs["system.yaml"], SystemConfig)
    assert isinstance(configs["security.yaml"], SecurityConfig)
    assert isinstance(configs["tools/filesystem.yaml"], FilesystemToolConfig)
    assert isinstance(configs["agents/software-developer/config.yaml"], AgentConfig)


def test_human_readable_error_message(faith_dir):
    data = {"schema_version": "1.0"}  # missing required fields
    path = faith_dir / "system.yaml"
    path.write_text(yaml.dump(data))
    with pytest.raises(ConfigValidationError) as exc_info:
        load_config(path)
    msg = exc_info.value.human_message
    assert "Config error" in msg


def test_database_tool_with_secret_ref(faith_dir):
    data = {
        "schema_version": "1.0",
        "connections": {
            "main_db": {
                "host": "localhost",
                "database": "myapp",
                "user": "admin",
                "password_secret_ref": "myproject_db_password",
                "access": "readonly",
            }
        },
    }
    path = faith_dir / "tools" / "database.yaml"
    path.write_text(yaml.dump(data))
    config = load_config(path)
    assert isinstance(config, DatabaseToolConfig)
    conn = config.connections["main_db"]
    assert conn.password_secret_ref == "myproject_db_password"
```

---

## Acceptance Criteria

1. All Pydantic models (`SystemConfig`, `SecretsConfig`, `SecurityConfig`, `AgentConfig`, `FilesystemToolConfig`, `PythonToolConfig`, `DatabaseToolConfig`, `BrowserToolConfig`, `ConfluenceToolConfig`, `ExternalMCPToolConfig`) are importable from `faith.config`.
2. `load_config()` correctly loads and validates each YAML file against its Pydantic model, inferring the model from the file path.
3. `load_config()` raises `ConfigValidationError` with a human-readable message for invalid configs — no raw Pydantic tracebacks surfaced.
4. `${VAR_NAME}` placeholders are substituted only in `secrets.yaml` (framework-level), not in project-level configs.
5. `load_secrets()` loads `.env` first, then loads `secrets.yaml` with substitution.
6. `resolve_secret_ref()` correctly resolves keys from the loaded `SecretsConfig`.
7. `load_project_configs()` discovers and loads all configs from a `.faith/` directory (system.yaml, security.yaml, tools/*.yaml, agents/*/config.yaml).
8. `python -m faith.config.schema_export` generates JSON Schema files in `faith/schemas/`.
9. Default values are applied correctly (e.g. `trust: standard`, `internet_access: true`, `history: false`).
10. All tests in `tests/test_config_loader.py` pass.

---

## Notes for Implementer

- Pydantic v2 is required (not v1). Use `model_validate()` and `model_json_schema()`, not the v1 equivalents.
- `SecretsConfig` uses `model_config = {"extra": "allow"}` because secret keys are dynamic (user-defined). Standard config models do NOT allow extras.
- The `secret_ref` pattern: tool configs store `password_secret_ref: "myproject_db_password"` — a key name. The PA calls `resolve_secret_ref()` at container startup to inject actual credential values. Agents never see `secrets.yaml`.
- The `SubfolderOverride` model handles the subfolder permission override pattern from FRS Section 4.3.2. In `filesystem.yaml`, subfolder overrides are nested under the mount they override.
- `schema_version` is included in every config root model. This is used by FAITH-006 (Config Migration) to detect version mismatches on upgrade.
- The `_infer_model_class()` function uses the file path (not just filename) to determine the model — `config.yaml` under `agents/*/` maps to `AgentConfig`, while tool files under `tools/` map based on filename.
- The `ConfigValidationError.human_message` is what the PA surfaces to the user in the Web UI — it must be clear, specific, and actionable.
- Do NOT import or depend on Redis in this module — configuration loading must work before Redis is available (for startup validation).
