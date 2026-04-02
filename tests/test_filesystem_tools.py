"""
Description:
    Verify the lower-level filesystem tools behave correctly for mount
    resolution, read/write operations, and history storage.

Requirements:
    - Cover mount resolution, file round-tripping, and history snapshots.
    - Verify the returned payloads preserve expected metadata.
"""

from faith_mcp.filesystem.history import FileHistoryManager, make_metadata
from faith_mcp.filesystem.mounts import MountConfig, MountRegistry
from faith_mcp.filesystem.operations import read_file, write_file


def test_mount_registry_resolves_registered_mount(tmp_path) -> None:
    """
    Description:
        Verify the mount registry resolves a registered mount-relative path onto
        the expected host path.

    Requirements:
        - This test is needed to prove named mounts are translated correctly
          before filesystem operations run.
        - Verify the resolved path is rooted under the registered host path.

    :param tmp_path: Temporary directory provided by pytest.
    """
    registry = MountRegistry()
    registry.register(MountConfig(name="workspace", host_path=tmp_path, access="readwrite"))
    resolved = registry.resolve_path("workspace", "src/app.py")
    assert resolved == tmp_path / "src/app.py"


def test_write_and_read_file_round_trip(tmp_path) -> None:
    """
    Description:
        Verify the raw filesystem operations can write and then read the same
        file successfully.

    Requirements:
        - This test is needed to prove the core read and write helpers work on a
          read-write mount.
        - Verify the write reports creation and the read returns the stored
          content.

    :param tmp_path: Temporary directory provided by pytest.
    """
    registry = MountRegistry()
    registry.register(
        MountConfig(name="workspace", host_path=tmp_path, access="readwrite", history=True)
    )
    result = write_file(
        registry,
        "workspace",
        "notes.txt",
        "hello",
        "dev",
        {"workspace": "readwrite"},
    )
    assert result["created"] is True
    read_back = read_file(registry, "workspace", "notes.txt", "dev", {"workspace": "readwrite"})
    assert read_back["content"] == "hello"


def test_history_manager_stores_versions_when_enabled(tmp_path) -> None:
    """
    Description:
        Verify the history manager stores a snapshot and lists its metadata when
        history is enabled.

    Requirements:
        - This test is needed to prove lower-level history storage works even
          outside the server facade.
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
