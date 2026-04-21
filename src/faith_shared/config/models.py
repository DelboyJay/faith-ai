"""
Description:
    Define the shared Pydantic models used by FAITH configuration loading,
    validation, and runtime status reporting.

Requirements:
    - Provide stable schemas for project config, tool config, and runtime status
      payloads.
    - Keep enum values aligned with the FRS vocabulary.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PrivacyProfile(str, Enum):
    """
    Description:
        Define the privacy tiers supported by FAITH configuration.

    Requirements:
        - Preserve the canonical privacy labels used across runtime and tool
          configuration.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


class AccessLevel(str, Enum):
    """
    Description:
        Define the access levels supported by FAITH mount and database grants.

    Requirements:
        - Preserve the canonical read-only and read-write labels.
    """

    READONLY = "readonly"
    READWRITE = "readwrite"


class TrustLevel(str, Enum):
    """
    Description:
        Define the trust levels supported by FAITH agent configuration.

    Requirements:
        - Preserve the canonical high, standard, and low trust labels.
    """

    HIGH = "high"
    STANDARD = "standard"
    LOW = "low"


class FileEventType(str, Enum):
    """
    Description:
        Define the filesystem watch event types supported by FAITH.

    Requirements:
        - Preserve the canonical event names used by file-watch configuration.
    """

    CHANGED = "file:changed"
    CREATED = "file:created"
    DELETED = "file:deleted"


class ConfigFileStatus(BaseModel):
    """
    Description:
        Describe whether one expected configuration file exists on disk.

    Requirements:
        - Preserve the file path and presence flag.
    """

    path: str
    exists: bool


class ConfigSummary(BaseModel):
    """
    Description:
        Summarise the currently loaded FAITH configuration state.

    Requirements:
        - Report the config directory, key config file presence, archetypes,
          recent projects, and redacted environment data.
    """

    config_dir: str
    env_file: ConfigFileStatus
    secrets_file: ConfigFileStatus
    recent_projects_file: ConfigFileStatus
    archetypes: list[str] = Field(default_factory=list)
    recent_projects: list[str] = Field(default_factory=list)
    env_keys: list[str] = Field(default_factory=list)
    secrets: dict[str, Any] = Field(default_factory=dict)


class RedisStatus(BaseModel):
    """
    Description:
        Describe the runtime connectivity state for Redis.

    Requirements:
        - Preserve the connection URL and connectivity flag.
    """

    url: str
    connected: bool


class RuntimeContainerSummary(BaseModel):
    """
    Description:
        Describe one FAITH-related container for operational runtime views.

    Requirements:
        - Preserve the container name, logical role, runtime state, and image reference.
        - Carry optional health, URL, and ownership metadata when available.
    """

    name: str
    category: str
    role: str
    state: str
    image: str
    health: str | None = None
    restart_count: int = 0
    url: str | None = None
    ownership: dict[str, str] = Field(default_factory=dict)


class DockerRuntimeSnapshot(BaseModel):
    """
    Description:
        Describe the FAITH Docker runtime snapshot exposed to the UI.

    Requirements:
        - Preserve whether Docker runtime inspection is available.
        - Carry a stable ordered container list for operational panels.
        - Preserve a deduplicated image inventory for the current FAITH environment.
    """

    docker_available: bool
    status: str
    images: list[str] = Field(default_factory=list)
    containers: list[RuntimeContainerSummary] = Field(default_factory=list)


class ServiceStatus(BaseModel):
    """
    Description:
        Describe the runtime health payload exposed by FAITH services.

    Requirements:
        - Include service identity, version, high-level status, Redis state, and
          configuration summary.
        - Include the optional Docker runtime snapshot when available.
    """

    service: str
    version: str
    status: str
    redis: RedisStatus
    config: ConfigSummary
    runtime: DockerRuntimeSnapshot | None = None


class SecretsConfig(BaseModel):
    """
    Description:
        Define the schema used by the framework-level secrets file.

    Requirements:
        - Preserve the schema version and secret key-value map.
    """

    schema_version: str = "1.0"
    secrets: dict[str, str] = Field(default_factory=dict)


class PAConfig(BaseModel):
    """
    Description:
        Define the PA model selection settings in `system.yaml`.

    Requirements:
        - Preserve the primary model and optional fallback model.
    """

    model: str
    fallback_model: str | None = None


class LoopDetectionConfig(BaseModel):
    """
    Description:
        Define the loop-detection settings used by the PA runtime.

    Requirements:
        - Preserve enablement, message window, and repeat threshold settings.
    """

    enabled: bool = True
    window_messages: int = Field(default=10, ge=1)
    state_repeat_threshold: int = Field(default=2, ge=1)


class CostWarningConfig(BaseModel):
    """
    Description:
        Define cost-warning thresholds for model usage.

    Requirements:
        - Preserve the warning threshold in USD.
    """

    threshold_usd: float = Field(default=1.0, ge=0)


class AuditConfig(BaseModel):
    """
    Description:
        Define audit-log retention settings.

    Requirements:
        - Preserve the retention period in days.
    """

    retention_days: int = Field(default=90, ge=1)


class OllamaConfig(BaseModel):
    """
    Description:
        Define Ollama runtime settings for the PA.

    Requirements:
        - Preserve enablement, container-versus-external mode, and optional
          endpoint override.
    """

    enabled: bool = True
    mode: str = Field(default="container", pattern="^(container|external)$")
    endpoint: str | None = None


class SystemConfig(BaseModel):
    """
    Description:
        Define the schema for project-level `.faith/system.yaml`.

    Requirements:
        - Capture PA, privacy, loop-detection, cost, audit, Ollama, retention,
          and heartbeat settings.
    """

    schema_version: str = "1.0"
    privacy_profile: PrivacyProfile = PrivacyProfile.INTERNAL
    pa: PAConfig
    default_agent_model: str
    editor: str | None = None
    loop_detection: LoopDetectionConfig = Field(default_factory=LoopDetectionConfig)
    cost_warning: CostWarningConfig = Field(default_factory=CostWarningConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    log_retention_days: int = Field(default=90, ge=1)
    session_retention_days: int = Field(default=365, ge=1)
    stall_timeout_seconds: int = Field(default=300, ge=30)
    heartbeat_interval_seconds: int = Field(default=30, ge=5)
    heartbeat_miss_threshold: int = Field(default=3, ge=1)
    channel_agent_limit: int = Field(default=5, ge=0)


class FileWatchConfig(BaseModel):
    """
    Description:
        Define one file-watch subscription in agent configuration.

    Requirements:
        - Preserve the watched pattern and subscribed event types.
    """

    pattern: str
    events: list[FileEventType] = Field(default_factory=lambda: [FileEventType.CHANGED])


class AgentContextConfig(BaseModel):
    """
    Description:
        Define context-management settings for one agent.

    Requirements:
        - Preserve the summary threshold and maximum retained messages.
    """

    summary_threshold_pct: int = Field(default=50, ge=10, le=90)
    max_messages: int = Field(default=50, ge=5)


class AgentConfig(BaseModel):
    """
    Description:
        Define the schema for one agent machine-readable config file.

    Requirements:
        - Capture model, trust, tool, database, mount, watch, context, CAG, and
          escalation settings.
    """

    schema_version: str = "1.0"
    name: str
    role: str
    model: str | None = None
    trust: TrustLevel = TrustLevel.STANDARD
    tools: list[str] = Field(default_factory=list)
    databases: dict[str, AccessLevel] = Field(default_factory=dict)
    mounts: dict[str, AccessLevel] = Field(default_factory=dict)
    file_watches: list[FileWatchConfig] = Field(default_factory=list)
    context: AgentContextConfig = Field(default_factory=AgentContextConfig)
    listen_tags: list[str] = Field(default_factory=list)
    cag_documents: list[str] = Field(default_factory=list)
    cag_max_tokens: int = Field(default=8000, ge=0)
    escalate_for: list[str] = Field(default_factory=list)
    python_timeout_seconds: int = Field(default=60, ge=5)
    mcp_native: bool = True


class SubfolderOverride(BaseModel):
    """
    Description:
        Define one subfolder-specific access override for a mount.

    Requirements:
        - Preserve the overridden access level.
    """

    access: AccessLevel


class MountConfig(BaseModel):
    """
    Description:
        Define one mounted host path for the filesystem tool.

    Requirements:
        - Capture path, access, recursion, history, and file-size settings.
        - Preserve nested subfolder overrides.
    """

    host_path: str
    access: AccessLevel
    recursive: bool = True
    history: bool = False
    history_depth: int = Field(default=10, ge=1)
    max_file_size_mb: int = Field(default=50, ge=1)
    subfolder_overrides: dict[str, SubfolderOverride] = Field(default_factory=dict)


class FilesystemToolConfig(BaseModel):
    """
    Description:
        Define the schema for the filesystem tool config file.

    Requirements:
        - Preserve the schema version and mounted-path definitions.
    """

    schema_version: str = "1.0"
    mounts: dict[str, MountConfig] = Field(default_factory=dict)


class PythonToolConfig(BaseModel):
    """
    Description:
        Define the schema for the Python tool config file.

    Requirements:
        - Preserve schema version, internet-access policy, and timeout default.
    """

    schema_version: str = "1.0"
    internet_access: bool = True
    timeout_seconds: int = Field(default=60, ge=1, le=3600)


class DatabaseConnectionConfig(BaseModel):
    """
    Description:
        Define one database connection entry for the database tool.

    Requirements:
        - Capture connection coordinates, authentication references, access
          controls, and result limits.
    """

    host: str
    port: int = 5432
    database: str
    user: str
    password_secret_ref: str | None = None
    password: str | None = None
    access: AccessLevel = AccessLevel.READONLY
    permission_override: bool = False
    max_rows: int = Field(default=1000, ge=1)
    max_result_mb: int = Field(default=5, ge=1)


class DatabaseToolConfig(BaseModel):
    """
    Description:
        Define the schema for the database tool config file.

    Requirements:
        - Preserve schema version and named database connection definitions.
    """

    schema_version: str = "1.0"
    connections: dict[str, DatabaseConnectionConfig] = Field(default_factory=dict)


class BrowserToolConfig(BaseModel):
    """
    Description:
        Define the schema for the browser tool config file.

    Requirements:
        - Preserve schema version, headless mode, and optional base URL.
    """

    schema_version: str = "1.0"
    headless: bool = True
    base_url: str | None = None


class ConfluenceToolConfig(BaseModel):
    """
    Description:
        Define the schema for the Confluence tool config file.

    Requirements:
        - Preserve connection details, secret references, and default space.
    """

    schema_version: str = "1.0"
    url: str | None = None
    username: str | None = None
    password_secret_ref: str | None = None
    password: str | None = None
    default_space: str | None = None


class ExternalMCPToolConfig(BaseModel):
    """
    Description:
        Define the schema for one external MCP tool installation record.

    Requirements:
        - Preserve registry reference, version, transport, environment, privacy,
          command arguments, and agent targeting settings.
    """

    schema_version: str = "1.0"
    source_type: str = Field(default="registry", pattern="^registry$")
    registry_ref: str
    package_version: str
    transport: str = Field(default="stdio", pattern="^stdio$")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_secret_refs: dict[str, str] = Field(default_factory=dict)
    privacy_tier: PrivacyProfile = PrivacyProfile.INTERNAL
    agents: list[str] = Field(default_factory=list)
    enabled: bool = True


TOOL_CONFIG_MAP: dict[str, type[BaseModel]] = {
    "filesystem.yaml": FilesystemToolConfig,
    "python.yaml": PythonToolConfig,
    "database.yaml": DatabaseToolConfig,
    "browser.yaml": BrowserToolConfig,
    "confluence.yaml": ConfluenceToolConfig,
}


class AgentApprovalRules(BaseModel):
    """
    Description:
        Define remembered approval rules for one agent.

    Requirements:
        - Preserve always-ask, always-deny, and always-allow rule sets.
    """

    always_ask: list[str] = Field(default_factory=list)
    always_deny: list[str] = Field(default_factory=list)
    always_allow: list[str] = Field(default_factory=list)


class SecurityConfig(BaseModel):
    """
    Description:
        Define the schema for project-level `.faith/security.yaml`.

    Requirements:
        - Preserve approval rules, trust overrides, and learned decision caches.
    """

    schema_version: str = "1.0"
    approval_rules: dict[str, AgentApprovalRules] = Field(default_factory=dict)
    trust_overrides: dict[str, TrustLevel] = Field(default_factory=dict)
    always_allow_learned: dict[str, list[str]] = Field(default_factory=dict)
    always_ask_learned: dict[str, list[str]] = Field(default_factory=dict)
    always_deny_learned: dict[str, list[str]] = Field(default_factory=dict)


class ArchetypeConfig(BaseModel):
    """
    Description:
        Define one reusable role archetype template for agent creation.

    Requirements:
        - Allow extra keys for forward-compatible archetype extensions.
        - Preserve suggested tools, trust, and tags.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    suggested_tools: list[str] = Field(default_factory=list)
    suggested_trust: TrustLevel = TrustLevel.STANDARD
    suggested_tags: list[str] = Field(default_factory=list)
