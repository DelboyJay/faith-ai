"""Description:
    Verify the FAITH effective-context panel browser contract.

Requirements:
    - Prove the browser shell loads the dedicated effective-context panel asset.
    - Prove the panel runtime can render persisted redacted snapshots.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from faith_web.app import create_app


def test_index_includes_effective_context_panel_asset() -> None:
    """Description:
        Verify the main Web UI page includes the effective-context panel JavaScript asset.

    Requirements:
        - This test is needed to prove the browser shell can load the dedicated inspection panel implementation.
        - Verify the root page references the expected asset path.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/")

    assert response.status_code == 200
    assert "/static/js/panels/effective-context-panel.js" in response.text


def test_effective_context_panel_runtime_behaviour() -> None:
    """Description:
        Verify the host-side effective-context panel runtime checks pass.

    Requirements:
        - This test is needed to prove the panel can fetch and render redacted snapshot payloads.
        - Verify the Node.js harness exits successfully.
    """

    project_root = Path(__file__).resolve().parents[1]
    runtime_test = project_root / "tests" / "test_effective_context_panel_runtime.js"
    result = subprocess.run(
        ["node", str(runtime_test)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
