"""Description:
    Provide Docker runtime routes for the FAITH Web UI.

Requirements:
    - Expose a dedicated HTTP and WebSocket feed for Docker runtime visibility.
    - Fail cleanly when the PA runtime feed is unavailable.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from faith_shared.config import DockerRuntimeSnapshot

router = APIRouter()


async def _fetch_runtime_snapshot(websocket_app=None) -> DockerRuntimeSnapshot:
    """Description:
        Fetch the current Docker runtime snapshot via the configured Web UI fetcher.

    Requirements:
        - Resolve the fetcher lazily from the active application state.
        - Raise a service-unavailable error when the fetcher is missing.

    :param websocket_app: Optional FastAPI application instance from a WebSocket.
    :raises HTTPException: If no runtime fetcher is configured.
    :returns: Docker runtime snapshot payload.
    """

    if websocket_app is None:
        raise HTTPException(status_code=503, detail="Docker runtime fetcher not configured")
    fetcher = getattr(websocket_app.state, "pa_runtime_fetcher", None)
    if fetcher is None:
        raise HTTPException(status_code=503, detail="Docker runtime fetcher not configured")
    return DockerRuntimeSnapshot.model_validate(await fetcher())


@router.get("/api/docker-runtime", response_model=DockerRuntimeSnapshot)
async def api_docker_runtime(request: Request) -> DockerRuntimeSnapshot:
    """Description:
        Return the current Docker runtime snapshot for the Web UI.

    Requirements:
        - Proxy the PA runtime snapshot through the Web UI service.

    :raises HTTPException: If the PA runtime feed is unavailable.
    :returns: Docker runtime snapshot payload.
    """

    fetcher = getattr(request.app.state, "pa_runtime_fetcher", None)
    if fetcher is None:
        raise HTTPException(status_code=503, detail="Docker runtime fetcher not configured")
    try:
        return await fetcher()
    except Exception as exc:  # pragma: no cover - exercised through request-style tests.
        raise HTTPException(status_code=503, detail=f"Docker runtime unavailable: {exc}") from exc


@router.websocket("/ws/docker")
async def websocket_docker(websocket: WebSocket) -> None:
    """Description:
        Stream Docker runtime snapshots to the browser.

    Requirements:
        - Poll the configured PA runtime fetcher for now.
        - Close the socket cleanly when the client disconnects.

    :param websocket: Connected browser WebSocket.
    """

    await websocket.accept()
    try:
        while True:
            try:
                snapshot = await _fetch_runtime_snapshot(websocket.app)
                await websocket.send_json(snapshot.model_dump(mode="json"))
            except Exception as exc:  # pragma: no cover - browser/runtime path.
                await websocket.send_json(
                    DockerRuntimeSnapshot(
                        docker_available=False,
                        status=f"unavailable: {exc}",
                        images=[],
                        containers=[],
                    ).model_dump(mode="json")
                )
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
