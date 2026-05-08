"""
Description:
    Build and query a symbol-aware code index for FAITH workspaces.

Requirements:
    - Index supported source files without loading the whole workspace into
      callers' context.
    - Provide file, symbol, and function navigation helpers for the MCP
      server facade.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tree_sitter_go as tree_sitter_go_grammar
import tree_sitter_java as tree_sitter_java_grammar
import tree_sitter_javascript as tree_sitter_javascript_grammar
import tree_sitter_python as tree_sitter_python_grammar
import tree_sitter_typescript as tree_sitter_typescript_grammar
from tree_sitter import Language, Node, Parser

from faith_mcp.code_index.models import FileInfo, FunctionResult, SymbolInfo, SymbolKind

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".faith",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    "logs",
    "data",
}

_SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
}

_TREE_SITTER_LANGUAGE_LOADERS = {
    "python": lambda: Language(tree_sitter_python_grammar.language()),
    "javascript": lambda: Language(tree_sitter_javascript_grammar.language()),
    "typescript": lambda: Language(tree_sitter_typescript_grammar.language_typescript()),
    "tsx": lambda: Language(tree_sitter_typescript_grammar.language_tsx()),
    "java": lambda: Language(tree_sitter_java_grammar.language()),
    "go": lambda: Language(tree_sitter_go_grammar.language()),
}

_TREE_SITTER_LANGUAGES: dict[str, Language] = {}
_TREE_SITTER_PARSERS: dict[str, Parser] = {}


@dataclass(slots=True)
class CodeDocument:
    """Represent one indexed workspace file."""

    path: str
    relative_path: str
    language: str
    checksum: str
    line_count: int
    size_bytes: int
    symbols: list[SymbolInfo] = field(default_factory=list)
    preview_lines: list[str] = field(default_factory=list)
    source: str = ""
    indexed_at: str = ""


@dataclass(slots=True)
class CodeSearchHit:
    """Represent one ranked code-search result."""

    relative_path: str
    path: str
    score: int
    snippet: str
    matches: list[str] = field(default_factory=list)
    symbols: list[SymbolInfo] = field(default_factory=list)


class CodeIndex:
    """Index source files and expose deterministic navigation helpers."""

    def __init__(
        self,
        root: Path,
        *,
        excluded_dirs: Iterable[str] | None = None,
        documents: Iterable[CodeDocument] | None = None,
        generated_at: str | None = None,
    ) -> None:
        self.root = str(Path(root).resolve())
        self.generated_at = generated_at or datetime.now(timezone.utc).isoformat()
        self.excluded_dirs = set(excluded_dirs or DEFAULT_EXCLUDED_DIRS)
        self._documents: dict[str, CodeDocument] = {}
        if documents is not None:
            for document in documents:
                self._documents[document.relative_path] = document

    @property
    def documents(self) -> list[CodeDocument]:
        """Return the indexed documents in sorted order."""
        return [self._documents[path] for path in sorted(self._documents)]

    @property
    def file_count(self) -> int:
        """Return the number of indexed files."""
        return len(self._documents)

    @classmethod
    def build(
        cls,
        root: Path,
        *,
        excluded_dirs: Iterable[str] | None = None,
        max_file_size_bytes: int = 1_000_000,
    ) -> CodeIndex:
        """
        Build a fresh index snapshot for the supplied workspace root.

        :param root: Workspace root to scan.
        :param excluded_dirs: Optional override for excluded directory names.
        :param max_file_size_bytes: Maximum file size to index.
        :returns: Newly built index snapshot.
        """
        index = cls(root, excluded_dirs=excluded_dirs)
        index.generated_at = datetime.now(timezone.utc).isoformat()
        index._documents.clear()
        for file_path in _iter_source_files(Path(index.root), index.excluded_dirs):
            index._index_path(file_path, max_file_size_bytes=max_file_size_bytes)
        return index

    @classmethod
    def load(cls, path: Path) -> CodeIndex:
        """
        Load a code index snapshot from disk.

        :param path: Path to a saved snapshot.
        :returns: Loaded code index snapshot.
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        documents = [
            CodeDocument(
                path=item["path"],
                relative_path=item["relative_path"],
                language=item["language"],
                checksum=item["checksum"],
                line_count=item["line_count"],
                size_bytes=item["size_bytes"],
                symbols=[SymbolInfo(**symbol) for symbol in item.get("symbols", [])],
                preview_lines=list(item.get("preview_lines", [])),
                source=item.get("source", ""),
                indexed_at=item.get("indexed_at", ""),
            )
            for item in raw.get("documents", [])
        ]
        return cls(
            Path(raw["root"]),
            excluded_dirs=raw.get("excluded_dirs", []),
            documents=documents,
            generated_at=raw.get("generated_at"),
        )

    def save(self, path: Path) -> Path:
        """
        Save the current index snapshot to disk.

        :param path: Destination JSON file path.
        :returns: Written snapshot path.
        """
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        return destination

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the index snapshot into a serialisable dictionary.

        :returns: JSON-safe index payload.
        """
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "excluded_dirs": sorted(self.excluded_dirs),
            "documents": [
                {
                    "path": document.path,
                    "relative_path": document.relative_path,
                    "language": document.language,
                    "checksum": document.checksum,
                    "line_count": document.line_count,
                    "size_bytes": document.size_bytes,
                    "symbols": [symbol.model_dump() for symbol in document.symbols],
                    "preview_lines": list(document.preview_lines),
                    "source": document.source,
                    "indexed_at": document.indexed_at,
                }
                for document in self.documents
            ],
        }

    def refresh(self, *, max_file_size_bytes: int = 1_000_000) -> CodeIndex:
        """
        Rebuild the index using the stored root and exclusion rules.

        :param max_file_size_bytes: Maximum file size to index.
        :returns: Freshly rebuilt index snapshot.
        """
        return self.build(
            Path(self.root),
            excluded_dirs=self.excluded_dirs,
            max_file_size_bytes=max_file_size_bytes,
        )

    def index_file(self, path: Path, *, max_file_size_bytes: int = 1_000_000) -> None:
        """
        Index one file in place.

        :param path: File path to index.
        :param max_file_size_bytes: Maximum file size to index.
        """
        file_path = Path(path)
        if self._should_skip_path(file_path, max_file_size_bytes=max_file_size_bytes):
            self.remove_file(file_path)
            return
        document = self._build_document(file_path, max_file_size_bytes=max_file_size_bytes)
        if document is None:
            self.remove_file(file_path)
            return
        self._documents[document.relative_path] = document

    def remove_file(self, path: Path) -> None:
        """
        Remove one file from the index.

        :param path: File path to remove.
        """
        relative_path = self._relative_path(path)
        self._documents.pop(relative_path, None)

    def list_files(self) -> list[FileInfo]:
        """
        Return metadata for all indexed files.

        :returns: File metadata ordered by relative path.
        """
        return [
            FileInfo(
                path=document.relative_path,
                language=document.language,
                size_bytes=document.size_bytes,
                symbol_count=len(document.symbols),
                last_indexed=document.indexed_at,
            )
            for document in self.documents
        ]

    def list_symbols(self, target: str | Path) -> list[SymbolInfo]:
        """
        Return symbols for one file or directory target.

        :param target: File path or directory path to inspect.
        :returns: Matching symbols ordered by path and line number.
        """
        documents = self._documents_for_target(target)
        symbols: list[SymbolInfo] = []
        for document in documents:
            symbols.extend(document.symbols)
        return sorted(
            symbols,
            key=lambda symbol: (
                symbol.file_path,
                symbol.line_start,
                symbol.parent or "",
                symbol.name,
            ),
        )

    def get_function(
        self,
        name: str,
        file_path: str | Path | None = None,
    ) -> FunctionResult | None:
        """
        Return the full source body for one function or method.

        :param name: Symbol name to locate.
        :param file_path: Optional file to limit the search.
        :returns: Function result when found, else `None`.
        """
        symbols = self.list_symbols(file_path or self.root)
        for symbol in symbols:
            if symbol.name == name and symbol.kind in {SymbolKind.FUNCTION, SymbolKind.METHOD}:
                document = self.find(symbol.file_path)
                if document is None:
                    return None
                return FunctionResult(
                    symbol=symbol,
                    source=_slice_source(document.source, symbol.line_start, symbol.line_end),
                )
        return None

    def search_symbol(self, query: str) -> list[SymbolInfo]:
        """
        Search for symbol names that contain the query string.

        :param query: Search term to match against symbol names.
        :returns: Matching symbols ordered by file and line number.
        """
        needle = query.lower().strip()
        if not needle:
            return []
        matches = [
            symbol
            for document in self.documents
            for symbol in document.symbols
            if needle in symbol.name.lower()
            or needle in (symbol.parent or "").lower()
            or needle in symbol.signature.lower()
        ]
        return sorted(
            matches,
            key=lambda symbol: (
                symbol.file_path,
                symbol.line_start,
                symbol.parent or "",
                symbol.name,
            ),
        )

    def describe_symbol(self, query: str) -> list[SymbolInfo]:
        """
        Return symbol metadata without the source body.

        :param query: Symbol name to describe.
        :returns: Matching symbols ordered by file and line number.
        """
        return self.search_symbol(query)

    def search(self, query: str, *, limit: int = 10) -> list[CodeSearchHit]:
        """
        Search the indexed documents using a small token scoring model.

        :param query: Search query string.
        :param limit: Maximum number of hits to return.
        :returns: Ranked code-search hits.
        """
        tokens = [token for token in _tokenize(query) if token]
        if not tokens:
            return []

        hits: list[CodeSearchHit] = []
        for document in self.documents:
            score, matches = _score_document(document, tokens)
            if score <= 0:
                continue
            hits.append(
                CodeSearchHit(
                    relative_path=document.relative_path,
                    path=document.path,
                    score=score,
                    snippet=_find_snippet(document, tokens),
                    matches=matches,
                    symbols=list(document.symbols),
                )
            )

        hits.sort(key=lambda item: (-item.score, item.relative_path))
        return hits[:limit]

    def find(self, relative_path: str) -> CodeDocument | None:
        """
        Return one indexed document by relative path.

        :param relative_path: Relative path to locate.
        :returns: Matching document or `None`.
        """
        return self._documents.get(_normalise_relative_path(relative_path))

    def _documents_for_target(self, target: str | Path) -> list[CodeDocument]:
        """
        Resolve one file-or-directory query against the in-memory index.

        Requirements:
            - Answer relative queries from indexed metadata rather than relying
              on host filesystem ``is_dir`` checks.
            - Support exact file matches as well as directory-prefix lookups.

        :param target: File path or directory path to inspect.
        :returns: Matching indexed documents.
        :raises ValueError: If an absolute target resolves outside the workspace root.
        """
        relative_target = self._normalise_target(target)
        if relative_target == "":
            return self.documents

        document = self.find(relative_target)
        if document is not None:
            return [document]

        prefix = f"{relative_target}/"
        return [
            indexed_document
            for indexed_document in self.documents
            if indexed_document.relative_path.startswith(prefix)
        ]

    def _index_path(
        self,
        file_path: Path,
        *,
        max_file_size_bytes: int,
    ) -> None:
        """Index one candidate path if it is supported."""
        document = self._build_document(file_path, max_file_size_bytes=max_file_size_bytes)
        if document is not None:
            self._documents[document.relative_path] = document

    def _build_document(
        self,
        file_path: Path,
        *,
        max_file_size_bytes: int,
    ) -> CodeDocument | None:
        """Build one document record for an indexable file."""
        if self._should_skip_path(file_path, max_file_size_bytes=max_file_size_bytes):
            return None
        try:
            raw = file_path.read_bytes()
        except OSError:
            return None
        if b"\x00" in raw:
            return None
        text = _decode_text(raw)
        if text is None:
            return None

        language = _detect_language(file_path)
        relative_path = self._relative_path(file_path)
        symbols = _extract_symbols(file_path, text, language, relative_path)
        checksum = hashlib.sha256(raw).hexdigest()
        return CodeDocument(
            path=str(file_path.resolve()),
            relative_path=relative_path,
            language=language,
            checksum=checksum,
            line_count=_count_lines(text),
            size_bytes=file_path.stat().st_size,
            symbols=symbols,
            preview_lines=_preview_lines(text),
            source=text,
            indexed_at=datetime.now(timezone.utc).isoformat(),
        )

    def _should_skip_path(
        self,
        path: Path,
        *,
        max_file_size_bytes: int,
    ) -> bool:
        """Decide whether a path should be skipped."""
        if any(part in self.excluded_dirs for part in path.parts):
            return True
        if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            return True
        try:
            return path.stat().st_size > max_file_size_bytes
        except OSError:
            return True

    def _normalise_target(self, target: str | Path) -> str:
        """
        Convert one query target into a stable relative lookup key.

        Requirements:
            - Preserve relative file and directory queries without consulting
              the host filesystem type metadata.
            - Reject absolute paths that escape the workspace root.

        :param target: File path or directory path to inspect.
        :returns: Normalized relative lookup key, or an empty string for the root.
        :raises ValueError: If an absolute target resolves outside the workspace root.
        """
        path = Path(target)
        if path.is_absolute():
            resolved = path.resolve()
            root_path = Path(self.root)
            try:
                return _normalise_relative_path(resolved.relative_to(root_path).as_posix())
            except ValueError as exc:
                raise ValueError(f"Path '{target}' resolves outside the workspace root") from exc
        return _normalise_relative_path(path.as_posix().strip("./"))

    def _relative_path(self, path: Path) -> str:
        """Return a normalized relative path for one file."""
        return _normalise_relative_path(path.resolve().relative_to(Path(self.root)).as_posix())


def _iter_source_files(root: Path, excluded_dirs: set[str]) -> Iterable[Path]:
    """Yield supported files under the workspace root in sorted order."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in excluded_dirs for part in path.parts):
            continue
        if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            continue
        yield path


def _detect_language(path: Path) -> str:
    """Infer a source language label from the file extension."""
    return _SUPPORTED_EXTENSIONS.get(path.suffix.lower(), "text")


def _decode_text(raw: bytes) -> str | None:
    """Decode file bytes into text using a small fallback chain."""
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _count_lines(text: str) -> int:
    """Count lines in a text payload using source-aware semantics."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _preview_lines(text: str, limit: int = 20) -> list[str]:
    """Return a short preview used by the search scorer."""
    return text.splitlines()[:limit]


def _normalise_relative_path(value: str) -> str:
    """Normalize path separators for stable lookups."""
    return value.replace("\\", "/")


def _slice_source(source: str, line_start: int, line_end: int) -> str:
    """Extract a source slice by 1-based line range."""
    lines = source.splitlines()
    start = max(0, line_start - 1)
    end = max(start, line_end)
    return "\n".join(lines[start:end]).rstrip()


def _tokenize(query: str) -> list[str]:
    """Split a query string into lowercase tokens."""
    return [token for token in re.split(r"\W+", query.lower()) if token]


def _symbol_search_text(name: str) -> str:
    """Expand CamelCase names into searchable token text."""
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)


def _score_document(document: CodeDocument, tokens: list[str]) -> tuple[int, list[str]]:
    """Score one document against a tokenised search query."""
    haystacks = [
        document.relative_path.lower(),
        Path(document.relative_path).name.lower(),
        "\n".join(document.preview_lines).lower(),
        " ".join(_symbol_search_text(symbol.name) for symbol in document.symbols).lower(),
    ]

    score = 0
    matches: list[str] = []
    for token in tokens:
        token_score = 0
        if token in haystacks[0] or token in haystacks[1]:
            token_score += 5
            matches.append(f"path:{token}")
        if token in haystacks[2]:
            token_score += 3
            matches.append(f"content:{token}")
        if token in haystacks[3]:
            token_score += 4
            matches.append(f"symbol:{token}")
        if token_score == 0:
            for preview in document.preview_lines:
                if token in preview.lower():
                    token_score += 2
                    matches.append(f"content:{token}")
                    break
        score += token_score

    if tokens and all(token in haystacks[2] for token in tokens):
        score += 2

    return score, sorted(set(matches))


def _find_snippet(document: CodeDocument, tokens: list[str]) -> str:
    """Choose a preview snippet for one search result."""
    if not document.preview_lines:
        return ""

    lowered = [line.lower() for line in document.preview_lines]
    for index, line in enumerate(lowered):
        if any(token in line for token in tokens):
            start = max(0, index - 1)
            end = min(len(document.preview_lines), index + 2)
            return "\n".join(document.preview_lines[start:end]).strip()

    return "\n".join(document.preview_lines[:3]).strip()


def _extract_symbols(
    path: Path,
    text: str,
    language: str,
    relative_path: str,
) -> list[SymbolInfo]:
    """
    Extract symbols from a source file using the tree-sitter parser backend.

    Requirements:
        - Parse supported languages through tree-sitter rather than fail-fast
          whole-file AST parsing.
        - Recover useful earlier symbols even when the source contains later
          syntax errors.

    :param path: Source file path.
    :param text: Decoded source text.
    :param language: Source language label.
    :param relative_path: Relative path recorded in symbol metadata.
    :returns: Extracted symbols ordered by line number.
    """
    parser = _get_tree_sitter_parser(language)
    source_bytes = text.encode("utf-8")
    tree = parser.parse(source_bytes)
    lines = text.splitlines()
    extractor = _TREE_SITTER_EXTRACTORS.get(language)
    if extractor is None:
        return []
    return extractor(tree.root_node, source_bytes, lines, relative_path, language)


def _extract_python_symbols(path: Path, text: str, relative_path: str) -> list[SymbolInfo]:
    """Retained for compatibility; tree-sitter now handles Python extraction."""
    return _extract_symbols(path, text, "python", relative_path)


def _python_signature(lines: list[str], start_line: int, end_line: int) -> str:
    """Return the leading signature line(s) for a Python definition."""
    collected: list[str] = []
    for line in lines[start_line - 1 : end_line]:
        collected.append(line.rstrip())
        if line.rstrip().endswith(":") and line.lstrip().startswith(
            ("def ", "async def ", "class ")
        ):
            break
    return "\n".join(collected).strip()


def _normalise_python_source(text: str) -> str:
    """
    Left-shift malformed top-level indentation in Python source snippets.

    Requirements:
        - Preserve already-valid first-line indentation.
        - Remove the smallest shared positive indentation from subsequent
          non-empty lines to recover common malformed snippets.

    :param text: Raw Python source text.
    :returns: Source text with a smaller shared indentation on later lines.
    """
    lines = text.splitlines()
    indents = [len(line) - len(line.lstrip()) for line in lines[1:] if line.strip()]
    if not indents:
        return text
    shift = min(indent for indent in indents if indent > 0)
    normalised = [lines[0].lstrip()]
    for line in lines[1:]:
        if not line.strip():
            normalised.append("")
            continue
        indent = len(line) - len(line.lstrip())
        if indent >= shift:
            normalised.append(line[shift:])
        else:
            normalised.append(line.lstrip())
    return "\n".join(normalised)


def _get_tree_sitter_language(language: str) -> Language:
    """
    Return the cached tree-sitter language object for one FAITH language label.

    Requirements:
        - Load each grammar lazily so the index pays the startup cost only when
          that language is first encountered.
        - Raise a clear runtime error when a required grammar package is
          missing, because the Code Index task depends on tree-sitter.

    :param language: FAITH language label such as ``python`` or ``typescript``.
    :returns: Loaded tree-sitter language object.
    :raises RuntimeError: If no grammar loader is available.
    """
    cached = _TREE_SITTER_LANGUAGES.get(language)
    if cached is not None:
        return cached
    loader = _TREE_SITTER_LANGUAGE_LOADERS.get(language)
    if loader is None:
        raise RuntimeError(f"Unsupported tree-sitter language: {language}")
    loaded = loader()
    _TREE_SITTER_LANGUAGES[language] = loaded
    return loaded


def _get_tree_sitter_parser(language: str) -> Parser:
    """
    Return the cached tree-sitter parser for one FAITH language label.

    Requirements:
        - Reuse parser instances per language to avoid repeated parser setup.
        - Support the current py-tree-sitter constructor and attribute styles.

    :param language: FAITH language label.
    :returns: Configured parser instance.
    """
    cached = _TREE_SITTER_PARSERS.get(language)
    if cached is not None:
        return cached
    ts_language = _get_tree_sitter_language(language)
    try:
        parser = Parser(ts_language)
    except TypeError:
        parser = Parser()
        parser.language = ts_language
    _TREE_SITTER_PARSERS[language] = parser
    return parser


def _walk_named_nodes(node: Node) -> Iterable[Node]:
    """
    Yield one node and all of its named descendants depth-first.

    :param node: Root node to traverse.
    :yields: Named tree-sitter nodes.
    """
    yield node
    for child in node.named_children:
        yield from _walk_named_nodes(child)


def _node_text(source_bytes: bytes, node: Node | None) -> str:
    """
    Extract decoded source text for one tree-sitter node.

    :param source_bytes: Original UTF-8 encoded source bytes.
    :param node: Node whose source slice should be returned.
    :returns: Decoded source text, or an empty string when the node is absent.
    """
    if node is None:
        return ""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _node_line_range(node: Node) -> tuple[int, int]:
    """
    Convert one node's point range into 1-based line numbers.

    :param node: Node to convert.
    :returns: ``(line_start, line_end)`` tuple.
    """
    return (node.start_point[0] + 1, node.end_point[0] + 1)


def _node_signature(lines: list[str], node: Node, *, terminators: tuple[str, ...]) -> str:
    """
    Build a compact declaration signature from the source lines for one node.

    :param lines: Source lines for the current document.
    :param node: Node whose declaration header should be returned.
    :param terminators: Line-ending tokens that complete the declaration header.
    :returns: Normalized declaration signature text.
    """
    line_start, line_end = _node_line_range(node)
    collected: list[str] = []
    for line in lines[line_start - 1 : line_end]:
        stripped = line.rstrip()
        collected.append(stripped)
        if any(stripped.endswith(token) for token in terminators):
            break
    return "\n".join(item for item in collected if item).strip()


def _python_docstring(source_bytes: bytes, node: Node) -> str | None:
    """
    Extract a Python docstring from a class or function body when present.

    :param source_bytes: Original UTF-8 encoded source bytes.
    :param node: Python class or function node.
    :returns: Docstring text without quote delimiters when available.
    """
    body = node.child_by_field_name("body")
    if body is None or not body.named_children:
        return None
    first_child = body.named_children[0]
    if first_child.type != "expression_statement" or not first_child.named_children:
        return None
    string_node = first_child.named_children[0]
    if string_node.type not in {"string", "concatenated_string"}:
        return None
    text = _node_text(source_bytes, string_node).strip()
    return text.strip("\"'").strip() or None


def _enclosing_parent_symbol(source_bytes: bytes, node: Node, type_name: str) -> str | None:
    """
    Return the enclosing parent symbol name for one nested node type.

    :param source_bytes: Original UTF-8 encoded source bytes.
    :param node: Node whose ancestors should be inspected.
    :param type_name: Ancestor node type to look for.
    :returns: Parent symbol name when found.
    """
    parent = node.parent
    while parent is not None:
        if parent.type == type_name:
            return _node_text(source_bytes, parent.child_by_field_name("name")) or None
        parent = parent.parent
    return None


def _extract_tree_sitter_python_symbols(
    root: Node,
    source_bytes: bytes,
    lines: list[str],
    relative_path: str,
    language: str,
) -> list[SymbolInfo]:
    """
    Extract Python symbols from a tree-sitter syntax tree.

    :param root: Parsed syntax-tree root node.
    :param source_bytes: Original UTF-8 encoded source bytes.
    :param lines: Source lines for signature extraction.
    :param relative_path: Relative file path recorded in symbol metadata.
    :param language: Source language label.
    :returns: Extracted symbols ordered by line number.
    """
    symbols: list[SymbolInfo] = []
    for node in _walk_named_nodes(root):
        if node.type == "class_definition":
            name = _node_text(source_bytes, node.child_by_field_name("name"))
            if not name:
                continue
            line_start, line_end = _node_line_range(node)
            symbols.append(
                SymbolInfo(
                    name=name,
                    kind=SymbolKind.CLASS,
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_node_signature(lines, node, terminators=(":",)),
                    docstring=_python_docstring(source_bytes, node),
                    language=language,
                    parent=None,
                )
            )
        elif node.type in {"function_definition", "decorated_definition"}:
            target = node
            if node.type == "decorated_definition":
                children = [
                    child
                    for child in node.named_children
                    if child.type in {"function_definition", "class_definition"}
                ]
                if not children:
                    continue
                target = children[-1]
                if target.type == "class_definition":
                    continue
            if target.type != "function_definition":
                continue
            name = _node_text(source_bytes, target.child_by_field_name("name"))
            if not name:
                continue
            parent_name = _enclosing_parent_symbol(source_bytes, target, "class_definition")
            line_start, line_end = _node_line_range(target)
            symbols.append(
                SymbolInfo(
                    name=name,
                    kind=SymbolKind.METHOD if parent_name else SymbolKind.FUNCTION,
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_node_signature(lines, target, terminators=(":",)),
                    docstring=_python_docstring(source_bytes, target),
                    language=language,
                    parent=parent_name,
                )
            )
    return sorted(symbols, key=lambda symbol: (symbol.line_start, symbol.name))


def _extract_tree_sitter_js_like_symbols(
    root: Node,
    source_bytes: bytes,
    lines: list[str],
    relative_path: str,
    language: str,
) -> list[SymbolInfo]:
    """
    Extract JavaScript, TypeScript, and TSX symbols from tree-sitter nodes.

    :param root: Parsed syntax-tree root node.
    :param source_bytes: Original UTF-8 encoded source bytes.
    :param lines: Source lines for signature extraction.
    :param relative_path: Relative file path recorded in symbol metadata.
    :param language: Source language label.
    :returns: Extracted symbols ordered by line number.
    """
    symbols: list[SymbolInfo] = []
    for node in _walk_named_nodes(root):
        if node.type == "class_declaration":
            name = _node_text(source_bytes, node.child_by_field_name("name"))
            if not name:
                continue
            line_start, line_end = _node_line_range(node)
            symbols.append(
                SymbolInfo(
                    name=name,
                    kind=SymbolKind.CLASS,
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_node_signature(lines, node, terminators=("{",)),
                    docstring=_preceding_comment_block(lines, line_start),
                    language=language,
                    parent=None,
                )
            )
        elif node.type == "function_declaration":
            name = _node_text(source_bytes, node.child_by_field_name("name"))
            if not name:
                continue
            line_start, line_end = _node_line_range(node)
            symbols.append(
                SymbolInfo(
                    name=name,
                    kind=SymbolKind.FUNCTION,
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_node_signature(lines, node, terminators=("{",)),
                    docstring=_preceding_comment_block(lines, line_start),
                    language=language,
                    parent=None,
                )
            )
        elif node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            name = _node_text(source_bytes, name_node)
            if not name:
                continue
            parent_name = _enclosing_parent_symbol(source_bytes, node, "class_declaration")
            line_start, line_end = _node_line_range(node)
            symbols.append(
                SymbolInfo(
                    name=name,
                    kind=SymbolKind.METHOD,
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_node_signature(lines, node, terminators=("{",)),
                    docstring=_preceding_comment_block(lines, line_start),
                    language=language,
                    parent=parent_name,
                )
            )
    return sorted(symbols, key=lambda symbol: (symbol.line_start, symbol.name))


def _extract_tree_sitter_java_symbols(
    root: Node,
    source_bytes: bytes,
    lines: list[str],
    relative_path: str,
    language: str,
) -> list[SymbolInfo]:
    """
    Extract Java class and method symbols from tree-sitter nodes.

    :param root: Parsed syntax-tree root node.
    :param source_bytes: Original UTF-8 encoded source bytes.
    :param lines: Source lines for signature extraction.
    :param relative_path: Relative file path recorded in symbol metadata.
    :param language: Source language label.
    :returns: Extracted symbols ordered by line number.
    """
    symbols: list[SymbolInfo] = []
    for node in _walk_named_nodes(root):
        if node.type == "class_declaration":
            name = _node_text(source_bytes, node.child_by_field_name("name"))
            if not name:
                continue
            line_start, line_end = _node_line_range(node)
            symbols.append(
                SymbolInfo(
                    name=name,
                    kind=SymbolKind.CLASS,
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_node_signature(lines, node, terminators=("{",)),
                    docstring=_preceding_comment_block(lines, line_start),
                    language=language,
                    parent=None,
                )
            )
        elif node.type == "method_declaration":
            name = _node_text(source_bytes, node.child_by_field_name("name"))
            if not name:
                continue
            parent_name = _enclosing_parent_symbol(source_bytes, node, "class_declaration")
            line_start, line_end = _node_line_range(node)
            symbols.append(
                SymbolInfo(
                    name=name,
                    kind=SymbolKind.METHOD if parent_name else SymbolKind.FUNCTION,
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_node_signature(lines, node, terminators=("{", ";")),
                    docstring=_preceding_comment_block(lines, line_start),
                    language=language,
                    parent=parent_name,
                )
            )
    return sorted(symbols, key=lambda symbol: (symbol.line_start, symbol.name))


def _extract_tree_sitter_go_symbols(
    root: Node,
    source_bytes: bytes,
    lines: list[str],
    relative_path: str,
    language: str,
) -> list[SymbolInfo]:
    """
    Extract Go type, function, and method symbols from tree-sitter nodes.

    :param root: Parsed syntax-tree root node.
    :param source_bytes: Original UTF-8 encoded source bytes.
    :param lines: Source lines for signature extraction.
    :param relative_path: Relative file path recorded in symbol metadata.
    :param language: Source language label.
    :returns: Extracted symbols ordered by line number.
    """
    symbols: list[SymbolInfo] = []
    for node in _walk_named_nodes(root):
        if node.type == "type_spec":
            type_node = node.child_by_field_name("type")
            if type_node is None or type_node.type not in {"struct_type", "interface_type"}:
                continue
            name = _node_text(source_bytes, node.child_by_field_name("name"))
            if not name:
                continue
            line_start, line_end = _node_line_range(node)
            symbols.append(
                SymbolInfo(
                    name=name,
                    kind=SymbolKind.STRUCT
                    if type_node.type == "struct_type"
                    else SymbolKind.INTERFACE,
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_node_signature(lines, node, terminators=("{",)),
                    docstring=_preceding_comment_block(lines, line_start),
                    language=language,
                    parent=None,
                )
            )
        elif node.type in {"function_declaration", "method_declaration"}:
            name = _node_text(source_bytes, node.child_by_field_name("name"))
            if not name:
                continue
            receiver = node.child_by_field_name("receiver")
            parent_name = None
            if receiver is not None:
                parent_name = _go_receiver_name(source_bytes, receiver)
            line_start, line_end = _node_line_range(node)
            symbols.append(
                SymbolInfo(
                    name=name,
                    kind=SymbolKind.METHOD if parent_name else SymbolKind.FUNCTION,
                    file_path=relative_path,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_node_signature(lines, node, terminators=("{",)),
                    docstring=_preceding_comment_block(lines, line_start),
                    language=language,
                    parent=parent_name,
                )
            )
    return sorted(symbols, key=lambda symbol: (symbol.line_start, symbol.name))


def _go_receiver_name(source_bytes: bytes, receiver: Node) -> str | None:
    """
    Extract the receiver type name from a Go method receiver node.

    :param source_bytes: Original UTF-8 encoded source bytes.
    :param receiver: Receiver node from a Go method declaration.
    :returns: Receiver type name without pointer markers when present.
    """
    for node in _walk_named_nodes(receiver):
        if node.type in {"type_identifier", "qualified_type"}:
            return _node_text(source_bytes, node).split(".")[-1].lstrip("*")
    raw = _node_text(source_bytes, receiver).strip("() ")
    parts = raw.split()
    if not parts:
        return None
    return parts[-1].split(".")[-1].lstrip("*")


_TREE_SITTER_EXTRACTORS = {
    "python": _extract_tree_sitter_python_symbols,
    "javascript": _extract_tree_sitter_js_like_symbols,
    "typescript": _extract_tree_sitter_js_like_symbols,
    "tsx": _extract_tree_sitter_js_like_symbols,
    "java": _extract_tree_sitter_java_symbols,
    "go": _extract_tree_sitter_go_symbols,
}


def _extract_js_like_symbols(
    path: Path,
    text: str,
    language: str,
    relative_path: str,
) -> list[SymbolInfo]:
    """Retained for compatibility; tree-sitter now handles JS-like extraction."""
    return _extract_symbols(path, text, language, relative_path)


def _extract_java_symbols(path: Path, text: str, relative_path: str) -> list[SymbolInfo]:
    """Retained for compatibility; tree-sitter now handles Java extraction."""
    return _extract_symbols(path, text, "java", relative_path)


def _extract_go_symbols(path: Path, text: str, relative_path: str) -> list[SymbolInfo]:
    """Retained for compatibility; tree-sitter now handles Go extraction."""
    return _extract_symbols(path, text, "go", relative_path)


def _preceding_comment_block(lines: list[str], start_line: int) -> str | None:
    """Collect the contiguous comment block immediately above a symbol."""
    comments: list[str] = []
    index = start_line - 2
    while index >= 0:
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            break
        if stripped.startswith("//"):
            comments.append(stripped.lstrip("/").strip())
            index -= 1
            continue
        if stripped.startswith("/*"):
            comments.append(stripped.lstrip("/*").rstrip("*/").strip())
            break
        if stripped.startswith("*"):
            comments.append(stripped.lstrip("*").strip())
            index -= 1
            continue
        break
    comments.reverse()
    return "\n".join(comment for comment in comments if comment) or None


_CLASS_RE_JS = re.compile(r"^\s*(?:export\s+)?class\s+(?P<name>[A-Za-z_]\w*)")
_FUNC_RE_JS = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_]\w*)")
_METHOD_RE_JS = re.compile(r"^\s*(?:async\s+)?(?P<name>[A-Za-z_]\w*)\s*\(")
_CLASS_RE_JAVA = re.compile(
    r"^\s*(?:public|private|protected|abstract|final|static|\s)*class\s+(?P<name>[A-Za-z_]\w*)"
)
_METHOD_RE_JAVA = re.compile(
    r"^\s*(?:public|private|protected|static|final|abstract|synchronized|\s)+"
    r"[\w<>\[\], ?]+?\s+(?P<name>[A-Za-z_]\w*)\s*\("
)
_STRUCT_RE_GO = re.compile(r"^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+(?P<kind>struct|interface)\s*\{")
_FUNC_RE_GO = re.compile(r"^\s*func\s+(?:\((?P<receiver>[^)]+)\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(")


def _class_ranges_js_like(text: str, language: str) -> list[tuple[str, int, int, str]]:
    """Return class declarations for JS-like languages."""
    results: list[tuple[str, int, int, str]] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        match = _CLASS_RE_JS.match(line)
        if match is None:
            continue
        start_index = _line_start_offset(lines, line_number)
        brace_index = text.find("{", start_index)
        if brace_index == -1:
            continue
        end_index = _find_matching_brace(text, brace_index)
        if end_index is None:
            continue
        end_line = _line_number_for_offset(text, end_index)
        header = line.strip()
        results.append((match.group("name"), line_number, end_line, header))
    return results


def _method_ranges_js_like(
    body_lines: list[str],
    offset_line: int,
) -> list[tuple[str, int, int, str]]:
    """Return method declarations within a JS-like class body."""
    results: list[tuple[str, int, int, str]] = []
    for line_number, line in enumerate(body_lines, start=offset_line + 1):
        match = _METHOD_RE_JS.match(line)
        if match is None:
            continue
        stripped = line.strip()
        if stripped.startswith("function "):
            continue
        if stripped.startswith("class "):
            continue
        if "(" not in stripped or stripped.startswith(("if ", "for ", "while ", "switch ")):
            continue
        start_index = _line_start_offset(body_lines, line_number - offset_line)
        brace_index = line.find("{")
        if brace_index == -1:
            continue
        end_index = _find_matching_brace("\n".join(body_lines), start_index + brace_index)
        if end_index is None:
            continue
        end_line = offset_line + _line_number_for_offset("\n".join(body_lines), end_index)
        results.append((match.group("name"), line_number, end_line, stripped))
    return results


def _function_ranges_js_like(text: str, language: str) -> list[tuple[str, int, int, str]]:
    """Return top-level function declarations for JS-like languages."""
    results: list[tuple[str, int, int, str]] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        match = _FUNC_RE_JS.match(line)
        if match is None:
            continue
        start_index = _line_start_offset(lines, line_number)
        brace_index = text.find("{", start_index)
        if brace_index == -1:
            continue
        end_index = _find_matching_brace(text, brace_index)
        if end_index is None:
            continue
        end_line = _line_number_for_offset(text, end_index)
        results.append((match.group("name"), line_number, end_line, line.strip()))
    return results


def _class_ranges_java(text: str) -> list[tuple[str, int, int, str]]:
    """Return Java class declarations."""
    results: list[tuple[str, int, int, str]] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        match = _CLASS_RE_JAVA.match(line)
        if match is None:
            continue
        start_index = _line_start_offset(lines, line_number)
        brace_index = text.find("{", start_index)
        if brace_index == -1:
            continue
        end_index = _find_matching_brace(text, brace_index)
        if end_index is None:
            continue
        end_line = _line_number_for_offset(text, end_index)
        results.append((match.group("name"), line_number, end_line, line.strip()))
    return results


def _method_ranges_java(
    body_lines: list[str],
    offset_line: int,
) -> list[tuple[str, int, int, str]]:
    """Return Java method declarations within a class body."""
    results: list[tuple[str, int, int, str]] = []
    body_text = "\n".join(body_lines)
    lines = body_text.splitlines()
    for line_number, line in enumerate(lines, start=offset_line + 1):
        match = _METHOD_RE_JAVA.match(line)
        if match is None:
            continue
        start_index = _line_start_offset(lines, line_number - offset_line)
        brace_index = body_text.find("{", start_index)
        if brace_index == -1:
            continue
        end_index = _find_matching_brace(body_text, brace_index)
        if end_index is None:
            continue
        end_line = offset_line + _line_number_for_offset(body_text, end_index)
        results.append((match.group("name"), line_number, end_line, line.strip()))
    return results


def _class_ranges_go(text: str) -> list[tuple[str, int, int, str, SymbolKind]]:
    """Return Go struct and interface declarations."""
    results: list[tuple[str, int, int, str, SymbolKind]] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        match = _STRUCT_RE_GO.match(line)
        if match is None:
            continue
        brace_index = line.find("{")
        end_index = _find_matching_brace(text, _line_start_offset(lines, line_number) + brace_index)
        if end_index is None:
            continue
        end_line = _line_number_for_offset(text, end_index)
        kind = SymbolKind.STRUCT if match.group("kind") == "struct" else SymbolKind.INTERFACE
        results.append((match.group("name"), line_number, end_line, line.strip(), kind))
    return results


def _function_ranges_go(text: str) -> list[tuple[str, int, int, str, str | None]]:
    """Return Go function and method declarations."""
    results: list[tuple[str, int, int, str, str | None]] = []
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        match = _FUNC_RE_GO.match(line)
        if match is None:
            continue
        start_index = _line_start_offset(lines, line_number)
        brace_index = text.find("{", start_index)
        if brace_index == -1:
            continue
        end_index = _find_matching_brace(text, brace_index)
        if end_index is None:
            continue
        end_line = _line_number_for_offset(text, end_index)
        receiver = match.group("receiver")
        parent = None
        if receiver:
            parent = receiver.split()[-1].lstrip("*")
        results.append((match.group("name"), line_number, end_line, line.strip(), parent))
    return results


def _line_start_offset(lines: list[str], line_number: int) -> int:
    """Return the character offset for the start of one line."""
    return sum(len(line) + 1 for line in lines[: line_number - 1])


def _line_number_for_offset(text: str, offset: int) -> int:
    """Convert a character offset into a 1-based line number."""
    return text.count("\n", 0, offset) + 1


def _find_matching_brace(text: str, open_index: int) -> int | None:
    """Find the closing brace for one code block."""
    depth = 0
    in_string: str | None = None
    escape = False
    in_line_comment = False
    in_block_comment = False

    for index in range(open_index, len(text)):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
            continue

        if in_string is not None:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == in_string:
                in_string = None
            continue

        if char == "/" and next_char == "/":
            in_line_comment = True
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            continue
        if char in {'"', "'", "`"}:
            in_string = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None
