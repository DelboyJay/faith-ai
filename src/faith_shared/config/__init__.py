"""Description:
    Export the canonical shared FAITH configuration contracts.

Requirements:
    - Keep `faith_shared` as the single source of truth for config models.
    - Re-export schema-version constants for runtime validation.
"""

from faith_shared.compatibility import CURRENT_SCHEMA_VERSION
from faith_shared.config.models import (
    TOOL_CONFIG_MAP,
    AccessLevel,
    AgentApprovalRules,
    AgentConfig,
    ArchetypeConfig,
    BrowserToolConfig,
    ConfigFileStatus,
    ConfigSummary,
    ConfluenceToolConfig,
    DatabaseToolConfig,
    DockerRuntimeSnapshot,
    ExternalMCPToolConfig,
    FilesystemToolConfig,
    MountConfig,
    PrivacyProfile,
    PythonToolConfig,
    RedisStatus,
    RuntimeContainerSummary,
    SecretsConfig,
    SecurityConfig,
    ServiceStatus,
    SystemConfig,
    TrustLevel,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "AccessLevel",
    "AgentApprovalRules",
    "AgentConfig",
    "ArchetypeConfig",
    "BrowserToolConfig",
    "ConfigFileStatus",
    "ConfigSummary",
    "ConfluenceToolConfig",
    "DatabaseToolConfig",
    "DockerRuntimeSnapshot",
    "ExternalMCPToolConfig",
    "FilesystemToolConfig",
    "MountConfig",
    "PrivacyProfile",
    "PythonToolConfig",
    "RedisStatus",
    "RuntimeContainerSummary",
    "SecretsConfig",
    "SecurityConfig",
    "ServiceStatus",
    "SystemConfig",
    "TOOL_CONFIG_MAP",
    "TrustLevel",
]
