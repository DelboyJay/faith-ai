"""Description:
    Publish the shared FAITH contract version and compatibility helpers.

Requirements:
    - Keep one package-level version for the shared contract package.
    - Re-export the compatibility helpers used by the CLI and PA.
"""

from faith_shared.compatibility import (
    CURRENT_API_VERSION,
    CURRENT_PROTOCOL_VERSION,
    CURRENT_SCHEMA_VERSION,
    FaithCompatibilityError,
    validate_component_versions,
    validate_schema_compatibility,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "CURRENT_API_VERSION",
    "CURRENT_PROTOCOL_VERSION",
    "CURRENT_SCHEMA_VERSION",
    "FaithCompatibilityError",
    "validate_component_versions",
    "validate_schema_compatibility",
]
