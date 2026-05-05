"""Description:
    Provide read-only FAITH log and session-history endpoints for the Web UI.

Requirements:
    - Read runtime logs without exposing browser-side write paths.
    - Return reverse-chronological results for time-ordered log views.
    - Skip malformed JSON-lines records without raising HTTP 500 responses.
    - Expose session-history browsing from persisted runtime state.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter()

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class PaginatedLogResponse(BaseModel):
    """Description:
        Represent one paginated log-view response payload.

    Requirements:
        - Preserve the filtered items together with total-count and paging metadata.
        - Allow optional summary payloads for views that expose aggregate information.
    """

    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    summary: dict[str, Any] | None = None


class SessionDetailResponse(BaseModel):
    """Description:
        Represent one detailed session-history response payload.

    Requirements:
        - Return session metadata, task metadata, and the persisted Project Agent transcript together.
    """

    session: dict[str, Any]
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    transcript: list[dict[str, str]] = Field(default_factory=list)


class ChannelLogResponse(BaseModel):
    """Description:
        Represent one read-only channel-log response payload.

    Requirements:
        - Preserve the owning session, task, channel, and full rendered markdown content.
    """

    session_id: str
    task_id: str
    channel: str
    content: str


def _get_logs_dir(request: Request) -> Path:
    """Description:
        Resolve the active Web UI logs directory.

    Requirements:
        - Use the application state configured at startup or by tests.

    :param request: Incoming FastAPI request object.
    :returns: Active read-only logs directory path.
    """

    return Path(request.app.state.logs_dir)


def _get_session_root(request: Request) -> Path:
    """Description:
        Resolve the active persisted Project Agent session root.

    Requirements:
        - Use the application state configured at startup or by tests.

    :param request: Incoming FastAPI request object.
    :returns: Active persisted Project Agent session-root path.
    """

    return Path(request.app.state.pa_session_root)


def _sessions_dir(request: Request) -> Path:
    """Description:
        Resolve the persisted sessions directory from the active session root.

    Requirements:
        - Mirror the PA session-manager path layout under `<root>/.faith/sessions`.

    :param request: Incoming FastAPI request object.
    :returns: Persisted sessions directory path.
    """

    return _get_session_root(request) / ".faith" / "sessions"


def _parse_timestamp(raw_value: object) -> datetime:
    """Description:
        Parse one timestamp-like value into a sortable UTC datetime.

    Requirements:
        - Accept ISO-8601 strings ending in `Z`.
        - Fall back to the Unix epoch when parsing fails so callers still sort deterministically.

    :param raw_value: Timestamp-like value from one log record.
    :returns: Parsed UTC datetime suitable for reverse sorting.
    """

    if not raw_value:
        return datetime.fromtimestamp(0, tz=UTC)
    try:
        return datetime.fromisoformat(str(raw_value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.fromtimestamp(0, tz=UTC)


def _safe_leaf_name(value: str, *, label: str) -> str:
    """Description:
        Validate one session or channel name used in a filesystem lookup.

    Requirements:
        - Reject path separators and traversal segments.
        - Return the original value unchanged when it is safe.

    :param value: User-supplied session or channel identifier.
    :param label: Human-readable field label for validation errors.
    :raises HTTPException: If the supplied name is not a safe leaf name.
    :returns: Original validated value.
    """

    if not value or "/" in value or "\\" in value or value in {".", ".."} or ".." in value:
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return value


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    """Description:
        Read one JSON-lines log file into structured records.

    Requirements:
        - Return an empty list when the file is missing.
        - Skip blank or malformed lines without raising.

    :param path: JSON-lines file path to read.
    :returns: Parsed JSON-object records.
    """

    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _search_records(records: list[dict[str, Any]], search: str | None) -> list[dict[str, Any]]:
    """Description:
        Apply one full-record substring search to structured log records.

    Requirements:
        - Search the compact JSON representation of each record case-insensitively.

    :param records: Candidate log records.
    :param search: Optional search string from the client.
    :returns: Filtered log records.
    """

    if not search:
        return records
    needle = search.casefold()
    return [
        record
        for record in records
        if needle in json.dumps(record, sort_keys=True, default=str).casefold()
    ]


def _paginate_records(
    records: list[dict[str, Any]],
    *,
    page: int,
    page_size: int,
    summary: dict[str, Any] | None = None,
) -> PaginatedLogResponse:
    """Description:
        Slice one filtered record list into the standard paginated response model.

    Requirements:
        - Clamp page-size handling to the caller-validated values.
        - Preserve the original total before slicing.

    :param records: Ordered records ready for paging.
    :param page: 1-based page number.
    :param page_size: Maximum number of items per page.
    :param summary: Optional aggregate payload for the response.
    :returns: Paginated response payload.
    """

    total = len(records)
    start = max(page - 1, 0) * page_size
    end = start + page_size
    return PaginatedLogResponse(
        items=records[start:end],
        total=total,
        page=page,
        page_size=page_size,
        summary=summary,
    )


def _sort_descending(
    records: list[dict[str, Any]], *, timestamp_key: str = "ts"
) -> list[dict[str, Any]]:
    """Description:
        Sort records so the newest timestamp appears first.

    Requirements:
        - Leave records with invalid timestamps at the end of the result.

    :param records: Candidate records to order.
    :param timestamp_key: Preferred timestamp field name.
    :returns: Reverse-chronological records.
    """

    return sorted(
        records,
        key=lambda record: _parse_timestamp(
            record.get(timestamp_key)
            or record.get("started_at")
            or record.get("updated_at")
            or record.get("ended_at")
        ),
        reverse=True,
    )


def _load_session_transcript(session_dir: Path) -> list[dict[str, str]]:
    """Description:
        Load the persisted Project Agent transcript for one session directory.

    Requirements:
        - Parse the markdown transcript into ordered role/content entries.
        - Return an empty list when no transcript file exists.

    :param session_dir: Persisted session directory path.
    :returns: Parsed transcript entries for the session.
    """

    transcript_path = session_dir / "pa-user.log"
    if not transcript_path.exists():
        return []
    lines = transcript_path.read_text(encoding="utf-8").splitlines()
    entries: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == "## User":
            role = "user"
        elif line == "## Assistant":
            role = "assistant"
        else:
            index += 1
            continue
        if index + 2 >= len(lines) or lines[index + 1] != "~~~text":
            index += 1
            continue
        index += 2
        content_lines: list[str] = []
        while index < len(lines) and lines[index] != "~~~":
            content_lines.append(lines[index])
            index += 1
        entries.append({"role": role, "content": "\n".join(content_lines).strip()})
        index += 1
    return entries


def _list_session_summaries(request: Request) -> list[dict[str, Any]]:
    """Description:
        Build summary records for all persisted sessions.

    Requirements:
        - Return reverse-chronological sessions by `started_at`.
        - Skip malformed or incomplete metadata files safely.

    :param request: Incoming FastAPI request object.
    :returns: Session summary records.
    """

    summaries: list[dict[str, Any]] = []
    sessions_dir = _sessions_dir(request)
    if not sessions_dir.exists():
        return summaries
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue
        meta_path = session_dir / "session.meta.json"
        if not meta_path.exists():
            continue
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        payload["task_count"] = len(payload.get("tasks", {}))
        payload["session_dir"] = session_dir.name
        summaries.append(payload)
    return _sort_descending(summaries, timestamp_key="started_at")


@router.get("/api/logs/audit", response_model=PaginatedLogResponse)
async def audit_trail(
    request: Request,
    agent: str | None = None,
    tool: str | None = None,
    action: str | None = None,
    decision: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> PaginatedLogResponse:
    """Description:
        Return read-only audit-log entries for the Web UI audit-trail panel.

    Requirements:
        - Filter by agent, tool, action, decision, and free-text search when supplied.
        - Return newest audit entries first.

    :param request: Incoming FastAPI request object.
    :param agent: Optional agent filter.
    :param tool: Optional tool filter.
    :param action: Optional action filter.
    :param decision: Optional decision filter.
    :param search: Optional case-insensitive free-text search.
    :param page: 1-based page number.
    :param page_size: Maximum number of items per page.
    :returns: Paginated audit-log response payload.
    """

    records = _read_json_lines(_get_logs_dir(request) / "audit.log")
    filtered = [
        record
        for record in records
        if (agent is None or record.get("agent") == agent)
        and (tool is None or record.get("tool") == tool)
        and (action is None or record.get("action") == action)
        and (decision is None or record.get("decision") == decision)
    ]
    filtered = _search_records(filtered, search)
    return _paginate_records(_sort_descending(filtered), page=page, page_size=page_size)


@router.get("/api/logs/events", response_model=PaginatedLogResponse)
async def event_timeline(
    request: Request,
    event: str | None = None,
    source: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> PaginatedLogResponse:
    """Description:
        Return read-only event-log entries for the Web UI event-timeline panel.

    Requirements:
        - Filter by event name, source, and free-text search when supplied.
        - Return newest events first.

    :param request: Incoming FastAPI request object.
    :param event: Optional event-name filter.
    :param source: Optional event-source filter.
    :param search: Optional case-insensitive free-text search.
    :param page: 1-based page number.
    :param page_size: Maximum number of items per page.
    :returns: Paginated event-log response payload.
    """

    records = _read_json_lines(_get_logs_dir(request) / "events.log")
    filtered = [
        record
        for record in records
        if (event is None or record.get("event") == event)
        and (source is None or record.get("source") == source)
    ]
    filtered = _search_records(filtered, search)
    return _paginate_records(_sort_descending(filtered), page=page, page_size=page_size)


@router.get("/api/logs/tokens", response_model=PaginatedLogResponse)
async def token_usage(
    request: Request,
    agent: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> PaginatedLogResponse:
    """Description:
        Return read-only token-log entries for the Web UI token-usage panel.

    Requirements:
        - Filter by agent, model, session, and free-text search when supplied.
        - Return newest entries first and include simple aggregate summaries by model and agent.

    :param request: Incoming FastAPI request object.
    :param agent: Optional agent filter.
    :param model: Optional model filter.
    :param session_id: Optional session identifier filter.
    :param search: Optional case-insensitive free-text search.
    :param page: 1-based page number.
    :param page_size: Maximum number of items per page.
    :returns: Paginated token-log response payload.
    """

    records = _read_json_lines(_get_logs_dir(request) / "tokens.log")
    filtered = [
        record
        for record in records
        if (agent is None or record.get("agent") == agent)
        and (model is None or record.get("model") == model)
        and (session_id is None or record.get("session_id") == session_id)
    ]
    filtered = _search_records(filtered, search)
    ordered = _sort_descending(filtered)

    by_model: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}
    for record in ordered:
        model_key = str(record.get("model", "unknown"))
        agent_key = str(record.get("agent", "unknown"))
        by_model.setdefault(model_key, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        by_agent.setdefault(agent_key, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        for bucket in (by_model[model_key], by_agent[agent_key]):
            bucket["calls"] += 1
            bucket["input_tokens"] += int(record.get("input_tokens", 0) or 0)
            bucket["output_tokens"] += int(record.get("output_tokens", 0) or 0)

    return _paginate_records(
        ordered,
        page=page,
        page_size=page_size,
        summary={"by_model": by_model, "by_agent": by_agent},
    )


@router.get("/api/logs/approvals", response_model=PaginatedLogResponse)
async def approval_history(
    request: Request,
    agent: str | None = None,
    tool: str | None = None,
    decision: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> PaginatedLogResponse:
    """Description:
        Return approval-related audit-log entries for the Web UI approval-history panel.

    Requirements:
        - Include only records that carry approval context.
        - Filter by agent, tool, decision, and free-text search when supplied.
        - Return newest approval decisions first.

    :param request: Incoming FastAPI request object.
    :param agent: Optional agent filter.
    :param tool: Optional tool filter.
    :param decision: Optional decision filter.
    :param search: Optional case-insensitive free-text search.
    :param page: 1-based page number.
    :param page_size: Maximum number of items per page.
    :returns: Paginated approval-history response payload.
    """

    records = _read_json_lines(_get_logs_dir(request) / "audit.log")
    approval_records = [
        record
        for record in records
        if record.get("approval_tier") is not None or record.get("rule_matched") is not None
    ]
    filtered = [
        record
        for record in approval_records
        if (agent is None or record.get("agent") == agent)
        and (tool is None or record.get("tool") == tool)
        and (decision is None or record.get("decision") == decision)
    ]
    filtered = _search_records(filtered, search)
    return _paginate_records(_sort_descending(filtered), page=page, page_size=page_size)


@router.get("/api/logs/sessions", response_model=PaginatedLogResponse)
async def session_history(
    request: Request,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> PaginatedLogResponse:
    """Description:
        Return session-history summaries for the Web UI session-history panel.

    Requirements:
        - Return persisted sessions in reverse chronological order by start time.
        - Support free-text search across session metadata.

    :param request: Incoming FastAPI request object.
    :param search: Optional case-insensitive free-text search.
    :param page: 1-based page number.
    :param page_size: Maximum number of items per page.
    :returns: Paginated session-summary response payload.
    """

    summaries = _search_records(_list_session_summaries(request), search)
    return _paginate_records(summaries, page=page, page_size=page_size)


@router.get("/api/logs/sessions/{session_id}", response_model=SessionDetailResponse)
async def session_detail(request: Request, session_id: str) -> SessionDetailResponse:
    """Description:
        Return detailed metadata and transcript content for one persisted session.

    Requirements:
        - Reject invalid session names before touching the filesystem.
        - Return HTTP 404 when the requested session does not exist.
        - Sort task details newest-first by task start time.

    :param request: Incoming FastAPI request object.
    :param session_id: Persisted session identifier.
    :raises HTTPException: If the session identifier is invalid or not found.
    :returns: Detailed session-history payload.
    """

    safe_session_id = _safe_leaf_name(session_id, label="session identifier")
    session_dir = _sessions_dir(request) / safe_session_id
    meta_path = session_dir / "session.meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        session_payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid session metadata: {exc}") from exc

    tasks: list[dict[str, Any]] = []
    tasks_dir = session_dir / "tasks"
    if tasks_dir.exists():
        for task_dir in tasks_dir.iterdir():
            if not task_dir.is_dir():
                continue
            task_meta_path = task_dir / "task.meta.json"
            if not task_meta_path.exists():
                continue
            try:
                task_payload = json.loads(task_meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(task_payload, dict):
                tasks.append(task_payload)

    return SessionDetailResponse(
        session=session_payload,
        tasks=_sort_descending(tasks, timestamp_key="started_at"),
        transcript=_load_session_transcript(session_dir),
    )


@router.get(
    "/api/logs/sessions/{session_id}/channels/{channel_name}", response_model=ChannelLogResponse
)
async def session_channel_log(
    request: Request,
    session_id: str,
    channel_name: str,
) -> ChannelLogResponse:
    """Description:
        Return the persisted markdown content for one session task channel log.

    Requirements:
        - Reject invalid session and channel names before touching the filesystem.
        - Search across all task directories inside the requested session.
        - Return HTTP 404 when the channel log does not exist.

    :param request: Incoming FastAPI request object.
    :param session_id: Persisted session identifier.
    :param channel_name: Channel log filename such as `ch-foo.log`.
    :raises HTTPException: If the session or channel cannot be resolved safely.
    :returns: Read-only channel-log payload.
    """

    safe_session_id = _safe_leaf_name(session_id, label="session identifier")
    safe_channel_name = _safe_leaf_name(channel_name, label="channel name")
    session_dir = _sessions_dir(request) / safe_session_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    tasks_dir = session_dir / "tasks"
    for task_dir in tasks_dir.iterdir() if tasks_dir.exists() else []:
        if not task_dir.is_dir():
            continue
        candidate = task_dir / safe_channel_name
        if candidate.exists():
            return ChannelLogResponse(
                session_id=safe_session_id,
                task_id=task_dir.name,
                channel=safe_channel_name,
                content=candidate.read_text(encoding="utf-8"),
            )
    raise HTTPException(status_code=404, detail="Channel log not found")
