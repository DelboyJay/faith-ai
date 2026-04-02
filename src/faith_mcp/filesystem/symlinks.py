"""
Description:
    Prevent filesystem mount traversal through broken or escaping symlinks.

Requirements:
    - Reject broken symlinks.
    - Reject resolved paths that escape the configured mount root.
"""

from __future__ import annotations

from pathlib import Path


class SymlinkEscapeError(Exception):
    """
    Description:
        Signal that a symlink or path component resolves outside the mount root.

    Requirements:
        - Represent mount-escape failures raised by the filesystem safety layer.
    """


class BrokenSymlinkError(Exception):
    """
    Description:
        Signal that a requested symlink is broken and cannot be resolved safely.

    Requirements:
        - Represent broken-link failures raised by the filesystem safety layer.
    """


def validate_path(path: Path, mount_root: Path) -> Path:
    """
    Description:
        Resolve a path and ensure the final target remains inside the mount root.

    Requirements:
        - Reject broken symlinks.
        - Reject resolved paths that escape the resolved mount root.

    :param path: Path to resolve and validate.
    :param mount_root: Mount root that the path must remain inside.
    :returns: Resolved safe path inside the mount root.
    :raises BrokenSymlinkError: If the symlink cannot be resolved safely.
    :raises SymlinkEscapeError: If the resolved path escapes the mount root.
    """
    mount_resolved = mount_root.resolve()
    if path.is_symlink() and not path.exists():
        raise BrokenSymlinkError(f"Broken symlink: {path}")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise BrokenSymlinkError(f"Broken symlink: {path}") from exc
    try:
        resolved.relative_to(mount_resolved)
    except ValueError as exc:
        raise SymlinkEscapeError(f"Symlink escape blocked: {path} -> {resolved}") from exc
    return resolved


def validate_path_components(path: Path, mount_root: Path) -> Path:
    """
    Description:
        Validate every symlinked component in a path before the target is
        created or accessed.

    Requirements:
        - Reject any component chain that escapes the mount root.
        - Reuse `validate_path()` for symlinked components found along the way.

    :param path: Path whose components should be validated.
    :param mount_root: Mount root that the path must remain inside.
    :returns: Resolved path with unresolved-final-target support.
    :raises SymlinkEscapeError: If the path escapes the mount root.
    :raises BrokenSymlinkError: If a symlink component is broken.
    """
    mount_resolved = mount_root.resolve()
    try:
        relative = path.resolve(strict=False).relative_to(mount_resolved)
    except ValueError as exc:
        raise SymlinkEscapeError(f"Path escapes mount root: {path}") from exc

    current = mount_resolved
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            validate_path(current, mount_resolved)
    return path.resolve(strict=False)
