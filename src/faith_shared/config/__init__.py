"""Description:
    Export the canonical shared FAITH configuration contracts.

Requirements:
    - Keep `faith_shared` as the single source of truth for config models.
    - Re-export schema-version constants for runtime validation.
"""

from faith_shared.compatibility import CURRENT_SCHEMA_VERSION
from faith_shared.config.models import (
    AccessLevel,
    AgentApprovalRules,
    AgentConfig,
    ArchetypeConfig,
    BrowserToolConfig,
    ConfigFileStatus,
    ConfigSummary,
    ConfluenceToolConfig,
    DatabaseToolConfig,
    ExternalMCPToolConfig,
    FilesystemToolConfig,
    MountConfig,
    PrivacyProfile,
    PythonToolConfig,
    RedisStatus,
    SecretsConfig,
    SecurityConfig,
    ServiceStatus,
    SystemConfig,
    TOOL_CONFIG_MAP,
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
    "ExternalMCPToolConfig",
    "FilesystemToolConfig",
    "MountConfig",
    "PrivacyProfile",
    "PythonToolConfig",
    "RedisStatus",
    "SecretsConfig",
    "SecurityConfig",
    "ServiceStatus",
    "SystemConfig",
    "TOOL_CONFIG_MAP",
    "TrustLevel",
]
