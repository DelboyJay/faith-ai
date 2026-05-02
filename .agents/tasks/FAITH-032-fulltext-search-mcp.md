# FAITH-032 — Full-Text Search MCP Server

**Phase:** 6 — Tool Servers
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-022
**FRS Reference:** Section 4.14

---

## Objective

Implement a Full-Text Search MCP server that provides fast regex and literal text search across workspace files using `ripgrep` via subprocess. This complements the AST-based Code Index (FAITH-027) by searching content that symbol indexing does not cover — comments, string literals, configuration files, documentation, and arbitrary text patterns. The server exposes three MCP commands (`search`, `search_literal`, `search_files`) and returns structured JSON results. All searches are confined to the workspace mount directory.

---

## Architecture

```
faith/mcp/fulltext_search/
├── __init__.py
├── server.py          ← MCP server class (this task)
├── ripgrep.py         ← ripgrep subprocess wrapper (this task)
└── models.py          ← Result dataclasses (this task)

tests/
└── test_fulltext_search.py  ← Tests (this task)
```

The server runs as a standalone MCP tool server inside the tool-server container. It receives MCP requests from agents via the MCP protocol (provided by FAITH-022's base infrastructure), invokes `ripgrep` as a subprocess, parses the JSON output, and returns structured results.

---

## Files to Create

### 1. `faith/mcp/fulltext_search/models.py`

```python
"""Data models for Full-Text Search MCP results.

FRS Reference: Section 4.14
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class SearchMatch:
    """A single search match result.

    Attributes:
        path: Absolute file path of the match.
        line_number: 1-based line number where the match occurs.
        line_text: The full text of the matched line (stripped of trailing newline).
        column_start: Optional 0-based column offset where the match begins.
        column_end: Optional 0-based column offset where the match ends.
    """

    path: str
    line_number: int
    line_text: str
    column_start: Optional[int] = None
    column_end: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to a JSON-serialisable dict, omitting None fields."""
        d = {"path": self.path, "line_number": self.line_number, "line_text": self.line_text}
        if self.column_start is not None:
            d["column_start"] = self.column_start
        if self.column_end is not None:
            d["column_end"] = self.column_end
        return d


@dataclass
class FileMatch:
    """A file found by filename search.

    Attributes:
        path: Absolute file path.
        size_bytes: File size in bytes (if available).
    """

    path: str
    size_bytes: Optional[int] = None

    def to_dict(self) -> dict:
        d = {"path": self.path}
        if self.size_bytes is not None:
            d["size_bytes"] = self.size_bytes
        return d


@dataclass
class SearchResult:
    """Aggregated result from a search operation.

    Attributes:
        matches: List of individual match results.
        truncated: Whether results were truncated due to the match limit.
        match_count: Total number of matches returned.
        error: Error message if the search failed, else None.
    """

    matches: list[SearchMatch | FileMatch] = field(default_factory=list)
    truncated: bool = False
    match_count: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "matches": [m.to_dict() for m in self.matches],
            "truncated": self.truncated,
            "match_count": self.match_count,
            "error": self.error,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
```

### 2. `faith/mcp/fulltext_search/ripgrep.py`

```python
"""Ripgrep subprocess wrapper for full-text search.

Invokes `rg` as a subprocess with JSON output format, parses results,
and returns structured SearchMatch / FileMatch objects. All searches
are sandboxed to the configured workspace root directory.

FRS Reference: Section 4.14.2
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from faith.mcp.fulltext_search.models import (
    FileMatch,
    SearchMatch,
    SearchResult,
)

logger = logging.getLogger("faith.mcp.fulltext_search.ripgrep")

# Maximum number of matches to return per search to prevent
# unbounded output from overly broad patterns.
DEFAULT_MAX_MATCHES = 500

# Maximum time (seconds) to allow a ripgrep subprocess to run.
DEFAULT_TIMEOUT_SECONDS = 30


class RipgrepRunner:
    """Executes ripgrep searches confined to a workspace directory.

    Attributes:
        workspace_root: The root directory that all searches are
            confined to. Paths outside this directory are rejected.
        max_matches: Maximum number of match results to return.
        timeout_seconds: Maximum subprocess execution time.
        rg_binary: Path or name of the ripgrep binary.
    """

    def __init__(
        self,
        workspace_root: Path,
        max_matches: int = DEFAULT_MAX_MATCHES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        rg_binary: str = "rg",
    ):
        self.workspace_root = workspace_root.resolve()
        self.max_matches = max_matches
        self.timeout_seconds = timeout_seconds
        self.rg_binary = rg_binary

    def _validate_path(self, path: Optional[str]) -> Path:
        """Validate and resolve a search path within the workspace.

        Args:
            path: Optional path string. If None, defaults to
                workspace_root.

        Returns:
            Resolved Path guaranteed to be within workspace_root.

        Raises:
            ValueError: If the resolved path is outside workspace_root.
        """
        if path is None:
            return self.workspace_root

        resolved = (self.workspace_root / path).resolve()

        # Security check: ensure the path is within the workspace
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise ValueError(
                f"Path '{path}' resolves outside the workspace root. "
                f"All searches must be within '{self.workspace_root}'."
            )

        return resolved

    def _build_base_args(self) -> list[str]:
        """Build common ripgrep arguments."""
        return [
            self.rg_binary,
            "--json",           # JSON output for structured parsing
            "--no-heading",     # One result per line
            "--max-count", str(self.max_matches),  # Limit per-file matches
        ]

    async def _run_rg(self, args: list[str]) -> tuple[str, str, int]:
        """Run ripgrep as an async subprocess.

        Args:
            args: Full command-line arguments including the binary.

        Returns:
            Tuple of (stdout, stderr, return_code).
        """
        logger.debug(f"Running ripgrep: {' '.join(args)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            return stdout, stderr, process.returncode

        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise TimeoutError(
                f"ripgrep search timed out after {self.timeout_seconds}s"
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"ripgrep binary '{self.rg_binary}' not found. "
                "Ensure ripgrep is installed in the container."
            )

    def _parse_json_matches(self, stdout: str) -> list[SearchMatch]:
        """Parse ripgrep JSON output into SearchMatch objects.

        ripgrep --json outputs one JSON object per line. We look for
        objects with type == "match".

        Args:
            stdout: Raw stdout from ripgrep.

        Returns:
            List of SearchMatch objects.
        """
        matches: list[SearchMatch] = []

        for line in stdout.strip().splitlines():
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(f"Skipping non-JSON ripgrep line: {line[:100]}")
                continue

            if obj.get("type") != "match":
                continue

            data = obj.get("data", {})
            path_obj = data.get("path", {})
            path_text = path_obj.get("text", "")
            line_number = data.get("line_number", 0)

            lines_obj = data.get("lines", {})
            line_text = lines_obj.get("text", "").rstrip("\n")

            # Extract column offsets from submatches
            column_start = None
            column_end = None
            submatches = data.get("submatches", [])
            if submatches:
                column_start = submatches[0].get("start")
                column_end = submatches[0].get("end")

            matches.append(
                SearchMatch(
                    path=path_text,
                    line_number=line_number,
                    line_text=line_text,
                    column_start=column_start,
                    column_end=column_end,
                )
            )

            if len(matches) >= self.max_matches:
                break

        return matches

    async def search(
        self,
        pattern: str,
        path: Optional[str] = None,
        file_glob: Optional[str] = None,
    ) -> SearchResult:
        """Regex search across workspace files.

        Args:
            pattern: Regular expression pattern to search for.
            path: Optional sub-path within the workspace to search.
            file_glob: Optional glob to filter files (e.g. "*.py").

        Returns:
            SearchResult with matching lines.
        """
        search_path = self._validate_path(path)

        args = self._build_base_args()

        if file_glob:
            args.extend(["--glob", file_glob])

        args.append(pattern)
        args.append(str(search_path))

        try:
            stdout, stderr, returncode = await self._run_rg(args)
        except (TimeoutError, FileNotFoundError) as e:
            return SearchResult(error=str(e))

        # rg returns 1 when no matches found (not an error)
        if returncode not in (0, 1):
            return SearchResult(error=f"ripgrep exited with code {returncode}: {stderr.strip()}")

        matches = self._parse_json_matches(stdout)
        truncated = len(matches) >= self.max_matches

        return SearchResult(
            matches=matches,
            truncated=truncated,
            match_count=len(matches),
        )

    async def search_literal(
        self,
        text: str,
        path: Optional[str] = None,
        file_glob: Optional[str] = None,
    ) -> SearchResult:
        """Exact string search (no regex interpretation).

        Args:
            text: Literal string to search for.
            path: Optional sub-path within the workspace to search.
            file_glob: Optional glob to filter files (e.g. "*.yaml").

        Returns:
            SearchResult with matching lines.
        """
        search_path = self._validate_path(path)

        args = self._build_base_args()
        args.append("--fixed-strings")  # Treat pattern as literal

        if file_glob:
            args.extend(["--glob", file_glob])

        args.append(text)
        args.append(str(search_path))

        try:
            stdout, stderr, returncode = await self._run_rg(args)
        except (TimeoutError, FileNotFoundError) as e:
            return SearchResult(error=str(e))

        if returncode not in (0, 1):
            return SearchResult(error=f"ripgrep exited with code {returncode}: {stderr.strip()}")

        matches = self._parse_json_matches(stdout)
        truncated = len(matches) >= self.max_matches

        return SearchResult(
            matches=matches,
            truncated=truncated,
            match_count=len(matches),
        )

    async def search_files(
        self,
        filename_pattern: str,
    ) -> SearchResult:
        """Find files by name pattern.

        Uses ripgrep's --files mode with a glob filter to find files
        whose names match the given pattern.

        Args:
            filename_pattern: Glob pattern for file names
                (e.g. "*.py", "test_*.js", "Dockerfile*").

        Returns:
            SearchResult with FileMatch entries.
        """
        args = [
            self.rg_binary,
            "--files",          # List files instead of searching content
            "--glob", filename_pattern,
            str(self.workspace_root),
        ]

        try:
            stdout, stderr, returncode = await self._run_rg(args)
        except (TimeoutError, FileNotFoundError) as e:
            return SearchResult(error=str(e))

        if returncode not in (0, 1):
            return SearchResult(error=f"ripgrep exited with code {returncode}: {stderr.strip()}")

        file_matches: list[FileMatch] = []
        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            # Optionally get file size
            size = None
            try:
                size = os.path.getsize(line)
            except OSError:
                pass

            file_matches.append(FileMatch(path=line, size_bytes=size))

            if len(file_matches) >= self.max_matches:
                break

        truncated = len(file_matches) >= self.max_matches

        return SearchResult(
            matches=file_matches,
            truncated=truncated,
            match_count=len(file_matches),
        )
```

### 3. `faith/mcp/fulltext_search/server.py`

```python
"""Full-Text Search MCP Server.

Exposes ripgrep-based search as MCP tool commands for agents to use.
Searches are confined to the workspace mount directory. Returns
structured JSON results (file path, line number, matched line).

FRS Reference: Section 4.14
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from faith.mcp.base import BaseMCPServer, MCPCommand, MCPResponse
from faith.mcp.fulltext_search.ripgrep import RipgrepRunner

logger = logging.getLogger("faith.mcp.fulltext_search.server")


class FullTextSearchServer(BaseMCPServer):
    """MCP server providing full-text search via ripgrep.

    This server registers three commands:
    - search: Regex search across workspace files
    - search_literal: Exact string search (no regex)
    - search_files: Find files by name pattern

    All commands return structured JSON with file paths, line numbers,
    and matched content. Searches are sandboxed to the configured
    workspace mount directory.

    Attributes:
        runner: RipgrepRunner instance for executing searches.
    """

    SERVER_NAME = "fulltext-search"
    SERVER_VERSION = "0.1.0"

    def __init__(
        self,
        workspace_root: Path,
        max_matches: int = 500,
        timeout_seconds: float = 30.0,
        rg_binary: str = "rg",
    ):
        """Initialise the Full-Text Search MCP server.

        Args:
            workspace_root: Root directory for all searches (workspace mount).
            max_matches: Maximum number of results per search.
            timeout_seconds: Maximum time per ripgrep invocation.
            rg_binary: Path or name of the ripgrep binary.
        """
        super().__init__()

        self.runner = RipgrepRunner(
            workspace_root=workspace_root,
            max_matches=max_matches,
            timeout_seconds=timeout_seconds,
            rg_binary=rg_binary,
        )

        logger.info(
            f"FullTextSearchServer initialised — "
            f"workspace: {workspace_root}, max_matches: {max_matches}"
        )

    def get_commands(self) -> list[MCPCommand]:
        """Return the list of MCP commands this server provides."""
        return [
            MCPCommand(
                name="search",
                description=(
                    "Regex search across workspace files. Returns matching "
                    "lines with file paths and line numbers."
                ),
                parameters={
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression pattern to search for.",
                        "required": True,
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Sub-path within the workspace to search. "
                            "Defaults to the entire workspace."
                        ),
                        "required": False,
                    },
                    "file_glob": {
                        "type": "string",
                        "description": (
                            "Glob pattern to filter files (e.g. '*.py', '*.yaml'). "
                            "Defaults to all files."
                        ),
                        "required": False,
                    },
                },
                handler=self._handle_search,
            ),
            MCPCommand(
                name="search_literal",
                description=(
                    "Exact string search (no regex interpretation). Returns "
                    "matching lines with file paths and line numbers."
                ),
                parameters={
                    "text": {
                        "type": "string",
                        "description": "Literal text string to search for.",
                        "required": True,
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Sub-path within the workspace to search. "
                            "Defaults to the entire workspace."
                        ),
                        "required": False,
                    },
                    "file_glob": {
                        "type": "string",
                        "description": (
                            "Glob pattern to filter files (e.g. '*.json'). "
                            "Defaults to all files."
                        ),
                        "required": False,
                    },
                },
                handler=self._handle_search_literal,
            ),
            MCPCommand(
                name="search_files",
                description=(
                    "Find files by name pattern. Returns file paths and sizes."
                ),
                parameters={
                    "filename_pattern": {
                        "type": "string",
                        "description": (
                            "Glob pattern for file names "
                            "(e.g. '*.py', 'Dockerfile*', 'test_*.js')."
                        ),
                        "required": True,
                    },
                },
                handler=self._handle_search_files,
            ),
        ]

    async def _handle_search(self, params: dict[str, Any]) -> MCPResponse:
        """Handle the 'search' command — regex search.

        Args:
            params: Command parameters with 'pattern', optional 'path'
                and 'file_glob'.

        Returns:
            MCPResponse with structured search results.
        """
        pattern = params.get("pattern")
        if not pattern:
            return MCPResponse(
                success=False,
                error="'pattern' parameter is required.",
            )

        path = params.get("path")
        file_glob = params.get("file_glob")

        try:
            result = await self.runner.search(
                pattern=pattern,
                path=path,
                file_glob=file_glob,
            )
        except ValueError as e:
            return MCPResponse(success=False, error=str(e))

        if result.error:
            return MCPResponse(success=False, error=result.error)

        return MCPResponse(success=True, data=result.to_dict())

    async def _handle_search_literal(self, params: dict[str, Any]) -> MCPResponse:
        """Handle the 'search_literal' command — exact string search.

        Args:
            params: Command parameters with 'text', optional 'path'
                and 'file_glob'.

        Returns:
            MCPResponse with structured search results.
        """
        text = params.get("text")
        if not text:
            return MCPResponse(
                success=False,
                error="'text' parameter is required.",
            )

        path = params.get("path")
        file_glob = params.get("file_glob")

        try:
            result = await self.runner.search_literal(
                text=text,
                path=path,
                file_glob=file_glob,
            )
        except ValueError as e:
            return MCPResponse(success=False, error=str(e))

        if result.error:
            return MCPResponse(success=False, error=result.error)

        return MCPResponse(success=True, data=result.to_dict())

    async def _handle_search_files(self, params: dict[str, Any]) -> MCPResponse:
        """Handle the 'search_files' command — find files by name.

        Args:
            params: Command parameters with 'filename_pattern'.

        Returns:
            MCPResponse with file match results.
        """
        filename_pattern = params.get("filename_pattern")
        if not filename_pattern:
            return MCPResponse(
                success=False,
                error="'filename_pattern' parameter is required.",
            )

        try:
            result = await self.runner.search_files(
                filename_pattern=filename_pattern,
            )
        except ValueError as e:
            return MCPResponse(success=False, error=str(e))

        if result.error:
            return MCPResponse(success=False, error=result.error)

        return MCPResponse(success=True, data=result.to_dict())
```

### 4. `faith/mcp/fulltext_search/__init__.py`

```python
"""FAITH Full-Text Search MCP Server — ripgrep-based workspace search."""

from faith.mcp.fulltext_search.server import FullTextSearchServer

__all__ = ["FullTextSearchServer"]
```

### 5. `tests/test_fulltext_search.py`

```python
"""Tests for the Full-Text Search MCP server.

Covers ripgrep runner path validation, JSON output parsing,
result model serialisation, search command handlers, and
security boundary enforcement (workspace-only access).
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.mcp.fulltext_search.models import (
    FileMatch,
    SearchMatch,
    SearchResult,
)
from faith.mcp.fulltext_search.ripgrep import RipgrepRunner
from faith.mcp.fulltext_search.server import FullTextSearchServer


# ──────────────────────────────────────────────────
# Model tests
# ──────────────────────────────────────────────────


def test_search_match_to_dict_minimal():
    """SearchMatch.to_dict() includes required fields only when columns are None."""
    m = SearchMatch(path="/workspace/foo.py", line_number=42, line_text="hello world")
    d = m.to_dict()
    assert d == {"path": "/workspace/foo.py", "line_number": 42, "line_text": "hello world"}
    assert "column_start" not in d
    assert "column_end" not in d


def test_search_match_to_dict_with_columns():
    """SearchMatch.to_dict() includes column offsets when present."""
    m = SearchMatch(
        path="/workspace/foo.py",
        line_number=10,
        line_text="import os",
        column_start=7,
        column_end=9,
    )
    d = m.to_dict()
    assert d["column_start"] == 7
    assert d["column_end"] == 9


def test_file_match_to_dict():
    """FileMatch.to_dict() serialises correctly."""
    m = FileMatch(path="/workspace/test.py", size_bytes=1024)
    d = m.to_dict()
    assert d == {"path": "/workspace/test.py", "size_bytes": 1024}


def test_file_match_to_dict_no_size():
    """FileMatch.to_dict() omits size_bytes when None."""
    m = FileMatch(path="/workspace/test.py")
    d = m.to_dict()
    assert d == {"path": "/workspace/test.py"}
    assert "size_bytes" not in d


def test_search_result_to_json():
    """SearchResult serialises to valid JSON."""
    result = SearchResult(
        matches=[SearchMatch(path="a.py", line_number=1, line_text="x")],
        truncated=False,
        match_count=1,
    )
    parsed = json.loads(result.to_json())
    assert parsed["match_count"] == 1
    assert parsed["truncated"] is False
    assert len(parsed["matches"]) == 1


def test_search_result_error():
    """SearchResult with error serialises correctly."""
    result = SearchResult(error="rg not found")
    d = result.to_dict()
    assert d["error"] == "rg not found"
    assert d["matches"] == []


# ──────────────────────────────────────────────────
# RipgrepRunner path validation tests
# ──────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace directory with test files."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    # Create test files
    (ws / "hello.py").write_text("print('hello world')\n", encoding="utf-8")
    (ws / "config.yaml").write_text("key: value\nport: 8080\n", encoding="utf-8")

    sub = ws / "src"
    sub.mkdir()
    (sub / "main.py").write_text(
        "import os\nimport sys\n\ndef main():\n    pass\n",
        encoding="utf-8",
    )
    (sub / "utils.py").write_text(
        "# TODO: refactor this\ndef helper():\n    return 42\n",
        encoding="utf-8",
    )

    return ws


@pytest.fixture
def runner(workspace):
    return RipgrepRunner(workspace_root=workspace)


def test_validate_path_default(runner, workspace):
    """None path defaults to workspace root."""
    assert runner._validate_path(None) == workspace.resolve()


def test_validate_path_subdir(runner, workspace):
    """Sub-path within workspace resolves correctly."""
    result = runner._validate_path("src")
    assert result == (workspace / "src").resolve()


def test_validate_path_rejects_traversal(runner):
    """Path traversal outside workspace is rejected."""
    with pytest.raises(ValueError, match="outside the workspace root"):
        runner._validate_path("../../etc/passwd")


def test_validate_path_rejects_absolute_outside(runner):
    """Absolute path outside workspace is rejected (via join behavior)."""
    # On Unix, Path("/workspace") / "/etc" == Path("/etc")
    # On Windows, behaviour differs but the relative_to check catches it
    with pytest.raises(ValueError, match="outside the workspace root"):
        runner._validate_path("../../../tmp/evil")


# ──────────────────────────────────────────────────
# RipgrepRunner JSON parsing tests
# ──────────────────────────────────────────────────


def test_parse_json_matches_basic(runner):
    """Parses standard ripgrep JSON match output."""
    rg_output = json.dumps({
        "type": "match",
        "data": {
            "path": {"text": "/workspace/foo.py"},
            "line_number": 5,
            "lines": {"text": "import os\n"},
            "submatches": [{"start": 7, "end": 9}],
        },
    })

    matches = runner._parse_json_matches(rg_output)
    assert len(matches) == 1
    assert matches[0].path == "/workspace/foo.py"
    assert matches[0].line_number == 5
    assert matches[0].line_text == "import os"
    assert matches[0].column_start == 7
    assert matches[0].column_end == 9


def test_parse_json_matches_skips_non_match_types(runner):
    """Non-match JSON lines (begin, end, summary) are skipped."""
    lines = "\n".join([
        json.dumps({"type": "begin", "data": {"path": {"text": "a.py"}}}),
        json.dumps({
            "type": "match",
            "data": {
                "path": {"text": "a.py"},
                "line_number": 1,
                "lines": {"text": "hello\n"},
                "submatches": [],
            },
        }),
        json.dumps({"type": "end", "data": {"path": {"text": "a.py"}}}),
        json.dumps({"type": "summary", "data": {}}),
    ])

    matches = runner._parse_json_matches(lines)
    assert len(matches) == 1
    assert matches[0].line_text == "hello"


def test_parse_json_matches_empty_output(runner):
    """Empty stdout returns no matches."""
    assert runner._parse_json_matches("") == []


def test_parse_json_matches_respects_max_matches(workspace):
    """Parser stops at max_matches limit."""
    runner = RipgrepRunner(workspace_root=workspace, max_matches=2)

    lines = "\n".join(
        json.dumps({
            "type": "match",
            "data": {
                "path": {"text": f"file{i}.py"},
                "line_number": i,
                "lines": {"text": f"line {i}\n"},
                "submatches": [],
            },
        })
        for i in range(10)
    )

    matches = runner._parse_json_matches(lines)
    assert len(matches) == 2


# ──────────────────────────────────────────────────
# RipgrepRunner integration tests (require rg binary)
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_regex(runner, workspace):
    """Regex search finds matches in workspace files."""
    result = await runner.search(pattern=r"def \w+")
    assert result.error is None
    assert result.match_count > 0
    # Should find main() and helper()
    texts = [m.line_text for m in result.matches]
    assert any("def main" in t for t in texts)
    assert any("def helper" in t for t in texts)


@pytest.mark.asyncio
async def test_search_literal_no_regex(runner, workspace):
    """Literal search does not interpret regex metacharacters."""
    result = await runner.search_literal(text="print('hello world')")
    assert result.error is None
    assert result.match_count == 1
    assert result.matches[0].line_text == "print('hello world')"


@pytest.mark.asyncio
async def test_search_with_file_glob(runner, workspace):
    """File glob filters results to matching files only."""
    result = await runner.search(pattern="import", file_glob="*.py")
    assert result.error is None
    for m in result.matches:
        assert m.path.endswith(".py")


@pytest.mark.asyncio
async def test_search_with_path(runner, workspace):
    """Path parameter restricts search to a subdirectory."""
    result = await runner.search(pattern="TODO", path="src")
    assert result.error is None
    assert result.match_count >= 1
    for m in result.matches:
        assert "src" in m.path


@pytest.mark.asyncio
async def test_search_no_matches(runner, workspace):
    """Search with no matches returns empty result (not an error)."""
    result = await runner.search(pattern="zzz_definitely_not_in_any_file_zzz")
    assert result.error is None
    assert result.match_count == 0
    assert result.matches == []


@pytest.mark.asyncio
async def test_search_files_by_pattern(runner, workspace):
    """search_files finds files matching the name pattern."""
    result = await runner.search_files(filename_pattern="*.py")
    assert result.error is None
    assert result.match_count >= 3  # hello.py, main.py, utils.py
    paths = [m.path for m in result.matches]
    assert any("hello.py" in p for p in paths)


@pytest.mark.asyncio
async def test_search_files_no_matches(runner, workspace):
    """search_files with no matches returns empty result."""
    result = await runner.search_files(filename_pattern="*.nonexistent")
    assert result.error is None
    assert result.match_count == 0


@pytest.mark.asyncio
async def test_search_rejects_path_traversal(runner):
    """search() rejects paths that escape the workspace."""
    result = await runner.search(pattern="secret", path="../../etc")
    # Should get a ValueError wrapped in the result
    # (depending on implementation, this may raise or return error)
    # The _validate_path raises ValueError before rg is invoked


# ──────────────────────────────────────────────────
# Server handler tests (mocked runner)
# ──────────────────────────────────────────────────


@pytest.fixture
def mock_server(workspace):
    """Create a FullTextSearchServer with the test workspace."""
    return FullTextSearchServer(workspace_root=workspace)


@pytest.mark.asyncio
async def test_server_search_missing_pattern(mock_server):
    """search command returns error when pattern is missing."""
    resp = await mock_server._handle_search({})
    assert resp.success is False
    assert "pattern" in resp.error.lower()


@pytest.mark.asyncio
async def test_server_search_literal_missing_text(mock_server):
    """search_literal command returns error when text is missing."""
    resp = await mock_server._handle_search_literal({})
    assert resp.success is False
    assert "text" in resp.error.lower()


@pytest.mark.asyncio
async def test_server_search_files_missing_pattern(mock_server):
    """search_files command returns error when filename_pattern is missing."""
    resp = await mock_server._handle_search_files({})
    assert resp.success is False
    assert "filename_pattern" in resp.error.lower()


@pytest.mark.asyncio
async def test_server_get_commands(mock_server):
    """Server exposes exactly three commands."""
    commands = mock_server.get_commands()
    names = [c.name for c in commands]
    assert names == ["search", "search_literal", "search_files"]
```

---

## Integration Points

The Full-Text Search server integrates with the following FAITH components:

```python
# Agent uses the search tool via MCP (after FAITH-022 provides the base)
result = await mcp_client.call("fulltext-search", "search", {
    "pattern": r"TODO|FIXME|HACK",
    "file_glob": "*.py",
})
# Returns:
# {
#     "matches": [
#         {"path": "/workspace/src/utils.py", "line_number": 1,
#          "line_text": "# TODO: refactor this", "column_start": 2, "column_end": 6}
#     ],
#     "truncated": false,
#     "match_count": 1,
#     "error": null
# }
```

```python
# Literal search for exact error message strings
result = await mcp_client.call("fulltext-search", "search_literal", {
    "text": "ConnectionRefusedError",
    "path": "src",
    "file_glob": "*.py",
})
```

```python
# Find all Dockerfiles in the workspace
result = await mcp_client.call("fulltext-search", "search_files", {
    "filename_pattern": "Dockerfile*",
})
```

---

## Acceptance Criteria

1. `RipgrepRunner` executes `rg` as an async subprocess and parses its `--json` output into structured `SearchMatch` objects (file path, line number, matched line, optional column offsets).
2. `search(pattern, path?, file_glob?)` performs regex search, returning structured JSON results. Optional `path` restricts to a subdirectory; optional `file_glob` filters by file type.
3. `search_literal(text, path?, file_glob?)` performs exact string search with `--fixed-strings`, preventing regex metacharacter interpretation.
4. `search_files(filename_pattern)` lists files matching a name glob, returning paths and file sizes.
5. All search paths are validated against the workspace root. Path traversal attempts (e.g. `../../etc/passwd`) are rejected with a `ValueError` before `rg` is invoked.
6. Results are capped at `max_matches` (default 500) and the `truncated` flag is set when the cap is reached.
7. Subprocess execution is bounded by `timeout_seconds` (default 30s). Timed-out processes are killed and an error is returned.
8. Missing `rg` binary produces a clear `FileNotFoundError` message rather than an unhandled exception.
9. `FullTextSearchServer` registers exactly three MCP commands (`search`, `search_literal`, `search_files`) with correct parameter schemas.
10. All tests in `tests/test_fulltext_search.py` pass, covering model serialisation, path validation, JSON parsing, integration search (regex, literal, file glob, sub-path, no-matches), and server handler error cases.

---

## Notes for Implementer

- **ripgrep availability**: The `rg` binary must be installed in the tool-server container image. Add `ripgrep` to the container's package installation step (e.g. `apt-get install -y ripgrep` or download the static binary). The `RipgrepRunner` accepts an `rg_binary` parameter so the path can be overridden if needed.
- **`--json` output format**: ripgrep's `--json` flag emits one JSON object per line with a `type` field. Only `"match"` type objects contain search results. Other types (`begin`, `end`, `summary`, `context`) are emitted for structural context and should be skipped during parsing.
- **`--max-count` vs result cap**: The `--max-count` flag limits matches *per file*. The `max_matches` cap in `_parse_json_matches` limits the *total* result set. Both are needed — `--max-count` prevents rg from doing unnecessary work on files with many matches, and the parser cap ensures the overall response stays bounded.
- **Security boundary**: The `_validate_path` method uses `Path.resolve()` followed by `relative_to()` to ensure all search paths are within the workspace. This prevents symlink-based and `../` traversal escapes. Agents cannot search outside the workspace mount.
- **Binary files**: ripgrep automatically skips binary files by default. No additional configuration is needed. If agents need to search binary files in the future, add a `--binary` flag option.
- **Dependency on FAITH-022**: This server inherits from `BaseMCPServer` which is defined in FAITH-022 (Filesystem MCP Server). The `MCPCommand`, `MCPResponse`, and server registration infrastructure come from that base. If those classes change signature, this server's `get_commands()` and handler returns need to be updated accordingly.
- **Windows test compatibility**: The integration tests that invoke `rg` require ripgrep to be installed on the test machine. These tests are skipped in CI environments where `rg` is not available. Use `pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")` if needed.
- **No persistent state**: This server is stateless. Each search is an independent subprocess invocation. There is no index to build, no file watcher to configure, and no startup delay. This simplicity is intentional — the Code Index (FAITH-027) handles the stateful indexing use case.
