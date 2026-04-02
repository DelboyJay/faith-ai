"""
Description:
    Provide a lightweight full-text search server facade over the ripgrep
    runner.

Requirements:
    - Expose regex, literal, and file-name search helpers for one workspace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from faith_mcp.fulltext_search.ripgrep import RipgrepRunner


class FullTextSearchServer:
    """
    Description:
        Coordinate full-text search requests for one workspace root.

    Requirements:
        - Delegate search execution to the ripgrep runner.
        - Return structured dictionaries suitable for MCP responses.

    :param workspace_root: Root directory that should be searched.
    :param rg_binary: Ripgrep executable name or path.
    """

    def __init__(self, workspace_root: Path, *, rg_binary: str = "rg"):
        """
        Description:
            Create the ripgrep runner used for all search requests.

        Requirements:
            - Bind the runner to the requested workspace root and binary.

        :param workspace_root: Root directory that should be searched.
        :param rg_binary: Ripgrep executable name or path.
        """
        self.runner = RipgrepRunner(workspace_root, rg_binary=rg_binary)

    async def search(
        self,
        pattern: str,
        *,
        path: str | None = None,
        ignore_case: bool = False,
    ) -> dict[str, Any]:
        """
        Description:
            Run a regex-based full-text search.

        Requirements:
            - Pass through the optional path filter and case-sensitivity flag.
            - Return the runner result as a plain dictionary.

        :param pattern: Regular-expression pattern to search for.
        :param path: Optional relative path filter.
        :param ignore_case: Whether ripgrep should ignore case.
        :returns: Structured search result payload.
        """
        return (await self.runner.search(pattern, path=path, ignore_case=ignore_case)).to_dict()

    async def search_literal(
        self,
        text: str,
        *,
        path: str | None = None,
        ignore_case: bool = False,
    ) -> dict[str, Any]:
        """
        Description:
            Run a literal-string full-text search.

        Requirements:
            - Pass through the optional path filter and case-sensitivity flag.
            - Return the runner result as a plain dictionary.

        :param text: Literal search text.
        :param path: Optional relative path filter.
        :param ignore_case: Whether ripgrep should ignore case.
        :returns: Structured search result payload.
        """
        return (
            await self.runner.search_literal(text, path=path, ignore_case=ignore_case)
        ).to_dict()

    async def search_files(self, pattern: str, *, path: str | None = None) -> dict[str, Any]:
        """
        Description:
            Search for matching file paths without inspecting file content.

        Requirements:
            - Pass through the optional relative path filter.
            - Return the runner result as a plain dictionary.

        :param pattern: File-name pattern to search for.
        :param path: Optional relative path filter.
        :returns: Structured file-search result payload.
        """
        return (await self.runner.search_files(pattern, path=path)).to_dict()
