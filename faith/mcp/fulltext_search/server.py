"""Practical full-text search server facade for the FAITH POC."""

from __future__ import annotations

from pathlib import Path

from faith.mcp.fulltext_search.ripgrep import RipgrepRunner


class FullTextSearchServer:
    def __init__(self, workspace_root: Path, *, rg_binary: str = "rg"):
        self.runner = RipgrepRunner(workspace_root, rg_binary=rg_binary)

    async def search(
        self, pattern: str, *, path: str | None = None, ignore_case: bool = False
    ) -> dict:
        return (await self.runner.search(pattern, path=path, ignore_case=ignore_case)).to_dict()

    async def search_literal(
        self, text: str, *, path: str | None = None, ignore_case: bool = False
    ) -> dict:
        return (
            await self.runner.search_literal(text, path=path, ignore_case=ignore_case)
        ).to_dict()

    async def search_files(self, pattern: str, *, path: str | None = None) -> dict:
        return (await self.runner.search_files(pattern, path=path)).to_dict()
