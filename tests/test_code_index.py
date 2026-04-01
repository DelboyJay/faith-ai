"""Tests for the FAITH code index POC."""

from __future__ import annotations

import json
from pathlib import Path

from faith.tools.code_index import CodeIndex, CodeIndexServer


def write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_build_index_extracts_symbols_and_ignores_generated_dirs(tmp_path):
    project = tmp_path / "project"
    write_text(
        project / "app.py",
        """
        class CodeIndexer:
            def search(self):
                return "ok"

        def helper_function():
            return CodeIndexer()
        """.strip(),
    )
    write_text(project / "notes.md", "Code index helps the PA find symbols and documentation.")
    write_text(project / ".git" / "ignored.py", "def should_not_be_indexed():\n    pass\n")

    index = CodeIndex.build(project)

    assert index.root == str(project.resolve())
    assert index.find("app.py") is not None
    assert index.find("notes.md") is not None
    assert index.find(".git/ignored.py") is None

    app_doc = index.find("app.py")
    assert app_doc is not None
    assert [symbol.name for symbol in app_doc.symbols] == ["CodeIndexer", "helper_function"]
    assert app_doc.language == "python"


def test_search_ranks_symbol_and_content_matches(tmp_path):
    project = tmp_path / "project"
    write_text(
        project / "faith" / "tools" / "code_index" / "index.py",
        """
        class CodeIndex:
            def search(self, query):
                return query
        """.strip(),
    )
    write_text(
        project / "docs" / "readme.md", "The code index keeps a lightweight searchable catalog."
    )

    index = CodeIndex.build(project)
    hits = index.search("code index", limit=5)

    assert hits
    assert hits[0].relative_path.endswith("index.py")
    assert any("symbol:code" in hit.matches or "symbol:index" in hit.matches for hit in hits)
    assert any(
        "content:searchable" in hit.matches or "content:index" in hit.matches for hit in hits
    )


def test_save_and_load_round_trip(tmp_path):
    project = tmp_path / "project"
    write_text(project / "main.py", "def alpha():\n    return 1\n")
    write_text(project / "README.md", "Alpha project")

    index = CodeIndex.build(project)
    snapshot = tmp_path / "index.json"
    index.save(snapshot)

    loaded = CodeIndex.load(snapshot)
    assert loaded.root == index.root
    assert loaded.documents[0].relative_path in {doc.relative_path for doc in index.documents}
    assert json.loads(snapshot.read_text(encoding="utf-8"))["documents"]
    assert loaded.search("alpha")


def test_search_ignores_binary_and_empty_queries(tmp_path):
    project = tmp_path / "project"
    write_text(project / "main.py", "def alpha():\n    return 1\n")
    (project / "image.bin").parent.mkdir(parents=True, exist_ok=True)
    (project / "image.bin").write_bytes(b"\x00\x01\x02")

    index = CodeIndex.build(project)

    assert index.search("") == []
    assert index.find("image.bin") is None
    assert index.search("alpha")


def test_code_index_server_builds_and_searches(tmp_path):
    (tmp_path / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    server = CodeIndexServer(tmp_path)
    hits = server.search("hello")
    assert len(hits) == 1
    assert hits[0].relative_path == "app.py"
