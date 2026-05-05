"""Description:
    Re-export the shared API route-manifest contracts.

Requirements:
    - Provide a stable import surface for service route discovery payloads.
    - Avoid embedding runtime behaviour in the package export module.
"""

from faith_shared.api.routes import (
    RouteManifestEntry,
    ServiceRouteManifest,
    describe_route_implementation,
)

__all__ = ["RouteManifestEntry", "ServiceRouteManifest", "describe_route_implementation"]
