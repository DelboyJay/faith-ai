"""Description:
    Define the shared route-manifest contracts used for service discovery.

Requirements:
    - Keep one machine-readable shape that the CLI can request from services.
    - Describe both HTTP and WebSocket endpoints consistently.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def describe_route_implementation(
    implementation: Callable[..., Any] | str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> str:
    """Description:
        Build a stable implementation reference for one discovered route.

    Requirements:
        - Return ``filename::class/function`` for Python callables.
        - Prefer repository-relative filenames for FAITH-owned code.
        - Preserve explicit string references unchanged for non-callable handlers.

    :param implementation: Route handler callable or explicit implementation string.
    :param project_root: Repository root used for relative-path rendering.
    :returns: Stable implementation reference for route-manifest consumers.
    """

    if isinstance(implementation, str):
        return implementation

    source_file = inspect.getsourcefile(implementation) or inspect.getfile(implementation)
    source_path = Path(source_file).resolve()
    try:
        display_path = source_path.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        display_path = source_path.as_posix()
    return f"{display_path}::{implementation.__qualname__}"


class RouteManifestEntry(BaseModel):
    """Description:
        Describe one externally reachable endpoint exposed by a FAITH service.

    Requirements:
        - Include enough metadata for CLI rendering without hard-coding service routes.
        - Support both HTTP and WebSocket endpoints.

    :param service: Service name that owns the route.
    :param protocol: Network protocol used by the endpoint.
    :param method: HTTP method when the route is HTTP-based, otherwise `None`.
    :param path: Public URL path for the endpoint.
    :param summary: Brief human-readable description of the endpoint purpose.
    :param expected_status_codes: Expected HTTP status codes for HTTP routes.
    :param implementation: Stable ``filename::class/function`` reference for the route handler.
    """

    service: str
    protocol: Literal["http", "websocket"]
    method: Literal["GET", "POST", "PUT"] | None = None
    path: str
    summary: str
    expected_status_codes: list[int] = Field(default_factory=list)
    implementation: str


class ServiceRouteManifest(BaseModel):
    """Description:
        Describe the endpoint manifest exposed by one FAITH service.

    Requirements:
        - Identify the service and its version clearly.
        - Return all externally supported routes in one payload.

    :param service: Stable service identifier.
    :param version: Service version string.
    :param routes: Endpoint entries exposed by the service.
    """

    service: str
    version: str
    routes: list[RouteManifestEntry]
