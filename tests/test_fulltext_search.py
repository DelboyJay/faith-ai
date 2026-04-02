"""
Description:
    Verify the full-text search helpers parse ripgrep output and expose
    JSON-safe server payloads.

Requirements:
    - Cover regex search parsing, file-name filtering, and server payload
      conversion.
    - Verify the tests exercise the public runner and server interfaces.
"""

from __future__ import annotations

import json

import pytest

from faith_mcp.fulltext_search import FullTextSearchServer, RipgrepRunner
from faith_mcp.fulltext_search.models import SearchResult


@pytest.mark.asyncio
async def test_search_parses_json_payload(tmp_path, monkeypatch) -> None:
    """
    Description:
        Verify the ripgrep runner parses JSON match output into structured
        search matches.

    Requirements:
        - This test is needed to prove ripgrep JSON records become typed search
          results.
        - Verify the parsed line number matches the emitted ripgrep payload.

    :param tmp_path: Temporary directory provided by pytest.
    :param monkeypatch: Pytest monkeypatch fixture.
    """
    runner = RipgrepRunner(tmp_path)

    async def fake_run(_args):
        """
        Description:
            Return one synthetic ripgrep JSON match payload.

        Requirements:
            - Mimic one valid ripgrep JSON match record.
        """
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
async def test_search_files_filters_by_name(tmp_path, monkeypatch) -> None:
    """
    Description:
        Verify file-name searches return only files whose names match the
        requested pattern.

    Requirements:
        - This test is needed to prove file-only searches do not return
          unrelated files.
        - Verify only the matching file path is returned.

    :param tmp_path: Temporary directory provided by pytest.
    :param monkeypatch: Pytest monkeypatch fixture.
    """
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "app.py"
    target.write_text("print('hi')\n", encoding="utf-8")
    other = tmp_path / "src" / "notes.txt"
    other.write_text("hello\n", encoding="utf-8")

    runner = RipgrepRunner(tmp_path)

    async def fake_run(_args):
        """
        Description:
            Return synthetic ripgrep file-list output.

        Requirements:
            - Mimic the newline-delimited output returned by `rg --files`.
        """
        return f"{target}\n{other}\n"

    monkeypatch.setattr(runner, "_run_rg", fake_run)
    result = await runner.search_files("app")
    assert result.match_count == 1
    assert result.matches[0].path.endswith("app.py")


@pytest.mark.asyncio
async def test_server_returns_json_safe_dict(tmp_path, monkeypatch) -> None:
    """
    Description:
        Verify the server facade converts search results into plain dictionaries.

    Requirements:
        - This test is needed to prove the MCP-facing server facade returns a
          JSON-safe payload.
        - Verify an empty search result becomes the expected dictionary shape.

    :param tmp_path: Temporary directory provided by pytest.
    :param monkeypatch: Pytest monkeypatch fixture.
    """
    server = FullTextSearchServer(tmp_path)

    async def fake_search(pattern, *, path=None, ignore_case=False):
        """
        Description:
            Return a minimal structured search result.

        Requirements:
            - Mimic the runner's structured search result return type.

        :param pattern: Search pattern supplied by the server.
        :param path: Optional path filter supplied by the server.
        :param ignore_case: Case-sensitivity flag supplied by the server.
        :returns: Empty structured search result.
        """
        return SearchResult(match_count=0)

    monkeypatch.setattr(server.runner, "search", fake_search)
    payload = await server.search("needle")
    assert payload["match_count"] == 0
    assert payload["matches"] == []
