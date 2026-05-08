"""
Description:
    Provide a small server facade over the code index.

Requirements:
    - Build the index lazily when callers ask for file or symbol navigation.
    - Expose the same structured helpers used by the MCP-facing task brief.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from faith_mcp.code_index.index import CodeIndex
from faith_mcp.code_index.models import FileInfo, FunctionResult, SymbolInfo


class CodeIndexServer:
    """Coordinate code-index lifecycle operations for one workspace."""

    def __init__(self, workspace_root: Path) -> None:
        """
        Create a server wrapper for one workspace root.

        :param workspace_root: Root directory to index.
        """
        self.workspace_root = Path(workspace_root).resolve()
        self._index: CodeIndex | None = None

    def build(self) -> CodeIndex:
        """
        Build a fresh index snapshot.

        :returns: Newly built code index.
        """
        self._index = CodeIndex.build(self.workspace_root)
        return self._index

    def load(self, snapshot_path: Path) -> CodeIndex:
        """
        Load a previously saved index snapshot.

        :param snapshot_path: Snapshot file to load.
        :returns: Loaded code index.
        """
        self._index = CodeIndex.load(snapshot_path)
        return self._index

    def save(self, snapshot_path: Path) -> Path:
        """
        Save the current index snapshot.

        :param snapshot_path: Destination file for the saved snapshot.
        :returns: Written snapshot path.
        """
        return self._require_index().save(snapshot_path)

    def list_files(self) -> list[FileInfo]:
        """
        Return metadata for all indexed files.

        :returns: File metadata from the current index.
        """
        return self._require_index().list_files()

    def list_symbols(self, target: str | Path) -> list[SymbolInfo]:
        """
        Return symbols for one file or directory.

        :param target: File path or directory path to inspect.
        :returns: Matching symbols from the current index.
        """
        return self._require_index().list_symbols(target)

    def get_function(
        self,
        name: str,
        file_path: str | Path | None = None,
    ) -> FunctionResult | None:
        """
        Return the full source for one function or method.

        :param name: Symbol name to locate.
        :param file_path: Optional file to limit the search.
        :returns: Matched function result or `None`.
        """
        return self._require_index().get_function(name, file_path)

    def search_symbol(self, query: str) -> list[SymbolInfo]:
        """
        Search symbol names across the workspace.

        :param query: Search term to match.
        :returns: Matching symbols from the current index.
        """
        return self._require_index().search_symbol(query)

    def describe_symbol(self, query: str) -> list[SymbolInfo]:
        """
        Return symbol metadata without source bodies.

        :param query: Symbol name to describe.
        :returns: Matching symbols from the current index.
        """
        return self._require_index().describe_symbol(query)

    def search(self, query: str, *, limit: int = 10) -> list[Any]:
        """
        Run the convenience content search used by the legacy prototype.

        :param query: Search query string.
        :param limit: Maximum number of hits to return.
        :returns: Ranked search hits.
        """
        return self._require_index().search(query, limit=limit)

    def refresh(self) -> CodeIndex:
        """
        Rebuild the current index from disk.

        :returns: Fresh code index snapshot.
        """
        self._index = CodeIndex.build(self.workspace_root)
        return self._index

    def _require_index(self) -> CodeIndex:
        """
        Return the current index, building one on demand.

        :returns: Available code index snapshot.
        """
        if self._index is None:
            return self.build()
        return self._index
