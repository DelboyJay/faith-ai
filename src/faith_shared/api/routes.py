"""Description:
    Define the shared route-manifest contracts used for service discovery.

Requirements:
    - Keep one machine-readable shape that the CLI can request from services.
    - Describe both HTTP and WebSocket endpoints consistently.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    """

    service: str
    protocol: Literal["http", "websocket"]
    method: Literal["GET", "POST"] | None = None
    path: str
    summary: str
    expected_status_codes: list[int] = Field(default_factory=list)


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
