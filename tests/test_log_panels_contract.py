"""Description:
    Verify the FAITH log-view panel browser contract.

Requirements:
    - Prove the browser shell loads the dedicated log-view assets.
    - Prove the bundled workspace exposes the expected add-panel options for log views.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from faith_web.app import create_app


def test_index_includes_log_panel_assets() -> None:
    """Description:
        Verify the main Web UI page includes the log-view JavaScript assets.

    Requirements:
        - This test is needed to prove the browser shell can load the dedicated log-view implementations.
        - Verify the root page references the shared helper plus each specific log panel asset.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/")

    assert response.status_code == 200
    assert "/static/js/panels/log-panel-common.js" in response.text
    assert "/static/js/panels/audit-trail.js" in response.text
    assert "/static/js/panels/event-timeline.js" in response.text
    assert "/static/js/panels/session-history.js" in response.text
    assert "/static/js/panels/token-usage.js" in response.text
    assert "/static/js/panels/approval-history.js" in response.text
    assert "/static/js/panels/effective-context-panel.js" in response.text


def test_toolbar_lists_log_view_panels() -> None:
    """Description:
        Verify the React Dockview shell exposes the log-view panel options.

    Requirements:
        - This test is needed to prove users can open the log views from the add-panel menu.
        - Verify the bundled shell includes Audit Trail, Event Timeline, Session History, Token Usage, and Approval History.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/static/dist/faith-ui.js")

    assert response.status_code == 200
    assert '"Audit Trail"' in response.text
    assert '"Event Timeline"' in response.text
    assert '"Session History"' in response.text
    assert '"Token Usage"' in response.text
    assert '"Approval History"' in response.text
    source = Path("web/src/main.jsx").read_text(encoding="utf-8")
    assert '"Effective Context"' in source
