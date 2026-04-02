"""
Description:
    Resolve effective filesystem permissions for FAITH mount access.

Requirements:
    - Combine mount-level policy with agent-granted access.
    - Prefer the most restrictive effective permission.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from faith_mcp.filesystem.mounts import MountConfig

_PERMISSION_RANK = {"none": 0, "readonly": 1, "readwrite": 2}


def _rank(permission: str) -> int:
    """
    Description:
        Convert a permission name into its numeric precedence.

    Requirements:
        - Return the lowest rank for unknown permission names so they fail
          closed.

    :param permission: Permission label to rank.
    :returns: Integer precedence for comparison operations.
    """
    return _PERMISSION_RANK.get(permission, 0)


def _most_restrictive(a: str, b: str) -> str:
    """
    Description:
        Return the more restrictive of two permission levels.

    Requirements:
        - Compare permissions using the shared rank table.

    :param a: First permission level to compare.
    :param b: Second permission level to compare.
    :returns: More restrictive permission level.
    """
    return a if _rank(a) <= _rank(b) else b


def resolve_mount_permission(mount: MountConfig, relative_path: str) -> str:
    """
    Description:
        Resolve the permission granted by the mount configuration for a relative
        path.

    Requirements:
        - Prefer the longest matching subfolder override when more than one rule
          applies.
        - Return `none` for nested paths on non-recursive mounts.

    :param mount: Mount definition being evaluated.
    :param relative_path: Mount-relative path being checked.
    :returns: Permission granted by the mount configuration.
    """
    normalised = PurePosixPath(relative_path).as_posix().lstrip("/")
    best_match = None
    best_length = -1
    for subfolder, access in mount.subfolder_overrides.items():
        if normalised == subfolder or normalised.startswith(subfolder + "/"):
            if len(subfolder) > best_length:
                best_match = access
                best_length = len(subfolder)
    if best_match is not None:
        return best_match
    if not mount.recursive and normalised and "/" in normalised:
        return "none"
    return mount.access


def resolve_effective_permission(
    mount: MountConfig,
    relative_path: str,
    agent_mount_access: str | None,
) -> str:
    """
    Description:
        Combine mount policy and agent-granted access into one effective
        permission.

    Requirements:
        - Deny access when the agent has no grant for the mount.
        - Use the most restrictive value between mount policy and agent grant.

    :param mount: Mount definition being evaluated.
    :param relative_path: Mount-relative path being checked.
    :param agent_mount_access: Access level granted to the agent for the mount.
    :returns: Effective permission level for the requested path.
    """
    if agent_mount_access is None:
        return "none"
    mount_permission = resolve_mount_permission(mount, relative_path)
    if mount_permission == "none":
        return "none"
    return _most_restrictive(mount_permission, agent_mount_access)


def check_permission(
    mount: MountConfig,
    relative_path: str,
    agent_mount_access: str | None,
    required: str,
) -> tuple[bool, str]:
    """
    Description:
        Check whether the effective permission satisfies the required access
        level.

    Requirements:
        - Return both the allow/deny decision and the effective permission used
          to make it.

    :param mount: Mount definition being evaluated.
    :param relative_path: Mount-relative path being checked.
    :param agent_mount_access: Access level granted to the agent for the mount.
    :param required: Minimum permission required by the requested operation.
    :returns: Tuple of allow flag and effective permission name.
    """
    effective = resolve_effective_permission(mount, relative_path, agent_mount_access)
    return (_rank(effective) >= _rank(required), effective)
