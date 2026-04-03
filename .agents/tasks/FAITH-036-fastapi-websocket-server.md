# FAITH-036 — FastAPI Server Setup & WebSocket Endpoints

**Phase:** 8 — Web UI
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** DONE
**Implementation Status:** Completed in `src/faith_web/app.py`, `src/faith_web/routes/http.py`, `src/faith_web/routes/websocket.py`, `src/faith_web/templates/index.html`, and `tests/test_web_server.py`.
**Dependencies:** FAITH-002, FAITH-008
**FRS Reference:** Section 6.3, 6.5

---

## Objective

Build the FastAPI service inside the `faith_web` package (`src/faith_web/`) that serves the FAITH browser UI. This includes: serving the initial HTML shell via Jinja2, static file serving for JS/CSS/fonts, HTTP endpoints for user input submission, file uploads, and approval decisions, and the baseline WebSocket endpoints that bridge Redis pub/sub channels to the browser in real time. Each WebSocket worker subscribes to its corresponding Redis channel and forwards JSON text frames to the browser. The message relay flow is: user types message -> POST /input -> FastAPI publishes to Redis -> PA picks up -> PA/agents publish to Redis output channels -> FastAPI WebSocket workers forward to browser panels. The dedicated Docker runtime feed (`/ws/docker`) is added by FAITH-058 on top of this baseline backend.

---

## Architecture

```
src/faith_web/
├── __init__.py
├── app.py               ← FastAPI app, lifespan, Redis connection pool
├── routes/
│   ├── __init__.py
│   ├── http.py          ← POST /input, POST /upload, POST /approve/{request_id}
│   └── websocket.py     ← WS /ws/agent/{agent_id}, /ws/tool/{tool_id}, /ws/approvals, /ws/status
└── templates/
    └── index.html       ← Jinja2 shell (placeholder — content built by FAITH-037+)

web/                         ← Frontend assets (Vue 3)
├── css/
│   └── theme.css    ← Placeholder terminal dark theme
├── js/
│   └── app.js       ← Placeholder (built by FAITH-037+)
└── fonts/
    └── .gitkeep     ← Fonts added later (JetBrains Mono etc.)

tests/
└── test_web_server.py   ← Tests for HTTP + WebSocket endpoints
```

### Container Layout

The `faith_web` service runs a single FastAPI process via uvicorn. It connects to Redis (FAITH-002) for pub/sub message relay and uses the event system (FAITH-008) for publishing user-originated events.

```
┌─────────────────────────────────────────────────┐
│  faith_web service                           │
│                                                 │
│  uvicorn main:app --host 0.0.0.0 --port 8000   │
│                                                 │
│  ┌─────────┐   ┌──────────────┐   ┌─────────┐  │
│  │ Jinja2  │   │ Static Files │   │ Redis   │  │
│  │ index   │   │ /static/*    │   │ Pool    │  │
│  └─────────┘   └──────────────┘   └────┬────┘  │
│                                        │        │
│  ┌──────────────────────┐  ┌───────────┴──────┐ │
│  │ HTTP Routes          │  │ WS Workers       │ │
│  │ POST /input          │  │ /ws/agent/{id}   │ │
│  │ POST /upload         │  │ /ws/tool/{id}    │ │
│  │ POST /approve/{id}   │  │ /ws/approvals    │ │
│  │ GET /                │  │ /ws/status        │ │
│  └──────────────────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────┘
```

### Redis Channel Mapping

| WebSocket Endpoint | Redis Subscribe Pattern | Direction |
|---|---|---|
| `/ws/agent/{agent_id}` | `agent:{agent_id}:output` | Redis -> Browser |
| `/ws/tool/{tool_id}` | `tool:{tool_id}:output` | Redis -> Browser |
| `/ws/approvals` | `approval-events` | Redis -> Browser |
| `/ws/status` | `system-events` | Redis -> Browser |
| `POST /input` | Publish to `user-input` | Browser -> Redis |
| `POST /upload` | Publish to `user-input` | Browser -> Redis |
| `POST /approve/{request_id}` | Publish to `approval-responses` | Browser -> Redis |

`/ws/docker` is intentionally out of scope for this task and is added by FAITH-058 once the PA exposes Docker runtime data.

---

## Files to Create

### 1. `src/faith_web/__init__.py`

```python
"""FAITH Web UI — FastAPI backend for the browser-based panel interface."""
```

### 2. `src/faith_web/routes/__init__.py`

```python
"""FAITH Web UI route handlers."""
```

### 3. `src/faith_web/main.py`

```python
"""FAITH Web UI — FastAPI application entry point.

Serves the Jinja2 HTML shell, static files, HTTP endpoints for user
interaction, and WebSocket endpoints that bridge Redis pub/sub to the
browser in real time.

FRS Reference: Section 6.3, 6.5
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from faith.web.routes.http import router as http_router
from faith.web.routes.websocket import router as ws_router

logger = logging.getLogger("faith.web")

# Paths relative to this file
_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"

# Module-level Redis pool — shared by all routes and WS workers.
# Initialised during app lifespan startup.
redis_pool: aioredis.Redis | None = None

# Jinja2 templates instance
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _get_redis_url() -> str:
    """Resolve the Redis connection URL.

    Reads from the FAITH_REDIS_URL environment variable, falling back
    to the default Docker Compose service name.

    Returns:
        Redis URL string.
    """
    import os

    return os.environ.get("FAITH_REDIS_URL", "redis://redis:6379/0")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — manages the Redis connection pool.

    Creates the async Redis pool on startup and closes it on shutdown.
    The pool is stored at module level so route handlers and WebSocket
    workers can import it directly.
    """
    global redis_pool

    redis_url = _get_redis_url()
    logger.info(f"Connecting to Redis at {redis_url}")

    redis_pool = aioredis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=50,
    )

    # Verify connectivity
    try:
        await redis_pool.ping()
        logger.info("Redis connection established")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise

    yield

    # Shutdown
    logger.info("Closing Redis connection pool")
    if redis_pool:
        await redis_pool.close()
        redis_pool = None


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI app instance.
    """
    app = FastAPI(
        title="FAITH Web UI",
        description="Browser interface for the FAITH multi-agent framework",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Mount static files
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    # Include route handlers
    app.include_router(http_router)
    app.include_router(ws_router)

    return app


# Default app instance for uvicorn
app = create_app()
```

### 4. `src/faith_web/routes/http.py`

```python
"""HTTP route handlers for FAITH Web UI.

Handles:
- GET /           — Serve the Jinja2 HTML shell
- POST /input     — Submit user text message to PA via Redis
- POST /upload    — Upload image or document to PA via Redis
- POST /approve/{request_id} — Submit an approval decision

FRS Reference: Section 6.3.2
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from faith.protocol.events import EventPublisher, EventType

logger = logging.getLogger("faith.web.routes.http")

router = APIRouter()


# ──────────────────────────────────────────────────
# Request / Response models
# ──────────────────────────────────────────────────


class UserInputRequest(BaseModel):
    """POST /input request body."""

    message: str
    session_id: Optional[str] = None


class UserInputResponse(BaseModel):
    """POST /input response body."""

    status: str
    message_id: str


class ApprovalRequest(BaseModel):
    """POST /approve/{request_id} request body."""

    decision: str  # "approve" | "deny"
    scope: str = "once"  # "once" | "session" | "always" | "deny_always"
    reason: Optional[str] = None


class ApprovalResponse(BaseModel):
    """POST /approve/{request_id} response body."""

    status: str
    request_id: str
    decision: str


class UploadResponse(BaseModel):
    """POST /upload response body."""

    status: str
    message_id: str
    filename: str
    content_type: str


# ──────────────────────────────────────────────────
# GET / — Serve index.html
# ──────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the main HTML shell via Jinja2.

    The template loads GoldenLayout, Vue 3, and xterm.js from CDN
    (or vendored static files) and bootstraps the panel workspace.
    """
    from faith.web.main import templates

    return templates.TemplateResponse(
        "index.html",
        {"request": request},
    )


# ──────────────────────────────────────────────────
# POST /input — User text message
# ──────────────────────────────────────────────────


@router.post("/input", response_model=UserInputResponse)
async def submit_input(body: UserInputRequest):
    """Submit a user text message to the PA via Redis.

    The message is published to the `user-input` Redis channel.
    The PA subscribes to this channel and processes user messages.

    Args:
        body: User input containing the message text and optional session ID.

    Returns:
        Confirmation with a generated message ID.
    """
    from faith.web.main import redis_pool

    if redis_pool is None:
        raise HTTPException(status_code=503, detail="Redis not available")

    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    payload = json.dumps({
        "type": "user_input",
        "message_id": message_id,
        "message": body.message,
        "session_id": body.session_id,
        "timestamp": timestamp,
    })

    try:
        await redis_pool.publish("user-input", payload)
        logger.info(
            f"Published user input {message_id}: "
            f"{body.message[:80]}{'...' if len(body.message) > 80 else ''}"
        )
    except Exception as e:
        logger.error(f"Failed to publish user input: {e}")
        raise HTTPException(status_code=500, detail="Failed to send message")

    return UserInputResponse(status="sent", message_id=message_id)


# ──────────────────────────────────────────────────
# POST /upload — File / image upload
# ──────────────────────────────────────────────────

# Accepted MIME types for upload
_ACCEPTED_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}

# Max upload size: 10 MB
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    message: str = Form(default=""),
    session_id: Optional[str] = Form(default=None),
):
    """Upload a file or image to the PA via Redis.

    The file content is read into memory (max 10 MB), base64-encoded,
    and published to the `user-input` Redis channel with the file
    metadata. The PA processes the upload and routes it to the
    appropriate agent.

    Accepted types: PDF, DOCX, TXT, MD, PNG, JPEG, GIF, WEBP.

    Args:
        file: The uploaded file.
        message: Optional accompanying text message.
        session_id: Optional session identifier.

    Returns:
        Confirmation with file metadata.
    """
    import base64

    from faith.web.main import redis_pool

    if redis_pool is None:
        raise HTTPException(status_code=503, detail="Redis not available")

    # Validate content type
    content_type = file.content_type or "application/octet-stream"
    if content_type not in _ACCEPTED_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {content_type}. "
            f"Accepted: {', '.join(sorted(_ACCEPTED_TYPES))}",
        )

    # Read file content
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    message_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    payload = json.dumps({
        "type": "user_upload",
        "message_id": message_id,
        "filename": file.filename,
        "content_type": content_type,
        "size_bytes": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
        "message": message,
        "session_id": session_id,
        "timestamp": timestamp,
    })

    try:
        await redis_pool.publish("user-input", payload)
        logger.info(
            f"Published upload {message_id}: {file.filename} "
            f"({content_type}, {len(content)} bytes)"
        )
    except Exception as e:
        logger.error(f"Failed to publish upload: {e}")
        raise HTTPException(status_code=500, detail="Failed to send upload")

    return UploadResponse(
        status="sent",
        message_id=message_id,
        filename=file.filename or "unknown",
        content_type=content_type,
    )


# ──────────────────────────────────────────────────
# POST /approve/{request_id} — Approval decision
# ──────────────────────────────────────────────────


@router.post("/approve/{request_id}", response_model=ApprovalResponse)
async def submit_approval(request_id: str, body: ApprovalRequest):
    """Submit an approval decision for a pending request.

    The decision is published to the `approval-responses` Redis channel.
    The PA listens on this channel and applies the decision to the
    pending approval request.

    Valid decisions: "approve", "deny".
    Valid scopes: "once", "session", "always", "deny_always".

    Args:
        request_id: The approval request ID (from the approval event).
        body: The approval decision and scope.

    Returns:
        Confirmation of the submitted decision.
    """
    from faith.web.main import redis_pool

    if redis_pool is None:
        raise HTTPException(status_code=503, detail="Redis not available")

    # Validate decision
    if body.decision not in ("approve", "deny"):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid decision: '{body.decision}'. Must be 'approve' or 'deny'.",
        )

    # Validate scope
    valid_scopes = {"once", "session", "always", "deny_always"}
    if body.scope not in valid_scopes:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid scope: '{body.scope}'. Must be one of: {valid_scopes}",
        )

    timestamp = datetime.now(timezone.utc).isoformat()

    payload = json.dumps({
        "type": "approval_response",
        "request_id": request_id,
        "decision": body.decision,
        "scope": body.scope,
        "reason": body.reason,
        "timestamp": timestamp,
    })

    try:
        await redis_pool.publish("approval-responses", payload)
        logger.info(
            f"Published approval decision for {request_id}: "
            f"{body.decision} (scope: {body.scope})"
        )
    except Exception as e:
        logger.error(f"Failed to publish approval decision: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to send approval decision"
        )

    return ApprovalResponse(
        status="sent",
        request_id=request_id,
        decision=body.decision,
    )
```

### 5. `src/faith_web/routes/websocket.py`

```python
"""WebSocket route handlers for FAITH Web UI.

Each endpoint subscribes to a corresponding Redis pub/sub channel and
forwards messages to the connected browser client as newline-delimited
JSON. When the client disconnects, the Redis subscription is cleaned up.

Endpoints:
- /ws/agent/{agent_id} — Stream agent output from agent:{agent_id}:output
- /ws/tool/{tool_id}   — Stream tool activity from tool:{tool_id}:output
- /ws/approvals        — Stream approval requests from approval-events
- /ws/status           — Stream system status from system-events

FRS Reference: Section 6.3.2, 6.5.1
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("faith.web.routes.websocket")

router = APIRouter()


async def _redis_to_ws_bridge(
    websocket: WebSocket,
    redis_channel: str,
    label: str,
) -> None:
    """Subscribe to a Redis pub/sub channel and forward messages to a WebSocket.

    This is the core relay loop used by all WebSocket endpoints. It:
    1. Creates a Redis pub/sub subscription for the given channel.
    2. Reads messages from the subscription in an async loop.
    3. Forwards each message to the WebSocket client as text.
    4. Cleans up the subscription when the WebSocket disconnects.

    Args:
        websocket: The connected WebSocket client.
        redis_channel: The Redis channel to subscribe to.
        label: Human-readable label for logging (e.g. "agent:dev").
    """
    from faith.web.main import redis_pool

    if redis_pool is None:
        logger.error(f"Redis not available for {label} WebSocket")
        await websocket.close(code=1011, reason="Redis not available")
        return

    pubsub = redis_pool.pubsub()

    try:
        await pubsub.subscribe(redis_channel)
        logger.info(f"WebSocket {label}: subscribed to {redis_channel}")

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )

            if message is None:
                # No message — check if WebSocket is still alive by
                # continuing the loop. The WebSocketDisconnect exception
                # will be raised on the next send if the client left.
                continue

            if message["type"] != "message":
                continue

            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode("utf-8")

            # Forward the message to the browser
            try:
                await websocket.send_text(data)
            except (WebSocketDisconnect, RuntimeError):
                # Client disconnected during send
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket {label}: client disconnected")
    except asyncio.CancelledError:
        logger.info(f"WebSocket {label}: task cancelled")
    except Exception as e:
        logger.error(f"WebSocket {label}: error in bridge loop: {e}", exc_info=True)
    finally:
        try:
            await pubsub.unsubscribe(redis_channel)
            await pubsub.close()
        except Exception:
            pass
        logger.info(f"WebSocket {label}: subscription cleaned up")


# ──────────────────────────────────────────────────
# /ws/agent/{agent_id} — Agent output stream
# ──────────────────────────────────────────────────


@router.websocket("/ws/agent/{agent_id}")
async def ws_agent(websocket: WebSocket, agent_id: str):
    """Stream agent output to a browser panel.

    Subscribes to Redis channel `agent:{agent_id}:output` and forwards
    all messages to the WebSocket client. Each agent panel in the browser
    maintains its own WebSocket connection via this endpoint.

    Messages are newline-delimited JSON with the format:
        {"type": "output", "agent": "dev", "text": "...", "ts": "..."}
        {"type": "status", "agent": "dev", "status": "active", "model": "..."}

    Args:
        websocket: The WebSocket connection from the browser panel.
        agent_id: The agent identifier (e.g. "software-developer", "qa").
    """
    await websocket.accept()
    logger.info(f"Agent WebSocket connected: {agent_id}")

    redis_channel = f"agent:{agent_id}:output"
    await _redis_to_ws_bridge(websocket, redis_channel, f"agent:{agent_id}")


# ──────────────────────────────────────────────────
# /ws/tool/{tool_id} — Tool activity stream
# ──────────────────────────────────────────────────


@router.websocket("/ws/tool/{tool_id}")
async def ws_tool(websocket: WebSocket, tool_id: str):
    """Stream tool activity to a browser panel.

    Subscribes to Redis channel `tool:{tool_id}:output` and forwards
    all messages. Tool panels show commands sent to the tool and
    the tool's responses in a shell-session format.

    Messages are newline-delimited JSON with the format:
        {"type": "command", "tool": "filesystem", "action": "write", "detail": "..."}
        {"type": "result", "tool": "filesystem", "status": "success", "output": "..."}
        {"type": "pending_approval", "tool": "filesystem", "request_id": "apr-042"}

    Args:
        websocket: The WebSocket connection from the browser panel.
        tool_id: The tool identifier (e.g. "filesystem", "python-exec").
    """
    await websocket.accept()
    logger.info(f"Tool WebSocket connected: {tool_id}")

    redis_channel = f"tool:{tool_id}:output"
    await _redis_to_ws_bridge(websocket, redis_channel, f"tool:{tool_id}")


# ──────────────────────────────────────────────────
# /ws/approvals — Approval request stream
# ──────────────────────────────────────────────────


@router.websocket("/ws/approvals")
async def ws_approvals(websocket: WebSocket):
    """Stream approval requests to the browser Approval panel.

    Subscribes to Redis channel `approval-events` and forwards
    all pending approval requests. The Approval panel renders each
    request as a card with Approve/Deny buttons.

    Messages are newline-delimited JSON with the format:
        {"type": "approval_required", "request_id": "apr-042",
         "agent": "dev", "action": "run_command", "detail": "pytest tests/auth/"}

    Args:
        websocket: The WebSocket connection from the Approval panel.
    """
    await websocket.accept()
    logger.info("Approvals WebSocket connected")

    await _redis_to_ws_bridge(websocket, "approval-events", "approvals")


# ──────────────────────────────────────────────────
# /ws/status — System status stream
# ──────────────────────────────────────────────────


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    """Stream system status updates to the browser Status panel.

    Subscribes to Redis channel `system-events` and forwards all
    system events. The Status panel renders agent status, tool
    health, token usage, and config change notifications.

    Messages are newline-delimited JSON matching the FaithEvent
    format from FAITH-008:
        {"event": "agent:heartbeat", "source": "dev",
         "data": {"status": "active"}, "ts": "..."}

    Args:
        websocket: The WebSocket connection from the Status panel.
    """
    await websocket.accept()
    logger.info("Status WebSocket connected")

    await _redis_to_ws_bridge(websocket, "system-events", "status")
```

### 6. `src/faith_web/templates/index.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FAITH — Framework AI Team Hive</title>
    <link rel="stylesheet" href="/static/css/theme.css">
</head>
<body>
    <div id="app">
        <h1>FAITH</h1>
        <p>Framework AI Team Hive — Web UI</p>
        <p class="status">Connecting to backend...</p>
    </div>

    <!-- Placeholder: GoldenLayout, Vue 3, xterm.js loaded here (FAITH-037+) -->
    <script src="/static/js/app.js"></script>
</body>
</html>
```

### 7. `web/css/theme.css`

```css
/* FAITH terminal dark theme — placeholder.
   Full styling implemented in FAITH-039 (Terminal CSS Theme).

   FRS Reference: Section 6.6
*/

:root {
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --text-primary: #c9d1d9;
    --text-dim: #8b949e;
    --accent-green: #3fb950;
    --accent-amber: #d29922;
    --accent-red: #f85149;
    --font-mono: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    background-color: var(--bg-primary);
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 14px;
}

#app {
    padding: 2rem;
}

h1 {
    color: var(--accent-green);
    margin-bottom: 0.5rem;
}

.status {
    color: var(--text-dim);
    font-style: italic;
}
```

### 8. `web/js/app.js`

```javascript
/* FAITH Web UI — placeholder application script.
   Full panel system implemented in FAITH-037 (GoldenLayout).
   Full WebSocket logic implemented in FAITH-038 (Panel Components).

   FRS Reference: Section 6.3, 6.4
*/

console.log("FAITH Web UI loaded. Panel framework not yet initialised.");
```

### 9. `web/fonts/.gitkeep`

Empty file. Fonts (JetBrains Mono) added by FAITH-039.

### 10. `tests/test_web_server.py`

```python
"""Tests for the FAITH FastAPI web server.

Covers HTTP endpoints (GET /, POST /input, POST /upload,
POST /approve/{request_id}) and WebSocket endpoints
(/ws/agent/{agent_id}, /ws/tool/{tool_id}, /ws/approvals, /ws/status).

Uses httpx AsyncClient and WebSocket test client from FastAPI/Starlette.
Redis is mocked via a FakeRedis/FakePubSub pair.
"""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from faith.web.main import create_app


# ──────────────────────────────────────────────────
# Fake Redis for testing
# ──────────────────────────────────────────────────


class FakePubSub:
    """Minimal fake async Redis PubSub for WebSocket tests."""

    def __init__(self):
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self._messages: list[dict] = []
        self._closed = False
        self._message_event = asyncio.Event()

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str = None) -> None:
        if channel:
            self.unsubscribed.append(channel)

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self._messages:
            return self._messages.pop(0)
        return None

    async def close(self) -> None:
        self._closed = True

    def inject_message(self, channel: str, data: str) -> None:
        self._messages.append({
            "type": "message",
            "channel": channel.encode("utf-8"),
            "data": data.encode("utf-8"),
        })


class FakeRedis:
    """Minimal fake async Redis client for testing."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self._pubsub_instance = FakePubSub()

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass

    def pubsub(self) -> FakePubSub:
        return self._pubsub_instance


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def app(fake_redis):
    """Create a test app with mocked Redis."""
    import faith.web.main as web_main

    test_app = create_app()

    # Patch the module-level redis_pool
    original_pool = web_main.redis_pool
    web_main.redis_pool = fake_redis
    yield test_app
    web_main.redis_pool = original_pool


@pytest.fixture
def client(app):
    """Synchronous test client for HTTP endpoint tests."""
    return TestClient(app)


@pytest.fixture
def async_client(app):
    """Async test client for HTTP endpoint tests."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ──────────────────────────────────────────────────
# GET / — Index page tests
# ──────────────────────────────────────────────────


def test_index_returns_html(client):
    """GET / returns the index.html template."""
    response = client.get("/")
    assert response.status_code == 200
    assert "FAITH" in response.text
    assert "text/html" in response.headers["content-type"]


# ──────────────────────────────────────────────────
# POST /input — User input tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_input_publishes_to_redis(async_client, fake_redis):
    """POST /input publishes the message to the user-input channel."""
    response = await async_client.post(
        "/input",
        json={"message": "Build a REST API", "session_id": "sess-1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "sent"
    assert "message_id" in data

    # Verify Redis publish
    assert len(fake_redis.published) == 1
    channel, payload_str = fake_redis.published[0]
    assert channel == "user-input"
    payload = json.loads(payload_str)
    assert payload["type"] == "user_input"
    assert payload["message"] == "Build a REST API"
    assert payload["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_submit_input_without_session_id(async_client, fake_redis):
    """POST /input works without a session_id."""
    response = await async_client.post(
        "/input",
        json={"message": "Hello"},
    )
    assert response.status_code == 200
    payload = json.loads(fake_redis.published[0][1])
    assert payload["session_id"] is None


@pytest.mark.asyncio
async def test_submit_input_redis_unavailable(async_client):
    """POST /input returns 503 when Redis is not available."""
    import faith.web.main as web_main

    original = web_main.redis_pool
    web_main.redis_pool = None
    try:
        response = await async_client.post(
            "/input",
            json={"message": "test"},
        )
        assert response.status_code == 503
    finally:
        web_main.redis_pool = original


# ──────────────────────────────────────────────────
# POST /upload — File upload tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_file(async_client, fake_redis):
    """POST /upload publishes file content to user-input."""
    file_content = b"Hello, world!"
    response = await async_client.post(
        "/upload",
        files={"file": ("test.txt", file_content, "text/plain")},
        data={"message": "Check this file", "session_id": "sess-1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "sent"
    assert data["filename"] == "test.txt"
    assert data["content_type"] == "text/plain"

    # Verify Redis publish
    payload = json.loads(fake_redis.published[0][1])
    assert payload["type"] == "user_upload"
    assert payload["filename"] == "test.txt"
    decoded = base64.b64decode(payload["content_base64"])
    assert decoded == file_content


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type(async_client):
    """POST /upload rejects unsupported content types."""
    response = await async_client.post(
        "/upload",
        files={"file": ("evil.exe", b"\x00\x00", "application/x-executable")},
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(async_client):
    """POST /upload rejects files over 10 MB."""
    large_content = b"x" * (11 * 1024 * 1024)  # 11 MB
    response = await async_client.post(
        "/upload",
        files={"file": ("big.txt", large_content, "text/plain")},
    )
    assert response.status_code == 413


# ──────────────────────────────────────────────────
# POST /approve/{request_id} — Approval tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_approval_approve(async_client, fake_redis):
    """POST /approve/{id} publishes an approve decision."""
    response = await async_client.post(
        "/approve/apr-042",
        json={"decision": "approve", "scope": "session"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "sent"
    assert data["request_id"] == "apr-042"
    assert data["decision"] == "approve"

    # Verify Redis publish
    payload = json.loads(fake_redis.published[0][1])
    assert payload["type"] == "approval_response"
    assert payload["request_id"] == "apr-042"
    assert payload["decision"] == "approve"
    assert payload["scope"] == "session"


@pytest.mark.asyncio
async def test_submit_approval_deny(async_client, fake_redis):
    """POST /approve/{id} publishes a deny decision."""
    response = await async_client.post(
        "/approve/apr-099",
        json={"decision": "deny", "scope": "once", "reason": "Too risky"},
    )
    assert response.status_code == 200
    payload = json.loads(fake_redis.published[0][1])
    assert payload["decision"] == "deny"
    assert payload["reason"] == "Too risky"


@pytest.mark.asyncio
async def test_submit_approval_invalid_decision(async_client):
    """POST /approve/{id} rejects invalid decisions."""
    response = await async_client.post(
        "/approve/apr-001",
        json={"decision": "maybe", "scope": "once"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_approval_invalid_scope(async_client):
    """POST /approve/{id} rejects invalid scopes."""
    response = await async_client.post(
        "/approve/apr-001",
        json={"decision": "approve", "scope": "forever"},
    )
    assert response.status_code == 422


# ──────────────────────────────────────────────────
# Static file tests
# ──────────────────────────────────────────────────


def test_static_css_served(client):
    """Static CSS files are served from /static/css/."""
    response = client.get("/static/css/theme.css")
    assert response.status_code == 200
    assert "bg-primary" in response.text


def test_static_js_served(client):
    """Static JS files are served from /static/js/."""
    response = client.get("/static/js/app.js")
    assert response.status_code == 200
    assert "FAITH" in response.text


# ──────────────────────────────────────────────────
# WebSocket tests
# ──────────────────────────────────────────────────


def test_ws_agent_connects(client, fake_redis):
    """WebSocket /ws/agent/{id} accepts connections."""
    with client.websocket_connect("/ws/agent/dev") as ws:
        # Inject a message into the fake pubsub
        fake_redis._pubsub_instance.inject_message(
            "agent:dev:output",
            json.dumps({"type": "output", "agent": "dev", "text": "Hello"}),
        )
        data = ws.receive_text()
        parsed = json.loads(data)
        assert parsed["type"] == "output"
        assert parsed["agent"] == "dev"

    # Verify subscription was created for the correct channel
    assert "agent:dev:output" in fake_redis._pubsub_instance.subscribed


def test_ws_tool_connects(client, fake_redis):
    """WebSocket /ws/tool/{id} accepts connections."""
    with client.websocket_connect("/ws/tool/filesystem") as ws:
        fake_redis._pubsub_instance.inject_message(
            "tool:filesystem:output",
            json.dumps({"type": "command", "tool": "filesystem", "action": "read"}),
        )
        data = ws.receive_text()
        parsed = json.loads(data)
        assert parsed["type"] == "command"
        assert parsed["tool"] == "filesystem"

    assert "tool:filesystem:output" in fake_redis._pubsub_instance.subscribed


def test_ws_approvals_connects(client, fake_redis):
    """WebSocket /ws/approvals accepts connections."""
    with client.websocket_connect("/ws/approvals") as ws:
        fake_redis._pubsub_instance.inject_message(
            "approval-events",
            json.dumps({
                "type": "approval_required",
                "request_id": "apr-042",
                "agent": "dev",
            }),
        )
        data = ws.receive_text()
        parsed = json.loads(data)
        assert parsed["type"] == "approval_required"
        assert parsed["request_id"] == "apr-042"

    assert "approval-events" in fake_redis._pubsub_instance.subscribed


def test_ws_status_connects(client, fake_redis):
    """WebSocket /ws/status accepts connections."""
    with client.websocket_connect("/ws/status") as ws:
        fake_redis._pubsub_instance.inject_message(
            "system-events",
            json.dumps({
                "event": "agent:heartbeat",
                "source": "dev",
                "data": {"status": "active"},
            }),
        )
        data = ws.receive_text()
        parsed = json.loads(data)
        assert parsed["event"] == "agent:heartbeat"

    assert "system-events" in fake_redis._pubsub_instance.subscribed


def test_ws_agent_subscription_cleanup(client, fake_redis):
    """WebSocket cleanup unsubscribes from Redis on disconnect."""
    with client.websocket_connect("/ws/agent/qa") as ws:
        # Inject one message so the loop runs at least once
        fake_redis._pubsub_instance.inject_message(
            "agent:qa:output",
            json.dumps({"type": "status", "agent": "qa", "status": "idle"}),
        )
        ws.receive_text()

    # After disconnect, the channel should be unsubscribed
    assert "agent:qa:output" in fake_redis._pubsub_instance.unsubscribed
```

---

## Integration Points

The web server integrates with several FAITH components:

```python
# User sends a message via the browser input panel:
# Browser → POST /input → FastAPI publishes to Redis "user-input"
# PA subscribes to "user-input" and processes the message.

await redis.publish("user-input", json.dumps({
    "type": "user_input",
    "message_id": "abc-123",
    "message": "Build a JWT auth module",
    "session_id": "sess-1",
    "timestamp": "2026-03-24T10:00:00Z",
}))
```

```python
# PA delegates to an agent, agent publishes output:
# Agent → Redis "agent:dev:output" → FastAPI WS worker → Browser panel

await redis.publish("agent:dev:output", json.dumps({
    "type": "output",
    "agent": "dev",
    "text": "Implementing JWT handler with httponly cookies...\n",
    "ts": "2026-03-24T10:00:05Z",
}))

# The WebSocket worker at /ws/agent/dev picks this up from Redis
# and forwards it to all connected browser panels for that agent.
```

```python
# Approval flow:
# PA → Redis "approval-events" → WS /ws/approvals → Browser
# Browser → POST /approve/apr-042 → Redis "approval-responses" → PA

# PA publishes approval request:
await redis.publish("approval-events", json.dumps({
    "type": "approval_required",
    "request_id": "apr-042",
    "agent": "dev",
    "action": "run_command",
    "detail": "pytest tests/auth/",
}))

# User clicks Approve in the browser → POST /approve/apr-042
# FastAPI publishes to Redis:
await redis.publish("approval-responses", json.dumps({
    "type": "approval_response",
    "request_id": "apr-042",
    "decision": "approve",
    "scope": "session",
}))
```

---

## Acceptance Criteria

1. `create_app()` returns a configured FastAPI instance with Jinja2 templates, static file serving, and all HTTP/WebSocket routes registered.
2. Application lifespan creates an async Redis connection pool on startup and closes it cleanly on shutdown.
3. `GET /` returns the `index.html` Jinja2 template with a 200 status and `text/html` content type.
4. `GET /static/css/theme.css` and `GET /static/js/app.js` return the correct static files.
5. `POST /input` publishes a JSON payload to the `user-input` Redis channel containing the user message, a generated UUID message ID, optional session ID, and ISO 8601 timestamp.
6. `POST /upload` validates content type against the accepted list (PDF, DOCX, TXT, MD, PNG, JPEG, GIF, WEBP), rejects files over 10 MB, base64-encodes the file content, and publishes to `user-input`.
7. `POST /approve/{request_id}` validates the canonical approval decision vocabulary (`allow_once`, `approve_session`, `always_allow`, `always_ask`, `deny_once`, `deny_permanently`) and publishes to the `approval-responses` Redis channel.
8. All three POST endpoints return 503 when Redis is unavailable and 422/413/415 for invalid input.
9. `WS /ws/agent/{agent_id}` subscribes to `agent:{agent_id}:output` and forwards all Redis messages to the WebSocket client.
10. `WS /ws/tool/{tool_id}` subscribes to `tool:{tool_id}:output` and forwards all Redis messages to the WebSocket client.
11. `WS /ws/approvals` subscribes to `approval-events` and forwards all Redis messages to the WebSocket client.
12. `WS /ws/status` subscribes to `system-events` and forwards all Redis messages to the WebSocket client.
13. All WebSocket endpoints clean up their Redis pub/sub subscriptions when the client disconnects.
14. All tests in `tests/test_web_server.py` pass, covering: index page rendering, user input submission, file upload (valid, invalid type, oversized), approval decisions (approve, deny, invalid), static file serving, and all four WebSocket endpoints including subscription cleanup.

---

## Notes for Implementer

- **Redis pool is module-level**: The `redis_pool` variable in `app.py` is set during the lifespan context manager and imported by route handlers. This avoids passing the pool through FastAPI dependency injection, keeping the WebSocket bridge function simple. The deferred import pattern inside route handlers ensures the pool is resolved at call time, not at import time.
- **WebSocket bridge is shared**: All four WebSocket endpoints use the same `_redis_to_ws_bridge()` coroutine. The only difference is the Redis channel name. This keeps the relay logic in one place and makes it easy to add new WebSocket endpoints for future panel types.
- **No authentication on endpoints**: Per FRS Section 1.4, FAITH runs locally as a single-user system. There is no authentication or session management on the web server. If multi-user support is added in v2, authentication middleware would be added here.
- **Upload size limit**: The 10 MB limit is enforced in application code, not by FastAPI/Starlette middleware. For production use, consider adding a `max_body_size` middleware or reverse proxy limit as a defence-in-depth measure.
- **Base64 encoding for uploads**: File content is base64-encoded before publishing to Redis because Redis pub/sub messages are strings. For large files, this is inefficient — a future optimisation could store the file in a shared volume and publish only the file path.
- **Placeholder templates and static files**: The `index.html`, `theme.css`, and `app.js` files are minimal placeholders. The full panel framework (GoldenLayout, Vue 3, xterm.js) is implemented in FAITH-037 through FAITH-039. This task only needs to verify that template rendering and static serving work.
- **WebSocket timeout loop**: The `_redis_to_ws_bridge` function uses a 1-second timeout on `pubsub.get_message()`. This means there is up to 1 second of latency between a Redis message arriving and it being forwarded to the browser. This is acceptable for v1; if sub-100ms latency is needed, switch to `pubsub.listen()` with an async generator.
- **FakeRedis in tests**: The tests use a custom `FakeRedis`/`FakePubSub` pair rather than the `fakeredis` package, matching the pattern established in FAITH-004, FAITH-009, and FAITH-010 test suites.
- **No Dockerfile in this task**: The `web-ui` container Dockerfile is assumed to be created by the Docker Compose setup (FAITH-001). This task focuses solely on the FastAPI application code.

