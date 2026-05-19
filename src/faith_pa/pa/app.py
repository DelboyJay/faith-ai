"""Description:
    Provide the Project Agent HTTP service and lightweight runtime bridges.

Requirements:
    - Expose PA health, status, route-discovery, and runtime WebSocket surfaces.
    - Run the lightweight browser-chat bridge that consumes browser input and
      streams project-agent output frames.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from httpx import AsyncClient
from pydantic import BaseModel, Field, field_validator

from faith_pa import __version__
from faith_pa.agent.caching import apply_cache_hints, detect_provider
from faith_pa.agent.llm_client import LLMClient
from faith_pa.config import (
    ConfigLoadError,
    ConfigSummary,
    DockerRuntimeSnapshot,
    RedisStatus,
    RuntimeContainerSummary,
    ServiceStatus,
    build_config_summary,
    data_dir,
    load_all_agent_configs,
    load_system_config,
    logs_dir,
    update_system_config_fields,
)
from faith_pa.logging import EventLogWriter, LogRotator, TokenLogger
from faith_pa.model_catalog import (
    OPENROUTER_MODELS_API_URL,
    ModelCatalog,
)
from faith_pa.pa.chat_tool_loop import (
    ProjectAgentMCPToolExecutor,
    build_mcp_inventory_answer,
    build_tool_manifest_prompt,
    format_tool_result_for_model,
    get_explicit_tool_family_request,
    is_mcp_inventory_question,
    parse_chat_tool_call,
)
from faith_pa.pa.container_manager import ContainerManager
from faith_pa.pa.context_compaction import (
    ContextCompactionController,
    ContextCompactionDecision,
    ContextCompactionMode,
)
from faith_pa.pa.effective_context import ProjectAgentContextCompiler
from faith_pa.pa.rule_promotion import assess_rule_promotion
from faith_pa.pa.session import SessionManager
from faith_pa.runtime_time_context import (
    RuntimeTimeContextProvider,
    RuntimeUserContextProvider,
)
from faith_pa.security.audit_log import AuditLogger
from faith_pa.utils import (
    SYSTEM_EVENTS_CHANNEL,
    USER_INPUT_CHANNEL,
    check_connection,
    get_async_client,
    get_redis_url,
)
from faith_pa.utils.tokens import count_text_tokens
from faith_shared.api import (
    RouteManifestEntry,
    ServiceRouteManifest,
    describe_route_implementation,
)

try:
    import docker
except ImportError:  # pragma: no cover - exercised when docker SDK is unavailable.
    docker = None


BOOTSTRAP_CONTAINER_METADATA: dict[str, dict[str, str]] = {
    "faith-pa": {
        "category": "bootstrap",
        "role": "Project Agent",
        "url": "http://localhost:8000",
    },
    "faith-web-ui": {
        "category": "bootstrap",
        "role": "Web UI",
        "url": "http://localhost:8080",
    },
    "faith-redis": {
        "category": "bootstrap",
        "role": "Redis",
    },
    "faith-ollama": {
        "category": "bootstrap",
        "role": "Ollama",
        "url": "http://localhost:11434",
    },
    "faith-mcp-registry": {
        "category": "bootstrap",
        "role": "MCP Registry",
        "url": "http://localhost:8081",
    },
}

PROJECT_AGENT_ID = "project-agent"
PROJECT_AGENT_OUTPUT_CHANNEL = f"agent:{PROJECT_AGENT_ID}:output"
DEFAULT_PROJECT_AGENT_MODEL = os.getenv("FAITH_PROJECT_AGENT_MODEL", "ollama/llama3:8b")
DEFAULT_PROJECT_AGENT_SYSTEM_PROMPT = (
    "You are the FAITH Project Agent.\n"
    "* Answer the user's question clearly, concisely, and helpfully. When you do not know something, say so plainly.\n"
    "* when tools provide a response do not acknowledge this with messages like "
    '"Thank you for the tool result! According to the output, ...", instead just pass on the output to the user.\n'
    "* When reporting the date and time to the user use the format `<day of week>, <day> <month name> <full year> <24 hour time>` "
    "here is an example, `Monday, 27rd April 2026 15:35:00`\n"
)
PROJECT_AGENT_SYSTEM_PROMPT = DEFAULT_PROJECT_AGENT_SYSTEM_PROMPT
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECT_AGENT_SESSION_ROOT_NAME = "pa-runtime"
DEFAULT_PROJECT_AGENT_COMPACTION_MODEL = os.getenv(
    "FAITH_PROJECT_AGENT_COMPACTION_MODEL", "ollama/llama3:8b"
)
MAX_PROJECT_AGENT_PROMPT_CHARS = 20_000
MAX_PROJECT_AGENT_HISTORY = 12
MAX_PROJECT_AGENT_TOOL_ITERATIONS = 3
DEFAULT_PROJECT_AGENT_SOFT_COMPACTION_THRESHOLD_PCT = 80
DEFAULT_PROJECT_AGENT_HARD_COMPACTION_THRESHOLD_PCT = 95
DEFAULT_PROJECT_AGENT_RETAINED_HISTORY_MESSAGES = 4
STREAM_CHUNK_SIZE = 24


class ProjectAgentPromptUpdate(BaseModel):
    """Description:
        Validate a user-submitted Project Agent system prompt update.

    Requirements:
        - Accept the edited prompt text from the prompt editor panel.
    """

    prompt: str


class UserSettingsUpdate(BaseModel):
    """Description:
        Validate one user-settings update submitted through the PA API.

    Requirements:
        - Accept optional display name, country, preferred locale, and timezone values.
        - Treat blank strings as unset values instead of persisting whitespace.
        - Keep the payload narrow so unrelated system settings cannot be changed here.
    """

    display_name: str | None = Field(default=None, max_length=120)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    preferred_locale: str | None = Field(default=None, max_length=35)
    timezone: str | None = Field(default=None, max_length=120)

    @field_validator("display_name", "preferred_locale", "timezone", mode="before")
    @classmethod
    def normalise_optional_text(cls, value: Any) -> Any:
        """Description:
            Strip whitespace from optional text fields and collapse blanks to `None`.

        Requirements:
            - Keep persisted settings free from accidental leading or trailing whitespace.
            - Let users clear a field by submitting an empty string.

        :param value: Raw field value received from the request payload.
        :returns: Normalised field value ready for model validation.
        """

        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("country_code", mode="before")
    @classmethod
    def normalise_country_code(cls, value: Any) -> Any:
        """Description:
            Normalise the optional country code to uppercase ISO-style text.

        Requirements:
            - Collapse blank strings to `None`.
            - Preserve non-string values for later validation failure.

        :param value: Raw country-code field value received from the request payload.
        :returns: Normalised uppercase country code when available.
        """

        if isinstance(value, str):
            stripped = value.strip().upper()
            return stripped or None
        return value


class SelectionOption(BaseModel):
    """Description:
        Represent one fixed-option entry returned to the Web UI settings panel.

    Requirements:
        - Preserve a stable machine value plus a user-visible label.
    """

    value: str
    label: str


class UserSettingsPayload(BaseModel):
    """Description:
        Represent the persisted user-settings payload returned to browser clients.

    Requirements:
        - Expose the current display name, country, preferred locale, timezone, config path, update metadata, and fixed-option lists.
        - Keep the response stable for the user-settings panel preload and save flows.
    """

    display_name: str | None = None
    country_code: str | None = None
    preferred_locale: str | None = None
    timezone: str | None = None
    country_options: list[SelectionOption] = Field(default_factory=list)
    locale_options: list[SelectionOption] = Field(default_factory=list)
    locale_options_by_country: dict[str, list[SelectionOption]] = Field(default_factory=dict)
    timezone_options: list[SelectionOption] = Field(default_factory=list)
    timezone_options_by_country: dict[str, list[SelectionOption]] = Field(default_factory=dict)
    path: str
    updated_at: str | None = None


class SessionStartPayload(BaseModel):
    """Description:
        Represent the browser-facing payload returned after starting a fresh Project Agent session.

    Requirements:
        - Preserve both the new active session identifier and the immediately previous session when one existed.
        - Expose enough metadata for the Session History panel to refresh and select the new session.
    """

    session_id: str
    previous_session_id: str | None = None
    status: str
    started_at: str
    task_count: int = 0


class AgentModelOverridePayload(BaseModel):
    """Description:
        Represent one persisted per-agent model override exposed to the browser.

    Requirements:
        - Preserve the agent identifier, role, current override, and config path.
    """

    agent_id: str
    role: str
    model: str | None = None
    path: str


class ModelSettingsPayload(BaseModel):
    """Description:
        Represent the persisted model-settings payload returned to browser clients.

    Requirements:
        - Expose the active PA model, default agent model, model catalog, and per-agent overrides.
        - Keep the response inspectable so diagnostics panels can show the exact persisted paths in use.
    """

    pa_model: str
    default_agent_model: str
    system_path: str
    catalog_path: str
    updated_at: str | None = None
    model_options: list[dict[str, str]] = Field(default_factory=list)
    catalog: list[dict[str, Any]] = Field(default_factory=list)
    agent_overrides: list[AgentModelOverridePayload] = Field(default_factory=list)


class ModelSettingsUpdate(BaseModel):
    """Description:
        Validate one model-settings update submitted through the PA API.

    Requirements:
        - Accept direct PA/default-agent model changes.
        - Accept per-agent model overrides keyed by agent identifier.
        - Accept context-window overrides keyed by fully qualified model identifier.
    """

    pa_model: str = Field(min_length=1)
    default_agent_model: str = Field(min_length=1)
    agent_overrides: dict[str, str | None] = Field(default_factory=dict)
    context_window_overrides: dict[str, int | None] = Field(default_factory=dict)


DEFAULT_USER_COUNTRY_CODE = "GB"
DEFAULT_USER_LOCALE = "en-GB"
DEFAULT_USER_TIMEZONE = "Europe/London"
COUNTRY_OPTIONS: tuple[SelectionOption, ...] = (
    SelectionOption(value="AU", label="Australia"),
    SelectionOption(value="CA", label="Canada"),
    SelectionOption(value="DE", label="Germany"),
    SelectionOption(value="ES", label="Spain"),
    SelectionOption(value="FR", label="France"),
    SelectionOption(value="GB", label="United Kingdom"),
    SelectionOption(value="IE", label="Ireland"),
    SelectionOption(value="IN", label="India"),
    SelectionOption(value="IT", label="Italy"),
    SelectionOption(value="JP", label="Japan"),
    SelectionOption(value="NL", label="Netherlands"),
    SelectionOption(value="NZ", label="New Zealand"),
    SelectionOption(value="SG", label="Singapore"),
    SelectionOption(value="US", label="United States"),
)
LOCALE_OPTIONS: tuple[SelectionOption, ...] = (
    SelectionOption(value="de-DE", label="German (Germany)"),
    SelectionOption(value="en-AU", label="English (Australia)"),
    SelectionOption(value="en-CA", label="English (Canada)"),
    SelectionOption(value="en-GB", label="English (United Kingdom)"),
    SelectionOption(value="en-IE", label="English (Ireland)"),
    SelectionOption(value="en-IN", label="English (India)"),
    SelectionOption(value="en-NZ", label="English (New Zealand)"),
    SelectionOption(value="en-SG", label="English (Singapore)"),
    SelectionOption(value="en-US", label="English (United States)"),
    SelectionOption(value="es-ES", label="Spanish (Spain)"),
    SelectionOption(value="fr-CA", label="French (Canada)"),
    SelectionOption(value="fr-FR", label="French (France)"),
    SelectionOption(value="hi-IN", label="Hindi (India)"),
    SelectionOption(value="it-IT", label="Italian (Italy)"),
    SelectionOption(value="ja-JP", label="Japanese (Japan)"),
    SelectionOption(value="nl-NL", label="Dutch (Netherlands)"),
)
COUNTRY_LOCALE_OPTIONS: dict[str, tuple[SelectionOption, ...]] = {
    "AU": (SelectionOption(value="en-AU", label="English (Australia)"),),
    "CA": (
        SelectionOption(value="en-CA", label="English (Canada)"),
        SelectionOption(value="fr-CA", label="French (Canada)"),
    ),
    "DE": (SelectionOption(value="de-DE", label="German (Germany)"),),
    "ES": (SelectionOption(value="es-ES", label="Spanish (Spain)"),),
    "FR": (SelectionOption(value="fr-FR", label="French (France)"),),
    "GB": (SelectionOption(value="en-GB", label="English (United Kingdom)"),),
    "IE": (SelectionOption(value="en-IE", label="English (Ireland)"),),
    "IN": (
        SelectionOption(value="en-IN", label="English (India)"),
        SelectionOption(value="hi-IN", label="Hindi (India)"),
    ),
    "IT": (SelectionOption(value="it-IT", label="Italian (Italy)"),),
    "JP": (SelectionOption(value="ja-JP", label="Japanese (Japan)"),),
    "NL": (SelectionOption(value="nl-NL", label="Dutch (Netherlands)"),),
    "NZ": (SelectionOption(value="en-NZ", label="English (New Zealand)"),),
    "SG": (SelectionOption(value="en-SG", label="English (Singapore)"),),
    "US": (SelectionOption(value="en-US", label="English (United States)"),),
}
COUNTRY_TIMEZONE_OPTIONS: dict[str, tuple[SelectionOption, ...]] = {
    "AU": (
        SelectionOption(value="Australia/Brisbane", label="Australia/Brisbane"),
        SelectionOption(value="Australia/Sydney", label="Australia/Sydney"),
        SelectionOption(value="Australia/Hobart", label="Australia/Hobart"),
        SelectionOption(value="Australia/Adelaide", label="Australia/Adelaide"),
        SelectionOption(value="Australia/Darwin", label="Australia/Darwin"),
        SelectionOption(value="Australia/Perth", label="Australia/Perth"),
    ),
    "CA": (
        SelectionOption(value="America/St_Johns", label="America/St_Johns"),
        SelectionOption(value="America/Halifax", label="America/Halifax"),
        SelectionOption(value="America/Toronto", label="America/Toronto"),
        SelectionOption(value="America/Winnipeg", label="America/Winnipeg"),
        SelectionOption(value="America/Edmonton", label="America/Edmonton"),
        SelectionOption(value="America/Vancouver", label="America/Vancouver"),
    ),
    "DE": (SelectionOption(value="Europe/Berlin", label="Europe/Berlin"),),
    "ES": (SelectionOption(value="Europe/Madrid", label="Europe/Madrid"),),
    "FR": (SelectionOption(value="Europe/Paris", label="Europe/Paris"),),
    "GB": (SelectionOption(value="Europe/London", label="Europe/London"),),
    "IE": (SelectionOption(value="Europe/Dublin", label="Europe/Dublin"),),
    "IN": (SelectionOption(value="Asia/Kolkata", label="Asia/Kolkata"),),
    "IT": (SelectionOption(value="Europe/Rome", label="Europe/Rome"),),
    "JP": (SelectionOption(value="Asia/Tokyo", label="Asia/Tokyo"),),
    "NL": (SelectionOption(value="Europe/Amsterdam", label="Europe/Amsterdam"),),
    "NZ": (
        SelectionOption(value="Pacific/Auckland", label="Pacific/Auckland"),
        SelectionOption(value="Pacific/Chatham", label="Pacific/Chatham"),
    ),
    "SG": (SelectionOption(value="Asia/Singapore", label="Asia/Singapore"),),
    "US": (
        SelectionOption(value="America/New_York", label="America/New_York"),
        SelectionOption(value="America/Chicago", label="America/Chicago"),
        SelectionOption(value="America/Denver", label="America/Denver"),
        SelectionOption(value="America/Los_Angeles", label="America/Los_Angeles"),
        SelectionOption(value="America/Anchorage", label="America/Anchorage"),
        SelectionOption(value="Pacific/Honolulu", label="Pacific/Honolulu"),
    ),
}


def _build_country_options() -> list[SelectionOption]:
    """Description:
        Return the fixed country options exposed by the user-settings API.

    Requirements:
        - Preserve the configured option order for stable UI rendering.

    :returns: Ordered country options for the settings panel.
    """

    return list(COUNTRY_OPTIONS)


def _build_locale_options(country_code: str | None = None) -> list[SelectionOption]:
    """Description:
        Return the fixed locale options exposed by the user-settings API.

    Requirements:
        - Return the configured country-specific locale list when the country is known.
        - Fall back to the default-country list when the supplied country is missing or unknown.

    :param country_code: Selected two-letter country code.
    :returns: Ordered locale options for the resolved country.
    """

    resolved_country_code = (country_code or DEFAULT_USER_COUNTRY_CODE).upper()
    return list(
        COUNTRY_LOCALE_OPTIONS.get(
            resolved_country_code, COUNTRY_LOCALE_OPTIONS[DEFAULT_USER_COUNTRY_CODE]
        )
    )


def _build_locale_options_by_country() -> dict[str, list[SelectionOption]]:
    """Description:
        Return the full curated locale-option map keyed by country code.

    Requirements:
        - Preserve the configured option order for each country.

    :returns: Mapping of country code to ordered locale options.
    """

    return {country_code: list(options) for country_code, options in COUNTRY_LOCALE_OPTIONS.items()}


def _build_timezone_options(country_code: str | None) -> list[SelectionOption]:
    """Description:
        Return timezone options filtered by one selected country code.

    Requirements:
        - Return the configured country-specific list when the country is known.
        - Fall back to the default-country list when the supplied country is missing or unknown.

    :param country_code: Selected two-letter country code.
    :returns: Ordered timezone options for the resolved country.
    """

    resolved_country_code = (country_code or DEFAULT_USER_COUNTRY_CODE).upper()
    return list(
        COUNTRY_TIMEZONE_OPTIONS.get(
            resolved_country_code, COUNTRY_TIMEZONE_OPTIONS[DEFAULT_USER_COUNTRY_CODE]
        )
    )


def _build_timezone_options_by_country() -> dict[str, list[SelectionOption]]:
    """Description:
        Return the full curated timezone-option map keyed by country code.

    Requirements:
        - Preserve the configured option order for each country.

    :returns: Mapping of country code to ordered timezone options.
    """

    return {
        country_code: list(options) for country_code, options in COUNTRY_TIMEZONE_OPTIONS.items()
    }


def _find_country_for_timezone(timezone_name: str | None) -> str | None:
    """Description:
        Return the first configured country containing one timezone option.

    Requirements:
        - Return `None` when the timezone is unknown to the curated settings registry.

    :param timezone_name: IANA timezone name to look up.
    :returns: Matching country code when the timezone is known.
    """

    if not timezone_name:
        return None
    for country_code, options in COUNTRY_TIMEZONE_OPTIONS.items():
        if any(option.value == timezone_name for option in options):
            return country_code
    return None


def _find_country_for_locale(locale_name: str | None) -> str | None:
    """Description:
        Return the first configured country containing one locale option.

    Requirements:
        - Return `None` when the locale is unknown to the curated settings registry.

    :param locale_name: Locale name to look up.
    :returns: Matching country code when the locale is known.
    """

    if not locale_name:
        return None
    for country_code, options in COUNTRY_LOCALE_OPTIONS.items():
        if any(option.value == locale_name for option in options):
            return country_code
    return None


def _resolve_user_country_code(country_code: str | None, timezone_name: str | None) -> str:
    """Description:
        Resolve one stable country code for the user-settings payload.

    Requirements:
        - Prefer the explicitly saved country code when it is valid for the saved timezone.
        - Fall back to the country implied by the saved timezone when needed.
        - Use the configured FAITH default when no better signal exists.

    :param country_code: Saved or submitted country code.
    :param timezone_name: Saved or submitted timezone name.
    :returns: Resolved two-letter country code for the settings payload.
    """

    saved_country_code = (country_code or "").upper()
    if saved_country_code and saved_country_code in COUNTRY_TIMEZONE_OPTIONS:
        if not timezone_name or any(
            option.value == timezone_name for option in COUNTRY_TIMEZONE_OPTIONS[saved_country_code]
        ):
            return saved_country_code
    inferred_country_code = _find_country_for_timezone(timezone_name)
    return inferred_country_code or DEFAULT_USER_COUNTRY_CODE


def _resolve_user_locale(country_code: str, preferred_locale: str | None) -> str:
    """Description:
        Resolve one stable locale value for the user-settings payload.

    Requirements:
        - Prefer the explicitly saved locale when it belongs to the resolved country.
        - Fall back to the first curated locale for the resolved country when needed.

    :param country_code: Resolved two-letter country code.
    :param preferred_locale: Saved or submitted locale value.
    :returns: Resolved locale value for the settings payload.
    """

    country_locales = _build_locale_options(country_code)
    if preferred_locale and any(option.value == preferred_locale for option in country_locales):
        return preferred_locale
    return country_locales[0].value if country_locales else DEFAULT_USER_LOCALE


class UserSettingsStore:
    """Description:
        Load and persist user-scoped settings from the project system configuration.

    Requirements:
        - Persist browser-saved user settings under the host-backed PA runtime volume when available.
        - Fall back to project `.faith/system.yaml` only for initial default values and local development.
        - Limit writes to the user-profile fields owned by the settings panel and return stable metadata.

    :param project_root: Project root that contains the `.faith` configuration directory.
    """

    def __init__(self, *, project_root: Path | None = None) -> None:
        """Description:
            Initialise the user-settings store for one project root.

        Requirements:
            - Resolve the backing `system.yaml` path relative to the supplied project root.

        :param project_root: Project root that contains the `.faith` configuration directory.
        """

        self.project_root = Path(project_root or PROJECT_ROOT)
        self.default_system_path = self.project_root / ".faith" / "system.yaml"
        runtime_root = _project_agent_session_root()
        if (
            runtime_root == PROJECT_ROOT
            and "FAITH_DATA_DIR" not in os.environ
            and "FAITH_PA_SESSION_ROOT" not in os.environ
        ):
            self.system_path = self.default_system_path
        else:
            self.system_path = runtime_root / "user-settings" / "system.yaml"

    def read(self) -> UserSettingsPayload:
        """Description:
            Load the current persisted user settings from the system config.

        Requirements:
            - Surface saved display name, country, locale, and timezone values.
            - Return fixed-option lists for country, locale, and timezone selectors.
            - Report update metadata when the config file exists on disk.

        :returns: Current persisted user-settings payload.
        """

        try:
            config = load_system_config(root=self.project_root)
            default_display_name = config.display_name
            default_country_code = config.country_code
            default_preferred_locale = config.preferred_locale
            default_timezone = config.timezone
        except ConfigLoadError:
            default_display_name = None
            default_country_code = None
            default_preferred_locale = None
            default_timezone = None
        persisted_payload = self._load_persisted_payload()
        resolved_timezone = (
            persisted_payload.get("timezone", default_timezone) or DEFAULT_USER_TIMEZONE
        )
        resolved_country_code = _resolve_user_country_code(
            persisted_payload.get("country_code", default_country_code),
            resolved_timezone,
        )
        resolved_preferred_locale = _resolve_user_locale(
            resolved_country_code,
            persisted_payload.get("preferred_locale", default_preferred_locale),
        )
        updated_at = None
        metadata_path = (
            self.default_system_path if self.default_system_path.exists() else self.system_path
        )
        if self.system_path.exists():
            metadata_path = self.system_path
        if metadata_path.exists():
            updated_at = datetime.fromtimestamp(
                metadata_path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        return UserSettingsPayload(
            display_name=persisted_payload.get("display_name", default_display_name),
            country_code=resolved_country_code,
            preferred_locale=resolved_preferred_locale,
            timezone=resolved_timezone,
            country_options=_build_country_options(),
            locale_options=_build_locale_options(resolved_country_code),
            locale_options_by_country=_build_locale_options_by_country(),
            timezone_options=_build_timezone_options(resolved_country_code),
            timezone_options_by_country=_build_timezone_options_by_country(),
            path=self.system_path.as_posix(),
            updated_at=updated_at,
        )

    def update(self, settings: UserSettingsUpdate) -> UserSettingsPayload:
        """Description:
            Persist one validated user-settings update to the system config.

        Requirements:
            - Reject unknown country codes before mutating config on disk.
            - Reject unknown locale values before mutating config on disk.
            - Reject invalid timezone identifiers before mutating config on disk.
            - Reject timezone choices that do not belong to the selected country.
            - Rewrite only the supported user-settings fields.

        :param settings: Validated browser-submitted user-settings update.
        :raises ValueError: If the timezone value is invalid.
        :returns: Updated persisted user-settings payload.
        """

        if settings.country_code and settings.country_code not in COUNTRY_TIMEZONE_OPTIONS:
            raise ValueError("Country must be one of the supported FAITH settings options.")
        if settings.preferred_locale and not any(
            option.value == settings.preferred_locale for option in LOCALE_OPTIONS
        ):
            raise ValueError(
                "Preferred locale must be one of the supported FAITH settings options."
            )
        if settings.timezone and not RuntimeTimeContextProvider._is_valid_timezone(
            settings.timezone
        ):
            raise ValueError(
                "Timezone must be a valid IANA timezone identifier such as Europe/London."
            )
        selected_country_code = (
            settings.country_code.upper()
            if settings.country_code
            else _find_country_for_locale(settings.preferred_locale)
            or _resolve_user_country_code(None, settings.timezone)
        )
        resolved_country_code = _resolve_user_country_code(
            selected_country_code,
            settings.timezone,
        )
        resolved_preferred_locale = _resolve_user_locale(
            resolved_country_code,
            settings.preferred_locale,
        )
        if settings.preferred_locale and not any(
            option.value == settings.preferred_locale
            for option in _build_locale_options(resolved_country_code)
        ):
            raise ValueError(
                "Preferred locale must belong to the selected country from the available dropdown options."
            )
        resolved_timezone = settings.timezone
        if resolved_timezone and not any(
            option.value == resolved_timezone
            for option in _build_timezone_options(resolved_country_code)
        ):
            raise ValueError(
                "Timezone must belong to the selected country from the available dropdown options."
            )
        if not resolved_timezone:
            timezone_options = _build_timezone_options(resolved_country_code)
            resolved_timezone = (
                timezone_options[0].value if timezone_options else DEFAULT_USER_TIMEZONE
            )
        if self.system_path == self.default_system_path:
            update_system_config_fields(
                {
                    "display_name": settings.display_name,
                    "country_code": resolved_country_code,
                    "preferred_locale": resolved_preferred_locale,
                    "timezone": resolved_timezone,
                },
                root=self.project_root,
            )
            return self.read()
        self.system_path.parent.mkdir(parents=True, exist_ok=True)
        self.system_path.write_text(
            json.dumps(
                {
                    "display_name": settings.display_name,
                    "country_code": resolved_country_code,
                    "preferred_locale": resolved_preferred_locale,
                    "timezone": resolved_timezone,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return self.read()

    def _load_persisted_payload(self) -> dict[str, Any]:
        """Description:
            Load the persisted user-settings overlay payload from the runtime volume.

        Requirements:
            - Return an empty mapping when no overlay has been saved yet.
            - Accept either JSON or YAML-compatible content for future migration safety.

        :returns: Persisted overlay payload for user-scoped settings.
        :raises ConfigLoadError: If the saved overlay cannot be parsed as a mapping.
        """

        if not self.system_path.exists():
            return {}
        try:
            payload = json.loads(self.system_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigLoadError(f"Invalid user settings in {self.system_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigLoadError(
                f"Invalid user settings root in {self.system_path}: expected a mapping"
            )
        return payload


class ModelSettingsStore:
    """Description:
        Load and persist model-management settings for the PA and specialist agents.

    Requirements:
        - Persist model-catalog metadata on the host-backed PA runtime volume.
        - Persist PA/default-agent model choices in project ``system.yaml``.
        - Persist per-agent model overrides in each agent ``config.yaml`` file.

    :param project_root: Project root that contains the `.faith` configuration directory.
    """

    def __init__(self, *, project_root: Path | None = None) -> None:
        """Description:
            Initialise the model-settings store.

        Requirements:
            - Resolve the project config path and host-backed model-catalog path eagerly.

        :param project_root: Project root that contains the `.faith` configuration directory.
        """

        self.project_root = Path(project_root or PROJECT_ROOT).resolve()
        self.system_path = self.project_root / ".faith" / "system.yaml"
        self.catalog_path = _project_agent_session_root() / "model-catalog.json"

    def read(
        self,
        *,
        openrouter_payload: dict[str, Any] | None = None,
        llm_client: LLMClient | None = None,
    ) -> ModelSettingsPayload:
        """Description:
            Load the current persisted model settings and merged model catalog.

        Requirements:
            - Merge current project model choices into the catalog even when provider discovery is unavailable.
            - Merge pricing-derived context-window hints and optional OpenRouter discovery metadata.
            - Keep the payload stable for the browser model-settings panel.

        :param openrouter_payload: Optional OpenRouter models API payload already fetched by the caller.
        :param llm_client: Optional live PA LLM client used to derive local runtime diagnostics.
        :returns: Current persisted model-settings payload.
        """

        system_config = load_system_config(root=self.project_root)
        agent_configs = load_all_agent_configs(root=self.project_root)
        catalog = self._load_catalog()
        self._merge_pricing_hints(catalog)
        self._merge_project_models(catalog, system_config, agent_configs)
        if openrouter_payload is not None:
            catalog.merge_openrouter_models_payload(openrouter_payload)
        self._apply_runtime_diagnostics(catalog, llm_client)
        self._save_catalog(catalog)
        metadata_candidates = [
            path for path in (self.system_path, self.catalog_path) if path.exists()
        ]
        updated_at = None
        if metadata_candidates:
            updated_at = datetime.fromtimestamp(
                max(path.stat().st_mtime for path in metadata_candidates),
                tz=timezone.utc,
            ).isoformat()
        return ModelSettingsPayload(
            pa_model=system_config.pa.model,
            default_agent_model=system_config.default_agent_model,
            system_path=self.system_path.as_posix(),
            catalog_path=self.catalog_path.as_posix(),
            updated_at=updated_at,
            model_options=catalog.model_options(),
            catalog=[
                entry.model_dump(mode="json") | {"key": entry.key}
                for entry in catalog.sorted_entries()
            ],
            agent_overrides=[
                AgentModelOverridePayload(
                    agent_id=agent_id,
                    role=config.role,
                    model=config.model,
                    path=(
                        self.project_root / ".faith" / "agents" / agent_id / "config.yaml"
                    ).as_posix(),
                )
                for agent_id, config in sorted(agent_configs.items())
            ],
        )

    def update(self, settings: ModelSettingsUpdate) -> ModelSettingsPayload:
        """Description:
            Persist one browser-submitted model-settings update.

        Requirements:
            - Rewrite the PA and default-agent model settings through the validated system-config path.
            - Rewrite per-agent model overrides in the project agent config files.
            - Persist context-window overrides in the host-backed model catalog.

        :param settings: Validated model-settings update payload.
        :returns: Updated persisted model-settings payload.
        """

        update_system_config_fields(
            {
                "pa": {"model": settings.pa_model},
                "default_agent_model": settings.default_agent_model,
            },
            root=self.project_root,
        )
        for agent_id, model_name in settings.agent_overrides.items():
            self._update_agent_override(agent_id, model_name)
        catalog = self._load_catalog()
        self._merge_pricing_hints(catalog)
        for model_key, value in settings.context_window_overrides.items():
            if value is None:
                continue
            catalog.apply_context_window_override(model_key, value)
        self._save_catalog(catalog)
        return self.read()

    def resolve_context_window(self, model_key: str) -> int | None:
        """Description:
            Return the best known context-window size for one fully qualified model key.

        Requirements:
            - Prefer the persisted model-catalog value when available.
            - Return `None` when the model is unknown or has no reliable context-window value.

        :param model_key: Fully qualified model key such as ``ollama/llama3:8b``.
        :returns: Best known context-window size for the model, if available.
        """

        catalog = self._load_catalog()
        self._merge_pricing_hints(catalog)
        entry = catalog.entries.get(model_key)
        if entry is None or entry.context_window.value <= 0:
            return None
        safe_usable_context = entry.runtime.get("safe_usable_context")
        if isinstance(safe_usable_context, int) and safe_usable_context > 0:
            return safe_usable_context
        return entry.context_window.value

    def _load_catalog(self) -> ModelCatalog:
        """Description:
            Load the persisted model catalog from disk.

        Requirements:
            - Return an empty catalog when no catalog has been saved yet.

        :returns: Loaded model catalog.
        """

        return ModelCatalog.load(self.catalog_path)

    def _save_catalog(self, catalog: ModelCatalog) -> None:
        """Description:
            Persist one model catalog to disk.

        Requirements:
            - Ensure the host-backed parent directory exists before writing.

        :param catalog: Catalog to persist.
        """

        catalog.dump(self.catalog_path)

    def _merge_pricing_hints(self, catalog: ModelCatalog) -> None:
        """Description:
            Merge persisted FAITH pricing context-window hints into the model catalog.

        Requirements:
            - Prefer the cached pricing file when it exists.
            - Fall back to the bundled default pricing file otherwise.

        :param catalog: Catalog to augment.
        """

        for path in (
            data_dir() / "model-prices.cache.json",
            data_dir() / "model-prices.default.json",
        ):
            catalog.merge_pricing_catalog(path)

    def _merge_project_models(
        self,
        catalog: ModelCatalog,
        system_config: Any,
        agent_configs: dict[str, Any],
    ) -> None:
        """Description:
            Ensure current project model selections exist in the catalog.

        Requirements:
            - Seed the PA, default-agent, and per-agent model choices into the catalog even when discovery metadata is unavailable.

        :param catalog: Catalog to augment.
        :param system_config: Loaded validated project system config.
        :param agent_configs: Loaded validated per-agent configs.
        """

        for model_key in [
            system_config.pa.model,
            system_config.default_agent_model,
            *[config.model for config in agent_configs.values() if config.model],
        ]:
            if not model_key or "/" not in model_key:
                continue
            provider, model = model_key.split("/", 1)
            catalog.ensure_entry(provider=provider, model=model)

    def _apply_runtime_diagnostics(
        self,
        catalog: ModelCatalog,
        llm_client: LLMClient | None,
    ) -> None:
        """Description:
            Apply deterministic local runtime diagnostics to known Ollama catalog entries.

        Requirements:
            - Distinguish nominal context-window values from safe usable context estimates.
            - Surface non-blocking warnings in entry runtime metadata when VRAM heuristics constrain the usable context.

        :param catalog: Catalog to augment.
        :param llm_client: Optional live PA LLM client.
        """

        if llm_client is None or not hasattr(llm_client, "build_model_context_diagnostic"):
            return
        usable_vram_mb = _read_int_env("FAITH_OLLAMA_USABLE_VRAM_MB")
        for entry in catalog.sorted_entries():
            if entry.provider != "ollama" or entry.context_window.value <= 0:
                continue
            diagnostic = llm_client.build_model_context_diagnostic(
                nominal_context_window=entry.context_window.value,
                usable_vram_mb=usable_vram_mb,
                system_ram_mb=llm_client.system_ram_mb,
                provenance=str(entry.context_window.provenance),
                route_kind=getattr(
                    getattr(llm_client, "ollama_resolution", None), "route_kind", "container"
                ),
            )
            entry.runtime.update(
                {
                    "route_kind": getattr(
                        getattr(llm_client, "ollama_resolution", None), "route_kind", "container"
                    ),
                    "safe_usable_context": diagnostic.safe_usable_context,
                    "context_warning": diagnostic.warning,
                    "system_ram_mb": llm_client.system_ram_mb,
                    "usable_vram_mb": usable_vram_mb,
                }
            )

    def _update_agent_override(self, agent_id: str, model_name: str | None) -> None:
        """Description:
            Persist one per-agent model override back into the agent config file.

        Requirements:
            - Reject unknown agent identifiers rather than silently creating new config files.
            - Keep the stored agent config valid after the model change.

        :param agent_id: Target agent identifier.
        :param model_name: New fully qualified model value, or `None` to clear the override.
        :raises ValueError: If the requested agent config does not exist.
        """

        config_path = self.project_root / ".faith" / "agents" / agent_id / "config.yaml"
        if not config_path.exists():
            raise ValueError(f"Unknown agent override target: {agent_id}")
        current_payload = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(current_payload, dict):
            raise ValueError(f"Invalid agent config root in {config_path}")
        if model_name:
            current_payload["model"] = model_name
        else:
            current_payload.pop("model", None)
        config_path.write_text(json.dumps(current_payload, indent=2), encoding="utf-8")


def _read_int_env(name: str) -> int | None:
    """Description:
        Read one integer environment hint safely.

    Requirements:
        - Return `None` when the variable is missing or malformed.

    :param name: Environment variable name.
    :returns: Parsed integer value, if valid.
    """

    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _fetch_openrouter_models_payload(api_key: str | None) -> dict[str, Any] | None:
    """Description:
        Fetch the OpenRouter models catalog payload when a key is available.

    Requirements:
        - Degrade gracefully when no API key is configured or the request fails.
        - Return only JSON-object payloads.

    :param api_key: Optional OpenRouter API key.
    :returns: Parsed OpenRouter models payload when available, otherwise `None`.
    """

    if not api_key:
        return None
    try:
        async with AsyncClient(timeout=15.0) as client:
            response = await client.get(
                OPENROUTER_MODELS_API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _project_agent_session_root() -> Path:
    """Description:
        Resolve the persistent Project Agent session root directory.

    Requirements:
        - Honour the explicit `FAITH_PA_SESSION_ROOT` override when present.
        - Otherwise place PA session state under the mounted FAITH data directory when configured.
        - Fall back to the repository-root behaviour only when no persistent runtime path is configured.

    :returns: Filesystem root used for Project Agent session persistence.
    """

    explicit_root = os.environ.get("FAITH_PA_SESSION_ROOT", "").strip()
    if explicit_root:
        return Path(explicit_root).resolve()

    data_root = os.environ.get("FAITH_DATA_DIR", "").strip()
    if data_root:
        return (Path(data_root).resolve() / DEFAULT_PROJECT_AGENT_SESSION_ROOT_NAME).resolve()

    return PROJECT_ROOT


class ProjectAgentPromptStore:
    """Description:
        Read, validate, persist, and reset the PA project-instruction surface.

    Requirements:
        - Map the editable PA prompt UI surface to project-root `AGENTS.md`.
        - Treat a missing `AGENTS.md` file as an empty instruction file rather than as an error.
        - Reject invalid project-instruction updates before mutating the source file.

    :param project_root: Repository or FAITH workspace root containing `.faith`.
    :param default_prompt: Built-in prompt used when no custom prompt exists.
    """

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        default_prompt: str = DEFAULT_PROJECT_AGENT_SYSTEM_PROMPT,
    ) -> None:
        """Description:
            Initialise the Project Agent prompt store.

        Requirements:
            - Resolve the prompt path relative to the supplied or default project root.

        :param project_root: Repository or FAITH workspace root containing `.faith`.
        :param default_prompt: Built-in prompt used when no custom prompt exists.
        """

        self.project_root = Path(project_root or PROJECT_ROOT)
        self.default_prompt = default_prompt
        self.prompt_path = self.project_root / "AGENTS.md"

    def read(self) -> dict[str, Any]:
        """Description:
            Return the active PA project-instruction text and editor metadata.

        Requirements:
            - Always report the project-root `AGENTS.md` path.
            - Treat a missing file as an empty instruction layer.
            - Report whether the file differs from the empty default.

        :returns: Active prompt metadata payload.
        """

        if self.prompt_path.exists():
            prompt_text = self.prompt_path.read_text(encoding="utf-8")
            updated_at = datetime.fromtimestamp(
                self.prompt_path.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat()
            return {
                "prompt": prompt_text,
                "source": "project",
                "path": self.prompt_path.as_posix(),
                "default_available": True,
                "differs_from_default": bool(prompt_text.strip()),
                "updated_at": updated_at,
            }
        return {
            "prompt": "",
            "source": "project",
            "path": self.prompt_path.as_posix(),
            "default_available": True,
            "differs_from_default": False,
            "updated_at": None,
        }

    def get_active_prompt(self) -> str:
        """Description:
            Return only the active project-instruction text for model calls.

        Requirements:
            - Avoid exposing editor metadata to the chat runtime.

        :returns: Active project-instruction text.
        """

        return str(self.read()["prompt"])

    def update(self, prompt: str) -> dict[str, Any]:
        """Description:
            Validate and persist one Project Agent prompt update.

        Requirements:
            - Reject invalid prompts before writing to disk.
            - Create the prompt directory when needed.

        :param prompt: Candidate prompt text.
        :raises ValueError: If the prompt is invalid.
        :returns: Updated active prompt metadata.
        """

        self.validate(prompt)
        self.prompt_path.parent.mkdir(parents=True, exist_ok=True)
        self.prompt_path.write_text(prompt, encoding="utf-8")
        return self.read()

    def reset(self) -> dict[str, Any]:
        """Description:
            Remove the project instruction file and return the empty metadata.

        Requirements:
            - Succeed even when no custom prompt file currently exists.

        :returns: Empty project-instruction metadata.
        """

        if self.prompt_path.exists():
            self.prompt_path.unlink()
        return self.read()

    def validate(self, prompt: str) -> None:
        """Description:
            Validate one candidate Project Agent system prompt.

        Requirements:
            - Reject blank prompts with a plain-English message.
            - Reject prompts that exceed the safe editor limit.

        :param prompt: Candidate prompt text.
        :raises ValueError: If the prompt is invalid.
        """

        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")
        if len(prompt) > MAX_PROJECT_AGENT_PROMPT_CHARS:
            raise ValueError(
                f"Prompt is too long. Maximum length is {MAX_PROJECT_AGENT_PROMPT_CHARS} characters."
            )


class ProjectAgentChatRuntime:
    """Description:
        Consume browser user-input messages and stream Project Agent replies.

    Requirements:
        - Subscribe to the shared browser input Redis channel.
        - Echo the user's message back onto the Project Agent output feed.
        - Call the shared LLM client for text requests and publish streamed
          response chunks for the browser chat panel.
        - Publish status transitions for active, idle, and error states.

    :param redis_client: Shared Redis client used for pub/sub and output frames.
    :param llm_client: Shared LLM client used to generate assistant replies.
    :param model_name: Human-readable model name surfaced to the UI.
    :param tool_executor: Optional PA MCP tool executor used for chat-time tool calls.
    :param prompt_store: Prompt store used to load the active PA system prompt.
    :param time_context_provider: Optional runtime time-context provider used for prompt assembly.
    :param user_context_provider: Optional runtime user-context provider used for prompt assembly.
    :param user_settings_store: Shared user-settings store used to load runtime user profile context.
    :param model_settings_store: Shared model-settings store used to load runtime model diagnostics.
    :param session_manager: Shared session manager used for transcript persistence and recovery.
    :param token_logger: Shared token logger used for model-usage and cost accounting.
    :param audit_logger: Shared audit logger used for PA chat-time tool visibility.
    :param compaction_llm_client: Optional local LLM client used only for history compaction summaries.
    :param output_channel: Redis output channel used by the Project Agent panel.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        llm_client: Any,
        model_name: str,
        tool_executor: Any | None = None,
        prompt_store: ProjectAgentPromptStore | None = None,
        time_context_provider: RuntimeTimeContextProvider | None = None,
        user_context_provider: RuntimeUserContextProvider | None = None,
        user_settings_store: UserSettingsStore | None = None,
        model_settings_store: ModelSettingsStore | None = None,
        session_manager: SessionManager | None = None,
        token_logger: TokenLogger | None = None,
        audit_logger: AuditLogger | None = None,
        compaction_llm_client: Any | None = None,
        output_channel: str = PROJECT_AGENT_OUTPUT_CHANNEL,
    ) -> None:
        """Description:
            Initialise the lightweight browser-chat runtime.

        Requirements:
            - Start with an empty bounded chat history.
            - Delay pub/sub initialisation until the runtime loop starts.

        :param redis_client: Shared Redis client used for pub/sub and output frames.
        :param llm_client: Shared LLM client used to generate assistant replies.
        :param model_name: Human-readable model name surfaced to the UI.
        :param tool_executor: Optional PA MCP tool executor used for chat-time tool calls.
        :param prompt_store: Prompt store used to load the active PA system prompt.
        :param time_context_provider: Optional runtime time-context provider used for prompt assembly.
        :param user_context_provider: Optional runtime user-context provider used for prompt assembly.
        :param user_settings_store: Shared user-settings store used to load runtime user profile context.
        :param model_settings_store: Shared model-settings store used to load runtime model diagnostics.
        :param session_manager: Shared session manager used for transcript persistence and recovery.
        :param token_logger: Shared token logger used for model-usage and cost accounting.
        :param audit_logger: Shared audit logger used for PA chat-time tool visibility.
        :param compaction_llm_client: Optional local LLM client used only for history compaction summaries.
        :param output_channel: Redis output channel used by the Project Agent panel.
        """

        self.redis = redis_client
        self.llm_client = llm_client
        self.model_name = model_name
        self.tool_executor = tool_executor or ProjectAgentMCPToolExecutor()
        self.prompt_store = prompt_store or ProjectAgentPromptStore()
        self.user_settings_store = user_settings_store or UserSettingsStore(
            project_root=Path(os.environ.get("FAITH_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()
        )
        self.model_settings_store = model_settings_store or ModelSettingsStore(
            project_root=Path(os.environ.get("FAITH_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()
        )
        self.time_context_provider = time_context_provider or RuntimeTimeContextProvider(
            configured_timezone=self._load_configured_timezone(),
        )
        self.user_context_provider = user_context_provider or self._load_configured_user_context()
        self.session_manager = session_manager or SessionManager(
            project_root=_project_agent_session_root()
        )
        self.token_logger = token_logger or _build_token_logger()
        self.audit_logger = audit_logger or _build_audit_logger()
        self.context_compiler = ProjectAgentContextCompiler(
            project_root=self.prompt_store.project_root,
            model_name=self.model_name,
            snapshot_root=_project_agent_session_root(),
        )
        self.compaction_llm_client = (
            compaction_llm_client
            or _build_project_agent_compaction_llm_client(base_llm_client=self.llm_client)
        )
        self.compaction_controller = ContextCompactionController(
            model_name=self.model_name,
            soft_threshold_pct=DEFAULT_PROJECT_AGENT_SOFT_COMPACTION_THRESHOLD_PCT,
            hard_threshold_pct=DEFAULT_PROJECT_AGENT_HARD_COMPACTION_THRESHOLD_PCT,
            retain_recent_messages=DEFAULT_PROJECT_AGENT_RETAINED_HISTORY_MESSAGES,
        )
        self.output_channel = output_channel
        self.history: list[dict[str, str]] = []
        self.transcript_messages: list[dict[str, str]] = []
        self.compacted_history_summary = ""
        self._last_compaction_record: dict[str, Any] | None = None
        self._last_effective_context_snapshot: dict[str, Any] | None = None
        self._last_context_window_limit: int | None = (
            self.model_settings_store.resolve_context_window(self.model_name)
        )
        self._pubsub: Any | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._restore_saved_transcript()

    async def start(self) -> asyncio.Task:
        """Description:
            Start the background browser-chat bridge task.

        Requirements:
            - Subscribe to the shared browser input channel exactly once.

        :returns: Running background task for the chat bridge.
        """

        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="project-agent-chat-runtime")
        return self._task

    async def stop(self) -> None:
        """Description:
            Stop the background browser-chat bridge cleanly.

        Requirements:
            - Cancel the background task when it is still running.
            - Unsubscribe the pub/sub object from the browser input channel.
            - Close the pub/sub object when it exposes a close API.
        """

        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(USER_INPUT_CHANNEL)
            except Exception:
                pass
            close = getattr(self._pubsub, "aclose", None)
            if callable(close):
                await close()
            else:
                close = getattr(self._pubsub, "close", None)
                if callable(close):
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
        self._pubsub = None

    async def _run_loop(self) -> None:
        """Description:
            Poll browser input messages from Redis and dispatch replies.

        Requirements:
            - Ignore non-message pub/sub frames.
            - Continue after non-fatal processing errors.
        """

        self._pubsub = self.redis.pubsub()
        await self._pubsub.subscribe(USER_INPUT_CHANNEL)
        try:
            while self._running:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if not message or message.get("type") != "message":
                    continue
                raw = message.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    payload = json.loads(str(raw))
                except json.JSONDecodeError:
                    await self._publish_error("Received malformed browser input payload.")
                    continue
                await self._handle_payload(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._publish_error("Project Agent chat bridge stopped unexpectedly.")
            raise

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        """Description:
            Process one browser-originated input payload.

        Requirements:
            - Handle text input by calling the shared LLM client.
            - Handle uploads by acknowledging receipt and preserving useful text
              content where available.
            - Publish status transitions for active and idle states around every
              handled message.

        :param payload: Browser-originated input payload.
        """

        payload_type = str(payload.get("type", ""))
        if payload_type not in {"user_input", "user_upload"}:
            return

        user_text = self._build_user_message(payload)
        if not user_text:
            return

        await self._ensure_active_session()
        active_task = self._ensure_active_task()
        await self._publish_status("active")
        self._record_transcript_message("user", user_text)
        await self._publish_output(f"User: {user_text}\n")

        try:
            if is_mcp_inventory_question(user_text):
                list_available_tools = getattr(self.tool_executor, "list_available_tools", None)
                tools = list_available_tools() if callable(list_available_tools) else ()
                reply_text = build_mcp_inventory_answer(tuple(tools))
                self._append_history("user", user_text)
                self._append_history("assistant", reply_text)
                self._record_transcript_message("assistant", reply_text)
                await self._stream_assistant_reply(reply_text)
                await self._publish_status("idle")
                return
            requested_tool_family = get_explicit_tool_family_request(user_text)
            promoted_rule_notice = self._maybe_promote_durable_rule(
                user_text=user_text,
                task_id=active_task.task_id,
            )
            await self._maybe_compact_before_turn(
                user_text=user_text,
                task_id=active_task.task_id,
                requested_tool_family=requested_tool_family,
            )
            messages = self._build_chat_messages(
                user_text,
                requested_tool_family=requested_tool_family,
                task_id=active_task.task_id,
            )
            reply_text = await self._generate_reply_with_tools(
                messages,
                task_id=active_task.task_id,
                requested_tool_family=requested_tool_family,
            )
            if promoted_rule_notice:
                reply_text = f"{promoted_rule_notice}\n\n{reply_text}".strip()
            if not reply_text:
                reply_text = "I did not generate a reply for that message."
            self._append_history("user", user_text)
            self._append_history("assistant", reply_text)
            self._record_transcript_message("assistant", reply_text)
            await self._stream_assistant_reply(reply_text)
            await self._publish_status("idle")
        except Exception as exc:
            await self._publish_error(f"Project Agent reply failed: {exc}")

    def _build_user_message(self, payload: dict[str, Any]) -> str:
        """Description:
            Convert one browser payload into the user text sent through the LLM.

        Requirements:
            - Preserve plain text input exactly.
            - Turn uploads into a concise textual instruction with filename and,
              for text uploads, inline content.

        :param payload: Browser-originated input payload.
        :returns: Normalised user message text.
        """

        if payload.get("type") == "user_input":
            return str(payload.get("message", "")).strip()

        filename = str(payload.get("filename", "upload"))
        content_type = str(payload.get("content_type", "application/octet-stream"))
        message = str(payload.get("message", "")).strip()
        size_bytes = int(payload.get("size_bytes", 0) or 0)
        summary = f"Uploaded file '{filename}' ({content_type}, {size_bytes} bytes)."
        if content_type in {"text/plain", "text/markdown"}:
            try:
                text_content = base64.b64decode(str(payload.get("content_base64", ""))).decode(
                    "utf-8",
                    errors="replace",
                )
                excerpt = text_content.strip()
                if excerpt:
                    summary = f"{summary}\n\nFile content:\n{excerpt}"
            except Exception:
                summary = f"{summary}\n\nThe file content could not be decoded as UTF-8 text."
        if message:
            summary = f"{message}\n\n{summary}"
        return summary.strip()

    def _build_chat_messages(
        self,
        user_text: str,
        *,
        requested_tool_family: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Description:
            Build the lightweight chat payload for one browser message.

        Requirements:
            - Include the active Project Agent system prompt first.
            - Preserve a bounded recent conversation history.

        :param user_text: Current user message text.
        :param requested_tool_family: Optional explicit tool-family preference for the current turn.
        :returns: Chat message payload for the shared LLM client.
        """

        tool_preference_block = ""
        if requested_tool_family is not None:
            tool_preference_block = (
                "[Runtime Tool Preference]\n"
                f"- The user explicitly requested the `{requested_tool_family}` tool family for this turn.\n"
                f"- Do not call any tool family other than `{requested_tool_family}` unless the user changes that instruction.\n\n"
            )
        compaction_memory_block = ""
        if self.compacted_history_summary.strip():
            compaction_memory_block = (
                f"[Compacted Working Memory]\n{self.compacted_history_summary.strip()}\n\n"
            )
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": self._build_system_context(
                    task_id=task_id,
                    tool_preference_block=tool_preference_block,
                    compaction_memory_block=compaction_memory_block,
                ),
            },
            *self.history,
            {"role": "user", "content": user_text},
        ]
        return messages

    def _build_system_context(
        self,
        *,
        task_id: str | None,
        tool_preference_block: str,
        compaction_memory_block: str = "",
    ) -> str:
        """Description:
            Build the compiled PA system-context text for one browser-chat turn.

        Requirements:
            - Keep protected FAITH core instructions outside project `AGENTS.md`.
            - Apply the project instruction layer from `AGENTS.md` on every turn.
            - Keep compacted working-memory notes separate from raw history when available.
            - Persist a redacted effective-context snapshot when session and turn metadata are available.

        :param task_id: Active task identifier for effective-context persistence.
        :param tool_preference_block: Optional per-turn tool-preference instruction block.
        :param compaction_memory_block: Optional compacted working-memory block derived from older resolved history.
        :returns: Compiled PA system-context text.
        """

        runtime_user_block = self.user_context_provider.build_prompt_block()
        runtime_time_block = self.time_context_provider.build_prompt_block()
        tool_manifest_block = (
            f"{compaction_memory_block}{tool_preference_block}{build_tool_manifest_prompt()}"
        )
        session_id = self.session_manager.session_id
        if session_id and task_id:
            snapshot = self.context_compiler.compile_for_turn(
                session_id=session_id,
                turn_id=task_id,
                core_instructions=self.prompt_store.default_prompt,
                runtime_user_block=runtime_user_block,
                runtime_time_block=runtime_time_block,
                tool_manifest_block=tool_manifest_block,
            )
            self._last_effective_context_snapshot = {
                "snapshot_id": snapshot.context_hash,
                "turn_id": task_id,
                "context_files": self.context_compiler.describe_context_files(),
            }
            return snapshot.compiled_context

        self._last_effective_context_snapshot = None
        return self.context_compiler.compose_context_text(
            core_instructions=self.prompt_store.default_prompt,
            runtime_user_block=runtime_user_block,
            runtime_time_block=runtime_time_block,
            tool_manifest_block=tool_manifest_block,
        )

    @staticmethod
    def _load_configured_timezone() -> str | None:
        """Description:
            Load the configured project timezone for PA browser-chat prompt assembly.

        Requirements:
            - Prefer the persisted host-backed user-settings override when available.
            - Reuse the project system configuration as a fallback when no saved override exists.
            - Fall back cleanly when project config is not ready yet.

        :returns: Configured timezone identifier when available.
        """

        try:
            settings_store = UserSettingsStore(
                project_root=Path(os.environ.get("FAITH_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()
            )
            return settings_store.read().timezone
        except ConfigLoadError:
            try:
                return load_system_config().timezone
            except ConfigLoadError:
                return None

    def _load_configured_user_context(self) -> RuntimeUserContextProvider:
        """Description:
            Load the configured user-profile context for PA browser-chat prompt assembly.

        Requirements:
            - Prefer the persisted host-backed user-settings override when available.
            - Reuse the project system configuration as a fallback when no saved override exists.
            - Fall back cleanly to an empty user-profile context when config is not ready yet.

        :returns: Runtime user-context provider seeded with saved user-profile fields.
        """

        try:
            settings = self.user_settings_store.read()
            return RuntimeUserContextProvider(
                display_name=settings.display_name,
                country_code=settings.country_code,
                preferred_locale=settings.preferred_locale,
            )
        except ConfigLoadError:
            try:
                system_config = load_system_config()
            except ConfigLoadError:
                return RuntimeUserContextProvider()
            return RuntimeUserContextProvider(
                display_name=system_config.display_name,
                country_code=system_config.country_code,
                preferred_locale=system_config.preferred_locale,
            )

    def _estimate_active_context_usage(
        self,
        *,
        user_text: str,
        task_id: str,
        requested_tool_family: str | None,
    ) -> ContextCompactionDecision:
        """Description:
            Estimate how full the active Project Agent context would be for the upcoming turn.

        Requirements:
            - Use the reliable configured context-window limit only when FAITH knows it.
            - Keep the estimate free from effective-context snapshot persistence side effects.

        :param user_text: Current user message text.
        :param task_id: Active task identifier for the turn.
        :param requested_tool_family: Optional explicit tool-family preference for the turn.
        :returns: Deterministic compaction decision for the upcoming turn.
        """

        del task_id
        tool_preference_block = ""
        if requested_tool_family is not None:
            tool_preference_block = (
                "[Runtime Tool Preference]\n"
                f"- The user explicitly requested the `{requested_tool_family}` tool family for this turn.\n"
                f"- Do not call any tool family other than `{requested_tool_family}` unless the user changes that instruction.\n\n"
            )
        compaction_memory_block = ""
        if self.compacted_history_summary.strip():
            compaction_memory_block = (
                f"[Compacted Working Memory]\n{self.compacted_history_summary.strip()}\n\n"
            )
        preview_messages = [
            {
                "role": "system",
                "content": self.context_compiler.compose_context_text(
                    core_instructions=self.prompt_store.default_prompt,
                    runtime_user_block=self.user_context_provider.build_prompt_block(),
                    runtime_time_block=self.time_context_provider.build_prompt_block(),
                    tool_manifest_block=(
                        f"{compaction_memory_block}{tool_preference_block}{build_tool_manifest_prompt()}"
                    ),
                ),
            },
            *self.history,
            {"role": "user", "content": user_text},
        ]
        usage_percentage = self.compaction_controller.estimate_usage_percentage(
            preview_messages,
            context_window_limit=self._last_context_window_limit,
        )
        return self.compaction_controller.classify_usage(
            usage_percentage=usage_percentage,
            context_window_limit=self._last_context_window_limit,
        )

    async def _maybe_compact_before_turn(
        self,
        *,
        user_text: str,
        task_id: str,
        requested_tool_family: str | None,
    ) -> None:
        """Description:
            Run soft or hard Project Agent history compaction before the next turn when thresholds demand it.

        Requirements:
            - Trigger hard compaction automatically at or above the hard threshold before the turn is processed.
            - Allow soft compaction to run without blocking the Input panel UI.

        :param user_text: Current user message text.
        :param task_id: Active task identifier for the turn.
        :param requested_tool_family: Optional explicit tool-family preference for the turn.
        """

        decision = self._estimate_active_context_usage(
            user_text=user_text,
            task_id=task_id,
            requested_tool_family=requested_tool_family,
        )
        if decision.mode is ContextCompactionMode.NONE:
            return
        await self._run_history_compaction(
            task_id=task_id,
            mode=decision.mode,
            usage_before_pct=decision.usage_percentage,
        )

    async def _run_history_compaction(
        self,
        *,
        task_id: str,
        mode: ContextCompactionMode,
        usage_before_pct: int | None,
    ) -> None:
        """Description:
            Compact older Project Agent history into the retained working-memory summary.

        Requirements:
            - Compact only history layers and never the protected system-context layers.
            - Persist an inspectable compaction record under the active session.
            - Emit a visible blocked-state signal only during hard compaction.

        :param task_id: Active task identifier receiving compaction diagnostics.
        :param mode: Chosen compaction mode.
        :param usage_before_pct: Estimated usage percentage before compaction when known.
        """

        selection = self.compaction_controller.select_history_for_compaction(self.history)
        if not selection.compacted_messages:
            self._last_compaction_record = {
                "mode": mode.value,
                "usage_before_pct": usage_before_pct,
                "usage_after_pct": usage_before_pct,
                "summary": self.compacted_history_summary,
                "retained_messages": selection.retained_messages,
                "compacted_messages": [],
            }
            return

        if mode is ContextCompactionMode.HARD:
            await self._publish_compaction_state(
                active=True,
                diagnostic="Compaction is temporarily pausing new sends.",
            )

        summary_prompt = self.compaction_controller.build_summary_prompt(
            existing_summary=self.compacted_history_summary,
            compacted_messages=selection.compacted_messages,
        )
        summary_response = await self.compaction_llm_client.chat(
            [
                {
                    "role": "system",
                    "content": "You are a concise Project Agent history compactor.",
                },
                {"role": "user", "content": summary_prompt},
            ],
            model=getattr(self.compaction_llm_client, "model", None),
            temperature=0.0,
        )
        summary_text = str(getattr(summary_response, "content", "")).strip()
        self.compacted_history_summary = summary_text or self.compacted_history_summary

        retained_messages = list(selection.retained_messages)
        retained_messages.insert(
            0,
            {
                "role": "system",
                "content": self.compaction_controller.build_compaction_note(
                    compacted_messages=len(selection.compacted_messages)
                ),
                "retain": True,
            },
        )
        self.history = retained_messages[-MAX_PROJECT_AGENT_HISTORY:]

        usage_after_pct = self.compaction_controller.estimate_usage_percentage(
            self._build_chat_messages(
                "",
                requested_tool_family=None,
                task_id=task_id,
            ),
            context_window_limit=self._last_context_window_limit,
        )
        self._last_compaction_record = self._persist_compaction_record(
            task_id=task_id,
            mode=mode,
            usage_before_pct=usage_before_pct,
            usage_after_pct=usage_after_pct,
            selection=selection,
            summary_text=self.compacted_history_summary,
        )
        self.session_manager.append_channel_message(
            task_id=task_id,
            channel_name="pa-user",
            sender=PROJECT_AGENT_ID,
            recipient=PROJECT_AGENT_ID,
            msg_type="context_compaction",
            summary=(
                f"Project Agent compacted active history in {mode.value} mode. "
                f"Usage before: {usage_before_pct}%."
            ),
            status="success",
        )
        await self._publish_warning(
            "Project Agent compacted the active context to keep the next turn within the model context window."
        )
        if mode is ContextCompactionMode.HARD:
            await self._publish_compaction_state(active=False, diagnostic="")

    def _persist_compaction_record(
        self,
        *,
        task_id: str,
        mode: ContextCompactionMode,
        usage_before_pct: int | None,
        usage_after_pct: int | None,
        selection: Any,
        summary_text: str,
    ) -> dict[str, Any]:
        """Description:
            Persist one inspectable Project Agent compaction record under the active session.

        Requirements:
            - Store the retained and compacted history split together with the working summary.
            - Keep the record under the host-backed session tree for later debugging.

        :param task_id: Active task identifier receiving the compaction record.
        :param mode: Chosen compaction mode.
        :param usage_before_pct: Estimated usage percentage before compaction.
        :param usage_after_pct: Estimated usage percentage after compaction.
        :param selection: Retained-versus-compacted history split.
        :param summary_text: Current compacted working-memory summary.
        :returns: Persisted compaction record payload.
        """

        session_id = self.session_manager.session_id or "unknown-session"
        record_dir = self.session_manager.sessions_dir / session_id / "compaction"
        record_dir.mkdir(parents=True, exist_ok=True)
        record_index = len(list(record_dir.glob("*.json"))) + 1
        record_path = record_dir / f"{task_id}-{record_index:03d}.json"
        payload = {
            "session_id": session_id,
            "task_id": task_id,
            "mode": mode.value,
            "usage_before_pct": usage_before_pct,
            "usage_after_pct": usage_after_pct,
            "summary": summary_text,
            "retained_messages": selection.retained_messages,
            "compacted_messages": selection.compacted_messages,
            "path": record_path.as_posix(),
        }
        record_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def _maybe_promote_durable_rule(self, *, user_text: str, task_id: str) -> str:
        """Description:
            Persist one explicitly durable user rule into the project AGENTS.md instruction file.

        Requirements:
            - Auto-promote only clearly declarative durable rules.
            - Avoid duplicating rules already present in the project instruction file.
            - Record the promotion in both audit and session history.

        :param user_text: Current user message text.
        :param task_id: Active task identifier receiving the audit trail.
        :returns: User-facing promotion notice, or an empty string when no promotion happened.
        """

        assessment = assess_rule_promotion(user_text)
        if not assessment.should_promote or not assessment.candidate_rule_text:
            return ""

        existing_prompt = self.prompt_store.get_active_prompt().strip()
        if assessment.candidate_rule_text in existing_prompt:
            return "That rule was already present in AGENTS.md."

        updated_prompt = (
            f"{existing_prompt}\n\n{assessment.candidate_rule_text}\n"
            if existing_prompt
            else f"{assessment.candidate_rule_text}\n"
        )
        self.prompt_store.update(updated_prompt)
        self.audit_logger.log_tool_operation(
            agent=PROJECT_AGENT_ID,
            tool="project_instructions",
            action="append_rule",
            target=assessment.candidate_rule_text,
            channel="pa-user",
        )
        self.session_manager.append_channel_message(
            task_id=task_id,
            channel_name="pa-user",
            sender=PROJECT_AGENT_ID,
            recipient=PROJECT_AGENT_ID,
            msg_type="rule_promotion",
            summary=(
                f"Promoted durable user rule into AGENTS.md: {assessment.candidate_rule_text}"
            ),
            status="success",
        )
        return "I automatically added it to AGENTS.md so it persists for future turns."

    async def _generate_reply_with_tools(
        self,
        messages: list[dict[str, str]],
        *,
        task_id: str,
        requested_tool_family: str | None = None,
    ) -> str:
        """Description:
            Generate one Project Agent reply with bounded MCP tool-call support.

        Requirements:
            - Let non-native models request tools by emitting compact JSON.
            - Execute requested tools through the PA MCP executor.
            - Feed tool results back to the model and stop on the first normal
              assistant answer or after the safe iteration limit.

        :param messages: Initial chat payload for the LLM.
        :param task_id: Active task identifier used for token and cost accounting.
        :param requested_tool_family: Optional explicit tool-family preference for the current turn.
        :returns: Final assistant reply text.
        """

        working_messages = list(messages)
        for _ in range(MAX_PROJECT_AGENT_TOOL_ITERATIONS):
            request_messages = apply_cache_hints(
                list(working_messages),
                provider=detect_provider(
                    self.model_name,
                    getattr(self.llm_client, "ollama_host", ""),
                ),
                cag_present=bool(self.context_compiler.read_project_instructions().strip()),
            )
            response = await self.llm_client.chat(
                request_messages,
                model=self.model_name,
                temperature=0.2,
            )
            await self._record_token_usage(task_id=task_id, response=response)
            reply_text = str(getattr(response, "content", "")).strip()
            tool_call = parse_chat_tool_call(reply_text)
            if tool_call is None:
                return reply_text
            if requested_tool_family is not None and tool_call.tool != requested_tool_family:
                working_messages.append({"role": "assistant", "content": reply_text})
                working_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tool selection correction:\n"
                            f"- The user explicitly requested the `{requested_tool_family}` tool family.\n"
                            f"- Do not call `{tool_call.tool}` for this turn.\n"
                            f"- Retry with a `{requested_tool_family}` tool call or answer without tools if that is impossible."
                        ),
                    }
                )
                continue

            await self._publish_output(f"PA is using {tool_call.tool}.{tool_call.action}...\n")
            tool_result = await self.tool_executor.execute(tool_call)
            self._record_tool_visibility(
                task_id=task_id, tool_call=tool_call, tool_result=tool_result
            )
            working_messages.append({"role": "assistant", "content": reply_text})
            working_messages.append(
                {
                    "role": "user",
                    "content": format_tool_result_for_model(tool_call, tool_result),
                }
            )
        return "I stopped because the tool-use loop reached its safety limit."

    async def _record_token_usage(self, *, task_id: str, response: Any) -> None:
        """Description:
            Record token and estimated-cost usage for one Project Agent model call.

        Requirements:
            - Ignore responses that do not expose token counts.
            - Update both the per-call token log and the session/task aggregate metadata.

        :param task_id: Active task identifier receiving the token usage.
        :param response: Raw LLM response object exposing token counts when available.
        """

        input_tokens = int(getattr(response, "input_tokens", 0) or 0)
        output_tokens = int(getattr(response, "output_tokens", 0) or 0)
        if input_tokens <= 0 and output_tokens <= 0:
            return
        if self.session_manager.session_id is None:
            return
        cache_diagnostics = self._extract_cache_diagnostics(response)
        context_window_percentage = None
        if self._last_context_window_limit and self._last_context_window_limit > 0:
            context_window_percentage = round(
                (input_tokens / self._last_context_window_limit) * 100
            )
        token_entry = self.token_logger.log_api_call(
            session_id=self.session_manager.session_id,
            task_id=task_id,
            agent=PROJECT_AGENT_ID,
            model=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit=cache_diagnostics["cache_hit"],
            cached_input_tokens=cache_diagnostics["cached_input_tokens"],
            cached_output_tokens=cache_diagnostics["cached_output_tokens"],
            effective_context_snapshot_id=(self._last_effective_context_snapshot or {}).get(
                "snapshot_id"
            ),
            effective_context_turn_id=(self._last_effective_context_snapshot or {}).get("turn_id"),
            context_window_percentage=context_window_percentage,
            context_files=(self._last_effective_context_snapshot or {}).get("context_files", []),
        )
        self.session_manager.record_token_usage(
            task_id=task_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=token_entry.estimated_cost,
        )
        if self.token_logger.consume_threshold_warning():
            highest_cost_agent = self.token_logger.highest_cost_agent(
                self.session_manager.session_id
            )
            agent_name = (
                str(highest_cost_agent.get("agent"))
                if isinstance(highest_cost_agent, dict) and highest_cost_agent.get("agent")
                else PROJECT_AGENT_ID
            )
            warning = (
                "Project Agent session cost warning: the configured model-usage threshold "
                f"has been reached. Highest cost agent: {agent_name}."
            )
            cheaper_model = self.token_logger.cheaper_model_option(self.model_name)
            if cheaper_model:
                warning += f" You can switch it to a cheaper model such as {cheaper_model}."
            else:
                warning += " You can switch it to a cheaper model to reduce cost."
            await self._publish_warning(warning)

    def _extract_cache_diagnostics(self, response: Any) -> dict[str, Any]:
        """Description:
            Extract provider cache diagnostics from one raw LLM response payload.

        Requirements:
            - Return explicit cache-hit metadata when the provider reports cached prompt tokens.
            - Fall back cleanly when the provider response carries no cache details.

        :param response: Raw LLM response object exposing an optional provider payload.
        :returns: Cache-diagnostic mapping with hit and cached-token counts.
        """

        raw_response = getattr(response, "raw_response", None)
        if not isinstance(raw_response, dict):
            return {
                "cache_hit": None,
                "cached_input_tokens": None,
                "cached_output_tokens": None,
            }
        usage = raw_response.get("usage")
        if not isinstance(usage, dict):
            return {
                "cache_hit": None,
                "cached_input_tokens": None,
                "cached_output_tokens": None,
            }
        prompt_details = usage.get("prompt_tokens_details")
        cached_input_tokens = None
        if isinstance(prompt_details, dict) and isinstance(
            prompt_details.get("cached_tokens"), int
        ):
            cached_input_tokens = int(prompt_details["cached_tokens"])
        cache_hit = None if cached_input_tokens is None else cached_input_tokens > 0
        return {
            "cache_hit": cache_hit,
            "cached_input_tokens": cached_input_tokens,
            "cached_output_tokens": None,
        }

    def _record_tool_visibility(
        self,
        *,
        task_id: str,
        tool_call: Any,
        tool_result: dict[str, Any],
    ) -> None:
        """Description:
            Persist PA chat-time tool-call visibility into the audit log and session task logs.

        Requirements:
            - Record the requested tool, action, and arguments into `audit.log`.
            - Append tool-call and tool-result summaries into the active `pa-user` task log.

        :param task_id: Active task identifier receiving the visibility entries.
        :param tool_call: Parsed chat tool-call request.
        :param tool_result: Structured tool execution result.
        """

        target = json.dumps(tool_call.args, sort_keys=True)
        self.audit_logger.log_tool_operation(
            agent=PROJECT_AGENT_ID,
            tool=tool_call.tool,
            action=tool_call.action,
            target=target,
            channel="pa-user",
        )
        self.session_manager.append_channel_message(
            task_id=task_id,
            channel_name="pa-user",
            sender=PROJECT_AGENT_ID,
            recipient=PROJECT_AGENT_ID,
            msg_type="tool_call",
            summary=(
                f"Project Agent requested {tool_call.tool}.{tool_call.action} with args {target}"
            ),
            status="requested",
        )
        self.session_manager.append_channel_message(
            task_id=task_id,
            channel_name="pa-user",
            sender=PROJECT_AGENT_ID,
            recipient=PROJECT_AGENT_ID,
            msg_type="tool_result",
            summary=(
                f"Tool result for {tool_call.tool}.{tool_call.action}: "
                f"{json.dumps(tool_result, sort_keys=True)}"
            ),
            status="success" if tool_result.get("success") else "error",
        )

    def _append_history(self, role: str, content: str) -> None:
        """Description:
            Append one message to the bounded Project Agent browser-chat history.

        Requirements:
            - Retain only the most recent bounded message pairs.

        :param role: Chat role for the stored message.
        :param content: Message content to retain.
        """

        self.history.append({"role": role, "content": content})
        if len(self.history) > MAX_PROJECT_AGENT_HISTORY:
            self.history = self.history[-MAX_PROJECT_AGENT_HISTORY:]

    def _restore_saved_transcript(self) -> None:
        """Description:
            Restore the latest persisted Project Agent transcript into runtime memory.

        Requirements:
            - Reload the newest transcript from the session log on startup.
            - Keep only a bounded suffix in ``history`` for future LLM calls.
            - Resume the latest non-ended session when one exists.
        """

        self.session_manager.resume_latest_session()
        self.transcript_messages = self.session_manager.load_latest_project_agent_transcript()
        self.history = self.transcript_messages[-MAX_PROJECT_AGENT_HISTORY:]

    def update_user_settings(self, settings: UserSettingsPayload) -> None:
        """Description:
            Apply updated user settings to the live Project Agent runtime.

        Requirements:
            - Refresh the runtime timezone and user-profile providers immediately so future turns use the saved settings.
            - Leave transcript and chat history intact when only user-profile fields change.

        :param settings: Persisted user-settings payload returned by the store.
        """

        self.time_context_provider.configured_timezone = settings.timezone
        self.user_context_provider.display_name = settings.display_name
        self.user_context_provider.country_code = settings.country_code
        self.user_context_provider.preferred_locale = settings.preferred_locale

    def update_model_settings(self, settings: ModelSettingsPayload) -> None:
        """Description:
            Apply updated model settings to the live Project Agent runtime.

        Requirements:
            - Refresh the active PA model, live LLM client model, and compiler model immediately.
            - Refresh the cached context-window limit used for token diagnostics.

        :param settings: Persisted model-settings payload returned by the store.
        """

        self.model_name = settings.pa_model
        self.llm_client.model = settings.pa_model
        self.context_compiler.model_name = settings.pa_model
        self.compaction_controller.model_name = settings.pa_model
        self._last_context_window_limit = self.model_settings_store.resolve_context_window(
            settings.pa_model
        )

    def _estimate_project_instruction_tokens(self) -> int:
        """Description:
            Return the estimated token count for the raw project instruction file.

        Requirements:
            - Treat a missing `AGENTS.md` file as zero tokens.

        :returns: Estimated token count for the raw project instruction file.
        """

        project_text = self.context_compiler.read_project_instructions()
        if not project_text.strip():
            return 0
        return count_text_tokens(project_text, self.context_compiler.model_name)

    async def _ensure_active_session(self) -> None:
        """Description:
            Ensure the Project Agent transcript has an active session before writing new messages.

        Requirements:
            - Reuse the restored active session when one exists.
            - Start a new Web UI session lazily on first new message after restart or teardown.
        """

        if self.session_manager.current_session is not None:
            return
        await self.session_manager.start_session(trigger="web-ui")
        self.token_logger.reset_session_total()

    async def start_new_session(self) -> dict[str, Any]:
        """Description:
            End any current Project Agent browser-chat session and start a fresh empty one immediately.

        Requirements:
            - Persist the previous session end state before starting the next session.
            - Clear transcript and bounded chat history so the next turn starts cleanly.
            - Reset session-scoped token totals for the live Project Agent runtime.

        :returns: Browser-facing metadata for the new active session.
        """

        previous_session_id = self.session_manager.session_id
        if self.session_manager.current_session is not None:
            await self.session_manager.end_session()
        self.transcript_messages = []
        self.history = []
        self.compacted_history_summary = ""
        self._last_compaction_record = None
        await self.session_manager.start_session(trigger="web-ui")
        self.token_logger.reset_session_total()
        return {
            "session_id": self.session_manager.session_id,
            "previous_session_id": previous_session_id,
            "status": "active",
            "started_at": self.session_manager.current_session.started_at
            if self.session_manager.current_session is not None
            else datetime.now(timezone.utc).isoformat(),
            "task_count": 0,
        }

    def _ensure_active_task(self) -> Any:
        """Description:
            Ensure direct Project Agent chat work is attached to one persisted task.

        Requirements:
            - Reuse the current active task when one already exists.
            - Create a lightweight Project Agent chat task lazily on first message.

        :returns: Active task record used for PA chat logging.
        """

        active_task = self.session_manager.get_active_task()
        if active_task is not None:
            return active_task
        return self.session_manager.create_task("Project Agent chat", channels=["pa-user"])

    def _record_transcript_message(self, role: str, content: str) -> None:
        """Description:
            Persist one Project Agent transcript message and mirror it into the exported UI transcript state.

        Requirements:
            - Keep the full exported transcript separate from the bounded LLM history.
            - Persist every user and assistant message into ``pa-user.log``.

        :param role: Transcript role name.
        :param content: Transcript content to persist.
        """

        message = {"role": role, "content": content}
        self.transcript_messages.append(message)
        self.session_manager.append_project_agent_message(role, content)

    def export_transcript_messages(self) -> list[dict[str, str]]:
        """Description:
            Return the full persisted Project Agent transcript for UI rehydration.

        Requirements:
            - Preserve the transcript in chronological order.
            - Return a detached copy safe for API serialisation.

        :returns: Full Project Agent transcript message list.
        """

        return list(self.transcript_messages)

    async def _stream_assistant_reply(self, reply_text: str) -> None:
        """Description:
            Publish one assistant reply as incremental streamed output chunks.

        Requirements:
            - Prefix the first chunk with an assistant label for readability.
            - Mark streamed chunks so the browser appends them inline.

        :param reply_text: Full assistant reply text.
        """

        chunks = [
            reply_text[index : index + STREAM_CHUNK_SIZE]
            for index in range(0, len(reply_text), STREAM_CHUNK_SIZE)
        ] or [reply_text]
        for index, chunk in enumerate(chunks):
            prefix = "PA: " if index == 0 else ""
            suffix = "\n" if index == len(chunks) - 1 else ""
            await self._publish_output(
                f"{prefix}{chunk}{suffix}",
                stream=True,
            )

    async def _publish_status(self, status: str) -> None:
        """Description:
            Publish one structured Project Agent status frame.

        Requirements:
            - Always include the configured model name for header updates.

        :param status: Current visible status label.
        """

        await self._publish_frame(
            {
                "type": "status",
                "agent": PROJECT_AGENT_ID,
                "status": status,
                "model": self.model_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def _publish_output(self, text: str, *, stream: bool = False) -> None:
        """Description:
            Publish one Project Agent output frame for the browser panel.

        Requirements:
            - Mark streamed chunks explicitly so the panel appends them inline.

        :param text: Output text to publish.
        :param stream: Whether the text is a streamed chunk rather than a full line.
        """

        await self._publish_frame(
            {
                "type": "output",
                "agent": PROJECT_AGENT_ID,
                "text": text,
                "stream": stream,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def _publish_error(self, message: str) -> None:
        """Description:
            Publish one error frame and transition the Project Agent back to idle.

        Requirements:
            - Surface failures to the browser instead of failing silently.

        :param message: Human-readable error message.
        """

        await self._publish_frame(
            {
                "type": "error",
                "agent": PROJECT_AGENT_ID,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        await self._publish_status("idle")

    async def _publish_warning(self, message: str) -> None:
        """Description:
            Publish one warning frame for the Project Agent browser panel.

        Requirements:
            - Surface non-fatal runtime warnings without changing the idle/error status state.

        :param message: Human-readable warning message.
        """

        await self._publish_frame(
            {
                "type": "warning",
                "agent": PROJECT_AGENT_ID,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def _publish_compaction_state(self, *, active: bool, diagnostic: str) -> None:
        """Description:
            Publish one Project Agent hard-compaction state frame for the browser runtime.

        Requirements:
            - Let the Input panel block new sends while hard compaction is underway.
            - Clear the blocked state immediately once hard compaction completes.

        :param active: Whether hard compaction is currently active.
        :param diagnostic: Optional user-facing compaction diagnostic text.
        """

        await self._publish_frame(
            {
                "type": "compaction_state",
                "agent": PROJECT_AGENT_ID,
                "active": active,
                "diagnostic": diagnostic,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def _publish_frame(self, payload: dict[str, Any]) -> None:
        """Description:
            Publish one structured Project Agent frame to Redis.

        Requirements:
            - Serialise the payload as JSON for the browser WebSocket bridge.

        :param payload: Structured frame payload.
        """

        await self.redis.publish(self.output_channel, json.dumps(payload))


def _derive_runtime_category(labels: dict[str, str], container_type: str) -> str:
    """Description:
        Derive the UI runtime category for one observed container.

    Requirements:
        - Classify sandbox, agent, tool, runtime, and bootstrap containers consistently.

    :param labels: Container label mapping.
    :param container_type: Logical container type reported by the runtime.
    :returns: Runtime category label for UI grouping.
    """

    if container_type == "sandbox":
        return "sandbox"
    if container_type == "agent":
        return "agent"
    if container_type == "tool":
        return "tool"
    if container_type == "mcp-runtime":
        return "runtime"
    if labels.get("faith.runtime") == "mcp-runtime":
        return "runtime"
    return "runtime"


def _derive_runtime_role(name: str, labels: dict[str, str], container_type: str) -> str:
    """Description:
        Derive the human-readable role label for one observed container.

    Requirements:
        - Prefer explicit bootstrap role metadata when the container name matches.
        - Fall back to the best available label-derived description for managed containers.

    :param name: Container name.
    :param labels: Container label mapping.
    :param container_type: Logical container type reported by the runtime.
    :returns: Human-readable role label.
    """

    bootstrap = BOOTSTRAP_CONTAINER_METADATA.get(name)
    if bootstrap is not None:
        return bootstrap["role"]
    if container_type == "agent" and labels.get("faith.agent"):
        return f"Agent: {labels['faith.agent']}"
    if container_type == "tool" and labels.get("faith.tool"):
        return f"Tool: {labels['faith.tool']}"
    if container_type == "sandbox":
        return "Sandbox"
    if container_type == "mcp-runtime" or labels.get("faith.runtime") == "mcp-runtime":
        return "MCP Runtime"
    return container_type.replace("-", " ").title()


def _extract_ownership(labels: dict[str, str]) -> dict[str, str]:
    """Description:
        Extract the container ownership metadata useful to the runtime UI.

    Requirements:
        - Preserve only the high-signal FAITH ownership labels.
        - Omit empty ownership values.

    :param labels: Container label mapping.
    :returns: Ownership metadata suitable for UI display.
    """

    ownership: dict[str, str] = {}
    for label_name, output_name in (
        ("faith.agent", "agent_id"),
        ("faith.tool", "tool_name"),
        ("faith.sandbox_id", "sandbox_id"),
        ("faith.session_id", "session_id"),
        ("faith.task_id", "task_id"),
        ("faith.runtime", "runtime"),
    ):
        value = labels.get(label_name)
        if value:
            ownership[output_name] = value
    return ownership


def _extract_health(attrs: dict[str, Any]) -> str | None:
    """Description:
        Extract one Docker health value from raw inspection attributes.

    Requirements:
        - Return `None` when the container has no healthcheck state.

    :param attrs: Raw Docker inspection attributes.
    :returns: Container health string when present.
    """

    health = (attrs.get("State", {}) or {}).get("Health") or {}
    status = health.get("Status")
    return str(status) if status else None


def _runtime_summary_from_container_info(info: Any) -> RuntimeContainerSummary:
    """Description:
        Convert one runtime container object into the shared UI summary model.

    Requirements:
        - Preserve role, category, state, image, restart count, and ownership metadata.
        - Add human-usable service URLs only for known bootstrap services.

    :param info: Container-like object exposing name, labels, image, status, and restart_count.
    :returns: Shared runtime summary record.
    """

    labels = dict(getattr(info, "labels", {}) or {})
    container_type = str(getattr(info, "container_type", labels.get("faith.role", "runtime")))
    bootstrap = BOOTSTRAP_CONTAINER_METADATA.get(str(getattr(info, "name", "")), {})
    return RuntimeContainerSummary(
        name=str(getattr(info, "name", "")),
        category=bootstrap.get("category", _derive_runtime_category(labels, container_type)),
        role=_derive_runtime_role(str(getattr(info, "name", "")), labels, container_type),
        state=str(getattr(info, "status", "unknown")),
        image=str(getattr(info, "image", "")),
        health=getattr(info, "health", None),
        restart_count=int(getattr(info, "restart_count", 0) or 0),
        url=bootstrap.get("url"),
        ownership=_extract_ownership(labels),
    )


def _build_runtime_snapshot() -> DockerRuntimeSnapshot:
    """Description:
        Build the current Docker runtime snapshot for PA and Web UI consumers.

    Requirements:
        - Include bootstrap services when Docker inspection is available.
        - Include FAITH-managed agent, tool, sandbox, and runtime containers.
        - Degrade cleanly when Docker inspection is unavailable.

    :returns: Current Docker runtime snapshot payload.
    """

    if docker is None:
        return DockerRuntimeSnapshot(
            docker_available=False,
            status="unavailable",
            images=[],
            containers=[],
        )

    try:
        docker_client = docker.from_env()
    except Exception:
        return DockerRuntimeSnapshot(
            docker_available=False,
            status="unavailable",
            images=[],
            containers=[],
        )

    containers: list[RuntimeContainerSummary] = []
    seen_names: set[str] = set()

    try:
        for name, metadata in BOOTSTRAP_CONTAINER_METADATA.items():
            container = docker_client.containers.get(name)
            container.reload()
            attrs = getattr(container, "attrs", {}) or {}
            summary = RuntimeContainerSummary(
                name=container.name,
                category=metadata["category"],
                role=metadata["role"],
                state=str(getattr(container, "status", "unknown")),
                image=str((attrs.get("Config", {}) or {}).get("Image") or ""),
                health=_extract_health(attrs),
                restart_count=int(attrs.get("RestartCount", 0) or 0),
                url=metadata.get("url"),
                ownership={},
            )
            containers.append(summary)
            seen_names.add(summary.name)
    except Exception:
        pass

    try:
        manager = ContainerManager(docker_client)
        for info in manager.list_containers():
            if info.name in seen_names:
                continue
            containers.append(_runtime_summary_from_container_info(info))
            seen_names.add(info.name)
    except Exception:
        pass

    containers.sort(key=lambda item: (item.category, item.name))
    images = sorted({container.image for container in containers if container.image})
    status = "ok" if containers else "degraded"
    return DockerRuntimeSnapshot(
        docker_available=True,
        status=status,
        images=images,
        containers=containers,
    )


def _build_project_agent_model_name() -> str:
    """Description:
        Resolve the effective Project Agent model name for browser chat replies.

    Requirements:
        - Prefer the configured project system model when it is available.
        - Fall back to the environment override or stable default when the
          project config is unavailable.

    :returns: Effective Project Agent model name.
    """

    try:
        system_config = load_system_config()
    except Exception:
        return DEFAULT_PROJECT_AGENT_MODEL
    return system_config.pa.model or DEFAULT_PROJECT_AGENT_MODEL


def _build_project_agent_llm_client() -> LLMClient:
    """Description:
        Build the shared LLM client used by the lightweight browser-chat bridge.

    Requirements:
        - Reuse the configured Project Agent model name.
        - Pass through the configured fallback model and Ollama endpoint when
          project config is available.

    :returns: Configured shared LLM client for browser-chat replies.
    """

    try:
        system_config = load_system_config()
    except Exception:
        return LLMClient(model=DEFAULT_PROJECT_AGENT_MODEL)
    return LLMClient(
        model=system_config.pa.model or DEFAULT_PROJECT_AGENT_MODEL,
        fallback_model=system_config.pa.fallback_model,
        ollama_host=system_config.ollama.endpoint if system_config.ollama.enabled else None,
    )


def _build_project_agent_compaction_llm_client(*, base_llm_client: Any | None = None) -> LLMClient:
    """Description:
        Build the local-only LLM client used for Project Agent history compaction summaries.

    Requirements:
        - Always target a local Ollama model rather than a paid remote model.
        - Reuse the resolved Ollama host from the main PA client when one is available.

    :param base_llm_client: Optional existing PA LLM client whose Ollama host can be reused.
    :returns: Configured local-only compaction LLM client.
    """

    inherited_ollama_host = getattr(base_llm_client, "ollama_host", None)
    try:
        system_config = load_system_config()
    except Exception:
        return LLMClient(
            model=DEFAULT_PROJECT_AGENT_COMPACTION_MODEL,
            ollama_host=inherited_ollama_host,
        )
    return LLMClient(
        model=DEFAULT_PROJECT_AGENT_COMPACTION_MODEL,
        ollama_host=(
            system_config.ollama.endpoint if system_config.ollama.enabled else inherited_ollama_host
        ),
    )


def _build_token_logger() -> TokenLogger:
    """Description:
        Build the shared token logger used by the Project Agent browser-chat runtime.

    Requirements:
        - Use configured cost-warning thresholds when the project config is available.
        - Fall back to the default logger threshold when config is not ready yet.

    :returns: Configured token logger for PA runtime API-call accounting.
    """

    try:
        system_config = load_system_config()
    except Exception:
        logger = TokenLogger(logs_dir=logs_dir())
        logger.load_pricing_catalog(data_dir=data_dir())
        return logger
    logger = TokenLogger(
        logs_dir=logs_dir(),
        cost_threshold_usd=system_config.cost_warning.threshold_usd,
    )
    logger.load_pricing_catalog(data_dir=data_dir())
    return logger


def _build_event_log_writer() -> EventLogWriter:
    """Description:
        Build the shared event log writer used by the PA runtime.

    Requirements:
        - Write to the canonical host-backed logs directory.

    :returns: Configured event log writer for system event persistence.
    """

    return EventLogWriter(logs_dir=logs_dir())


def _build_audit_logger() -> AuditLogger:
    """Description:
        Build the shared audit logger used by the PA runtime.

    Requirements:
        - Write PA browser-chat tool visibility entries into the canonical host-backed logs directory.

    :returns: Configured audit logger for PA runtime audit entries.
    """

    return AuditLogger(logs_dir=logs_dir())


def _build_log_rotator() -> LogRotator:
    """Description:
        Build the shared log rotator used by the PA runtime.

    Requirements:
        - Honour configured retention thresholds when the project config is available.
        - Fall back to default thresholds when config loading fails.

    :returns: Configured log rotator for startup retention checks.
    """

    try:
        system_config = load_system_config()
    except Exception:
        return LogRotator(logs_dir=logs_dir(), session_root=_project_agent_session_root())
    return LogRotator.from_system_config(
        logs_dir=logs_dir(),
        session_root=_project_agent_session_root(),
        system_config=system_config.model_dump(mode="python"),
    )


async def _build_status(app: FastAPI) -> ServiceStatus:
    """Description:
        Build the current runtime status snapshot.

    Requirements:
        - Include Redis and config state in every response.
        - Include the current Docker runtime snapshot for UI consumers.

    :param app: FastAPI application containing shared runtime state.
    :returns: Shared PA service status payload.
    """

    redis_client = getattr(app.state, "redis", None)
    redis_connected = await check_connection(redis_client)
    runtime_builder = getattr(app.state, "runtime_snapshot_builder", _build_runtime_snapshot)
    runtime = runtime_builder()
    status = "ok" if redis_connected else "degraded"
    return ServiceStatus(
        service="faith-project-agent",
        version=__version__,
        status=status,
        redis=RedisStatus(url=get_redis_url(), connected=redis_connected),
        config=build_config_summary(),
        runtime=runtime,
    )


def _build_route_manifest() -> ServiceRouteManifest:
    """Description:
        Build the structured route manifest exposed by the PA service.

    Requirements:
        - Describe all currently supported public PA endpoints.
        - Keep the manifest machine-readable so CLI clients do not hard-code PA routes.

    :returns: Route manifest payload for the PA service.
    """

    return ServiceRouteManifest(
        service="faith-project-agent",
        version=__version__,
        routes=[
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/health",
                summary="Return PA liveness and dependency health.",
                expected_status_codes=[200, 503],
                implementation=describe_route_implementation(health),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/status",
                summary="Return the current PA runtime status snapshot.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_status),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/docker-runtime",
                summary="Return the current PA Docker runtime snapshot.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_docker_runtime),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/config",
                summary="Return the redacted PA config summary.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_config),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="POST",
                path="/api/events/test",
                summary="Publish a test event into the PA system-events channel.",
                expected_status_codes=[200, 503],
                implementation=describe_route_implementation(publish_test_event),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/pa/system-prompt",
                summary="Return the active Project Agent system prompt and metadata.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_get_project_agent_system_prompt),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/pa/transcript",
                summary="Return the latest persisted Project Agent transcript for Web UI rehydration.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_get_project_agent_transcript),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="POST",
                path="/api/pa/session/new",
                summary="End the current Project Agent browser-chat session and start a fresh empty one.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_start_project_agent_session),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/user-settings",
                summary="Return persisted user settings for the Web UI settings panel.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_get_user_settings),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/model-settings",
                summary="Return persisted model settings and model catalog metadata for the Web UI model-settings panel.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_get_model_settings),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="PUT",
                path="/api/pa/system-prompt",
                summary="Validate and persist an edited Project Agent system prompt.",
                expected_status_codes=[200, 400],
                implementation=describe_route_implementation(
                    api_update_project_agent_system_prompt
                ),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="PUT",
                path="/api/user-settings",
                summary="Validate and persist user settings and refresh the live Project Agent runtime.",
                expected_status_codes=[200, 400],
                implementation=describe_route_implementation(api_update_user_settings),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="PUT",
                path="/api/model-settings",
                summary="Validate and persist model settings and refresh the live Project Agent runtime.",
                expected_status_codes=[200, 400],
                implementation=describe_route_implementation(api_update_model_settings),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="POST",
                path="/api/pa/system-prompt/reset",
                summary="Reset the Project Agent system prompt to the built-in default.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_reset_project_agent_system_prompt),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="http",
                method="GET",
                path="/api/routes",
                summary="Return the structured PA route manifest for CLI discovery.",
                expected_status_codes=[200],
                implementation=describe_route_implementation(api_routes),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="websocket",
                path="/ws/status",
                summary="Stream PA status snapshots over WebSocket.",
                implementation=describe_route_implementation(websocket_status),
            ),
            RouteManifestEntry(
                service="faith-project-agent",
                protocol="websocket",
                path="/ws/docker",
                summary="Stream PA Docker runtime snapshots over WebSocket.",
                implementation=describe_route_implementation(websocket_docker),
            ),
        ],
    )


def _require_redis(app: FastAPI):
    """Return the shared Redis client or raise a service-unavailable error.

    :param app: FastAPI application holding shared runtime state.
    :raises HTTPException: If Redis is not available.
    :returns: Shared Redis client.
    """
    redis_client = getattr(app.state, "redis", None)
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis not available")
    return redis_client


def _get_project_agent_prompt_store(app: FastAPI) -> ProjectAgentPromptStore:
    """Description:
        Return the shared Project Agent prompt store for the PA application.

    Requirements:
        - Reuse the lifespan-created store when present.
        - Lazily create a store for tests or direct module usage.

    :param app: FastAPI application holding shared runtime state.
    :returns: Shared Project Agent prompt store.
    """

    prompt_store = getattr(app.state, "project_agent_prompt_store", None)
    if prompt_store is None:
        prompt_store = ProjectAgentPromptStore()
        app.state.project_agent_prompt_store = prompt_store
    return prompt_store


def _get_project_agent_session_manager(app: FastAPI) -> SessionManager:
    """Description:
        Return the shared Project Agent session manager.

    Requirements:
        - Reuse the lifespan-created session manager when present.
        - Lazily create a manager for tests or direct module usage.

    :param app: FastAPI application holding shared runtime state.
    :returns: Shared Project Agent session manager.
    """

    session_manager = getattr(app.state, "project_agent_session_manager", None)
    if session_manager is None:
        session_manager = SessionManager(project_root=_project_agent_session_root())
        app.state.project_agent_session_manager = session_manager
    return session_manager


def _get_user_settings_store(app: FastAPI) -> UserSettingsStore:
    """Description:
        Return the shared user-settings store for the PA application.

    Requirements:
        - Reuse the lifespan-created store when present.
        - Lazily create a store for tests or direct module usage.

    :param app: FastAPI application holding shared runtime state.
    :returns: Shared user-settings store.
    """

    desired_root = Path(os.environ.get("FAITH_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()
    runtime_root = _project_agent_session_root()
    if (
        runtime_root == PROJECT_ROOT
        and "FAITH_DATA_DIR" not in os.environ
        and "FAITH_PA_SESSION_ROOT" not in os.environ
    ):
        desired_path = desired_root / ".faith" / "system.yaml"
    else:
        desired_path = runtime_root / "user-settings" / "system.yaml"
    settings_store = getattr(app.state, "user_settings_store", None)
    if (
        settings_store is None
        or settings_store.project_root != desired_root
        or settings_store.system_path != desired_path
    ):
        settings_store = UserSettingsStore(project_root=desired_root)
        app.state.user_settings_store = settings_store
    return settings_store


def _get_model_settings_store(app: FastAPI) -> ModelSettingsStore:
    """Description:
        Return the shared model-settings store for the PA application.

    Requirements:
        - Reuse the lifespan-created store when present.
        - Lazily create a store for tests or direct module usage.

    :param app: FastAPI application holding shared runtime state.
    :returns: Shared model-settings store.
    """

    desired_root = Path(os.environ.get("FAITH_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()
    settings_store = getattr(app.state, "model_settings_store", None)
    if settings_store is None or settings_store.project_root != desired_root:
        settings_store = ModelSettingsStore(project_root=desired_root)
        app.state.model_settings_store = settings_store
    return settings_store


def _apply_updated_user_settings(app: FastAPI, settings: UserSettingsPayload) -> None:
    """Description:
        Apply persisted user-settings changes to any live PA browser-chat runtime.

    Requirements:
        - Refresh the running Project Agent time-context provider immediately when present.
        - Remain a no-op when the browser-chat runtime is not active.

    :param app: FastAPI application holding shared runtime state.
    :param settings: Persisted user-settings payload returned by the store.
    """

    chat_runtime = getattr(app.state, "project_agent_chat_runtime", None)
    if chat_runtime is not None:
        chat_runtime.update_user_settings(settings)


def _apply_updated_model_settings(app: FastAPI, settings: ModelSettingsPayload) -> None:
    """Description:
        Apply persisted model-settings changes to any live PA browser-chat runtime.

    Requirements:
        - Refresh the running Project Agent model and effective-context compiler immediately when present.
        - Remain a no-op when the browser-chat runtime is not active.

    :param app: FastAPI application holding shared runtime state.
    :param settings: Persisted model-settings payload returned by the store.
    """

    chat_runtime = getattr(app.state, "project_agent_chat_runtime", None)
    if chat_runtime is not None:
        chat_runtime.update_model_settings(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Description:
        Create shared resources for the PA API lifespan.

    Requirements:
        - Open the shared Redis client for API and browser-chat runtime use.
        - Start the lightweight Project Agent browser-chat bridge when Redis is
          available.
        - Stop the bridge and close Redis cleanly during shutdown.

    :param app: FastAPI application being started.
    :yields: Control back to FastAPI once startup has completed.
    """

    app.state.project_agent_prompt_store = ProjectAgentPromptStore()
    app.state.project_agent_session_manager = SessionManager(
        project_root=_project_agent_session_root()
    )
    app.state.user_settings_store = UserSettingsStore(
        project_root=Path(os.environ.get("FAITH_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()
    )
    app.state.model_settings_store = ModelSettingsStore(
        project_root=Path(os.environ.get("FAITH_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()
    )
    app.state.project_agent_token_logger = _build_token_logger()
    app.state.audit_logger = _build_audit_logger()
    app.state.event_log_writer = _build_event_log_writer()
    app.state.log_rotator = _build_log_rotator()
    app.state.redis = await get_async_client()
    chat_runtime = None
    event_log_task = None
    if app.state.redis is not None:
        llm_client = _build_project_agent_llm_client()
        app.state.chat_llm_client = llm_client
        chat_runtime = ProjectAgentChatRuntime(
            redis_client=app.state.redis,
            llm_client=llm_client,
            model_name=_build_project_agent_model_name(),
            prompt_store=app.state.project_agent_prompt_store,
            session_manager=app.state.project_agent_session_manager,
            user_settings_store=app.state.user_settings_store,
            model_settings_store=app.state.model_settings_store,
            token_logger=app.state.project_agent_token_logger,
            audit_logger=app.state.audit_logger,
        )
        app.state.project_agent_chat_runtime = chat_runtime
        await chat_runtime.start()
        for _ in range(10):
            if chat_runtime._pubsub is not None:
                break
            await asyncio.sleep(0)
        event_log_task = asyncio.create_task(
            app.state.event_log_writer.run(app.state.redis),
            name="faith-event-log-writer",
        )
        rotation_summary = app.state.log_rotator.rotate_all()
        if rotation_summary["archive_size_threshold_exceeded"]:
            await chat_runtime._publish_warning(
                "FAITH log archive warning: retained logs exceed the configured archive-size threshold."
            )
    yield
    if event_log_task is not None:
        await app.state.event_log_writer.stop()
        await event_log_task
    if chat_runtime is not None:
        await chat_runtime.stop()
    redis_client = getattr(app.state, "redis", None)
    if redis_client is not None:
        await redis_client.aclose()


app = FastAPI(
    title="FAITH Project Agent",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness and dependency status."""

    status = await _build_status(app)
    code = 200 if status.status == "ok" else 503
    return JSONResponse(status.model_dump(mode="json"), status_code=code)


@app.get("/api/status", response_model=ServiceStatus)
async def api_status() -> ServiceStatus:
    """Return current PA runtime status."""

    return await _build_status(app)


@app.get("/api/docker-runtime", response_model=DockerRuntimeSnapshot)
async def api_docker_runtime() -> DockerRuntimeSnapshot:
    """Description:
        Return the current PA Docker runtime snapshot.

    Requirements:
        - Reuse the application runtime snapshot builder so tests can override it safely.

    :returns: Current Docker runtime snapshot payload.
    """

    runtime_builder = getattr(app.state, "runtime_snapshot_builder", _build_runtime_snapshot)
    return runtime_builder()


@app.get("/api/config", response_model=ConfigSummary)
async def api_config() -> ConfigSummary:
    """Return the redacted config summary."""

    return build_config_summary()


@app.get("/api/pa/system-prompt")
async def api_get_project_agent_system_prompt() -> dict[str, Any]:
    """Description:
        Return the active Project Agent system prompt and editor metadata.

    Requirements:
        - Load the prompt from the approved prompt store.
        - Remain available without depending on Redis health.

    :returns: Active prompt metadata payload.
    """

    return _get_project_agent_prompt_store(app).read()


@app.get("/api/pa/transcript")
async def api_get_project_agent_transcript() -> dict[str, Any]:
    """Description:
        Return the latest persisted Project Agent transcript for browser rehydration.

    Requirements:
        - Remain available even when the live Redis chat runtime is unavailable.
        - Return the newest persisted transcript and its session identifier.

    :returns: Transcript payload for the Web UI Project Agent panel.
    """

    session_manager = _get_project_agent_session_manager(app)
    chat_runtime = getattr(app.state, "project_agent_chat_runtime", None)
    if chat_runtime is not None:
        messages = chat_runtime.export_transcript_messages()
    else:
        messages = session_manager.load_latest_project_agent_transcript()
    return {
        "session_id": session_manager.latest_project_agent_session_id(),
        "messages": messages,
    }


@app.post("/api/pa/session/new", response_model=SessionStartPayload)
async def api_start_project_agent_session() -> SessionStartPayload:
    """Description:
        End the current Project Agent browser-chat session and start a fresh one immediately.

    Requirements:
        - Work whether or not the Redis-backed browser-chat runtime is currently active.
        - Clear the live transcript snapshot so the browser can start from a blank session.

    :returns: Metadata for the newly started Project Agent session.
    """

    chat_runtime = getattr(app.state, "project_agent_chat_runtime", None)
    if chat_runtime is not None:
        return SessionStartPayload.model_validate(await chat_runtime.start_new_session())

    session_manager = _get_project_agent_session_manager(app)
    previous_session_id = session_manager.session_id
    if session_manager.current_session is not None:
        await session_manager.end_session()
    session = await session_manager.start_session(trigger="web-ui")
    return SessionStartPayload(
        session_id=session.session_id,
        previous_session_id=previous_session_id,
        status="active",
        started_at=session.started_at,
        task_count=0,
    )


@app.get("/api/user-settings", response_model=UserSettingsPayload)
async def api_get_user_settings() -> UserSettingsPayload:
    """Description:
        Return the persisted user settings used by the browser settings panel.

    Requirements:
        - Load the settings from the shared project-backed store.
        - Remain available without depending on Redis health.

    :returns: Persisted user-settings payload.
    """

    return _get_user_settings_store(app).read()


@app.get("/api/model-settings", response_model=ModelSettingsPayload)
async def api_get_model_settings() -> ModelSettingsPayload:
    """Description:
        Return the persisted model settings used by the browser model-settings panel.

    Requirements:
        - Load the settings from the shared host-backed model-settings store.
        - Merge optional OpenRouter and local runtime diagnostics when available.

    :returns: Persisted model-settings payload.
    """

    chat_runtime = getattr(app.state, "project_agent_chat_runtime", None)
    llm_client = getattr(chat_runtime, "llm_client", None)
    openrouter_payload = await _fetch_openrouter_models_payload(
        os.environ.get("OPENROUTER_API_KEY")
    )
    return _get_model_settings_store(app).read(
        openrouter_payload=openrouter_payload,
        llm_client=llm_client,
    )


@app.put("/api/pa/system-prompt")
async def api_update_project_agent_system_prompt(
    body: ProjectAgentPromptUpdate,
) -> dict[str, Any]:
    """Description:
        Validate and persist an edited Project Agent system prompt.

    Requirements:
        - Reject invalid edits with a plain-English HTTP 400 error.
        - Persist accepted edits for future Project Agent model calls.

    :param body: User-submitted prompt update payload.
    :raises HTTPException: If validation fails.
    :returns: Updated active prompt metadata payload.
    """

    try:
        return _get_project_agent_prompt_store(app).update(body.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/user-settings", response_model=UserSettingsPayload)
async def api_update_user_settings(body: UserSettingsUpdate) -> UserSettingsPayload:
    """Description:
        Validate and persist one user-settings update.

    Requirements:
        - Reject invalid timezone identifiers with a plain-English HTTP 400 error.
        - Refresh the live Project Agent runtime immediately after accepted updates.

    :param body: User-submitted settings update payload.
    :raises HTTPException: If validation fails.
    :returns: Updated persisted user-settings payload.
    """

    try:
        payload = _get_user_settings_store(app).update(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _apply_updated_user_settings(app, payload)
    return payload


@app.put("/api/model-settings", response_model=ModelSettingsPayload)
async def api_update_model_settings(body: ModelSettingsUpdate) -> ModelSettingsPayload:
    """Description:
        Validate and persist one model-settings update.

    Requirements:
        - Persist PA/default-agent model changes and per-agent overrides.
        - Refresh the live Project Agent runtime immediately after accepted updates.

    :param body: User-submitted model-settings update payload.
    :returns: Updated persisted model-settings payload.
    """

    try:
        payload = _get_model_settings_store(app).update(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _apply_updated_model_settings(app, payload)
    return payload


@app.post("/api/pa/system-prompt/reset")
async def api_reset_project_agent_system_prompt() -> dict[str, Any]:
    """Description:
        Reset the active Project Agent system prompt to the built-in default.

    Requirements:
        - Remove the persisted custom prompt when present.
        - Return the active default prompt metadata after reset.

    :returns: Default active prompt metadata payload.
    """

    return _get_project_agent_prompt_store(app).reset()


@app.post("/api/events/test")
async def publish_test_event() -> dict[str, str]:
    """Publish a simple test event to Redis for the POC."""

    payload = {
        "event": "poc:test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    redis_client = _require_redis(app)
    await redis_client.publish(SYSTEM_EVENTS_CHANNEL, str(payload))
    return payload


@app.get("/api/routes", response_model=ServiceRouteManifest)
async def api_routes() -> ServiceRouteManifest:
    """Description:
        Return the machine-readable PA route manifest.

    Requirements:
        - Expose a discovery contract for CLI tooling instead of requiring hard-coded route knowledge.
        - Remain available without depending on Redis health.

    :returns: Structured manifest for PA HTTP and WebSocket routes.
    """

    return _build_route_manifest()


@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket) -> None:
    """Push a status snapshot to connected clients."""

    await websocket.accept()
    try:
        while True:
            status = await _build_status(app)
            await websocket.send_json(status.model_dump(mode="json"))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return


@app.websocket("/ws/docker")
async def websocket_docker(websocket: WebSocket) -> None:
    """Description:
        Push Docker runtime snapshots to connected clients.

    Requirements:
        - Stream the current runtime snapshot repeatedly without requiring Redis.

    :param websocket: Connected browser WebSocket.
    """

    await websocket.accept()
    try:
        while True:
            runtime_builder = getattr(
                app.state, "runtime_snapshot_builder", _build_runtime_snapshot
            )
            snapshot = DockerRuntimeSnapshot.model_validate(runtime_builder())
            await websocket.send_json(snapshot.model_dump(mode="json"))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
