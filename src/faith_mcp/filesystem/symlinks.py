"""Symlink escape prevention for filesystem mounts."""

from __future__ import annotations

from pathlib import Path


class SymlinkEscapeError(Exception):
    pass


class BrokenSymlinkError(Exception):
    pass


def validate_path(path: Path, mount_root: Path) -> Path:
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
