"""Description:
    Verify the FAITH Project Agent system prompt editor panel contract.

Requirements:
    - Prove the browser shell loads the dedicated PA project-instructions panel asset.
    - Prove the host-side prompt panel runtime can load, edit, save, reload, and reset AGENTS.md-backed instruction state.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from faith_web.app import create_app


def test_index_includes_pa_system_prompt_panel_asset() -> None:
    """Description:
        Verify the main Web UI page includes the PA project-instructions panel JavaScript asset.

    Requirements:
        - This test is needed to prove the browser shell can load the dedicated project-instruction editor implementation.
        - Verify the root page references the expected asset path.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/")

    assert response.status_code == 200
    assert "/static/js/panels/pa-system-prompt-panel.js" in response.text


def test_pa_system_prompt_panel_runtime_behaviour() -> None:
    """Description:
        Verify the host-side PA project-instructions panel runtime checks pass.

    Requirements:
        - This test is needed to prove AGENTS.md loading, dirty tracking, save, reload, and reset work together.
        - Verify the Node.js harness exits successfully.
    """

    project_root = Path(__file__).resolve().parents[1]
    runtime_test = project_root / "tests" / "test_pa_system_prompt_panel_runtime.js"
    result = subprocess.run(
        ["node", str(runtime_test)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
