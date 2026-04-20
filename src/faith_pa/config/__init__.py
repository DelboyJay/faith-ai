"""Description:
    Export the PA-facing configuration helpers.

Requirements:
    - Re-export shared config models through the PA package for compatibility.
    - Surface the current schema version from the shared compatibility module.
"""

from faith_pa.config.hot_reload import ConfigWatcher
from faith_pa.config.loader import (
    ConfigLoadError,
    ConfigValidationError,
    StartupValidationError,
    build_config_summary,
    config_dir,
    data_dir,
    load_agent_config,
    load_all_agent_configs,
    load_all_tool_configs,
    load_archetype,
    load_config,
    load_recent_projects,
    load_secrets,
    load_security_config,
    load_system_config,
    load_tool_config,
    logs_dir,
    project_config_dir,
    project_root,
    resolve_secret_ref,
    validate_startup_config,
)
from faith_pa.config.migration import MigrationEngine, MigrationNeeded, MigrationResult
from faith_shared.compatibility import CURRENT_SCHEMA_VERSION
from faith_shared.config import (
    AccessLevel,
    AgentConfig,
    ArchetypeConfig,
    ConfigSummary,
    DockerRuntimeSnapshot,
    PrivacyProfile,
    RedisStatus,
    RuntimeContainerSummary,
    SecretsConfig,
    SecurityConfig,
    ServiceStatus,
    SystemConfig,
    TrustLevel,
)

__all__ = [
    "AccessLevel",
    "AgentConfig",
    "ArchetypeConfig",
    "CURRENT_SCHEMA_VERSION",
    "ConfigLoadError",
    "ConfigSummary",
    "ConfigValidationError",
    "ConfigWatcher",
    "DockerRuntimeSnapshot",
    "MigrationEngine",
    "MigrationNeeded",
    "MigrationResult",
    "PrivacyProfile",
    "RedisStatus",
    "RuntimeContainerSummary",
    "SecretsConfig",
    "SecurityConfig",
    "ServiceStatus",
    "StartupValidationError",
    "SystemConfig",
    "TrustLevel",
    "build_config_summary",
    "config_dir",
    "data_dir",
    "load_agent_config",
    "load_all_agent_configs",
    "load_all_tool_configs",
    "load_archetype",
    "load_config",
    "load_recent_projects",
    "load_security_config",
    "load_secrets",
    "load_system_config",
    "load_tool_config",
    "logs_dir",
    "project_config_dir",
    "project_root",
    "resolve_secret_ref",
    "validate_startup_config",
]
