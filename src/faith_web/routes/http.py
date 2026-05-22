"""Description:
    Provide HTTP routes for the FAITH Web UI backend.

Requirements:
    - Accept browser input and uploads for forwarding into the PA runtime.
    - Validate request payloads and fail cleanly when Redis is unavailable.
"""

from __future__ import annotations

import base64
import inspect
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from httpx import AsyncClient, HTTPStatusError, RequestError
from pydantic import BaseModel, Field

from faith_pa.utils.redis_client import USER_INPUT_CHANNEL
from faith_web.app import (
    APPROVAL_RESPONSES_CHANNEL,
    DEFAULT_PA_URL,
    get_static_asset_version,
    templates,
)
from faith_web.version import __version__

router = APIRouter()

ACCEPTED_UPLOAD_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/markdown",
    "text/plain",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DICTATION_BYTES = 25 * 1024 * 1024


class UserInputRequest(BaseModel):
    """Description:
        Validate one user text-input submission from the browser.

    Requirements:
        - Require a non-empty message.
        - Allow the browser to attach an optional session identifier.
    """

    message: str = Field(min_length=1)
    session_id: str | None = None
    scope: str | None = None


class UserInputResponse(BaseModel):
    """Description:
        Return the acknowledgment payload for one accepted user text submission.

    Requirements:
        - Report the delivery status, generated message ID, and target channel.
    """

    status: str
    message_id: str
    channel: str


class ApprovalDecisionRequest(BaseModel):
    """Description:
        Validate one approval decision submitted from the browser.

    Requirements:
        - Restrict decisions to the supported approval vocabulary.
        - Allow optional scope, reason, and pattern override metadata.
    """

    decision: Literal[
        "allow_once",
        "approve_session",
        "always_allow",
        "always_ask",
        "deny_once",
        "deny_permanently",
    ]
    scope: str | None = None
    reason: str | None = None
    pattern_override: str | None = None


class ApprovalDecisionResponse(BaseModel):
    """Description:
        Return the acknowledgment payload for one accepted approval decision.

    Requirements:
        - Report the delivery status, request ID, and recorded decision.
    """

    status: str
    request_id: str
    decision: str


class UploadResponse(BaseModel):
    """Description:
        Return the acknowledgment payload for one accepted file upload.

    Requirements:
        - Report the delivery status, generated message ID, filename, MIME type, and target channel.
    """

    status: str
    message_id: str
    filename: str
    content_type: str
    channel: str


class ProjectAgentPromptUpdate(BaseModel):
    """Description:
        Validate a Project Agent prompt update submitted through the Web UI proxy.

    Requirements:
        - Preserve the edited prompt text before forwarding it to the PA service.
    """

    prompt: str


class UserSettingsUpdate(BaseModel):
    """Description:
        Validate one user-settings update submitted through the Web UI proxy.

    Requirements:
        - Preserve the display name, country, preferred locale, and timezone values before forwarding.
    """

    display_name: str | None = None
    country_code: str | None = None
    preferred_locale: str | None = None
    timezone: str | None = None


class ModelSettingsUpdate(BaseModel):
    """Description:
        Validate one model-settings update submitted through the Web UI proxy.

    Requirements:
        - Preserve PA/default-agent model changes, per-agent overrides, and context-window overrides before forwarding.
    """

    pa_model: str
    default_agent_model: str
    agent_overrides: dict[str, str | None] = Field(default_factory=dict)
    context_window_overrides: dict[str, int | None] = Field(default_factory=dict)


def _utc_now() -> str:
    """Description:
        Return the current UTC timestamp in ISO 8601 format.

    Requirements:
        - Emit timezone-aware timestamps for browser-originated events.

    :returns: Current UTC timestamp as an ISO 8601 string.
    """

    return datetime.now(timezone.utc).isoformat()


def _dictation_temp_suffix(content_type: str | None, filename: str | None) -> str:
    """Description:
        Return a safe file suffix for one recorded dictation payload.

    Requirements:
        - Preserve a useful extension when the browser supplies one.
        - Fall back to a web-audio friendly suffix when the MIME type is unknown.

    :param content_type: Declared MIME type for the uploaded audio.
    :param filename: Original browser filename for the uploaded audio.
    :returns: File suffix suitable for a temporary audio file.
    """

    if filename:
        # Keep the browser-supplied suffix if one exists.
        suffix = Path(filename).suffix
        if suffix:
            return suffix
    mime_suffixes = {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/webm": ".webm",
    }
    return mime_suffixes.get(content_type or "", ".webm")


def _invoke_local_dictation_engine(
    audio_bytes: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
    language: str | None = None,
) -> str:
    """Description:
        Run one recorded audio payload through the configured local speech engine.

    Requirements:
        - Prefer local/free transcription engines when available.
        - Fail cleanly when no local engine is installed instead of pretending to use a hosted API.

    :param audio_bytes: Raw browser-recorded audio bytes.
    :param content_type: Browser-declared MIME type.
    :param filename: Browser-declared filename.
    :param language: Optional language hint forwarded by the browser.
    :raises HTTPException: If no local transcription engine is available or transcription fails.
    :returns: Plain-text transcript.
    """

    model_name = os.getenv("FAITH_DICTATION_MODEL", "tiny")
    suffix = _dictation_temp_suffix(content_type, filename)
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name

        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            whisper_kwargs = {"language": language} if language else {}
            segments, _info = model.transcribe(temp_path, **whisper_kwargs)
            transcript = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            )
            if transcript.strip():
                return transcript.strip()
        except ImportError:
            pass
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Local dictation engine failed: {exc}",
            ) from exc

        try:
            import whisper  # type: ignore[import-not-found]

            model = whisper.load_model(model_name)
            whisper_kwargs = {"language": language} if language else {}
            result = model.transcribe(temp_path, **whisper_kwargs)
            transcript = str(result.get("text", "")).strip()
            if transcript:
                return transcript
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail="Local speech transcription is not configured.",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Local dictation engine failed: {exc}",
            ) from exc
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    raise HTTPException(
        status_code=503,
        detail="Local speech transcription returned no transcript.",
    )


async def _run_dictation_transcriber(
    request: Request,
    *,
    audio_bytes: bytes,
    content_type: str | None,
    filename: str | None,
    language: str | None,
) -> str:
    """Description:
        Invoke the active dictation transcriber for one browser audio payload.

    Requirements:
        - Let tests install a lightweight local transcriber on app state.
        - Fall back to the repository's optional local transcription engines when no override exists.

    :param request: Incoming FastAPI request object.
    :param audio_bytes: Raw browser-recorded audio bytes.
    :param content_type: Browser-declared MIME type.
    :param filename: Browser-declared filename.
    :param language: Optional language hint forwarded by the browser.
    :returns: Plain-text transcript.
    """

    transcriber = getattr(request.app.state, "dictation_transcriber", None)
    if not callable(transcriber):
        transcriber = _invoke_local_dictation_engine

    result = transcriber(
        audio_bytes,
        content_type=content_type,
        filename=filename,
        language=language,
    )
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, dict):
        transcript = str(result.get("transcript") or result.get("text") or "").strip()
    else:
        transcript = str(result or "").strip()
    if not transcript:
        raise HTTPException(
            status_code=503, detail="Local speech transcription returned no transcript."
        )
    return transcript


def _require_redis():
    """Description:
        Return the shared Web UI Redis client or raise a service-unavailable error.

    Requirements:
        - Fail with HTTP 503 when Redis is not configured.
        - Resolve the shared client lazily so tests can replace it.

    :raises HTTPException: If Redis is unavailable.
    :returns: Shared Redis client.
    """

    import faith_web.app as web_app_module

    redis = web_app_module.redis_pool
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis not available")
    return redis


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Description:
        Serve the main FAITH Web UI page.

    Requirements:
        - Render the ``index.html`` template with the current request object.

    :param request: Incoming FastAPI request object.
    :returns: Rendered HTML response for the main Web UI page.
    """

    return templates.TemplateResponse(
        request,
        "index.html",
        {"asset_version": get_static_asset_version(), "ui_version": __version__},
    )


@router.post("/input", response_model=UserInputResponse)
async def submit_input(body: UserInputRequest) -> UserInputResponse:
    """Description:
        Accept one browser text input payload and publish it to the PA input channel.

    Requirements:
        - Generate a unique message identifier for each accepted submission.
        - Publish a JSON payload into the shared PA input channel.

    :param body: Validated user input payload from the browser.
    :returns: Acknowledgment response describing the queued message.
    """

    redis = _require_redis()
    message_id = str(uuid.uuid4())
    payload = {
        "type": "user_input",
        "message_id": message_id,
        "message": body.message,
        "session_id": body.session_id,
        "timestamp": _utc_now(),
    }
    await redis.publish(USER_INPUT_CHANNEL, json.dumps(payload))
    return UserInputResponse(status="sent", message_id=message_id, channel=USER_INPUT_CHANNEL)


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    message: str = Form(default=""),
    session_id: str | None = Form(default=None),
    scope: str = Form(default="session"),
) -> UploadResponse:
    """Description:
        Accept one browser file upload and publish it to the PA input channel.

    Requirements:
        - Reject unsupported MIME types with HTTP 415.
        - Reject oversized uploads with HTTP 413.
        - Base64-encode accepted file contents before publishing.

    :param file: Uploaded browser file.
    :param message: Optional user message accompanying the upload.
    :param session_id: Optional session identifier supplied by the browser.
    :raises HTTPException: If the upload type or size is invalid.
    :returns: Acknowledgment response describing the queued upload.
    """

    redis = _require_redis()
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ACCEPTED_UPLOAD_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {content_type}")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    message_id = str(uuid.uuid4())
    payload = {
        "type": "user_upload",
        "message_id": message_id,
        "filename": file.filename or "upload.bin",
        "content_type": content_type,
        "size_bytes": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
        "message": message,
        "session_id": session_id,
        "scope": scope,
        "timestamp": _utc_now(),
    }
    await redis.publish(USER_INPUT_CHANNEL, json.dumps(payload))
    return UploadResponse(
        status="sent",
        message_id=message_id,
        filename=payload["filename"],
        content_type=content_type,
        channel=USER_INPUT_CHANNEL,
    )


@router.post("/api/dictation/transcribe")
async def transcribe_dictation(
    request: Request,
    audio: UploadFile = File(...),
    language: str | None = Form(default=None),
) -> dict[str, object]:
    """Description:
        Accept one browser-recorded audio payload and return a local transcript.

    Requirements:
        - Keep dictation on the same-origin Web UI route.
        - Use a local/free transcription engine when one is available.
        - Fail cleanly when no local transcription path is configured.

    :param request: Incoming FastAPI request object.
    :param audio: Uploaded browser audio blob.
    :param language: Optional browser-provided language hint.
    :raises HTTPException: If the audio is invalid or no local transcription engine is available.
    :returns: Transcript payload for the Input panel.
    """

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="No audio provided")
    if len(audio_bytes) > MAX_DICTATION_BYTES:
        raise HTTPException(status_code=413, detail="Dictation audio too large")

    transcript = await _run_dictation_transcriber(
        request,
        audio_bytes=audio_bytes,
        content_type=audio.content_type,
        filename=audio.filename,
        language=language,
    )
    return {
        "status": "transcribed",
        "transcript": transcript,
        "engine": "local",
    }


async def _proxy_pa_prompt_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Description:
        Forward one Web UI prompt request to the PA service.

    Requirements:
        - Keep the browser on same-origin Web UI routes.
        - Preserve useful upstream validation errors for the prompt editor panel.

    :param method: HTTP method to use for the upstream PA request.
    :param path: PA API path to call.
    :param json_body: Optional JSON body to forward.
    :raises HTTPException: If the PA service is unreachable or rejects the request.
    :returns: Decoded PA JSON payload.
    """

    try:
        async with AsyncClient(base_url=DEFAULT_PA_URL, timeout=10.0) as client:
            response = await client.request(method, path, json=json_body)
            response.raise_for_status()
            return dict(response.json())
    except HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", exc.response.text)
        except ValueError:
            detail = exc.response.text
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Project Agent service is unavailable: {exc}",
        ) from exc


async def _proxy_pa_request(
    request: Request,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Description:
        Forward one Web UI request to the PA service with test override support.

    Requirements:
        - Use an app-state override when tests need to intercept proxied PA requests.
        - Fall back to the normal PA proxy implementation during production use.

    :param request: Incoming Web UI request object.
    :param method: HTTP method to use for the upstream PA request.
    :param path: PA API path to call.
    :param json_body: Optional JSON body to forward.
    :returns: Decoded PA JSON payload.
    """

    proxy = getattr(request.app.state, "pa_prompt_request_proxy", None)
    if callable(proxy):
        return dict(await proxy(method, path, json_body=json_body))
    return await _proxy_pa_prompt_request(method, path, json_body=json_body)


async def _proxy_pa_storage_upload(
    file: UploadFile,
    *,
    scope: str,
    description: str,
    session_bindings: str,
) -> dict[str, object]:
    """Description:
        Forward one browser storage upload to the PA service.

    Requirements:
        - Preserve the multipart file payload, requested scope, and session bindings.

    :param file: Uploaded browser file.
    :param scope: Requested storage scope.
    :param description: Optional user-facing description.
    :param session_bindings: JSON-encoded session binding list.
    :returns: Decoded PA JSON payload.
    """

    file_bytes = await file.read()
    try:
        async with AsyncClient(base_url=DEFAULT_PA_URL, timeout=30.0) as client:
            response = await client.post(
                "/api/storage/files",
                files={
                    "file": (
                        file.filename or "upload.bin",
                        file_bytes,
                        file.content_type or "application/octet-stream",
                    )
                },
                data={
                    "scope": scope,
                    "description": description,
                    "session_bindings": session_bindings,
                },
            )
            response.raise_for_status()
            return dict(response.json())
    except HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", exc.response.text)
        except ValueError:
            detail = exc.response.text
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Project Agent service is unavailable: {exc}",
        ) from exc


def _decorate_storage_payload(payload: dict[str, object]) -> dict[str, object]:
    """Description:
        Add UI-friendly action URLs and binding labels to one storage inventory payload.

    Requirements:
        - Preserve the original PA payload while exposing same-origin action routes.

    :param payload: Raw proxied PA storage payload.
    :returns: Decorated browser-facing storage payload.
    """

    items = payload.get("items", [])
    if not isinstance(items, list):
        return payload
    decorated_items: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("file_id", ""))
        bindings = item.get("session_bindings", [])
        binding_label = (
            "global" if item.get("scope") == "global" else ", ".join(bindings or []) or "—"
        )
        decorated = dict(item)
        decorated.setdefault("sha256", decorated.get("file_id", ""))
        decorated["binding"] = binding_label
        if str(item.get("trashed_at") or ""):
            decorated["actions"] = {
                "restore": f"/api/storage/trash/{file_id}/restore",
                "delete": f"/api/storage/trash/{file_id}",
            }
        else:
            decorated["actions"] = {
                "scope": f"/api/storage/files/{file_id}",
                "delete": f"/api/storage/files/{file_id}",
            }
        decorated_items.append(decorated)
    return {"items": decorated_items}


@router.get("/api/pa/system-prompt")
async def get_project_agent_system_prompt(request: Request) -> dict[str, object]:
    """Description:
        Return the active Project Agent prompt through the Web UI same-origin API.

    Requirements:
        - Proxy the request to the PA service prompt endpoint.

    :returns: Active prompt metadata payload.
    """

    return await _proxy_pa_request(request, "GET", "/api/pa/system-prompt")


@router.get("/api/pa/transcript")
async def get_project_agent_transcript(request: Request) -> dict[str, object]:
    """Description:
        Return the latest persisted Project Agent transcript through the Web UI same-origin API.

    Requirements:
        - Proxy the request to the PA transcript endpoint.
        - Preserve the transcript message list for browser rehydration.

    :param request: Incoming FastAPI request object.
    :returns: Transcript payload for the Project Agent panel.
    """

    return await _proxy_pa_request(request, "GET", "/api/pa/transcript")


@router.post("/api/pa/session/new")
async def start_project_agent_session(request: Request) -> dict[str, object]:
    """Description:
        Start a fresh Project Agent session through the Web UI same-origin API.

    Requirements:
        - Proxy the request to the PA new-session endpoint.
        - Preserve the PA session metadata payload for the Session History panel.

    :param request: Incoming FastAPI request object.
    :returns: New Project Agent session metadata payload.
    """

    return await _proxy_pa_request(request, "POST", "/api/pa/session/new")


@router.get("/api/storage/files")
async def get_storage_inventory(request: Request) -> dict[str, object]:
    """Description:
        Return the browser-facing storage inventory through a same-origin proxy route.

    Requirements:
        - Preserve the PA storage inventory while exposing same-origin action URLs.

    :param request: Incoming Web UI request object.
    :returns: Decorated storage inventory payload.
    """

    payload = await _proxy_pa_request(request, "GET", "/api/storage/files")
    return _decorate_storage_payload(payload)


@router.get("/api/storage/trash")
async def get_storage_trash(request: Request) -> dict[str, object]:
    """Description:
        Return the browser-facing trashed-file inventory through a same-origin proxy route.

    Requirements:
        - Preserve the PA trash inventory while exposing same-origin action URLs.

    :param request: Incoming Web UI request object.
    :returns: Decorated trash inventory payload.
    """

    payload = await _proxy_pa_request(request, "GET", "/api/storage/trash")
    return _decorate_storage_payload(payload)


@router.post("/api/storage/files")
async def upload_storage_file(
    file: UploadFile = File(...),
    scope: str = Form(default="global"),
    description: str = Form(default=""),
    session_bindings: str = Form(default="[]"),
) -> dict[str, object]:
    """Description:
        Persist one file in the browser-facing storage inventory through a same-origin proxy route.

    Requirements:
        - Preserve the uploaded file bytes, requested scope, and session bindings.

    :param file: Uploaded browser file.
    :param scope: Requested storage scope.
    :param description: Optional user-facing description.
    :param session_bindings: JSON-encoded session binding list.
    :returns: Stored-file payload.
    """

    return await _proxy_pa_storage_upload(
        file,
        scope=scope,
        description=description,
        session_bindings=session_bindings,
    )


@router.put("/api/storage/files/{file_id}")
async def update_storage_file(
    request: Request,
    file_id: str,
    body: dict[str, object],
) -> dict[str, object]:
    """Description:
        Update one stored-file metadata record through a same-origin proxy route.

    Requirements:
        - Preserve the caller-supplied storage metadata payload.

    :param request: Incoming Web UI request object.
    :param file_id: Canonical stored-file identifier.
    :param body: Replacement metadata payload.
    :returns: Updated stored-file payload.
    """

    return await _proxy_pa_request(request, "PUT", f"/api/storage/files/{file_id}", json_body=body)


@router.post("/api/storage/files/bulk-delete")
async def bulk_delete_storage_files(request: Request, body: dict[str, object]) -> dict[str, object]:
    """Description:
        Move multiple stored files into trash through a same-origin proxy route.

    Requirements:
        - Preserve the selected file identifier list.

    :param request: Incoming Web UI request object.
    :param body: Bulk-selection payload.
    :returns: Updated trash inventory payload.
    """

    return await _proxy_pa_request(
        request, "POST", "/api/storage/files/bulk-delete", json_body=body
    )


@router.post("/api/storage/files/bulk-export")
async def bulk_export_storage_files(request: Request, body: dict[str, object]) -> dict[str, object]:
    """Description:
        Export selected stored files through a same-origin proxy route.

    Requirements:
        - Preserve the selected file identifier list for the PA export helper.

    :param request: Incoming Web UI request object.
    :param body: Bulk-selection payload.
    :returns: Export archive metadata payload.
    """

    return await _proxy_pa_request(
        request, "POST", "/api/storage/files/bulk-export", json_body=body
    )


@router.delete("/api/storage/files/{file_id}")
async def delete_storage_file(request: Request, file_id: str) -> dict[str, object]:
    """Description:
        Trash one stored file through a same-origin proxy route.

    Requirements:
        - Preserve the canonical file identifier path parameter.

    :param request: Incoming Web UI request object.
    :param file_id: Canonical stored-file identifier.
    :returns: Trashed stored-file payload.
    """

    return await _proxy_pa_request(request, "DELETE", f"/api/storage/files/{file_id}")


@router.post("/api/storage/trash/{file_id}/restore")
async def restore_storage_file(request: Request, file_id: str) -> dict[str, object]:
    """Description:
        Restore one trashed file through a same-origin proxy route.

    Requirements:
        - Preserve the canonical file identifier path parameter.

    :param request: Incoming Web UI request object.
    :param file_id: Canonical stored-file identifier.
    :returns: Restored stored-file payload.
    """

    return await _proxy_pa_request(request, "POST", f"/api/storage/trash/{file_id}/restore")


@router.post("/api/storage/trash/bulk-restore")
async def bulk_restore_storage_files(
    request: Request, body: dict[str, object]
) -> dict[str, object]:
    """Description:
        Restore multiple trashed files through a same-origin proxy route.

    Requirements:
        - Preserve the selected file identifier list.

    :param request: Incoming Web UI request object.
    :param body: Bulk-selection payload.
    :returns: Updated active storage inventory payload.
    """

    return await _proxy_pa_request(
        request, "POST", "/api/storage/trash/bulk-restore", json_body=body
    )


@router.delete("/api/storage/trash/{file_id}")
async def hard_delete_storage_file(request: Request, file_id: str) -> dict[str, object]:
    """Description:
        Permanently delete one stored file through a same-origin proxy route.

    Requirements:
        - Preserve the canonical file identifier path parameter.

    :param request: Incoming Web UI request object.
    :param file_id: Canonical stored-file identifier.
    :returns: Deleted stored-file payload.
    """

    return await _proxy_pa_request(request, "DELETE", f"/api/storage/trash/{file_id}")


@router.delete("/api/storage/trash/bulk-delete")
async def bulk_hard_delete_storage_files(
    request: Request, body: dict[str, object]
) -> dict[str, object]:
    """Description:
        Permanently delete multiple trashed files through a same-origin proxy route.

    Requirements:
        - Preserve the selected file identifier list.

    :param request: Incoming Web UI request object.
    :param body: Bulk-selection payload.
    :returns: Updated trashed-file inventory payload.
    """

    return await _proxy_pa_request(
        request, "DELETE", "/api/storage/trash/bulk-delete", json_body=body
    )


@router.post("/api/logs/sessions/{session_id}/rename")
async def rename_session(
    request: Request, session_id: str, body: dict[str, object]
) -> dict[str, object]:
    """Description:
        Rename one persisted session through a same-origin proxy route.

    Requirements:
        - Preserve the submitted replacement session name.

    :param request: Incoming Web UI request object.
    :param session_id: Persisted session identifier.
    :param body: Replacement session-name payload.
    :returns: Updated session metadata payload.
    """

    return await _proxy_pa_request(
        request, "POST", f"/api/pa/sessions/{session_id}/rename", json_body=body
    )


@router.post("/api/logs/sessions/{session_id}/archive")
async def archive_session(request: Request, session_id: str) -> dict[str, object]:
    """Description:
        Archive one persisted session through a same-origin proxy route.

    Requirements:
        - Preserve the persisted session identifier path parameter.

    :param request: Incoming Web UI request object.
    :param session_id: Persisted session identifier.
    :returns: Updated session metadata payload.
    """

    return await _proxy_pa_request(request, "POST", f"/api/pa/sessions/{session_id}/archive")


@router.post("/api/logs/sessions/{session_id}/activate")
async def activate_session(request: Request, session_id: str) -> dict[str, object]:
    """Description:
        Activate one persisted session through a same-origin proxy route.

    Requirements:
        - Preserve the persisted session identifier path parameter.
        - Return the activated transcript payload for immediate panel rehydration.

    :param request: Incoming Web UI request object.
    :param session_id: Persisted session identifier.
    :returns: Activated session payload.
    """

    return await _proxy_pa_request(request, "POST", f"/api/pa/sessions/{session_id}/activate")


@router.post("/api/logs/sessions/{session_id}/unarchive")
async def unarchive_session(request: Request, session_id: str) -> dict[str, object]:
    """Description:
        Restore one archived session through a same-origin proxy route.

    Requirements:
        - Preserve the persisted session identifier path parameter.

    :param request: Incoming Web UI request object.
    :param session_id: Persisted session identifier.
    :returns: Updated session metadata payload.
    """

    return await _proxy_pa_request(request, "POST", f"/api/pa/sessions/{session_id}/unarchive")


@router.delete("/api/logs/sessions/{session_id}")
async def delete_session(request: Request, session_id: str) -> dict[str, object]:
    """Description:
        Delete one persisted session through a same-origin proxy route.

    Requirements:
        - Preserve the persisted session identifier path parameter.

    :param request: Incoming Web UI request object.
    :param session_id: Persisted session identifier.
    :returns: Session-deletion acknowledgment payload.
    """

    return await _proxy_pa_request(request, "DELETE", f"/api/pa/sessions/{session_id}")


@router.post("/api/logs/sessions/{session_id}/export")
async def export_session(
    request: Request, session_id: str, body: dict[str, object]
) -> dict[str, object]:
    """Description:
        Export one persisted session through a same-origin proxy route.

    Requirements:
        - Translate browser export-scope labels into the PA export contract.

    :param request: Incoming Web UI request object.
    :param session_id: Persisted session identifier.
    :param body: Browser export request payload.
    :returns: Session-export payload.
    """

    export_scope = str(body.get("export_scope", "session-only"))
    mode = "session_with_linked_files" if export_scope == "session+linked" else "session_only"
    return await _proxy_pa_request(
        request,
        "POST",
        f"/api/pa/sessions/{session_id}/export",
        json_body={"mode": mode},
    )


@router.get("/api/user-settings")
async def get_user_settings(request: Request) -> dict[str, object]:
    """Description:
        Return persisted user settings through the Web UI same-origin API.

    Requirements:
        - Proxy the request to the PA user-settings endpoint.

    :param request: Incoming Web UI request object.
    :returns: Persisted user-settings payload.
    """

    return await _proxy_pa_request(request, "GET", "/api/user-settings")


@router.get("/api/model-settings")
async def get_model_settings(request: Request) -> dict[str, object]:
    """Description:
        Return persisted model settings through the Web UI same-origin API.

    Requirements:
        - Proxy the request to the PA model-settings endpoint.

    :param request: Incoming Web UI request object.
    :returns: Persisted model-settings payload.
    """

    return await _proxy_pa_request(request, "GET", "/api/model-settings")


@router.put("/api/pa/system-prompt")
async def update_project_agent_system_prompt(
    request: Request,
    body: ProjectAgentPromptUpdate,
) -> dict[str, object]:
    """Description:
        Forward an edited Project Agent prompt to the PA service.

    Requirements:
        - Preserve PA-side validation and persistence behaviour.

    :param body: User-submitted prompt update payload.
    :returns: Updated active prompt metadata payload.
    """

    return await _proxy_pa_request(
        request,
        "PUT",
        "/api/pa/system-prompt",
        json_body=body.model_dump(),
    )


@router.put("/api/user-settings")
async def update_user_settings(
    request: Request,
    body: UserSettingsUpdate,
) -> dict[str, object]:
    """Description:
        Forward a user-settings update through the Web UI same-origin API.

    Requirements:
        - Preserve PA-side validation and persistence behaviour for settings updates.

    :param request: Incoming Web UI request object.
    :param body: User-settings update payload submitted by the browser.
    :returns: Updated persisted user-settings payload.
    """

    return await _proxy_pa_request(
        request,
        "PUT",
        "/api/user-settings",
        json_body=body.model_dump(),
    )


@router.put("/api/model-settings")
async def update_model_settings(
    request: Request,
    body: ModelSettingsUpdate,
) -> dict[str, object]:
    """Description:
        Forward a model-settings update through the Web UI same-origin API.

    Requirements:
        - Preserve PA-side validation and persistence behaviour for model-settings updates.

    :param request: Incoming Web UI request object.
    :param body: Model-settings update payload submitted by the browser.
    :returns: Updated persisted model-settings payload.
    """

    return await _proxy_pa_request(
        request,
        "PUT",
        "/api/model-settings",
        json_body=body.model_dump(),
    )


@router.post("/api/pa/system-prompt/reset")
async def reset_project_agent_system_prompt(request: Request) -> dict[str, object]:
    """Description:
        Reset the Project Agent prompt through the Web UI same-origin API.

    Requirements:
        - Proxy the reset request to the PA service prompt endpoint.

    :returns: Default active prompt metadata payload.
    """

    return await _proxy_pa_request(request, "POST", "/api/pa/system-prompt/reset")


@router.post("/approve/{request_id}", response_model=ApprovalDecisionResponse)
async def submit_approval(
    request: Request,
    request_id: str,
    body: ApprovalDecisionRequest,
) -> ApprovalDecisionResponse:
    """Description:
        Accept one approval decision and publish it back to the PA.

    Requirements:
        - Preserve the request identifier the PA originally issued.
        - Publish the decision payload into the approval-response channel.

    :param request: Incoming FastAPI request object.
    :param request_id: Approval request identifier.
    :param body: Validated approval decision payload.
    :raises HTTPException: If the Web UI has seen approval IDs and the requested ID is unknown.
    :returns: Acknowledgment response describing the queued approval decision.
    """

    redis = _require_redis()
    pending_ids = getattr(request.app.state, "pending_approval_ids", set())
    registry_active = bool(getattr(request.app.state, "approval_registry_active", False))
    if registry_active and request_id not in pending_ids:
        raise HTTPException(status_code=404, detail="Unknown approval request")

    payload = {
        "type": "approval_response",
        "request_id": request_id,
        "decision": body.decision,
        "scope": body.scope,
        "reason": body.reason,
        "pattern_override": body.pattern_override,
        "timestamp": _utc_now(),
    }
    await redis.publish(APPROVAL_RESPONSES_CHANNEL, json.dumps(payload))
    if registry_active:
        pending_ids.discard(request_id)
    return ApprovalDecisionResponse(status="sent", request_id=request_id, decision=body.decision)
