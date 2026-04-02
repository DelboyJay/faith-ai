"""Code-index server facade for the FAITH POC."""

from __future__ import annotations

from pathlib import Path

from faith_mcp.code_index.index import CodeIndex


class CodeIndexServer:
    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self._index: CodeIndex | None = None

    def build(self) -> CodeIndex:
        self._index = CodeIndex.build(self.workspace_root)
        return self._index

    def load(self, snapshot_path: Path) -> CodeIndex:
        self._index = CodeIndex.load(snapshot_path)
        return self._index

    def save(self, snapshot_path: Path) -> Path:
        index = self._require_index()
        return index.save(snapshot_path)

    def list_files(self) -> list[str]:
        return [document.relative_path for document in self._require_index().documents]

    def search(self, query: str, *, limit: int = 10):
        return self._require_index().search(query, limit=limit)

    def describe_symbol(self, name: str):
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
        if self._index is None:
            return self.build()
        return self._index

