"""Description:
    Verify the FAITH agent panel browser contract.

Requirements:
    - Prove the browser shell loads the dedicated agent panel asset.
    - Prove the layout registry can mount the agent panel implementation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from faith_web.app import create_app


def test_index_includes_agent_panel_asset() -> None:
    """Description:
        Verify the main Web UI page includes the agent panel JavaScript asset.

    Requirements:
        - This test is needed to prove the browser shell can load the dedicated agent panel implementation.
        - Verify the root page references the expected asset path.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/")

    assert response.status_code == 200
    assert "/static/js/panels/agent-panel.js" in response.text


def test_agent_panel_asset_targets_agent_websocket() -> None:
    """Description:
        Verify the agent panel asset targets the dedicated agent WebSocket route.

    Requirements:
        - This test is needed to prove the panel consumes the expected backend stream.
        - Verify the asset references the agent WebSocket path and local action labels.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/static/js/panels/agent-panel.js")

    assert response.status_code == 200
    assert '"/ws/agent/"' in response.text
    assert '"Pause"' in response.text
    assert '"Copy"' in response.text
    assert '"Pin"' in response.text


def test_agent_panel_runtime_behaviour() -> None:
    """Description:
        Verify the host-side agent panel runtime checks pass.

    Requirements:
        - This test is needed to prove the panel runtime handles streamed messages and local actions.
        - Verify the Node.js harness exits successfully.
    """

    project_root = Path(__file__).resolve().parents[1]
    runtime_test = project_root / "tests" / "test_agent_panel_runtime.js"
    result = subprocess.run(
        ["node", str(runtime_test)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
