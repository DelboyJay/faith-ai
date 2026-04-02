"""Description:
    Provide HTTP helpers for communicating with the running PA and Web UI services.

Requirements:
    - Keep the CLI decoupled from service internals by using service-exposed discovery endpoints.
    - Fail cleanly when local services are unavailable.
"""

from __future__ import annotations

from typing import Any

import requests

PA_BASE_URL = "http://localhost:8000"
WEB_BASE_URL = "http://localhost:8080"


def pa_is_reachable() -> bool:
    """Description:
        Return whether the local PA health endpoint is currently reachable.

    Requirements:
        - Treat any non-5xx response as proof that the PA is up enough for CLI coordination.
        - Return ``False`` instead of raising request exceptions.

    :returns: ``True`` when the PA health route responds without a server error, otherwise ``False``.
    """

    try:
        response = requests.get(f"{PA_BASE_URL}/health", timeout=3)
        return response.status_code < 500
    except requests.RequestException:
        return False


def get_status() -> dict[str, Any] | None:
    """Description:
        Fetch the current PA status payload.

    Requirements:
        - Return ``None`` when the PA is unreachable or returns an error.

    :returns: Parsed PA status payload, or ``None`` when unavailable.
    """

    try:
        response = requests.get(f"{PA_BASE_URL}/api/status", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def get_route_manifest(base_url: str) -> dict[str, Any] | None:
    """Description:
        Fetch a structured route manifest from one FAITH HTTP service.

    Requirements:
        - Request the standard ``/api/routes`` endpoint.
        - Return ``None`` when the service is unreachable or responds with an error.

    :param base_url: Service base URL.
    :returns: Parsed route manifest payload, or ``None`` when unavailable.
    """

    try:
        response = requests.get(f"{base_url}/api/routes", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def get_known_route_manifests() -> list[tuple[str, dict[str, Any] | None]]:
    """Description:
        Collect route manifests for the known local FAITH services.

    Requirements:
        - Probe the PA and Web UI using the shared route-discovery contract.
        - Preserve service ordering for predictable CLI output.

    :returns: Ordered list of ``(base_url, manifest)`` pairs.
    """

    return [
        (PA_BASE_URL, get_route_manifest(PA_BASE_URL)),
        (WEB_BASE_URL, get_route_manifest(WEB_BASE_URL)),
    ]


def request_shutdown() -> bool:
    """Description:
        Attempt a coordinated PA shutdown through the dedicated shutdown endpoint.

    Requirements:
        - Return ``False`` when the endpoint is unavailable or returns an error.

    :returns: ``True`` when the PA accepted the shutdown request, otherwise ``False``.
    """

    try:
        response = requests.post(f"{PA_BASE_URL}/api/shutdown", timeout=30)
        return response.status_code == 200
    except requests.RequestException:
        return False
