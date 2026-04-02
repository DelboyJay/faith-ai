"""Pydantic models for FAITH configuration and runtime status."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class ConfigFileStatus(BaseModel):
    """Presence information for a config file."""

    path: str
    exists: bool


class ConfigSummary(BaseModel):
    """High-level summary of the current framework config."""

    config_dir: str
    env_file: ConfigFileStatus
    secrets_file: ConfigFileStatus
    recent_projects_file: ConfigFileStatus
    archetypes: list[str] = Field(default_factory=list)
    recent_projects: list[str] = Field(default_factory=list)
    env_keys: list[str] = Field(default_factory=list)
    secrets: dict[str, Any] = Field(default_factory=dict)


class RedisStatus(BaseModel):
    """Connectivity status for Redis."""

    url: str
    connected: bool


class ServiceStatus(BaseModel):
    """Runtime status exposed by the PA."""

    service: str
    version: str
    status: str
    redis: RedisStatus
    config: ConfigSummary


class SecretsConfig(BaseModel):
    """Framework-level secrets file."""

    schema_version: str = "1.0"
    secrets: dict[str, str] = Field(default_factory=dict)


class PAConfig(BaseModel):
    model: str
    fallback_model: str | None = None


class LoopDetectionConfig(BaseModel):
    enabled: bool = True
    window_messages: int = Field(default=10, ge=1)
    state_repeat_threshold: int = Field(default=2, ge=1)


class CostWarningConfig(BaseModel):
    threshold_usd: float = Field(default=1.0, ge=0)


class AuditConfig(BaseModel):
    retention_days: int = Field(default=90, ge=1)


class OllamaConfig(BaseModel):
    enabled: bool = True
    mode: str = Field(default="container", pattern="^(container|external)$")
    endpoint: str | None = None


class SystemConfig(BaseModel):
    """Project-level .faith/system.yaml."""

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
    pattern: str
    events: list[FileEventType] = Field(default_factory=lambda: [FileEventType.CHANGED])


class AgentContextConfig(BaseModel):
    summary_threshold_pct: int = Field(default=50, ge=10, le=90)
    max_messages: int = Field(default=50, ge=5)


class AgentConfig(BaseModel):
    """Per-agent machine-readable config."""

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
    schema_version: str = "1.0"
    mounts: dict[str, MountConfig] = Field(default_factory=dict)


class PythonToolConfig(BaseModel):
    schema_version: str = "1.0"
    internet_access: bool = True


class DatabaseConnectionConfig(BaseModel):
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
    schema_version: str = "1.0"
    connections: dict[str, DatabaseConnectionConfig] = Field(default_factory=dict)


class BrowserToolConfig(BaseModel):
    schema_version: str = "1.0"
    headless: bool = True
    base_url: str | None = None


class ConfluenceToolConfig(BaseModel):
    schema_version: str = "1.0"
    url: str | None = None
    username: str | None = None
    password_secret_ref: str | None = None
    password: str | None = None
    default_space: str | None = None


class ExternalMCPToolConfig(BaseModel):
    schema_version: str = "1.0"
    registry_ref: str
    package_version: str | None = None
    transport: str = Field(default="stdio", pattern="^stdio$")
    env: dict[str, str] = Field(default_factory=dict)
    env_secret_refs: dict[str, str] = Field(default_factory=dict)
    privacy_tier: PrivacyProfile = PrivacyProfile.INTERNAL
    agents: list[str] = Field(default_factory=list)


TOOL_CONFIG_MAP: dict[str, type[BaseModel]] = {
    "filesystem.yaml": FilesystemToolConfig,
    "python.yaml": PythonToolConfig,
    "database.yaml": DatabaseToolConfig,
    "browser.yaml": BrowserToolConfig,
    "confluence.yaml": ConfluenceToolConfig,
}


class AgentApprovalRules(BaseModel):
    always_ask: list[str] = Field(default_factory=list)
    always_allow: list[str] = Field(default_factory=list)


class SecurityConfig(BaseModel):
    """Project-level .faith/security.yaml."""

    schema_version: str = "1.0"
    approval_rules: dict[str, AgentApprovalRules] = Field(default_factory=dict)
    trust_overrides: dict[str, TrustLevel] = Field(default_factory=dict)
    always_allow_learned: dict[str, list[str]] = Field(default_factory=dict)
    always_ask_learned: dict[str, list[str]] = Field(default_factory=dict)
    always_deny_learned: dict[str, list[str]] = Field(default_factory=dict)


class ArchetypeConfig(BaseModel):
    """Role archetype template used by the PA when creating agents."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    suggested_tools: list[str] = Field(default_factory=list)
    suggested_trust: TrustLevel = TrustLevel.STANDARD
    suggested_tags: list[str] = Field(default_factory=list)
