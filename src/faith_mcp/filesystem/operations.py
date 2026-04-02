"""Core filesystem operations for the FAITH POC."""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from faith_mcp.filesystem.deny_list import is_denied, normalize_relative_path
from faith_mcp.filesystem.history import FileHistoryManager, make_metadata
from faith_mcp.filesystem.mounts import MountRegistry
from faith_mcp.filesystem.permissions import check_permission
from faith_mcp.filesystem.symlinks import validate_path, validate_path_components


class FilesystemError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PermissionDeniedError(FilesystemError):
    def __init__(self, message: str = "Permission denied"):
        super().__init__("PERMISSION_DENIED", message)


class DenyListError(FilesystemError):
    def __init__(self, message: str = "Path is blocked by deny list"):
        super().__init__("DENY_LIST", message)


class FileSizeLimitError(FilesystemError):
    def __init__(self, message: str = "File size limit exceeded"):
        super().__init__("FILE_SIZE_LIMIT", message)


class MountNotFoundError(FilesystemError):
    def __init__(self, mount_name: str):
        super().__init__("MOUNT_NOT_FOUND", f"Unknown mount '{mount_name}'")


def _require_mount(mount_registry: MountRegistry, mount_name: str):
    mount = mount_registry.get(mount_name)
    if mount is None:
        raise MountNotFoundError(mount_name)
    return mount


def _resolve_target(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    required: str,
    agent_id: str,
    agent_mounts: dict[str, str],
):
    mount = _require_mount(mount_registry, mount_name)
    normalised = normalize_relative_path(relative_path)
    if is_denied(normalised):
        raise DenyListError(f"'{relative_path}' is blocked")
    allowed, effective = check_permission(mount, normalised, agent_mounts.get(mount_name), required)
    if not allowed:
        raise PermissionDeniedError(
            f"'{relative_path}' requires {required}, effective permission is {effective}"
        )
    target = mount.host_path / Path(PurePosixPath(normalised))
    validate_path_components(target.parent if not target.exists() else target, mount.host_path)
    return mount, normalised, target


def _ensure_within_limit(path: Path, limit_mb: int) -> None:
    limit_bytes = max(1, limit_mb) * 1024 * 1024
    size = path.stat().st_size
    if size > limit_bytes:
        raise FileSizeLimitError(f"File size {size} exceeds limit {limit_bytes}")


def read_file(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    mount, normalised, target = _resolve_target(
        mount_registry, mount_name, relative_path, "readonly", agent_id, agent_mounts
    )
    if not target.exists():
        raise FilesystemError("NOT_FOUND", f"'{relative_path}' does not exist")
    resolved = validate_path(target, mount.host_path)
    _ensure_within_limit(resolved, mount.max_file_size_mb)
    return {
        "mount": mount_name,
        "path": normalised,
        "content": resolved.read_text(encoding="utf-8"),
        "size": resolved.stat().st_size,
    }


def write_file(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    content: str,
    agent_id: str,
    agent_mounts: dict[str, str],
    history_manager: FileHistoryManager | None = None,
) -> dict[str, Any]:
    mount, normalised, target = _resolve_target(
        mount_registry, mount_name, relative_path, "readwrite", agent_id, agent_mounts
    )
    limit_bytes = max(1, mount.max_file_size_mb) * 1024 * 1024
    encoded = content.encode("utf-8")
    if len(encoded) > limit_bytes:
        raise FileSizeLimitError(f"Content size {len(encoded)} exceeds limit {limit_bytes}")
    created = not target.exists()
    if target.exists() and history_manager is not None:
        history_manager.store_version(
            normalised, target, make_metadata(agent_id, f"Pre-write snapshot for {normalised}")
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    if history_manager is not None and created:
        history_manager.store_version(
            normalised, target, make_metadata(agent_id, f"Initial write for {normalised}")
        )
    return {
        "mount": mount_name,
        "path": normalised,
        "created": created,
        "size": len(encoded),
    }


def list_directory(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    mount, normalised, target = _resolve_target(
        mount_registry, mount_name, relative_path, "readonly", agent_id, agent_mounts
    )
    if not target.exists():
        raise FilesystemError("NOT_FOUND", f"'{relative_path}' does not exist")
    if not target.is_dir():
        raise FilesystemError("NOT_DIRECTORY", f"'{relative_path}' is not a directory")
    entries = []
    for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
        entries.append(
            {
                "name": child.name,
                "type": "directory" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            }
        )
    return {"mount": mount_name, "path": normalised, "entries": entries}


def stat_file(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    mount, normalised, target = _resolve_target(
        mount_registry, mount_name, relative_path, "readonly", agent_id, agent_mounts
    )
    if not target.exists():
        return {"mount": mount_name, "path": normalised, "exists": False}
    resolved = validate_path(target, mount.host_path)
    return {
        "mount": mount_name,
        "path": normalised,
        "exists": True,
        "type": "directory" if resolved.is_dir() else "file",
        "size": resolved.stat().st_size if resolved.is_file() else 0,
    }


def delete_file(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
    history_manager: FileHistoryManager | None = None,
) -> dict[str, Any]:
    mount, normalised, target = _resolve_target(
        mount_registry, mount_name, relative_path, "readwrite", agent_id, agent_mounts
    )
    if not target.exists():
        raise FilesystemError("NOT_FOUND", f"'{relative_path}' does not exist")
    if history_manager is not None and target.is_file():
        history_manager.store_version(
            normalised, target, make_metadata(agent_id, f"Pre-delete snapshot for {normalised}")
        )
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"mount": mount_name, "path": normalised, "deleted": True}


def make_directory(
    mount_registry: MountRegistry,
    mount_name: str,
    relative_path: str,
    agent_id: str,
    agent_mounts: dict[str, str],
) -> dict[str, Any]:
    _mount, normalised, target = _resolve_target(
        mount_registry, mount_name, relative_path, "readwrite", agent_id, agent_mounts
    )
    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    return {"mount": mount_name, "path": normalised, "created": created}

