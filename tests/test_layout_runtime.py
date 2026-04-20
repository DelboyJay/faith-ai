"""Description:
    Execute the host-side JavaScript runtime checks for the FAITH panel workspace.

Requirements:
    - Verify the shared layout runtime enforces dedupe and close/reopen state behaviour.
    - Verify the JavaScript checks run successfully through the host Node.js runtime.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_layout_runtime_behaviour() -> None:
    """Description:
        Verify the host-side layout runtime checks pass.

    Requirements:
        - This test is needed to prove panel dedupe and removal logic work in the JavaScript runtime, not only as static text.
        - Verify the Node.js layout harness exits successfully.
    """

    project_root = Path(__file__).resolve().parents[1]
    runtime_test = project_root / "tests" / "test_layout_runtime.js"
    result = subprocess.run(
        ["node", str(runtime_test)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
