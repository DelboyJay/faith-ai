"""
Description:
    Execute ripgrep-backed searches for the FAITH full-text search MCP server.

Requirements:
    - Validate requested paths against the workspace root.
    - Support regex, literal, and file-name searches with bounded result sets.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from faith_mcp.fulltext_search.models import FileMatch, SearchMatch, SearchResult

DEFAULT_MAX_MATCHES = 500
DEFAULT_TIMEOUT_SECONDS = 30.0


class RipgrepRunner:
    """
    Description:
        Run ripgrep commands safely against one workspace root.

    Requirements:
        - Constrain all search paths to the configured workspace root.
        - Enforce match-count and timeout limits.

    :param workspace_root: Root directory that may be searched.
    :param max_matches: Maximum number of matches to return.
    :param timeout_seconds: Maximum allowed runtime for one ripgrep process.
    :param rg_binary: Ripgrep executable name or path.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        max_matches: int = DEFAULT_MAX_MATCHES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        rg_binary: str = "rg",
    ):
        """
        Description:
            Configure the runner for one workspace and one ripgrep binary.

        Requirements:
            - Resolve the workspace root once during initialisation.
            - Preserve result and timeout limits for later search operations.

        :param workspace_root: Root directory that may be searched.
        :param max_matches: Maximum number of matches to return.
        :param timeout_seconds: Maximum allowed runtime for one ripgrep process.
        :param rg_binary: Ripgrep executable name or path.
        """
        self.workspace_root = Path(workspace_root).resolve()
        self.max_matches = max_matches
        self.timeout_seconds = timeout_seconds
        self.rg_binary = rg_binary

    def _validate_path(self, path: str | None) -> Path:
        """
        Description:
            Resolve an optional relative path and ensure it stays inside the
            workspace root.

        Requirements:
            - Return the workspace root when no path filter is supplied.
            - Raise `ValueError` when the resolved path escapes the workspace.

        :param path: Optional relative path filter.
        :returns: Validated absolute target path for ripgrep.
        :raises ValueError: If the resolved path is outside the workspace root.
        """
        if path is None:
            return self.workspace_root
        resolved = (self.workspace_root / path).resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError(f"Path '{path}' resolves outside the workspace root") from exc
        return resolved

    async def search(
        self,
        pattern: str,
        *,
        path: str | None = None,
        ignore_case: bool = False,
    ) -> SearchResult:
        """
        Description:
            Run a regex-based search against the workspace.

        Requirements:
            - Validate the optional path filter before invoking ripgrep.
            - Parse the JSON output into structured search results.

        :param pattern: Regular-expression pattern to search for.
        :param path: Optional relative path filter.
        :param ignore_case: Whether ripgrep should ignore case.
        :returns: Structured search result payload.
        """
        target = self._validate_path(path)
        args = [self.rg_binary, "--json", pattern, str(target)]
        if ignore_case:
            args.insert(1, "-i")
        payload = await self._run_rg(args)
        return self._parse_search_output(payload)

    async def search_literal(
        self,
        text: str,
        *,
        path: str | None = None,
        ignore_case: bool = False,
    ) -> SearchResult:
        """
        Description:
            Run a literal-text search against the workspace.

        Requirements:
            - Validate the optional path filter before invoking ripgrep.
            - Parse the JSON output into structured search results.

        :param text: Literal text to search for.
        :param path: Optional relative path filter.
        :param ignore_case: Whether ripgrep should ignore case.
        :returns: Structured search result payload.
        """
        target = self._validate_path(path)
        args = [self.rg_binary, "--json", "-F", text, str(target)]
        if ignore_case:
            args.insert(1, "-i")
        payload = await self._run_rg(args)
        return self._parse_search_output(payload)

    async def search_files(self, pattern: str, *, path: str | None = None) -> SearchResult:
        """
        Description:
            Search for matching file names under the workspace.

        Requirements:
            - Validate the optional path filter before invoking ripgrep.
            - Apply the configured match limit to the returned file list.

        :param pattern: Case-insensitive file-name pattern to search for.
        :param path: Optional relative path filter.
        :returns: Structured file-search result payload.
        """
        target = self._validate_path(path)
        args = [self.rg_binary, "--files", str(target)]
        payload = await self._run_rg(args)
        matches: list[FileMatch] = []
        for line in payload.splitlines():
            entry = line.strip()
            if not entry:
                continue
            entry_path = Path(entry)
            name = entry_path.name
            if pattern.lower() not in name.lower():
                continue
            size = entry_path.stat().st_size if entry_path.exists() else None
            matches.append(FileMatch(path=str(entry_path), size_bytes=size))
            if len(matches) >= self.max_matches:
                return SearchResult(matches=matches, truncated=True, match_count=len(matches))
        return SearchResult(matches=matches, truncated=False, match_count=len(matches))

    async def _run_rg(self, args: list[str]) -> str:
        """
        Description:
            Execute one ripgrep command and return its standard output.

        Requirements:
            - Enforce the configured timeout.
            - Treat ripgrep exit codes `0` and `1` as non-fatal.

        :param args: Full ripgrep command-line argument list.
        :returns: Standard-output payload produced by ripgrep.
        :raises TimeoutError: If ripgrep exceeds the configured timeout.
        :raises RuntimeError: If ripgrep exits with an unexpected error.
        """
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace_root),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError("ripgrep search timed out")

        if process.returncode not in (0, 1):
            message = stderr.decode("utf-8", errors="replace").strip() or "ripgrep failed"
            raise RuntimeError(message)
        return stdout.decode("utf-8", errors="replace")

    def _parse_search_output(self, payload: str) -> SearchResult:
        """
        Description:
            Parse ripgrep JSON output into structured content matches.

        Requirements:
            - Ignore non-match records.
            - Stop early and mark the result truncated once the configured match
              limit is reached.

        :param payload: JSON-lines output emitted by ripgrep.
        :returns: Structured search result payload.
        """
        matches: list[SearchMatch] = []
        for line in payload.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("type") != "match":
                continue
            data = record.get("data", {})
            path_text = data.get("path", {}).get("text", "")
            line_number = int(data.get("line_number", 0))
            line_text = data.get("lines", {}).get("text", "").rstrip("\n")
            submatches = data.get("submatches", [])
            column_start = submatches[0].get("start") if submatches else None
            column_end = submatches[0].get("end") if submatches else None
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
                return SearchResult(matches=matches, truncated=True, match_count=len(matches))
        return SearchResult(matches=matches, truncated=False, match_count=len(matches))
