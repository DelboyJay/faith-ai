"""Hardcoded deny-list for filesystem access."""

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
    return PurePosixPath(relative_path).as_posix().lstrip("/")


def is_denied(relative_path: str) -> bool:
    normalised = normalize_relative_path(relative_path)
    if normalised in _EXACT_DENY:
        return True
    return any(pattern.search(normalised) for pattern in _PATTERN_DENY)
