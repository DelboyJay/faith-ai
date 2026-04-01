from faith.tools.filesystem import FileHistoryManager, make_metadata


def test_history_manager_stores_versions_when_enabled(tmp_path):
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
