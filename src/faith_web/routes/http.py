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
from pydantic import BaseModel, Field

from faith_pa.utils.redis_client import USER_INPUT_CHANNEL
from faith_web.app import APPROVAL_RESPONSES_CHANNEL, get_static_asset_version, templates

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
        {"asset_version": get_static_asset_version()},
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
