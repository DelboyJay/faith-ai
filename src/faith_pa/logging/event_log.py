"""Description:
    Persist FAITH system events as newline-delimited JSON.

Requirements:
    - Subscribe to the canonical system event channel.
    - Write every received event to `events.log` with immediate flush.
    - Expose stable read and query helpers for Web UI log views.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from faith_shared.protocol.events import SYSTEM_EVENTS_CHANNEL, FaithEvent


def _now_iso() -> str:
    """Description:
        Return the current UTC time as an ISO-8601 string.

    Requirements:
        - Always emit a trailing `Z` suffix for UTC timestamps.

    :returns: Current UTC timestamp string.
    """

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventLogEntry(BaseModel):
    """Description:
        Represent one persisted FAITH event log entry.

    Requirements:
        - Preserve timestamp, event type, source, optional channel, and payload data.
    """

    ts: str = Field(default_factory=_now_iso)
    event: str
    source: str
    channel: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    def to_json_line(self) -> str:
        """Description:
            Serialise the event entry as one JSON-lines record.

        Requirements:
            - Exclude `None` values from the payload.

        :returns: JSON-lines representation of the event entry.
        """

        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_json_line(cls, payload: str) -> EventLogEntry:
        """Description:
            Restore one event entry from JSON-lines text.

        Requirements:
            - Strip surrounding whitespace before validation.

        :param payload: JSON-lines payload to parse.
        :returns: Parsed event entry.
        """

        return cls.model_validate_json(payload.strip())

    @classmethod
    def from_faith_event(cls, event: FaithEvent) -> EventLogEntry:
        """Description:
            Convert one canonical FAITH event into a persisted log entry.

        Requirements:
            - Preserve the canonical event name and channel when present.

        :param event: Parsed FAITH event payload.
        :returns: Event log entry ready for persistence.
        """

        return cls(
            ts=event.ts,
            event=event.event.value,
            source=event.source,
            channel=event.channel,
            data=dict(event.data or {}),
        )


class EventLogWriter:
    """Description:
        Subscribe to `system-events` and persist every received event.

    Requirements:
        - Create the target logs directory eagerly.
        - Manage its own pubsub lifecycle.
        - Support direct write, read, and query helpers for the Web UI.

    :param logs_dir: Directory containing `events.log`.
    """

    def __init__(self, *, logs_dir: Path) -> None:
        """Description:
            Initialise the event log writer.

        Requirements:
            - Create the target logs directory eagerly.

        :param logs_dir: Directory containing `events.log`.
        """

        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.logs_dir / "events.log"
        self._running = False
        self._pubsub: Any | None = None

    def write(self, entry: EventLogEntry) -> None:
        """Description:
            Append one event entry to the active event log.

        Requirements:
            - Flush the file immediately after writing.

        :param entry: Event entry to persist.
        """

        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_json_line() + "\n")
            handle.flush()

    async def handle_event(self, event: FaithEvent) -> None:
        """Description:
            Persist one parsed FAITH event.

        Requirements:
            - Convert the incoming event into the event log entry format before writing.

        :param event: Parsed FAITH event payload.
        """

        self.write(EventLogEntry.from_faith_event(event))

    async def run(self, redis_client: Any) -> None:
        """Description:
            Subscribe to the shared system event channel and persist incoming events.

        Requirements:
            - Subscribe to `SYSTEM_EVENTS_CHANNEL`.
            - Ignore non-message pubsub frames.
            - Decode byte payloads before parsing.
            - Unsubscribe and close the pubsub cleanly on shutdown.

        :param redis_client: Async Redis client exposing `pubsub()`.
        """

        self._pubsub = redis_client.pubsub()
        await self._pubsub.subscribe(SYSTEM_EVENTS_CHANNEL)
        self._running = True
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
                await self.handle_event(FaithEvent.from_json(str(raw)))
        except asyncio.CancelledError:
            raise
        finally:
            await self._close_pubsub()

    async def stop(self) -> None:
        """Description:
            Request shutdown of the running event subscriber loop.

        Requirements:
            - Leave pubsub cleanup to the `run()` finaliser.
        """

        self._running = False

    async def _close_pubsub(self) -> None:
        """Description:
            Unsubscribe and close the active pubsub object when present.

        Requirements:
            - Support both `aclose()` and `close()` shutdown APIs.
        """

        if self._pubsub is None:
            return
        try:
            await self._pubsub.unsubscribe(SYSTEM_EVENTS_CHANNEL)
        finally:
            close = getattr(self._pubsub, "aclose", None)
            if callable(close):
                await close()
            else:
                await self._pubsub.close()
        self._pubsub = None

    def read_entries(self, *, limit: int = 100, offset: int = 0) -> list[EventLogEntry]:
        """Description:
            Read event log entries from disk in file order.

        Requirements:
            - Skip malformed JSON-lines records without raising.
            - Honour the requested offset and limit.

        :param limit: Maximum number of entries to return.
        :param offset: Number of valid entries to skip first.
        :returns: Parsed event log entries.
        """

        if not self.log_path.exists():
            return []
        entries: list[EventLogEntry] = []
        valid_index = 0
        for raw_line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = EventLogEntry.from_json_line(line)
            except Exception:
                continue
            if valid_index < offset:
                valid_index += 1
                continue
            if len(entries) >= limit:
                break
            entries.append(entry)
            valid_index += 1
        return entries

    def query(
        self,
        *,
        event: str | None = None,
        source: str | None = None,
        channel: str | None = None,
        limit: int = 100,
    ) -> list[EventLogEntry]:
        """Description:
            Return event log entries filtered in reverse chronological order.

        Requirements:
            - Filter by event name, source, and channel when provided.
            - Skip malformed JSON-lines records without raising.

        :param event: Optional event-type filter.
        :param source: Optional source filter.
        :param channel: Optional channel filter.
        :param limit: Maximum number of entries to return.
        :returns: Matching entries ordered newest first.
        """

        if not self.log_path.exists():
            return []
        matched: list[EventLogEntry] = []
        for raw_line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = EventLogEntry.from_json_line(line)
            except Exception:
                continue
            if event is not None and entry.event != event:
                continue
            if source is not None and entry.source != source:
                continue
            if channel is not None and entry.channel != channel:
                continue
            matched.append(entry)
        matched.reverse()
        return matched[:limit]
