"""Description:
    Verify the FAITH model-settings panel browser contract.

Requirements:
    - Prove the browser shell loads the dedicated model-settings panel asset.
    - Prove the host-side model-settings runtime can load, edit, save, and reload persisted settings.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from faith_web.app import create_app


def test_index_includes_model_settings_panel_asset() -> None:
    """Description:
        Verify the main Web UI page includes the model-settings panel JavaScript asset.

    Requirements:
        - This test is needed to prove the browser shell can load the dedicated model-settings implementation.
        - Verify the root page references the expected asset path.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/")

    assert response.status_code == 200
    assert "/static/js/panels/model-settings-panel.js" in response.text


def test_model_settings_panel_runtime_behaviour() -> None:
    """Description:
        Verify the host-side model-settings panel runtime checks pass.

    Requirements:
        - This test is needed to prove the panel can fetch and render persisted model-settings payloads.
        - Verify the Node.js harness exits successfully.
    """

    project_root = Path(__file__).resolve().parents[1]
    runtime_test = project_root / "tests" / "test_model_settings_panel_runtime.js"
    result = subprocess.run(
        ["node", str(runtime_test)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
