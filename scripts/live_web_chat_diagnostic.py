"""Description:
    Run an optional live Web UI chat diagnostic against a running FAITH stack.

Requirements:
    - Exercise the real HTTP input endpoint and Project Agent WebSocket together.
    - Stay outside pytest collection so the default test suite has no skipped tests.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Callable
from typing import Any

import requests
import websocket


def collect_agent_frames(
    base_url: str, submit_input: Callable[[], None], *, timeout_seconds: float = 30.0
) -> list[dict[str, Any]]:
    """Description:
        Collect Project Agent WebSocket frames while one input request is submitted.

    Requirements:
        - Connect to the live agent WebSocket before sending input.
        - Return parsed JSON frames for endpoint-level assertions.

    :param base_url: Base Web UI URL such as ``http://localhost:8080``.
    :param submit_input: Callback that submits one input request after the socket opens.
    :param timeout_seconds: Maximum time to wait for the Project Agent to become idle.
    :returns: Parsed WebSocket frames received from the Project Agent stream.
    :raises RuntimeError: If the WebSocket reports client errors.
    """

    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    frames: list[dict[str, Any]] = []
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
            Record WebSocket client errors for diagnostic failure messages.

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
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if any(frame.get("type") == "status" and frame.get("status") == "idle" for frame in frames):
            break
        time.sleep(0.2)
    socket.close()
    if errors:
        raise RuntimeError("; ".join(errors))
    return frames


def run_diagnostic(base_url: str, *, message: str) -> list[dict[str, Any]]:
    """Description:
        Submit one browser-style message and verify live Project Agent frames.

    Requirements:
        - Use the same ``/input`` endpoint as the browser Send button.
        - Raise clear assertion errors when expected frames are missing.

    :param base_url: Base Web UI URL such as ``http://localhost:8080``.
    :param message: Message to submit through the live endpoint.
    :returns: Captured Project Agent frames.
    """

    clean_base_url = base_url.rstrip("/")

    def submit_input() -> None:
        """Description:
            Submit one live browser-style text message.

        Requirements:
            - Fail loudly if the live Web UI rejects the request.
        """

        response = requests.post(
            f"{clean_base_url}/input",
            json={"message": message},
            timeout=10,
        )
        response.raise_for_status()

    frames = collect_agent_frames(clean_base_url, submit_input)
    assert any(
        frame.get("type") == "status" and frame.get("status") == "active" for frame in frames
    ), "No active status frame received."
    assert any(
        frame.get("type") == "output" and f"User: {message}" in str(frame.get("text", ""))
        for frame in frames
    ), "No user echo output frame received."
    assert any(
        frame.get("type") == "output" and str(frame.get("text", "")).startswith("PA: ")
        for frame in frames
    ), "No Project Agent response output frame received."
    assert any(
        frame.get("type") == "status" and frame.get("status") == "idle" for frame in frames
    ), "No idle status frame received."
    return frames


def parse_args() -> argparse.Namespace:
    """Description:
        Parse command-line arguments for the live diagnostic.

    Requirements:
        - Default to the local FAITH Web UI URL.
        - Allow the submitted message to be overridden.

    :returns: Parsed command-line namespace.
    """

    parser = argparse.ArgumentParser(description="Run a live FAITH Web UI chat diagnostic.")
    parser.add_argument("--url", default="http://localhost:8080", help="FAITH Web UI base URL.")
    parser.add_argument(
        "--message",
        default="hello live endpoint diagnostic",
        help="Message to submit through the Web UI input endpoint.",
    )
    return parser.parse_args()


def main() -> None:
    """Description:
        Run the live diagnostic and print a concise success summary.

    Requirements:
        - Exit with an exception traceback when the diagnostic fails.
        - Print the captured frame count when the diagnostic succeeds.
    """

    args = parse_args()
    frames = run_diagnostic(args.url, message=args.message)
    print(f"Live Web UI chat diagnostic passed with {len(frames)} frames.")


if __name__ == "__main__":
    main()
