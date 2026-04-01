"""Best-effort browser helpers for the FAITH CLI."""

from __future__ import annotations

import time
import webbrowser

import requests

WEB_UI_URL = "http://localhost:8080"
MAX_WAIT_SECONDS = 30


def wait_for_web_ui(timeout_seconds: int = MAX_WAIT_SECONDS) -> bool:
    """Poll the Web UI until it responds or the timeout expires."""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{WEB_UI_URL}/health", timeout=1)
            if response.status_code < 500:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


def open_browser(url: str = WEB_UI_URL) -> bool:
    """Open the default browser if possible."""

    try:
        return webbrowser.open(url)
    except Exception:
        return False


def wait_and_open_browser(url: str = WEB_UI_URL) -> bool:
    """Wait for the UI to come up and then try to open it."""

    if not wait_for_web_ui():
        return False
    open_browser(url)
    return True
