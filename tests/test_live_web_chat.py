"""Description:
    Provide optional live Web UI chat diagnostics against a running FAITH stack.

Requirements:
    - Exercise the real HTTP input endpoint and agent WebSocket together.
    - Skip by default so normal test runs do not require Docker, Redis, or Ollama.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable

import pytest
import requests
import websocket


def _collect_agent_frames(
    base_url: str, submit_input: Callable[[], None]
) -> list[dict[str, object]]:
    """Description:
        Collect Project Agent WebSocket frames while one input request is submitted.

    Requirements:
        - Connect to the live agent WebSocket before sending input.
        - Return parsed JSON frames for endpoint-level assertions.

    :param base_url: Base Web UI URL such as ``http://localhost:8080``.
    :param submit_input: Callback that submits one input request after the socket opens.
    :returns: Parsed WebSocket frames received from the Project Agent stream.
    """

    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    frames: list[dict[str, object]] = []
    errors: list[str] = []

    def on_message(_socket: websocket.WebSocketApp, message: str) -> None:
        """Description:
            Record one JSON message from the live agent socket.

        Requirements:
            - Preserve only parseable dictionary frames for the assertions.

        :param _socket: WebSocket app instance supplied by websocket-client.
        :param message: Raw WebSocket message text.
        """

        payload = json.loads(message)
        if isinstance(payload, dict):
            frames.append(payload)

    def on_error(_socket: websocket.WebSocketApp, error: object) -> None:
        """Description:
            Record WebSocket client errors for assertion failure messages.

        Requirements:
            - Keep diagnostics visible when the live endpoint cannot be reached.

        :param _socket: WebSocket app instance supplied by websocket-client.
        :param error: Error object supplied by websocket-client.
        """

        errors.append(str(error))

    socket = websocket.WebSocketApp(
        f"{ws_url}/ws/agent/project-agent",
        on_message=on_message,
        on_error=on_error,
    )
    thread = threading.Thread(target=socket.run_forever, daemon=True)
    thread.start()
    time.sleep(1)
    submit_input()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if any(frame.get("type") == "status" and frame.get("status") == "idle" for frame in frames):
            break
        time.sleep(0.2)
    socket.close()
    assert not errors, errors
    return frames


@pytest.mark.skipif(
    not os.getenv("FAITH_LIVE_WEB_UI_URL"),
    reason="Set FAITH_LIVE_WEB_UI_URL to run against a live FAITH Web UI.",
)
def test_live_input_endpoint_streams_project_agent_output() -> None:
    """Description:
        Verify the live Web UI input endpoint produces visible agent output frames.

    Requirements:
        - This test is needed to diagnose browser chat failures at the HTTP/WebSocket boundary.
        - Verify a POST to ``/input`` produces status, user echo, and PA output frames on the agent socket.
    """

    base_url = os.environ["FAITH_LIVE_WEB_UI_URL"].rstrip("/")

    def submit_input() -> None:
        """Description:
            Submit one live browser-style text message.

        Requirements:
            - Use the same endpoint as the browser Send button.
            - Fail loudly if the live Web UI rejects the request.
        """

        response = requests.post(
            f"{base_url}/input",
            json={"message": "hello live endpoint diagnostic"},
            timeout=10,
        )
        response.raise_for_status()

    frames = _collect_agent_frames(base_url, submit_input)

    assert any(
        frame.get("type") == "status" and frame.get("status") == "active" for frame in frames
    )
    assert any(
        frame.get("type") == "output"
        and "User: hello live endpoint diagnostic" in str(frame.get("text", ""))
        for frame in frames
    )
    assert any(
        frame.get("type") == "output" and str(frame.get("text", "")).startswith("PA: ")
        for frame in frames
    )
    assert any(frame.get("type") == "status" and frame.get("status") == "idle" for frame in frames)
