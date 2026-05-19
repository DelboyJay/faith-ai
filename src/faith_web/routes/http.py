"""Description:
    Provide HTTP routes for the FAITH Web UI backend.

Requirements:
    - Accept browser input and uploads for forwarding into the PA runtime.
    - Validate request payloads and fail cleanly when Redis is unavailable.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Literal

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


class UserInputRequest(BaseModel):
    """Description:
        Validate one user text-input submission from the browser.

    Requirements:
        - Require a non-empty message.
        - Allow the browser to attach an optional session identifier.
    """

    message: str = Field(min_length=1)
    session_id: str | None = None


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


async def _proxy_pa_prompt_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, str] | None = None,
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
    json_body: dict[str, str] | None = None,
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
