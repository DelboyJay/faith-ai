"""Description:
    Provide helpers for locating generated shared JSON schemas.

Requirements:
    - Keep schema path discovery inside the shared package.
    - Avoid hard-coded relative paths in callers.
"""

from pathlib import Path


def schema_dir() -> Path:
    """Description:
        Return the directory containing shared JSON schema files.

    Requirements:
        - Resolve relative to this package so callers can run from any cwd.

    :returns: Path to the shared schema directory.
    """

    return Path(__file__).resolve().parent
