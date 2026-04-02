"""
Description:
    Provide a lightweight code-index server facade over the FAITH code index.

Requirements:
    - Build, load, save, search, and symbol-describe index snapshots for one
      workspace root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from faith_mcp.code_index.index import CodeIndex


class CodeIndexServer:
    """
    Description:
        Coordinate code-index lifecycle operations for one workspace.

    Requirements:
        - Build or load an index lazily.
        - Expose simple file, search, and symbol lookup helpers to callers.

    :param workspace_root: Root directory that should be indexed.
    """

    def __init__(self, workspace_root: Path):
        """
        Description:
            Store the workspace root and start with no in-memory index.

        Requirements:
            - Resolve the workspace root to an absolute path.

        :param workspace_root: Root directory that should be indexed.
        """
        self.workspace_root = Path(workspace_root).resolve()
        self._index: CodeIndex | None = None

    def build(self) -> CodeIndex:
        """
        Description:
            Build a fresh index snapshot for the configured workspace root.

        Requirements:
            - Cache the built index for later operations.

        :returns: Newly built code index snapshot.
        """
        self._index = CodeIndex.build(self.workspace_root)
        return self._index

    def load(self, snapshot_path: Path) -> CodeIndex:
        """
        Description:
            Load an index snapshot from disk.

        Requirements:
            - Cache the loaded index for later operations.

        :param snapshot_path: Path to the saved index snapshot.
        :returns: Loaded code index snapshot.
        """
        self._index = CodeIndex.load(snapshot_path)
        return self._index

    def save(self, snapshot_path: Path) -> Path:
        """
        Description:
            Save the current index snapshot to disk.

        Requirements:
            - Build the index first when no snapshot is loaded yet.

        :param snapshot_path: Destination path for the saved snapshot.
        :returns: Path written by the save operation.
        """
        index = self._require_index()
        return index.save(snapshot_path)

    def list_files(self) -> list[str]:
        """
        Description:
            Return the indexed relative file paths for the current snapshot.

        Requirements:
            - Build the index first when no snapshot is loaded yet.

        :returns: Relative paths for the indexed documents.
        """
        return [document.relative_path for document in self._require_index().documents]

    def search(self, query: str, *, limit: int = 10) -> list[Any]:
        """
        Description:
            Search the current index snapshot.

        Requirements:
            - Build the index first when no snapshot is loaded yet.
            - Pass through the result limit to the underlying index search.

        :param query: Search query string.
        :param limit: Maximum number of hits to return.
        :returns: Search hits returned by the code index.
        """
        return self._require_index().search(query, limit=limit)

    def describe_symbol(self, name: str) -> list[dict[str, Any]]:
        """
        Description:
            Return all indexed symbol definitions that match the requested name.

        Requirements:
            - Search across every indexed document.
            - Return symbol metadata in a structured dictionary form.

        :param name: Symbol name to locate in the current index.
        :returns: Matching symbol definitions with kind, line, and path metadata.
        """
        matches = []
        for document in self._require_index().documents:
            for symbol in document.symbols:
                if symbol.name == name:
                    matches.append(
                        {
                            "name": symbol.name,
                            "kind": symbol.kind,
                            "line": symbol.line,
                            "path": document.relative_path,
                        }
                    )
        return matches

    def _require_index(self) -> CodeIndex:
        """
        Description:
            Return the current in-memory index, building one when necessary.

        Requirements:
            - Lazily build an index when callers use the server before an
              explicit build or load.

        :returns: Available code index snapshot.
        """
        if self._index is None:
            return self.build()
        return self._index
