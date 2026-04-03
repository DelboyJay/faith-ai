"""
Description:
    Verify filesystem history snapshots are stored and listed correctly.

Requirements:
    - Cover basic history storage for enabled mounts.
    - Verify stored metadata includes the acting agent.
"""

from faith_mcp.filesystem.history import FileHistoryManager, make_metadata


def test_history_manager_stores_versions_when_enabled(tmp_path) -> None:
    """
    Description:
        Verify the history manager stores a snapshot and lists its metadata when
        history is enabled.

    Requirements:
        - This test is needed to prove version history is actually persisted for
          enabled mounts.
        - Verify the stored history entry preserves the acting agent metadata.

    :param tmp_path: Temporary directory provided by pytest.
    """
    source = tmp_path / "workspace"
    source.mkdir()
    file_path = source / "doc.txt"
    file_path.write_text("v1", encoding="utf-8")

    manager = FileHistoryManager("workspace", tmp_path / ".faith", source, depth=3, enabled=True)
    slot = manager.store_version("doc.txt", file_path, make_metadata("dev", "first"))
    assert slot == 1
    history = manager.list_history("doc.txt")
    assert len(history) == 1
    assert history[0]["agent"] == "dev"


def test_history_restore_creates_new_history_entry(tmp_path) -> None:
    """
    Description:
        Verify restoring a stored version writes the restored content and records
        a fresh history entry.

    Requirements:
        - This test is needed to prove history restoration is append-only rather than destructive.
        - Verify restoring an older version creates a new stored snapshot.

    :param tmp_path: Temporary directory provided by pytest.
    """

    source = tmp_path / "workspace"
    source.mkdir()
    file_path = source / "doc.txt"
    file_path.write_text("v1", encoding="utf-8")

    manager = FileHistoryManager("workspace", tmp_path / ".faith", source, depth=5, enabled=True)
    manager.store_version("doc.txt", file_path, make_metadata("dev", "first"))
    file_path.write_text("v2", encoding="utf-8")
    manager.store_version("doc.txt", file_path, make_metadata("dev", "second"))

    restored = manager.restore_version("doc.txt", 1, file_path, make_metadata("dev", "restore"))
    assert restored is True
    assert file_path.read_text(encoding="utf-8") == "v1"
    history = manager.list_history("doc.txt")
    assert len(history) == 3
