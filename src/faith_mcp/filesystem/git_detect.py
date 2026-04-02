"""Git repository detection for filesystem history auto-skip."""

from __future__ import annotations

from pathlib import Path


def is_git_managed(path: Path) -> bool:
    resolved = path.resolve()
    check = resolved if resolved.is_dir() else resolved.parent
    while True:
        if (check / ".git").exists():
            return True
        parent = check.parent
        if parent == check:
            return False
        check = parent
