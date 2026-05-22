"""Description:
    Verify scoped file-storage ingestion, deduplication, and trash lifecycle.

Requirements:
    - Prove identical content is stored once and keyed by SHA-256.
    - Prove conflicts and one-time cleanup behave deterministically.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from faith_pa.pa.file_storage import FileStorageRegistry, StorageConflictError


def _sha256_bytes(payload: bytes) -> str:
    """Description:
        Return the canonical SHA-256 identifier for one byte payload.

    Requirements:
        - Match the storage-registry identifier format exactly.

    :param payload: File content bytes to hash.
    :returns: Canonical SHA-256 hex digest.
    """

    return hashlib.sha256(payload).hexdigest()


def test_registry_ingests_file_and_reuses_identical_content(tmp_path: Path) -> None:
    """Description:
        Verify the storage registry persists one uploaded file and reuses identical content.

    Requirements:
        - This test is needed to prove Phase 19 stores files by SHA-256 rather than duplicating bytes.
        - Verify the first ingest writes one physical file and a second identical ingest reuses the same record.

    :param tmp_path: Temporary project root used for host-backed storage.
    """

    registry = FileStorageRegistry(project_root=tmp_path)
    payload = b"hello from FAITH storage\n"

    first_record = registry.ingest_bytes(
        filename="notes.txt",
        content=payload,
        scope="session",
        session_bindings=["sess-a"],
        description="First upload.",
    )
    second_record = registry.ingest_bytes(
        filename="notes.txt",
        content=payload,
        scope="session",
        session_bindings=["sess-a"],
        description="First upload.",
    )

    expected_id = _sha256_bytes(payload)
    stored_files = list((tmp_path / ".faith" / "storage" / "files").glob("*"))

    assert first_record["file_id"] == expected_id
    assert second_record["file_id"] == expected_id
    assert first_record["path"] == second_record["path"]
    assert len(stored_files) == 1


def test_registry_rejects_metadata_conflict_for_identical_content(tmp_path: Path) -> None:
    """Description:
        Verify the storage registry raises a conflict when identical content is uploaded with different metadata.

    Requirements:
        - This test is needed to prove identical bytes do not silently change filename, description, or scope.
        - Verify a different filename for the same SHA-256 content forces explicit conflict resolution.

    :param tmp_path: Temporary project root used for host-backed storage.
    """

    registry = FileStorageRegistry(project_root=tmp_path)
    payload = b"same bytes\n"
    registry.ingest_bytes(
        filename="notes.txt",
        content=payload,
        scope="session",
        session_bindings=["sess-a"],
        description="Original record.",
    )

    with pytest.raises(StorageConflictError) as exc_info:
        registry.ingest_bytes(
            filename="renamed.txt",
            content=payload,
            scope="global",
            session_bindings=[],
            description="Original record.",
        )

    assert exc_info.value.file_id == _sha256_bytes(payload)
    assert exc_info.value.conflicts == ["filename", "scope", "session_bindings"]


def test_registry_moves_deleted_file_to_trash_and_restores_it(tmp_path: Path) -> None:
    """Description:
        Verify deleting a stored file moves it into trash and restore brings it back.

    Requirements:
        - This test is needed to prove deletes are reversible during the active run.
        - Verify trashed files disappear from the active inventory and reappear after restore.

    :param tmp_path: Temporary project root used for host-backed storage.
    """

    registry = FileStorageRegistry(project_root=tmp_path)
    record = registry.ingest_bytes(
        filename="notes.txt",
        content=b"trash me\n",
        scope="global",
        session_bindings=[],
        description="Delete candidate.",
    )

    registry.trash_file(record["file_id"])
    active_inventory = registry.list_files()
    trash_inventory = registry.list_trash()

    assert active_inventory == []
    assert len(trash_inventory) == 1
    assert trash_inventory[0]["file_id"] == record["file_id"]

    registry.restore_file(record["file_id"])
    restored_inventory = registry.list_files()

    assert len(restored_inventory) == 1
    assert restored_inventory[0]["file_id"] == record["file_id"]


def test_registry_cleans_up_one_time_files_after_round(tmp_path: Path) -> None:
    """Description:
        Verify one-time files are removed after their inference round finishes.

    Requirements:
        - This test is needed to prove transient uploads do not persist beyond the active turn.
        - Verify cleanup removes both metadata and the stored file bytes for the completed round.

    :param tmp_path: Temporary project root used for host-backed storage.
    """

    registry = FileStorageRegistry(project_root=tmp_path)
    payload = b"one-time data\n"
    record = registry.ingest_bytes(
        filename="one-time.txt",
        content=payload,
        scope="one-time",
        session_bindings=["sess-a"],
        description="Transient file.",
        inference_id="inf-001",
    )

    removed = registry.cleanup_one_time_files("inf-001")
    manifest_path = tmp_path / ".faith" / "storage" / "registry.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert removed == [record["file_id"]]
    assert manifest["files"] == {}
    assert not Path(record["path"]).exists()
