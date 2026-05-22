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
import zipfile
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


def write_zip(path: Path, members: dict[str, str]) -> Path:
    """
    Description:
        Write a tiny zip-based office-style fixture file.

    Requirements:
        - Create parent directories before writing the archive.
        - Encode every member as UTF-8 text for deterministic parser tests.

    :param path: Archive path to write.
    :param members: Mapping of archive member path to UTF-8 text payload.
    :returns: Written archive path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member_name, content in members.items():
            archive.writestr(member_name, textwrap.dedent(content).strip())
    return path


def write_fake_pdf(path: Path, literal_text: str) -> Path:
    """
    Description:
        Write a tiny PDF-like fixture whose literal text can be extracted deterministically.

    Requirements:
        - Preserve the supplied text inside PDF string delimiters so the lightweight PDF extractor can recover it.

    :param path: PDF path to write.
    :param literal_text: Literal text chunk to embed.
    :returns: Written PDF path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        textwrap.dedent(
            f"""
            %PDF-1.4
            1 0 obj
            << /Type /Catalog >>
            endobj
            stream
            BT
            ({literal_text})
            ET
            endstream
            """
        ).encode("latin-1")
    )
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
    write_text(root / "docs" / "data.xml", "<root><item>needle in xml</item></root>\n")
    write_text(root / "web" / "page.html", "<html><body>needle</body></html>\n")
    write_zip(
        root / "docs" / "report.docx",
        {
            "word/document.xml": """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>needle in docx</w:t></w:r></w:p>
              </w:body>
            </w:document>
            """,
        },
    )
    write_zip(
        root / "docs" / "sheet.xlsx",
        {
            "xl/sharedStrings.xml": """
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>needle in xlsx</t></si>
            </sst>
            """,
            "xl/worksheets/sheet1.xml": """
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1"><c r="A1" t="s"><v>0</v></c></row>
              </sheetData>
            </worksheet>
            """,
        },
    )
    write_zip(
        root / "docs" / "notes.odt",
        {
            "content.xml": """
            <office:document-content
              xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
              xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
              <office:body>
                <office:text>
                  <text:p>needle in odt</text:p>
                </office:text>
              </office:body>
            </office:document-content>
            """,
        },
    )
    write_zip(
        root / "docs" / "cells.ods",
        {
            "content.xml": """
            <office:document-content
              xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
              xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
              xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
              <office:body>
                <office:spreadsheet>
                  <table:table>
                    <table:table-row>
                      <table:table-cell><text:p>needle in ods</text:p></table:table-cell>
                    </table:table-row>
                  </table:table>
                </office:spreadsheet>
              </office:body>
            </office:document-content>
            """,
        },
    )
    write_fake_pdf(root / "docs" / "paper.pdf", "needle in pdf")
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
async def test_excerpt_resolver_maps_file_groups_and_block_types(workspace: Path) -> None:
    """
    Description:
        Verify the excerpt resolver classifies files into deterministic file
        groups and advertises the block types each group supports.

    Requirements:
        - This test is needed to prove the new resolver framework does not
          guess or silently downgrade unsupported block boundaries.
        - Verify representative document, code, config/data, and markup files
          resolve to the expected groups.

    :param workspace: Workspace root fixture.
    """
    runner = RipgrepRunner(workspace)

    assert runner.resolve_file_group(workspace / "docs" / "README.md") == "document"
    assert runner.resolve_file_group(workspace / "src" / "app.py") == "code"
    assert runner.resolve_file_group(workspace / "src" / "config.yaml") == "config_data"
    assert runner.resolve_file_group(workspace / "web" / "page.html") == "markup"
    assert runner.supported_block_types(workspace / "src" / "app.py") == [
        "line",
        "module",
        "class",
        "function",
    ]
    assert runner.supported_block_types(workspace / "docs" / "README.md") == [
        "line",
        "sentence",
        "paragraph",
        "section",
    ]


@pytest.mark.asyncio
async def test_excerpt_discovery_returns_compact_stable_references(workspace: Path) -> None:
    """
    Description:
        Verify discovery returns a compact per-file summary with stable
        references instead of the full matching text.

    Requirements:
        - This test is needed to prove the discovery step stays low-token and
          exposes deterministic references for follow-up retrieval.
        - Verify the payload includes match counts, file groups, and stable
          excerpt references for the matching code file.

    :param workspace: Workspace root fixture.
    """
    server = FullTextSearchServer(workspace)
    payload = await server.discover_excerpts(
        "needle",
        paths=["src/app.py", "docs/README.md", "src/config.yaml"],
    )

    assert payload["error"] is None
    assert payload["files"]

    app_summary = next(item for item in payload["files"] if item["path"].endswith("app.py"))
    assert app_summary["file_group"] == "code"
    assert "function" in app_summary["supported_block_types"]
    assert app_summary["match_count"] == 2
    assert app_summary["block_type_counts"]["function"] == 2
    assert app_summary["matches"][0]["reference"]
    assert "needle" not in app_summary["matches"][0]


@pytest.mark.asyncio
async def test_excerpt_retrieval_returns_requested_text_block(workspace: Path) -> None:
    """
    Description:
        Verify retrieval resolves a stable reference into the exact excerpt
        text block that was discovered earlier.

    Requirements:
        - This test is needed to prove the retrieval step returns the actual
          text payload without broad file reads.
        - Verify a discovered reference can be round-tripped back to the
          original function text from the code file.

    :param workspace: Workspace root fixture.
    """
    server = FullTextSearchServer(workspace)
    discovered = await server.discover_excerpts("needle", paths=["src/app.py"])
    reference = discovered["files"][0]["matches"][0]["reference"]

    payload = await server.retrieve_excerpts([reference])

    assert payload["error"] is None
    assert payload["blocks"][0]["reference"] == reference
    assert payload["blocks"][0]["block_type"] == "function"
    assert 'return "needle"' in payload["blocks"][0]["text"]


@pytest.mark.asyncio
async def test_excerpt_discovery_rejects_unsupported_block_types(workspace: Path) -> None:
    """
    Description:
        Verify unsupported excerpt boundary requests fail clearly instead of
        falling back to another block type.

    Requirements:
        - This test is needed to prove the file-group resolver enforces the
          supported block-type contract.
        - Verify a code file rejects a document-style paragraph request.

    :param workspace: Workspace root fixture.
    """
    server = FullTextSearchServer(workspace)
    payload = await server.discover_excerpts(
        "needle",
        paths=["src/app.py"],
        block_types=["paragraph"],
    )

    assert payload["error"] is not None
    assert "unsupported" in payload["error"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("relative_path", "expected_text"),
    [
        ("docs/report.docx", "needle in docx"),
        ("docs/sheet.xlsx", "needle in xlsx"),
        ("docs/notes.odt", "needle in odt"),
        ("docs/cells.ods", "needle in ods"),
        ("docs/paper.pdf", "needle in pdf"),
        ("docs/data.xml", "needle in xml"),
    ],
)
async def test_excerpt_retrieval_supports_multi_format_document_files(
    workspace: Path,
    relative_path: str,
    expected_text: str,
) -> None:
    """
    Description:
        Verify deterministic excerpt discovery and retrieval work for multi-format document files.

    Requirements:
        - This test is needed to prove the excerpt tool can inspect office, PDF, and markup-style document files without broad full-file reads.
        - Verify discovery returns a stable reference and retrieval returns the expected literal text for each supported format.

    :param workspace: Workspace root fixture.
    :param relative_path: Workspace-relative document path under test.
    :param expected_text: Literal excerpt text expected from retrieval.
    """
    server = FullTextSearchServer(workspace)
    discovered = await server.discover_excerpts(
        "needle",
        paths=[relative_path],
        block_types=["line"],
    )

    assert discovered["error"] is None
    assert discovered["files"][0]["matches"]
    reference = discovered["files"][0]["matches"][0]["reference"]

    payload = await server.retrieve_excerpts([reference])

    assert payload["error"] is None
    assert expected_text in payload["blocks"][0]["text"]


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
