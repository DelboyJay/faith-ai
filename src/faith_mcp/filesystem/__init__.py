"""Security-first filesystem helpers for the FAITH POC."""

from faith_mcp.filesystem.deny_list import is_denied
from faith_mcp.filesystem.git_detect import is_git_managed
from faith_mcp.filesystem.history import FileHistoryManager, VersionMetadata, make_metadata
from faith_mcp.filesystem.mounts import MountConfig, MountRegistry
from faith_mcp.filesystem.operations import (
    DenyListError,
    FileSizeLimitError,
    FilesystemError,
    PermissionDeniedError,
    delete_file,
    list_directory,
    make_directory,
    read_file,
    stat_file,
    write_file,
)
from faith_mcp.filesystem.permissions import (
    check_permission,
    resolve_effective_permission,
    resolve_mount_permission,
)
from faith_mcp.filesystem.server import FilesystemServer
from faith_mcp.filesystem.symlinks import BrokenSymlinkError, SymlinkEscapeError
from faith_mcp.filesystem.watcher import FileChangeEvent, FileSubscription, FileWatcher

__all__ = [
    "BrokenSymlinkError",
    "DenyListError",
    "FileChangeEvent",
    "FileHistoryManager",
    "FileSizeLimitError",
    "FileSubscription",
    "FileWatcher",
    "FilesystemError",
    "FilesystemServer",
    "MountConfig",
    "MountRegistry",
    "PermissionDeniedError",
    "SymlinkEscapeError",
    "VersionMetadata",
    "check_permission",
    "delete_file",
    "is_denied",
    "is_git_managed",
    "list_directory",
    "make_directory",
    "make_metadata",
    "read_file",
    "resolve_effective_permission",
    "resolve_mount_permission",
    "stat_file",
    "write_file",
]

