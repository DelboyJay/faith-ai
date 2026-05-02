"""
Description:
    Build per-turn runtime time context for FAITH system prompts.

Requirements:
    - Resolve the current local date and time in the configured user timezone.
    - Provide a stable prompt block reused by the PA and specialist agents.
    - Fall back safely to UTC when no valid configured timezone is available.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class RuntimeTimeContext:
    """Description:
        Represent the resolved per-turn local time context for one model call.

    Requirements:
        - Preserve the local date, local time, and explicit timezone identifier.
        - Preserve whether timezone resolution fell back to a safe default.

    :param local_date: Local calendar date resolved for the current turn.
    :param local_time: Local wall-clock time resolved for the current turn.
    :param timezone_name: Explicit timezone identifier used for resolution.
    :param used_fallback: Whether timezone resolution fell back to UTC.
    """

    local_date: str
    local_time: str
    timezone_name: str
    used_fallback: bool = False

    def to_prompt_block(self) -> str:
        """Description:
            Render the runtime time context as a system-prompt block.

        Requirements:
            - Keep the block concise and deterministic for every agent turn.
            - Preserve the explicit timezone identifier in the rendered text.

        :returns: Runtime time-context block ready for prompt assembly.
        """

        return (
            "[Runtime Time Context]\n"
            f"Current local date: {self.local_date}\n"
            f"Current local time: {self.local_time}\n"
            f"User timezone: {self.timezone_name}"
        )


class RuntimeTimeContextProvider:
    """Description:
        Resolve and render runtime-managed local time context for FAITH prompts.

    Requirements:
        - Prefer an explicitly configured timezone when one is available.
        - Fall back to environment timezone hints and then UTC.
        - Recompute the time block for every call so values refresh between turns.

    :param configured_timezone: Explicit user-configured timezone identifier.
    :param now_provider: Optional callable returning the current UTC datetime.
    """

    def __init__(
        self,
        *,
        configured_timezone: str | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """Description:
            Initialise the runtime time-context provider.

        Requirements:
            - Preserve the configured timezone and optional clock override.

        :param configured_timezone: Explicit user-configured timezone identifier.
        :param now_provider: Optional callable returning the current UTC datetime.
        """

        self.configured_timezone = configured_timezone
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def build_context(self) -> RuntimeTimeContext:
        """Description:
            Resolve the current local time context for one agent turn.

        Requirements:
            - Convert the current UTC instant into the resolved local timezone.
            - Return UTC with a fallback marker when timezone resolution fails.

        :returns: Resolved runtime time-context payload.
        """

        timezone_name, used_fallback = self._resolve_timezone_name()
        zone = self._resolve_zoneinfo(timezone_name)
        current_utc = self._normalise_utc_datetime(self.now_provider())
        local_now = current_utc.astimezone(zone)
        return RuntimeTimeContext(
            local_date=local_now.strftime("%Y-%m-%d"),
            local_time=local_now.strftime("%H:%M:%S"),
            timezone_name=timezone_name,
            used_fallback=used_fallback,
        )

    def build_prompt_block(self) -> str:
        """Description:
            Return the current runtime time context as prompt text.

        Requirements:
            - Recompute the block on every call so time values stay fresh.

        :returns: Runtime time-context prompt block.
        """

        return self.build_context().to_prompt_block()

    def _resolve_timezone_name(self) -> tuple[str, bool]:
        """Description:
            Resolve the preferred timezone identifier for prompt assembly.

        Requirements:
            - Prefer explicit config.
            - Fall back to `FAITH_TIMEZONE`, then `TZ`, then UTC.
            - Treat invalid timezone identifiers as UTC fallback.

        :returns: Tuple of resolved timezone name and fallback-used flag.
        """

        candidates = [
            self.configured_timezone,
            os.environ.get("FAITH_TIMEZONE"),
            os.environ.get("TZ"),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            if self._is_valid_timezone(candidate):
                return candidate, False
        return "UTC", True

    @staticmethod
    def _normalise_utc_datetime(value: datetime) -> datetime:
        """Description:
            Normalise one datetime value into a timezone-aware UTC instant.

        Requirements:
            - Accept naive datetimes by interpreting them as UTC for tests.
            - Convert timezone-aware datetimes into UTC.

        :param value: Datetime returned by the provider clock.
        :returns: Timezone-aware UTC datetime.
        """

        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _is_valid_timezone(name: str) -> bool:
        """Description:
            Return whether one timezone identifier is supported.

        Requirements:
            - Use the standard-library ZoneInfo registry for validation.

        :param name: Candidate timezone identifier.
        :returns: ``True`` when the timezone identifier is supported.
        """

        if name.upper() == "UTC":
            return True
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError:
            return False
        return True

    @staticmethod
    def _resolve_zoneinfo(name: str) -> tzinfo:
        """Description:
            Resolve one timezone identifier into a ZoneInfo object.

        Requirements:
            - Fall back to UTC if the supplied identifier is unexpectedly invalid.

        :param name: Timezone identifier to resolve.
        :returns: Resolved ZoneInfo instance.
        """

        if name.upper() == "UTC":
            return timezone.utc
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            return timezone.utc
