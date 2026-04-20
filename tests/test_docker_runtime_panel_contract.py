"""Description:
    Verify the FAITH Docker runtime panel contract across the Web UI assets.

Requirements:
    - Prove the dedicated Docker runtime panel script is served to the browser shell.
    - Prove the layout registry exposes a Docker Runtime panel entry and backend route hooks.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from faith_web.app import create_app


def test_index_includes_docker_runtime_panel_asset() -> None:
    """Description:
    Verify the main Web UI page includes the Docker runtime panel JavaScript asset.

    Requirements:
    - This test is needed to prove the browser shell can load the dedicated Docker Runtime panel implementation.
    - Verify the root page references the expected panel asset path.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/")

    assert response.status_code == 200
    assert "/static/js/panels/docker-runtime-panel.js" in response.text


def test_layout_asset_registers_docker_runtime_panel() -> None:
    """Description:
    Verify the layout asset defines a dedicated Docker Runtime panel type.

    Requirements:
    - This test is needed to prove the panel registry exposes Docker Runtime as a distinct panel.
    - Verify the layout asset includes the Docker panel component identifier and menu label.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/static/js/layout.js")

    assert response.status_code == 200
    assert 'DOCKER_RUNTIME: "docker-runtime-panel"' in response.text
    assert 'label: "Docker Runtime Panel"' in response.text


def test_docker_runtime_panel_asset_targets_runtime_routes() -> None:
    """Description:
    Verify the Docker runtime panel asset targets the dedicated runtime routes.

    Requirements:
    - This test is needed to prove the panel consumes the dedicated runtime API instead of the generic status endpoint.
    - Verify the asset references both the HTTP fallback and WebSocket feed paths.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/static/js/panels/docker-runtime-panel.js")

    assert response.status_code == 200
    assert '"/api/docker-runtime"' in response.text
    assert '"/ws/docker"' in response.text


def test_docker_runtime_panel_asset_renders_image_inventory() -> None:
    """Description:
    Verify the Docker runtime panel asset includes an image-inventory rendering path.

    Requirements:
    - This test is needed to prove the panel can show the image inventory separately from container cards.
    - Verify the asset includes the dedicated image inventory CSS hook.
    """

    client = TestClient(create_app(testing=True))

    response = client.get("/static/js/panels/docker-runtime-panel.js")

    assert response.status_code == 200
    assert "faith-runtime-images" in response.text
