"""Description:
    Define the shared FAITH compatibility and versioning rules.

Requirements:
    - Provide one canonical schema, API, and protocol version source.
    - Offer human-readable validation helpers that the CLI and PA can reuse.
    - Keep compatibility checks simple and deterministic for the Phase 1 stack.
"""

from __future__ import annotations

from dataclasses import dataclass

CURRENT_SCHEMA_VERSION = "1.0"
CURRENT_API_VERSION = "v1"
CURRENT_PROTOCOL_VERSION = "1.0"


@dataclass(slots=True)
class FaithCompatibilityError(RuntimeError):
    """Description:
        Represent a compatibility failure between FAITH components.

    Requirements:
        - Preserve a concise human-readable message for CLI output.
        - Carry the failing component name for diagnostics.

    :param component: Component name that failed validation.
    :param message: Human-readable description of the compatibility issue.
    """

    component: str
    message: str

    def __post_init__(self) -> None:
        """Description:
            Initialise the base runtime error message after dataclass creation.

        Requirements:
            - Make the exception printable without extra formatting.
        """

        super().__init__(self.message)


def _normalise_component_name(component: str) -> str:
    """Description:
        Return a stable component label for compatibility reporting.

    Requirements:
        - Strip surrounding whitespace from the provided component name.
        - Reject empty component identifiers.

    :param component: Raw component name supplied by the caller.
    :returns: Normalised non-empty component identifier.
    :raises FaithCompatibilityError: If the name is empty after trimming.
    """

    normalised = component.strip()
    if not normalised:
        raise FaithCompatibilityError("unknown", "Component name cannot be empty.")
    return normalised


def validate_component_versions(component_versions: dict[str, str]) -> None:
    """Description:
        Verify that all supplied FAITH component versions are identical.

    Requirements:
        - Treat the shared package version as the canonical expected version.
        - Raise one human-readable error when any component drifts.

    :param component_versions: Mapping of component name to semantic version.
    :raises FaithCompatibilityError: If any component version differs.
    """

    if not component_versions:
        return

    normalised = {
        _normalise_component_name(name): version.strip()
        for name, version in component_versions.items()
    }
    expected = next(iter(normalised.values()))
    mismatched = {name: version for name, version in normalised.items() if version != expected}
    if mismatched:
        details = ", ".join(f"{name}={version}" for name, version in sorted(mismatched.items()))
        raise FaithCompatibilityError(
            "version",
            f"FAITH component version mismatch detected: {details}. "
            f"Expected all components to match version {expected}.",
        )


def validate_schema_compatibility(
    *,
    component: str,
    schema_version: str,
    expected_version: str = CURRENT_SCHEMA_VERSION,
) -> None:
    """Description:
        Verify that one FAITH schema version matches the shared contract.

    Requirements:
        - Raise a human-readable error naming the failing component.
        - Allow callers to override the expected version for tests.

    :param component: Component or file being validated.
    :param schema_version: Observed schema version.
    :param expected_version: Shared schema version expected by this build.
    :raises FaithCompatibilityError: If the schema version does not match.
    """

    name = _normalise_component_name(component)
    if schema_version != expected_version:
        raise FaithCompatibilityError(
            name,
            f"{name} uses schema version {schema_version}, but FAITH expects "
            f"{expected_version}. Run the config migration flow before continuing.",
        )
