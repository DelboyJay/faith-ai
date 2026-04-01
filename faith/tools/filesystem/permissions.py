"""Permission resolution for secure filesystem access."""

from __future__ import annotations

from pathlib import PurePosixPath

from faith.tools.filesystem.mounts import MountConfig

_PERMISSION_RANK = {"none": 0, "readonly": 1, "readwrite": 2}


def _rank(permission: str) -> int:
    return _PERMISSION_RANK.get(permission, 0)


def _most_restrictive(a: str, b: str) -> str:
    return a if _rank(a) <= _rank(b) else b


def resolve_mount_permission(mount: MountConfig, relative_path: str) -> str:
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
    mount: MountConfig, relative_path: str, agent_mount_access: str | None
) -> str:
    if agent_mount_access is None:
        return "none"
    mount_permission = resolve_mount_permission(mount, relative_path)
    if mount_permission == "none":
        return "none"
    return _most_restrictive(mount_permission, agent_mount_access)


def check_permission(
    mount: MountConfig, relative_path: str, agent_mount_access: str | None, required: str
) -> tuple[bool, str]:
    effective = resolve_effective_permission(mount, relative_path, agent_mount_access)
    return (_rank(effective) >= _rank(required), effective)
