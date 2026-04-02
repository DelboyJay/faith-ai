"""Description:
    Provide browser helpers for the FAITH CLI.

Requirements:
    - Poll the Web UI until it is reachable.
    - Open the system browser only as a best-effort convenience step.
"""

from __future__ import annotations

import time
import webbrowser

import requests

WEB_UI_URL = "http://localhost:8080"
MAX_WAIT_SECONDS = 30


def wait_for_web_ui(timeout_seconds: int = MAX_WAIT_SECONDS) -> bool:
    """Description:
        Poll the Web UI health endpoint until it responds or a timeout expires.

    Requirements:
        - Treat any non-5xx response as proof that the UI is up enough to open.
        - Stop polling once the timeout budget is exhausted.

    :param timeout_seconds: Maximum number of seconds to wait.
    :returns: ``True`` when the Web UI becomes reachable, otherwise ``False``.
    """

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
    """Description:
        Open the default browser for one FAITH URL when possible.

    Requirements:
        - Avoid raising browser-launch exceptions into CLI commands.
        - Return a boolean so callers can decide what fallback message to show.

    :param url: Target URL to open in the browser.
    :returns: ``True`` when the browser launch was accepted, otherwise ``False``.
    """

    try:
        return webbrowser.open(url)
    except Exception:
        return False


def wait_and_open_browser(url: str = WEB_UI_URL) -> bool:
    """Description:
        Wait for the Web UI to come up and then try to open it in the browser.

    Requirements:
        - Do not attempt to open the browser until the Web UI is reachable.
        - Return ``False`` when the UI never becomes ready in time.

    :param url: Target URL to open once the UI is reachable.
    :returns: ``True`` when the UI is reachable and the browser open attempt ran, otherwise ``False``.
    """

    if not wait_for_web_ui():
        return False
    open_browser(url)
    return True
