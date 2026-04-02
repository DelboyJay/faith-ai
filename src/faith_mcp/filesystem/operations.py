"""
Description:
    Implement the core FAITH filesystem operations used by the filesystem MCP
    server.

Requirements:
    - Enforce mount existence, deny-list rules, permission checks, symlink
      safety, and file-size limits.
    - Provide structured read, write, list, stat, delete, and mkdir helpers.
"""

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
    """
    Description:
        Represent a structured filesystem operation failure.

    Requirements:
        - Preserve a stable error code alongside the human-readable message.

    :param code: Stable machine-readable error code.
    :param message: Human-readable error message.
    """

    def __init__(self, code: str, message: str):
        """
        Description:
            Store the structured filesystem error details.

        Requirements:
            - Preserve the supplied error code on the exception instance.

        :param code: Stable machine-readable error code.
        :param message: Human-readable error message.
        """
        super().__init__(message)
        self.code = code


class PermissionDeniedError(FilesystemError):
    """
    Description:
        Represent a filesystem permission denial.

    Requirements:
        - Use the canonical `PERMISSION_DENIED` error code.
    """

    def __init__(self, message: str = "Permission denied"):
        """
        Description:
            Build a permission-denied filesystem error.

        Requirements:
            - Use the canonical permission-denied error code.

        :param message: Human-readable denial message.
        """
        super().__init__("PERMISSION_DENIED", message)


class DenyListError(FilesystemError):
    """
    Description:
        Represent a deny-list block in the filesystem layer.

    Requirements:
        - Use the canonical `DENY_LIST` error code.
    """

    def __init__(self, message: str = "Path is blocked by deny list"):
        """
        Description:
            Build a deny-list filesystem error.

        Requirements:
            - Use the canonical deny-list error code.

        :param message: Human-readable deny-list message.
        """
        super().__init__("DENY_LIST", message)


class FileSizeLimitError(FilesystemError):
    """
    Description:
        Represent a filesystem file-size limit violation.

    Requirements:
        - Use the canonical `FILE_SIZE_LIMIT` error code.
    """

    def __init__(self, message: str = "File size limit exceeded"):
        """
        Description:
            Build a file-size-limit filesystem error.

        Requirements:
            - Use the canonical file-size-limit error code.

        :param message: Human-readable size-limit message.
        """
        super().__init__("FILE_SIZE_LIMIT", message)


class MountNotFoundError(FilesystemError):
    """
    Description:
        Represent a request that references an unknown mount.

    Requirements:
        - Use the canonical `MOUNT_NOT_FOUND` error code.
    """

    def __init__(self, mount_name: str):
        """
        Description:
            Build a mount-not-found filesystem error.

        Requirements:
            - Include the missing mount name in the error message.

        :param mount_name: Missing mount name.
        """
        super().__init__("MOUNT_NOT_FOUND", f"Unknown mount '{mount_name}'")


def _require_mount(mount_registry: MountRegistry, mount_name: str):
    """
    Description:
        Return a configured mount or raise a structured mount-not-found error.

    Requirements:
        - Fail fast when the requested mount does not exist.

    :param mount_registry: Mount registry used for the lookup.
    :param mount_name: Name of the mount to resolve.
    :returns: Resolved mount configuration.
    :raises MountNotFoundError: If the mount is unknown.
    """
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
    """
    Description:
        Resolve and validate the target path for a filesystem operation.

    Requirements:
        - Enforce mount existence, deny-list rules, permissions, and symlink
          component safety.
        - Return the resolved mount, normalised relative path, and target path.

    :param mount_registry: Mount registry used for the lookup.
    :param mount_name: Name of the mount to resolve.
    :param relative_path: Mount-relative path requested by the caller.
    :param required: Minimum required permission for the operation.
    :param agent_id: Agent performing the operation.
    :param agent_mounts: Mount permissions granted to the agent.
    :returns: Tuple of mount config, normalised relative path, and target path.
    :raises DenyListError: If the path is blocked by the deny list.
    :raises PermissionDeniedError: If the effective permission is insufficient.
    """
    _ = agent_id
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
    """
    Description:
        Enforce the configured maximum file size for a resolved file.

    Requirements:
        - Raise a structured size-limit error when the file is too large.

    :param path: Resolved file path to check.
    :param limit_mb: Maximum allowed file size in megabytes.
    :raises FileSizeLimitError: If the file exceeds the configured limit.
    """
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
    """
    Description:
        Read one text file through the filesystem policy layer.

    Requirements:
        - Enforce permissions, deny-list rules, symlink safety, and file-size
          limits.
        - Return the mounted path, normalised relative path, text content, and
          file size.

    :param mount_registry: Mount registry used for the lookup.
    :param mount_name: Name of the mount to read from.
    :param relative_path: Mount-relative path to read.
    :param agent_id: Agent performing the read.
    :param agent_mounts: Mount permissions granted to the agent.
    :returns: Structured read-file payload.
    :raises FilesystemError: If the target does not exist.
    """
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
    """
    Description:
        Write one text file through the filesystem policy layer.

    Requirements:
        - Enforce permissions, deny-list rules, symlink safety, and content-size
          limits.
        - Store history snapshots before overwrite and after initial creation
          when a history manager is supplied.

    :param mount_registry: Mount registry used for the lookup.
    :param mount_name: Name of the mount to write to.
    :param relative_path: Mount-relative path to write.
    :param content: Text content to persist.
    :param agent_id: Agent performing the write.
    :param agent_mounts: Mount permissions granted to the agent.
    :param history_manager: Optional history manager used to store snapshots.
    :returns: Structured write-file payload.
    :raises FileSizeLimitError: If the content exceeds the configured limit.
    """
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
            normalised,
            target,
            make_metadata(agent_id, f"Pre-write snapshot for {normalised}"),
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    if history_manager is not None and created:
        history_manager.store_version(
            normalised,
            target,
            make_metadata(agent_id, f"Initial write for {normalised}"),
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
    """
    Description:
        List one directory through the filesystem policy layer.

    Requirements:
        - Enforce permissions and deny-list rules for the requested path.
        - Return stable entry ordering with basic type and size metadata.

    :param mount_registry: Mount registry used for the lookup.
    :param mount_name: Name of the mount to list from.
    :param relative_path: Mount-relative directory path to list.
    :param agent_id: Agent performing the directory listing.
    :param agent_mounts: Mount permissions granted to the agent.
    :returns: Structured directory-listing payload.
    :raises FilesystemError: If the target does not exist or is not a directory.
    """
    _mount, normalised, target = _resolve_target(
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
    """
    Description:
        Return metadata for one file or directory through the filesystem policy
        layer.

    Requirements:
        - Enforce permissions and deny-list rules for the requested path.
        - Return an `exists: false` payload when the target is missing.

    :param mount_registry: Mount registry used for the lookup.
    :param mount_name: Name of the mount to inspect.
    :param relative_path: Mount-relative path to inspect.
    :param agent_id: Agent performing the stat call.
    :param agent_mounts: Mount permissions granted to the agent.
    :returns: Structured stat payload.
    """
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
    """
    Description:
        Delete one file or directory through the filesystem policy layer.

    Requirements:
        - Enforce read-write permissions for the target.
        - Store a pre-delete snapshot when deleting a file and a history manager
          is supplied.

    :param mount_registry: Mount registry used for the lookup.
    :param mount_name: Name of the mount to delete from.
    :param relative_path: Mount-relative path to delete.
    :param agent_id: Agent performing the delete.
    :param agent_mounts: Mount permissions granted to the agent.
    :param history_manager: Optional history manager used to store snapshots.
    :returns: Structured delete payload.
    :raises FilesystemError: If the target does not exist.
    """
    _mount, normalised, target = _resolve_target(
        mount_registry, mount_name, relative_path, "readwrite", agent_id, agent_mounts
    )
    if not target.exists():
        raise FilesystemError("NOT_FOUND", f"'{relative_path}' does not exist")
    if history_manager is not None and target.is_file():
        history_manager.store_version(
            normalised,
            target,
            make_metadata(agent_id, f"Pre-delete snapshot for {normalised}"),
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
    """
    Description:
        Create one directory through the filesystem policy layer.

    Requirements:
        - Enforce read-write permissions for the target path.
        - Report whether the directory was created during this call.

    :param mount_registry: Mount registry used for the lookup.
    :param mount_name: Name of the mount to create the directory in.
    :param relative_path: Mount-relative directory path to create.
    :param agent_id: Agent performing the mkdir operation.
    :param agent_mounts: Mount permissions granted to the agent.
    :returns: Structured mkdir payload.
    """
    _mount, normalised, target = _resolve_target(
        mount_registry, mount_name, relative_path, "readwrite", agent_id, agent_mounts
    )
    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    return {"mount": mount_name, "path": normalised, "created": created}
