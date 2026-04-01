import json

import pytest

from faith.mcp.fulltext_search import FullTextSearchServer, RipgrepRunner


@pytest.mark.asyncio
async def test_search_parses_json_payload(tmp_path, monkeypatch):
    runner = RipgrepRunner(tmp_path)

    async def fake_run(_args):
        return json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": str(tmp_path / "app.py")},
                    "line_number": 3,
                    "lines": {"text": "print('hi')\n"},
                    "submatches": [{"start": 0, "end": 5}],
                },
            }
        )

    monkeypatch.setattr(runner, "_run_rg", fake_run)
    result = await runner.search("print")
    assert result.match_count == 1
    assert result.matches[0].line_number == 3


@pytest.mark.asyncio
async def test_search_files_filters_by_name(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    other = tmp_path / "src" / "notes.txt"
    other.write_text("hello\n", encoding="utf-8")

    runner = RipgrepRunner(tmp_path)

    async def fake_run(_args):
        return f"{target}\n{other}\n"

    monkeypatch.setattr(runner, "_run_rg", fake_run)
    result = await runner.search_files("app")
    assert result.match_count == 1
    assert result.matches[0].path.endswith("app.py")


@pytest.mark.asyncio
async def test_server_returns_json_safe_dict(tmp_path, monkeypatch):
    server = FullTextSearchServer(tmp_path)

    async def fake_search(pattern, *, path=None, ignore_case=False):
        from faith.mcp.fulltext_search.models import SearchResult

        return SearchResult(match_count=0)

    monkeypatch.setattr(server.runner, "search", fake_search)
    payload = await server.search("needle")
    assert payload["match_count"] == 0
    assert payload["matches"] == []
