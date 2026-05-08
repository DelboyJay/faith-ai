"""
Description:
    Verify the code-index MCP package exposes AST-style symbol navigation for
    the workspace.

Requirements:
    - Prove supported source files are indexed and skipped directories stay
      out of the index.
    - Verify file, symbol, function, search, and description queries return the
      structured data needed by the task brief.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from faith_mcp.code_index import CodeIndex, CodeIndexServer, FileWatcher


def write_text(path: Path, content: str) -> Path:
    """
    Description:
        Write a text file for code-index test fixtures.

    Requirements:
        - Create parent directories before writing the file.
        - Return the path so callers can chain fixture creation.

    :param path: File path to write.
    :param content: Text content to store in the file.
    :returns: Written file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
    return path


def build_workspace(tmp_path: Path) -> Path:
    """
    Description:
        Create a workspace that exercises the supported language set.

    Requirements:
        - Include Python, JavaScript, TypeScript, Java, and Go source files.
        - Include an ignored directory so the skip logic is tested too.

    :param tmp_path: Temporary directory provided by pytest.
    :returns: Workspace root containing fixture files.
    """
    project = tmp_path / "project"

    write_text(
        project / "src" / "python" / "auth.py",
        """
        class TokenManager:
            \"\"\"Manage tokens for the session.\"\"\"

            def create_token(self, subject: str) -> str:
                \"\"\"Create a compact token for one subject.\"\"\"
                return subject.upper()


        def hash_password(password: str) -> str:
            \"\"\"Hash a password for storage.\"\"\"
            return password[::-1]
        """.strip(),
    )
    write_text(
        project / "src" / "web" / "client.js",
        """
        export function formatDate(value) {
            return value.toISOString();
        }

        class Logger {
            log(message) {
                console.log(message);
            }
        }
        """.strip(),
    )
    write_text(
        project / "src" / "web" / "view.tsx",
        """
        export function buildTitle(name: string) {
            return name.trim();
        }
        """.strip(),
    )
    write_text(
        project / "src" / "java" / "Main.java",
        """
        public class Main {
            public static String greet(String name) {
                return name.trim();
            }
        }
        """.strip(),
    )
    write_text(
        project / "src" / "go" / "service.go",
        """
        package service

        type Cache struct {}

        func (c *Cache) Put(key string) {}

        func BuildName(value string) string {
            return value
        }
        """.strip(),
    )
    write_text(project / "docs" / "notes.md", "The code index should ignore documentation.")
    write_text(
        project / "node_modules" / "ignored.js",
        "function shouldNotAppear() { return true; }",
    )

    return project


def test_code_index_supports_symbol_navigation(tmp_path: Path) -> None:
    """
    Description:
        Verify the code index extracts symbols, supports file and directory
        queries, and keeps ignored directories out of the result set.

    Requirements:
        - This test is needed to prove the index can answer navigation queries
          without loading whole files into context.
        - Verify the returned symbols include signatures, docstrings, and line
          ranges for the indexed file and module query.

    :param tmp_path: Temporary directory provided by pytest.
    """
    project = build_workspace(tmp_path)
    index = CodeIndex.build(project)

    files = index.list_files()
    paths = [item.path for item in files]
    assert "node_modules/ignored.js" not in paths
    assert "src/python/auth.py" in paths
    assert "src/web/client.js" in paths

    python_symbols = index.list_symbols("src/python/auth.py")
    python_names = [symbol.name for symbol in python_symbols]
    assert python_names[:2] == ["TokenManager", "create_token"]
    assert "hash_password" in python_names

    auth_class = next(symbol for symbol in python_symbols if symbol.name == "TokenManager")
    assert auth_class.signature.startswith("class TokenManager")
    assert "Manage tokens" in (auth_class.docstring or "")
    assert auth_class.line_start <= auth_class.line_end

    module_symbols = index.list_symbols("src")
    module_names = {symbol.name for symbol in module_symbols}
    assert {"TokenManager", "formatDate", "Main", "BuildName"}.issubset(module_names)

    function = index.get_function("hash_password", "src/python/auth.py")
    assert function is not None
    assert function.symbol.name == "hash_password"
    assert "return password[::-1]" in function.source

    search_results = index.search_symbol("token")
    search_names = [symbol.name for symbol in search_results]
    assert "TokenManager" in search_names
    assert "create_token" in search_names

    description_results = index.describe_symbol("TokenManager")
    assert description_results
    assert description_results[0].docstring is not None


def test_code_index_updates_and_removes_files(tmp_path: Path) -> None:
    """
    Description:
        Verify the code index can refresh a single file and remove deleted
        files from the in-memory index.

    Requirements:
        - This test is needed to prove the index stays aligned with workspace
          changes after file writes and deletions.
        - Verify an updated file exposes the new symbol and a removed file
          disappears from the file list.

    :param tmp_path: Temporary directory provided by pytest.
    """
    project = tmp_path / "project"
    auth_path = write_text(
        project / "src" / "python" / "auth.py",
        """
        def first() -> str:
            return "first"
        """.strip(),
    )
    index = CodeIndex.build(project)
    assert [symbol.name for symbol in index.list_symbols("src/python/auth.py")] == ["first"]

    write_text(
        auth_path,
        """
        def first() -> str:
            return "first"


        def second() -> str:
            return "second"
        """.strip(),
    )
    index.index_file(auth_path)
    updated_names = [symbol.name for symbol in index.list_symbols("src/python/auth.py")]
    assert updated_names == ["first", "second"]

    index.remove_file(auth_path)
    remaining_paths = [item.path for item in index.list_files()]
    assert "src/python/auth.py" not in remaining_paths


def test_code_index_recovers_symbols_from_syntax_errors(tmp_path: Path) -> None:
    """
    Description:
        Verify the code index can still recover symbols from a malformed source
        file instead of dropping the whole file on the floor.

    Requirements:
        - This test is needed to prove the Code Index uses a tolerant parser
          backend rather than a fail-fast whole-file AST parse.
        - Verify a malformed Python file still exposes the valid earlier symbol
          definitions that appear before the syntax error.

    :param tmp_path: Temporary directory provided by pytest.
    """
    project = tmp_path / "project"
    write_text(
        project / "broken.py",
        """
        def first() -> str:
            return "first"


        def broken(
            return "broken"
        """.strip(),
    )

    index = CodeIndex.build(project)
    names = [symbol.name for symbol in index.list_symbols("broken.py")]
    assert "first" in names


def test_code_index_server_facade_builds_on_demand(tmp_path: Path) -> None:
    """
    Description:
        Verify the server facade can build an index lazily and serve symbol
        queries from it.

    Requirements:
        - This test is needed to prove the public server wrapper is usable
          without a separate manual build step.
        - Verify the facade returns the expected file and symbol data.

    :param tmp_path: Temporary directory provided by pytest.
    """
    write_text(
        tmp_path / "app.py",
        """
        def hello_world() -> str:
            \"\"\"Return a greeting.\"\"\"
            return "hello"
        """.strip(),
    )
    server = CodeIndexServer(tmp_path)

    files = server.list_files()
    assert files[0].path == "app.py"

    descriptions = server.describe_symbol("hello_world")
    assert descriptions and descriptions[0].name == "hello_world"

    function = server.get_function("hello_world", "app.py")
    assert function is not None
    assert "Return a greeting" in (function.symbol.docstring or "")


@pytest.mark.asyncio
async def test_code_index_watcher_refreshes_changes(tmp_path: Path) -> None:
    """
    Description:
        Verify the code-index watcher starts, scans, and stops cleanly.

    Requirements:
        - This test is needed to prove the polling watcher can pick up a file
          added after startup.
        - Verify the watcher reindexes the new file without errors.

    :param tmp_path: Temporary directory provided by pytest.
    """
    project = tmp_path / "project"
    write_text(
        project / "initial.py",
        """
        def first() -> str:
            return "first"
        """.strip(),
    )
    index = CodeIndex.build(project)
    watcher = FileWatcher(project, index, debounce_ms=10)

    await watcher.start()
    write_text(
        project / "added.py",
        """
        def second() -> str:
            return "second"
        """.strip(),
    )
    await watcher.scan_once()
    await watcher.stop()

    names = {symbol.name for symbol in index.search_symbol("second")}
    assert "second" in names
