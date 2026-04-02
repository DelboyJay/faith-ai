"""Description:
Pytest configuration for the FAITH repository.

Requirements:
- Ensure the canonical ``src`` package layout is importable during test runs.
- Keep test imports aligned with the FRS package structure instead of relying
  on legacy root-level package directories.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_src_on_path() -> None:
    """Description:
    Add the repository ``src`` directory to ``sys.path`` for tests.

    Requirements:
    - Insert the path at the front so tests resolve the canonical packages
      before any legacy module locations.
    - Avoid duplicating the entry if it is already present.
    """

    src_path = Path(__file__).resolve().parents[1] / "src"
    src_value = str(src_path)
    if src_value not in sys.path:
        sys.path.insert(0, src_value)


_ensure_src_on_path()
