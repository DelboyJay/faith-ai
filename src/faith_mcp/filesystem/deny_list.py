"""
Description:
    Enforce the built-in deny-list rules for the FAITH filesystem MCP server.

Requirements:
    - Normalise relative paths before matching them.
    - Block sensitive secret-file paths regardless of calling agent.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_EXACT_DENY: set[str] = {
    "config/secrets.yaml",
    "config/.env",
}

_PATTERN_DENY = [
    re.compile(r"(^|/)secrets\.yaml$"),
    re.compile(r"(^|/)\.env$"),
]


def normalize_relative_path(relative_path: str) -> str:
    """
    Description:
        Convert a caller-supplied relative path into the canonical POSIX form
        used by the filesystem policy layer.

    Requirements:
        - Strip any leading slash so policy checks stay mount-relative.
        - Use POSIX separators consistently across platforms.

    :param relative_path: Caller-supplied relative file path.
    :returns: Normalised mount-relative POSIX path.
    """
    return PurePosixPath(relative_path).as_posix().lstrip("/")


def is_denied(relative_path: str) -> bool:
    """
    Description:
        Determine whether a relative path is blocked by the built-in deny-list.

    Requirements:
        - Honour both exact path blocks and regex-based pattern blocks.
        - Evaluate the path after normalisation so equivalent forms match.

    :param relative_path: Caller-supplied relative file path.
    :returns: `True` when the path is blocked, otherwise `False`.
    """
    normalised = normalize_relative_path(relative_path)
    if normalised in _EXACT_DENY:
        return True
    return any(pattern.search(normalised) for pattern in _PATTERN_DENY)
