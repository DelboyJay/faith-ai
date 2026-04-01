"""Security-first filesystem helpers for the FAITH POC."""

from faith.tools.filesystem.deny_list import is_denied
from faith.tools.filesystem.git_detect import is_git_managed
from faith.tools.filesystem.history import FileHistoryManager, VersionMetadata, make_metadata
from faith.tools.filesystem.mounts import MountConfig, MountRegistry
from faith.tools.filesystem.operations import (
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
from faith.tools.filesystem.permissions import (
    check_permission,
    resolve_effective_permission,
    resolve_mount_permission,
)
from faith.tools.filesystem.server import FilesystemServer
from faith.tools.filesystem.symlinks import BrokenSymlinkError, SymlinkEscapeError
from faith.tools.filesystem.watcher import FileChangeEvent, FileSubscription, FileWatcher

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
