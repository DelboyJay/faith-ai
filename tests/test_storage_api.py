"""Description:
    Verify storage-backed PA HTTP endpoints expose deterministic file lifecycle behavior.

Requirements:
    - Prove the PA can list stored files and accept browser-facing uploads without HTTP 500 errors.
    - Prove session export can bundle linked files when requested.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import faith_pa.pa.app as pa_app_module


@pytest_asyncio.fixture
async def async_client(tmp_path: Path):
    """Description:
        Provide a PA test client backed by one temporary host-persisted project root.

    Requirements:
        - Install isolated session and storage managers after startup so storage tests do not share state.

    :param tmp_path: Temporary project root used for PA runtime state.
    :yields: Async client bound to the PA application.
    """

    app = pa_app_module.app
    app.state.project_agent_session_manager = pa_app_module.SessionManager(project_root=tmp_path)
    app.state.project_agent_storage_registry = pa_app_module.FileStorageRegistry(
        project_root=tmp_path
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_storage_endpoint_lists_uploaded_files(async_client: AsyncClient) -> None:
    """Description:
        Verify the PA exposes a storage inventory endpoint for browser panels.

    Requirements:
        - This test is needed to prove the Storage panel has a request-style happy path.
        - Verify a stored file appears in the inventory response with filename, scope, and SHA-256 metadata.

    :param async_client: Async client for the PA application.
    """

    upload_response = await async_client.post(
        "/api/storage/files",
        files={"file": ("note.txt", b"hello storage\n", "text/plain")},
        data={
            "scope": "global",
            "description": "Greeting note.",
        },
    )
    assert upload_response.status_code == 200

    response = await async_client.get("/api/storage/files")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["filename"] == "note.txt"
    assert payload["items"][0]["scope"] == "global"
    assert payload["items"][0]["description"] == "Greeting note."


@pytest.mark.asyncio
async def test_storage_endpoint_returns_conflict_for_identical_bytes_with_new_filename(
    async_client: AsyncClient,
) -> None:
    """Description:
        Verify the PA storage endpoint returns HTTP 409 when identical bytes arrive with conflicting metadata.

    Requirements:
        - This test is needed to prove browser uploads do not silently replace metadata for existing content.
        - Verify the response identifies the conflicting metadata fields.

    :param async_client: Async client for the PA application.
    """

    first = await async_client.post(
        "/api/storage/files",
        files={"file": ("note.txt", b"same content\n", "text/plain")},
        data={"scope": "session", "description": "Same file."},
    )
    assert first.status_code == 200

    conflict = await async_client.post(
        "/api/storage/files",
        files={"file": ("renamed.txt", b"same content\n", "text/plain")},
        data={"scope": "global", "description": "Same file."},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["conflicts"] == ["filename", "scope"]


@pytest.mark.asyncio
async def test_session_export_endpoint_bundles_linked_files(tmp_path: Path) -> None:
    """Description:
        Verify session export can include linked stored files in a portable zip archive.

    Requirements:
        - This test is needed to prove session exports can bundle durable context alongside metadata.
        - Verify the exported zip contains session metadata and a linked stored file when requested.

    :param tmp_path: Temporary project root used for PA runtime state.
    """

    app = pa_app_module.app
    app.state.project_agent_session_manager = pa_app_module.SessionManager(project_root=tmp_path)
    app.state.project_agent_storage_registry = pa_app_module.FileStorageRegistry(
        project_root=tmp_path
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post("/api/pa/session/new")
        assert session_response.status_code == 200
        session_id = session_response.json()["session_id"]

        storage_response = await client.post(
            "/api/storage/files",
            files={"file": ("linked.txt", b"linked session file\n", "text/plain")},
            data={
                "scope": "session",
                "session_bindings": json.dumps([session_id]),
                "description": "Linked export file.",
            },
        )
        assert storage_response.status_code == 200

        export_response = await client.post(
            f"/api/pa/sessions/{session_id}/export",
            json={"mode": "session_with_linked_files"},
        )

    assert export_response.status_code == 200
    archive_path = Path(export_response.json()["archive_path"])
    assert archive_path.exists()
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()

    assert any(name.endswith("session.meta.json") for name in names)
    assert any(name.endswith("linked.txt") for name in names)
