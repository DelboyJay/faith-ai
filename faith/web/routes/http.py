"""HTTP routes for the FAITH web backend."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from faith.utils.redis_client import USER_INPUT_CHANNEL
from faith.web.app import APPROVAL_RESPONSES_CHANNEL, templates

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
    message: str = Field(min_length=1)
    session_id: str | None = None


class UserInputResponse(BaseModel):
    status: str
    message_id: str
    channel: str


class ApprovalDecisionRequest(BaseModel):
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
    status: str
    request_id: str
    decision: str


class UploadResponse(BaseModel):
    status: str
    message_id: str
    filename: str
    content_type: str
    channel: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_redis():
    import faith.web.app as web_app_module

    redis = web_app_module.redis_pool
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis not available")
    return redis


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@router.post("/input", response_model=UserInputResponse)
async def submit_input(body: UserInputRequest) -> UserInputResponse:
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
    request_id: str,
    body: ApprovalDecisionRequest,
) -> ApprovalDecisionResponse:
    redis = _require_redis()
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
    return ApprovalDecisionResponse(status="sent", request_id=request_id, decision=body.decision)
