"""
Description:
    Verify the full-text search MCP package uses ripgrep safely inside the
    workspace boundary.

Requirements:
    - Prove regex, literal, and filename searches return structured results.
    - Prove path validation and missing-binary handling stay inside the
      workspace boundary and do not raise unhandled crashes.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from faith_mcp.fulltext_search import FullTextSearchServer, RipgrepRunner


def write_text(path: Path, content: str) -> Path:
    """
    Description:
        Write a text fixture used by the full-text search tests.

    Requirements:
        - Create parent directories before writing the file.
        - Return the path so callers can keep building the workspace.

    :param path: File path to write.
    :param content: Text content to store in the file.
    :returns: Written file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """
    Description:
        Create a small workspace with content that exercises regex, literal,
        and file searches.

    Requirements:
        - Include repeated text to verify truncation.
        - Include files whose names match filename globs.

    :param tmp_path: Temporary directory provided by pytest.
    :returns: Workspace root for search tests.
    """
    root = tmp_path / "workspace"
    write_text(
        root / "src" / "app.py",
        """
        def one():
            return "needle"


        def two():
            return "needle"
        """.strip(),
    )
    write_text(root / "src" / "config.yaml", "value: needle\nliteral: a+b*c\n")
    write_text(root / "docs" / "README.md", "needle appears in docs.\n")
    write_text(root / "docs" / "notes.txt", "hello world\n")
    write_text(root / "nested" / "sub" / "Dockerfile.prod", "FROM python:3.12-slim\n")
    return root


@pytest.mark.asyncio
async def test_search_regex_returns_absolute_paths(workspace: Path) -> None:
    """
    Description:
        Verify regex searches return structured line hits inside the workspace.

    Requirements:
        - This test is needed to prove the server can search arbitrary text
          without loading whole files.
        - Verify the returned hit points at the expected file and line text.

    :param workspace: Workspace root fixture.
    """
    runner = RipgrepRunner(workspace)
    result = await runner.search("needle")

    assert result.error is None
    assert result.match_count >= 3
    assert all(Path(match.path).is_absolute() for match in result.matches)
    assert any(match.line_text == 'return "needle"' for match in result.matches)


@pytest.mark.asyncio
async def test_search_literal_treats_metacharacters_as_text(workspace: Path) -> None:
    """
    Description:
        Verify literal searches treat regex metacharacters as plain text.

    Requirements:
        - This test is needed to prove the literal command does not interpret
          regex operators.
        - Verify the literal search can find text containing plus and star
          characters.

    :param workspace: Workspace root fixture.
    """
    runner = RipgrepRunner(workspace)
    result = await runner.search_literal("a+b*c")

    assert result.error is None
    assert result.match_count == 1
    assert result.matches[0].line_text == "literal: a+b*c"


@pytest.mark.asyncio
async def test_search_files_returns_absolute_paths_and_sizes(workspace: Path) -> None:
    """
    Description:
        Verify filename searches return file paths and file sizes.

    Requirements:
        - This test is needed to prove file-name searches stay within the
          workspace and surface metadata for matching files.
        - Verify the Dockerfile match includes its byte size.

    :param workspace: Workspace root fixture.
    """
    runner = RipgrepRunner(workspace)
    result = await runner.search_files("Dockerfile*")

    assert result.error is None
    assert result.match_count == 1
    match = result.matches[0]
    assert Path(match.path).is_absolute()
    assert match.path.endswith("Dockerfile.prod")
    assert match.size_bytes is not None


@pytest.mark.asyncio
async def test_search_rejects_paths_outside_workspace(workspace: Path) -> None:
    """
    Description:
        Verify path validation blocks traversal outside the workspace.

    Requirements:
        - This test is needed to prove the runner refuses to search outside
          the workspace boundary.
        - Verify the returned error points to the workspace restriction.

    :param workspace: Workspace root fixture.
    """
    runner = RipgrepRunner(workspace)
    result = await runner.search("needle", path="../../outside")

    assert result.error is not None
    assert "outside the workspace root" in result.error


@pytest.mark.asyncio
async def test_missing_rg_binary_becomes_error_result(workspace: Path) -> None:
    """
    Description:
        Verify a missing ripgrep binary is converted into a structured error
        result instead of an unhandled crash.

    Requirements:
        - This test is needed to prove the runner handles missing binaries
          gracefully.
        - Verify the returned error message mentions the missing executable.

    :param workspace: Workspace root fixture.
    """
    runner = RipgrepRunner(workspace, rg_binary="rg-that-does-not-exist")
    result = await runner.search("needle")

    assert result.error is not None
    assert "ripgrep binary" in result.error


@pytest.mark.asyncio
async def test_server_returns_plain_dict_payloads(workspace: Path) -> None:
    """
    Description:
        Verify the server facade returns JSON-safe dictionaries for MCP use.

    Requirements:
        - This test is needed to prove the public server wrapper forwards the
          runner output unchanged into plain data structures.
        - Verify the response shape matches the structured search result.

    :param workspace: Workspace root fixture.
    """
    if shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")

    server = FullTextSearchServer(workspace)
    payload = await server.search_literal("a+b*c")

    assert payload["match_count"] == 1
    assert payload["matches"][0]["line_text"] == "literal: a+b*c"
