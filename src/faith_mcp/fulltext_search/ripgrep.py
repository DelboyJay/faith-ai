"""Ripgrep-backed full-text search helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from faith_mcp.fulltext_search.models import FileMatch, SearchMatch, SearchResult

DEFAULT_MAX_MATCHES = 500
DEFAULT_TIMEOUT_SECONDS = 30.0


class RipgrepRunner:
    def __init__(
        self,
        workspace_root: Path,
        *,
        max_matches: int = DEFAULT_MAX_MATCHES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        rg_binary: str = "rg",
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.max_matches = max_matches
        self.timeout_seconds = timeout_seconds
        self.rg_binary = rg_binary

    def _validate_path(self, path: str | None) -> Path:
        if path is None:
            return self.workspace_root
        resolved = (self.workspace_root / path).resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError(f"Path '{path}' resolves outside the workspace root") from exc
        return resolved

    async def search(
        self, pattern: str, *, path: str | None = None, ignore_case: bool = False
    ) -> SearchResult:
        target = self._validate_path(path)
        args = [self.rg_binary, "--json", pattern, str(target)]
        if ignore_case:
            args.insert(1, "-i")
        payload = await self._run_rg(args)
        return self._parse_search_output(payload)

    async def search_literal(
        self, text: str, *, path: str | None = None, ignore_case: bool = False
    ) -> SearchResult:
        target = self._validate_path(path)
        args = [self.rg_binary, "--json", "-F", text, str(target)]
        if ignore_case:
            args.insert(1, "-i")
        payload = await self._run_rg(args)
        return self._parse_search_output(payload)

    async def search_files(self, pattern: str, *, path: str | None = None) -> SearchResult:
        target = self._validate_path(path)
        args = [self.rg_binary, "--files", str(target)]
        payload = await self._run_rg(args)
        matches: list[FileMatch] = []
        for line in payload.splitlines():
            entry = line.strip()
            if not entry:
                continue
            name = Path(entry).name
            if pattern.lower() not in name.lower():
                continue
            size = Path(entry).stat().st_size if Path(entry).exists() else None
            matches.append(FileMatch(path=str(Path(entry)), size_bytes=size))
            if len(matches) >= self.max_matches:
                return SearchResult(matches=matches, truncated=True, match_count=len(matches))
        return SearchResult(matches=matches, truncated=False, match_count=len(matches))

    async def _run_rg(self, args: list[str]) -> str:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace_root),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
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

