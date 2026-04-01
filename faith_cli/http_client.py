"""HTTP helpers for communicating with the running PA."""

from __future__ import annotations

from typing import Any

import requests

PA_BASE_URL = "http://localhost:8000"


def pa_is_reachable() -> bool:
    """Return True when the local PA health endpoint responds."""

    try:
        response = requests.get(f"{PA_BASE_URL}/health", timeout=3)
        return response.status_code < 500
    except requests.RequestException:
        return False


def get_status() -> dict[str, Any] | None:
    """Fetch the PA status payload."""

    try:
        response = requests.get(f"{PA_BASE_URL}/api/status", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def request_shutdown() -> bool:
    """Attempt a coordinated shutdown if the endpoint exists."""

    try:
        response = requests.post(f"{PA_BASE_URL}/api/shutdown", timeout=30)
        return response.status_code == 200
    except requests.RequestException:
        return False
