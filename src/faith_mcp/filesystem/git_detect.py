"""
Description:
    Detect whether a path belongs to a Git-managed workspace.

Requirements:
    - Walk up the directory tree until a `.git` directory is found or the file
      system root is reached.
    - Support both file and directory inputs.
"""

from __future__ import annotations

from pathlib import Path


def is_git_managed(path: Path) -> bool:
    """
    Description:
        Determine whether the supplied path sits inside a Git repository.

    Requirements:
        - Start from the directory itself when given a folder, or from the
          parent directory when given a file.
        - Stop cleanly at the file-system root when no repository exists.

    :param path: File or directory that should be checked for Git ancestry.
    :returns: `True` when a parent `.git` directory exists, otherwise `False`.
    """
    resolved = path.resolve()
    check = resolved if resolved.is_dir() else resolved.parent
    while True:
        if (check / ".git").exists():
            return True
        parent = check.parent
        if parent == check:
            return False
        check = parent
