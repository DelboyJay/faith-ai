"""Description:
    Verify the FAITH input panel browser contract.

Requirements:
    - Prove the browser shell loads the dedicated input panel asset.
    - Prove the input panel runtime supports send and attachment flows.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from faith_web.app import create_app


def test_index_includes_input_panel_asset() -> None:
    """Description:
        Verify the main Web UI page includes the input panel JavaScript asset.

    Requirements:
        - This test is needed to prove the browser shell can load the dedicated input panel implementation.
        - Verify the root page references the expected asset path.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/")

    assert response.status_code == 200
    assert "/static/js/panels/input-panel.js" in response.text


def test_input_panel_asset_targets_input_and_upload_routes() -> None:
    """Description:
        Verify the input panel asset targets the expected backend routes.

    Requirements:
        - This test is needed to prove the panel posts text and uploads to the supported endpoints.
        - Verify the asset references both `/input` and `/upload`.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/static/js/panels/input-panel.js")

    assert response.status_code == 200
    assert '"/input"' in response.text
    assert '"/upload"' in response.text
    assert "drop" in response.text
    assert "paste" in response.text


def test_input_panel_runtime_behaviour() -> None:
    """Description:
        Verify the host-side input panel runtime checks pass.

    Requirements:
        - This test is needed to prove the panel handles send, validation, and attachment queue actions.
        - Verify the Node.js harness exits successfully.
    """

    project_root = Path(__file__).resolve().parents[1]
    runtime_test = project_root / "tests" / "test_input_panel_runtime.js"
    result = subprocess.run(
        ["node", str(runtime_test)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
