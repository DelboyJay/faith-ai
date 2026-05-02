# FAITH-027 — Code Index MCP Server (tree-sitter)

**Phase:** 6 — MCP Tool Servers (Built-in)
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-022
**FRS Reference:** Section 4.8

---

## Objective

Implement a dedicated MCP tool server that maintains a live, AST-based index of all source files in the workspace mount. The server uses `tree-sitter` to parse source code into abstract syntax trees, extracting functions, classes, methods, and module-level symbols. It exposes five MCP commands (`list_files`, `list_symbols`, `get_function`, `search_symbol`, `describe_symbol`) that return structured JSON, enabling agents to understand codebase structure without loading full files into their context window. The index updates in real time via filesystem event watching, so agents always query a current view of the codebase.

---

## Architecture

```
containers/code-index/
├── Dockerfile                ← Dedicated container image
└── requirements.txt          ← tree-sitter, tree-sitter-languages, watchfiles, mcp

faith/tools/code_index/
├── __init__.py
├── server.py                 ← MCP server entry point + command handlers
├── indexer.py                ← Core indexing engine (tree-sitter parsing)
├── watcher.py                ← Filesystem event watcher (real-time re-index)
├── models.py                 ← Pydantic response models (structured JSON)
└── languages.py              ← Language registry + tree-sitter grammar loading

tests/
└── test_code_index.py        ← Unit and integration tests
```

### Container Design

The Code Index server runs as a standalone Docker container on the `maf-network`. It receives the project workspace as a read-only volume mount. It does **not** need Redis — agents call it via MCP tool calls routed through the PA's MCP adapter (FAITH-022 establishes the tool container pattern).

```
┌──────────────────────┐
│   Agent Container    │
│  (software-developer)│
└──────────┬───────────┘
           │ MCP tool_call (via PA adapter)
           ▼
┌──────────────────────┐      ┌────────────────────┐
│    PA Container      │─────►│  Code Index MCP    │
│  (routes tool calls) │ MCP  │  Container         │
└──────────────────────┘      │                    │
                              │  workspace (ro)    │
                              │  ┌──────────────┐  │
                              │  │ tree-sitter   │  │
                              │  │ index (memory)│  │
                              │  └──────────────┘  │
                              │  watchfiles loop   │
                              └────────────────────┘
```

---

## Files to Create

### 1. `faith/tools/code_index/models.py`

```python
"""Pydantic models for Code Index MCP server responses.

All MCP commands return structured JSON via these models.
Agents receive predictable, parseable responses — never raw text.

FRS Reference: Section 4.8.3
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SymbolKind(str, Enum):
    """Classification of a code symbol."""

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    VARIABLE = "variable"
    CONSTANT = "constant"
    MODULE = "module"
    ENUM = "enum"
    STRUCT = "struct"
    TYPE_ALIAS = "type_alias"


class SymbolInfo(BaseModel):
    """A single symbol extracted from source code."""

    name: str = Field(description="Symbol name (e.g. 'parse_config')")
    kind: SymbolKind = Field(description="Symbol classification")
    file_path: str = Field(description="Relative path from workspace root")
    line_start: int = Field(description="1-based start line number")
    line_end: int = Field(description="1-based end line number")
    signature: str = Field(
        description="Full signature (e.g. 'def parse_config(path: str) -> dict')"
    )
    docstring: Optional[str] = Field(
        default=None,
        description="First docstring/comment block, if present",
    )
    language: str = Field(description="Source language (e.g. 'python')")
    parent: Optional[str] = Field(
        default=None,
        description="Parent symbol name (e.g. class name for a method)",
    )


class FileInfo(BaseModel):
    """Metadata for a single indexed file."""

    path: str = Field(description="Relative path from workspace root")
    language: str = Field(description="Detected language")
    size_bytes: int = Field(description="File size in bytes")
    symbol_count: int = Field(description="Number of top-level symbols")
    last_indexed: str = Field(description="ISO 8601 timestamp of last index")


class ListFilesResponse(BaseModel):
    """Response for the list_files command."""

    workspace_root: str
    total_files: int
    files: list[FileInfo]


class ListSymbolsResponse(BaseModel):
    """Response for the list_symbols command."""

    target: str = Field(description="File path or module path queried")
    total_symbols: int
    symbols: list[SymbolInfo]


class GetFunctionResponse(BaseModel):
    """Response for the get_function command."""

    symbol: SymbolInfo
    source: str = Field(description="Full source code of the function/method")


class SearchSymbolResponse(BaseModel):
    """Response for the search_symbol command."""

    query: str = Field(description="The search term used")
    total_matches: int
    matches: list[SymbolInfo]


class DescribeSymbolResponse(BaseModel):
    """Response for the describe_symbol command."""

    query: str
    total_matches: int
    descriptions: list[SymbolInfo] = Field(
        description="Symbols with signature + docstring only (no full body)"
    )


class ErrorResponse(BaseModel):
    """Structured error response."""

    error: str
    detail: Optional[str] = None
```

### 2. `faith/tools/code_index/languages.py`

```python
"""Language registry for tree-sitter grammar loading.

Manages the mapping between file extensions and tree-sitter parsers.
Supports Python, JavaScript, TypeScript, Java, and Go at minimum,
with extensibility for additional languages.

FRS Reference: Section 4.8.2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import tree_sitter

logger = logging.getLogger("faith.tools.code_index.languages")

# Mapping from tree-sitter-languages library names to our internal names.
# tree-sitter-languages bundles pre-built grammars for many languages.
_LANGUAGE_NAMES = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "java": "java",
    "go": "go",
}

# File extension to language name mapping.
_EXTENSION_MAP: dict[str, str] = {
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

# Directories and patterns to skip during indexing.
SKIP_DIRS: set[str] = {
    "__pycache__",
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    ".faith",
    "venv",
    ".venv",
    "env",
    ".env",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",       # Java/Go build output
    "vendor",       # Go vendor directory
    "coverage",
    ".coverage",
}

SKIP_FILES: set[str] = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
}

# Maximum file size to index (in bytes). Files larger than this are
# skipped to prevent memory issues with generated/minified code.
MAX_FILE_SIZE = 1_048_576  # 1 MB


@dataclass
class LanguageConfig:
    """Configuration for a supported language."""

    name: str
    extensions: list[str]
    parser: Optional[tree_sitter.Parser] = field(default=None, repr=False)

    # tree-sitter query patterns for extracting symbols.
    # These are language-specific S-expression queries.
    function_query: str = ""
    class_query: str = ""
    docstring_node_type: str = ""


class LanguageRegistry:
    """Manages tree-sitter parsers and language configurations.

    Lazily loads parsers on first use. Thread-safe for read access
    after initialisation.
    """

    def __init__(self) -> None:
        self._languages: dict[str, LanguageConfig] = {}
        self._parsers: dict[str, tree_sitter.Parser] = {}
        self._initialised = False

    def initialise(self) -> None:
        """Load all supported language grammars.

        Uses tree_sitter_languages to load pre-built grammars.
        Languages that fail to load are logged and skipped — the
        server continues with whatever languages are available.
        """
        if self._initialised:
            return

        try:
            import tree_sitter_languages
        except ImportError:
            logger.error(
                "tree_sitter_languages not installed — "
                "no languages will be available"
            )
            self._initialised = True
            return

        for lang_name in _LANGUAGE_NAMES:
            try:
                language = tree_sitter_languages.get_language(lang_name)
                parser = tree_sitter_languages.get_parser(lang_name)

                extensions = [
                    ext for ext, name in _EXTENSION_MAP.items()
                    if name == lang_name
                ]

                config = LanguageConfig(
                    name=lang_name,
                    extensions=extensions,
                    parser=parser,
                )
                self._languages[lang_name] = config
                self._parsers[lang_name] = parser
                logger.info(f"Loaded tree-sitter grammar: {lang_name}")

            except Exception as e:
                logger.warning(
                    f"Failed to load tree-sitter grammar for "
                    f"'{lang_name}': {e}"
                )

        self._initialised = True
        logger.info(
            f"Language registry initialised: "
            f"{len(self._languages)} languages available"
        )

    def get_language_for_file(self, file_path: str | Path) -> Optional[str]:
        """Determine the language for a file based on its extension.

        Args:
            file_path: Path to the file.

        Returns:
            Language name or None if unsupported.
        """
        ext = Path(file_path).suffix.lower()
        return _EXTENSION_MAP.get(ext)

    def get_parser(self, language: str) -> Optional[tree_sitter.Parser]:
        """Get the tree-sitter parser for a language.

        Args:
            language: Language name (e.g. 'python').

        Returns:
            Parser instance or None if language not available.
        """
        if not self._initialised:
            self.initialise()
        return self._parsers.get(language)

    def is_supported(self, file_path: str | Path) -> bool:
        """Check if a file's language is supported for indexing.

        Args:
            file_path: Path to check.

        Returns:
            True if the file can be indexed.
        """
        return self.get_language_for_file(file_path) is not None

    def should_skip_dir(self, dir_name: str) -> bool:
        """Check if a directory should be skipped during scanning.

        Args:
            dir_name: Directory name (not full path).

        Returns:
            True if the directory should be skipped.
        """
        return dir_name in SKIP_DIRS or dir_name.startswith(".")

    def should_skip_file(self, file_path: str | Path) -> bool:
        """Check if a file should be skipped during scanning.

        Skips unsupported languages, files exceeding MAX_FILE_SIZE,
        and known skip-list files.

        Args:
            file_path: Path to check.

        Returns:
            True if the file should be skipped.
        """
        path = Path(file_path)

        if path.name in SKIP_FILES:
            return True

        if not self.is_supported(path):
            return True

        try:
            if path.stat().st_size > MAX_FILE_SIZE:
                logger.debug(f"Skipping oversized file: {path}")
                return True
        except OSError:
            return True

        return False

    @property
    def supported_extensions(self) -> list[str]:
        """Return all supported file extensions."""
        return list(_EXTENSION_MAP.keys())

    @property
    def supported_languages(self) -> list[str]:
        """Return all available language names."""
        return list(self._languages.keys())
```

### 3. `faith/tools/code_index/indexer.py`

```python
"""Core indexing engine — parses source files with tree-sitter and
maintains an in-memory index of all symbols.

The indexer performs two operations:
1. Full scan — walk the workspace directory and index every supported file.
2. Incremental update — re-index a single file after a change event.

The index is an in-memory dict keyed by relative file path. Each entry
holds the parsed symbol list and file metadata. The index is not
persisted — it is rebuilt on container startup (fast for typical project
sizes).

FRS Reference: Section 4.8
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import tree_sitter

from faith.tools.code_index.languages import LanguageRegistry
from faith.tools.code_index.models import (
    FileInfo,
    SymbolInfo,
    SymbolKind,
)

logger = logging.getLogger("faith.tools.code_index.indexer")


# ──────────────────────────────────────────────────
# tree-sitter query patterns per language
# ──────────────────────────────────────────────────

# These S-expression queries extract function/class definitions from
# the AST. They must match the tree-sitter grammar for each language.

_PYTHON_FUNCTION_QUERY = """
(function_definition
  name: (identifier) @func.name) @func.def
"""

_PYTHON_CLASS_QUERY = """
(class_definition
  name: (identifier) @class.name) @class.def
"""

_PYTHON_METHOD_QUERY = """
(class_definition
  name: (identifier) @class.name
  body: (block
    (function_definition
      name: (identifier) @method.name) @method.def))
"""

_JS_TS_FUNCTION_QUERY = """
[
  (function_declaration
    name: (identifier) @func.name) @func.def
  (export_statement
    declaration: (function_declaration
      name: (identifier) @func.name) @func.def)
]
"""

_JS_TS_CLASS_QUERY = """
[
  (class_declaration
    name: (identifier) @class.name) @class.def
  (export_statement
    declaration: (class_declaration
      name: (identifier) @class.name) @class.def)
]
"""

_JS_TS_METHOD_QUERY = """
(class_declaration
  name: (identifier) @class.name
  body: (class_body
    (method_definition
      name: (property_identifier) @method.name) @method.def))
"""

_JAVA_FUNCTION_QUERY = """
(method_declaration
  name: (identifier) @func.name) @func.def
"""

_JAVA_CLASS_QUERY = """
(class_declaration
  name: (identifier) @class.name) @class.def
"""

_GO_FUNCTION_QUERY = """
(function_declaration
  name: (identifier) @func.name) @func.def
"""

_GO_METHOD_QUERY = """
(method_declaration
  name: (field_identifier) @method.name) @method.def
"""

_GO_STRUCT_QUERY = """
(type_declaration
  (type_spec
    name: (type_identifier) @class.name
    type: (struct_type))) @class.def
"""

_GO_INTERFACE_QUERY = """
(type_declaration
  (type_spec
    name: (type_identifier) @class.name
    type: (interface_type))) @class.def
"""

# Language -> list of (query_string, symbol_kind, is_method)
_QUERY_REGISTRY: dict[str, list[tuple[str, SymbolKind, bool]]] = {
    "python": [
        (_PYTHON_FUNCTION_QUERY, SymbolKind.FUNCTION, False),
        (_PYTHON_CLASS_QUERY, SymbolKind.CLASS, False),
        (_PYTHON_METHOD_QUERY, SymbolKind.METHOD, True),
    ],
    "javascript": [
        (_JS_TS_FUNCTION_QUERY, SymbolKind.FUNCTION, False),
        (_JS_TS_CLASS_QUERY, SymbolKind.CLASS, False),
        (_JS_TS_METHOD_QUERY, SymbolKind.METHOD, True),
    ],
    "typescript": [
        (_JS_TS_FUNCTION_QUERY, SymbolKind.FUNCTION, False),
        (_JS_TS_CLASS_QUERY, SymbolKind.CLASS, False),
        (_JS_TS_METHOD_QUERY, SymbolKind.METHOD, True),
    ],
    "tsx": [
        (_JS_TS_FUNCTION_QUERY, SymbolKind.FUNCTION, False),
        (_JS_TS_CLASS_QUERY, SymbolKind.CLASS, False),
        (_JS_TS_METHOD_QUERY, SymbolKind.METHOD, True),
    ],
    "java": [
        (_JAVA_FUNCTION_QUERY, SymbolKind.FUNCTION, False),
        (_JAVA_CLASS_QUERY, SymbolKind.CLASS, False),
    ],
    "go": [
        (_GO_FUNCTION_QUERY, SymbolKind.FUNCTION, False),
        (_GO_METHOD_QUERY, SymbolKind.METHOD, True),
        (_GO_STRUCT_QUERY, SymbolKind.STRUCT, False),
        (_GO_INTERFACE_QUERY, SymbolKind.INTERFACE, False),
    ],
}


class _FileIndex:
    """Index entry for a single file."""

    def __init__(
        self,
        rel_path: str,
        language: str,
        size_bytes: int,
        symbols: list[SymbolInfo],
        indexed_at: datetime,
    ):
        self.rel_path = rel_path
        self.language = language
        self.size_bytes = size_bytes
        self.symbols = symbols
        self.indexed_at = indexed_at

    def to_file_info(self) -> FileInfo:
        return FileInfo(
            path=self.rel_path,
            language=self.language,
            size_bytes=self.size_bytes,
            symbol_count=len(self.symbols),
            last_indexed=self.indexed_at.isoformat(),
        )


class CodeIndexer:
    """In-memory code index backed by tree-sitter parsing.

    Thread-safe: uses a read-write lock so queries can run
    concurrently with single-file updates.

    Attributes:
        workspace_root: Absolute path to the mounted workspace.
        language_registry: Manages tree-sitter parsers.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        language_registry: LanguageRegistry,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.lang = language_registry
        self._index: dict[str, _FileIndex] = {}
        self._lock = threading.RLock()
        self._compiled_queries: dict[str, list] = {}

    # ──────────────────────────────────────────────
    # Full scan
    # ──────────────────────────────────────────────

    def full_scan(self) -> int:
        """Walk the workspace and index all supported files.

        Returns:
            Number of files indexed.
        """
        logger.info(f"Starting full scan of {self.workspace_root}")
        count = 0

        for dirpath, dirnames, filenames in os.walk(self.workspace_root):
            # Prune skip directories in-place
            dirnames[:] = [
                d for d in dirnames
                if not self.lang.should_skip_dir(d)
            ]

            for filename in filenames:
                file_path = Path(dirpath) / filename
                if self.lang.should_skip_file(file_path):
                    continue

                try:
                    self.index_file(file_path)
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to index {file_path}: {e}")

        logger.info(f"Full scan complete: {count} files indexed")
        return count

    # ──────────────────────────────────────────────
    # Single file indexing
    # ──────────────────────────────────────────────

    def index_file(self, file_path: str | Path) -> Optional[_FileIndex]:
        """Parse and index a single file.

        Replaces any existing index entry for this file.

        Args:
            file_path: Absolute path to the file.

        Returns:
            The file index entry, or None if the file could not
            be parsed.
        """
        file_path = Path(file_path).resolve()
        rel_path = str(file_path.relative_to(self.workspace_root))

        language = self.lang.get_language_for_file(file_path)
        if language is None:
            return None

        parser = self.lang.get_parser(language)
        if parser is None:
            return None

        try:
            source_bytes = file_path.read_bytes()
            source_text = source_bytes.decode("utf-8", errors="replace")
        except OSError as e:
            logger.warning(f"Cannot read {file_path}: {e}")
            return None

        tree = parser.parse(source_bytes)
        symbols = self._extract_symbols(
            tree, source_text, source_bytes, language, rel_path
        )

        entry = _FileIndex(
            rel_path=rel_path,
            language=language,
            size_bytes=len(source_bytes),
            symbols=symbols,
            indexed_at=datetime.now(timezone.utc),
        )

        with self._lock:
            self._index[rel_path] = entry

        logger.debug(
            f"Indexed {rel_path}: {len(symbols)} symbols "
            f"({language})"
        )
        return entry

    def remove_file(self, file_path: str | Path) -> None:
        """Remove a file from the index (after deletion).

        Args:
            file_path: Absolute path to the deleted file.
        """
        try:
            rel_path = str(
                Path(file_path).resolve().relative_to(self.workspace_root)
            )
        except ValueError:
            return

        with self._lock:
            removed = self._index.pop(rel_path, None)

        if removed:
            logger.debug(f"Removed from index: {rel_path}")

    # ──────────────────────────────────────────────
    # Symbol extraction
    # ──────────────────────────────────────────────

    def _extract_symbols(
        self,
        tree: tree_sitter.Tree,
        source_text: str,
        source_bytes: bytes,
        language: str,
        rel_path: str,
    ) -> list[SymbolInfo]:
        """Extract all symbols from a parsed AST.

        Args:
            tree: Parsed tree-sitter tree.
            source_text: Source code as string (for signature extraction).
            source_bytes: Source code as bytes (for tree-sitter queries).
            language: Language name.
            rel_path: Relative file path.

        Returns:
            List of extracted symbols.
        """
        symbols: list[SymbolInfo] = []
        source_lines = source_text.splitlines()

        queries = _QUERY_REGISTRY.get(language, [])
        if not queries:
            return symbols

        ts_language = self.lang.get_parser(language)
        if ts_language is None:
            return symbols

        for query_str, default_kind, is_method in queries:
            try:
                self._run_query(
                    tree=tree,
                    language=language,
                    query_str=query_str,
                    default_kind=default_kind,
                    is_method=is_method,
                    source_text=source_text,
                    source_lines=source_lines,
                    rel_path=rel_path,
                    symbols=symbols,
                )
            except Exception as e:
                logger.debug(
                    f"Query failed for {language} in {rel_path}: {e}"
                )

        return symbols

    def _run_query(
        self,
        tree: tree_sitter.Tree,
        language: str,
        query_str: str,
        default_kind: SymbolKind,
        is_method: bool,
        source_text: str,
        source_lines: list[str],
        rel_path: str,
        symbols: list[SymbolInfo],
    ) -> None:
        """Run a single tree-sitter query and append results to symbols.

        Uses the tree-sitter query API to match patterns in the AST.
        Each match produces one SymbolInfo entry.
        """
        import tree_sitter_languages

        ts_language = tree_sitter_languages.get_language(language)
        query = ts_language.query(query_str)
        captures = query.captures(tree.root_node)

        # Group captures by their definition node
        # captures is a list of (node, capture_name) tuples
        i = 0
        while i < len(captures):
            node, capture_name = captures[i]

            if capture_name.endswith(".name"):
                name = node.text.decode("utf-8", errors="replace")

                # Look for the corresponding .def capture
                def_node = None
                parent_name = None

                # Check if next capture is the def node
                if i + 1 < len(captures):
                    next_node, next_capture = captures[i + 1]
                    if next_capture.endswith(".def"):
                        def_node = next_node
                        i += 1

                # For methods, look for the class.name capture
                if is_method and i >= 2:
                    prev_node, prev_capture = captures[i - 2]
                    if prev_capture == "class.name":
                        parent_name = prev_node.text.decode(
                            "utf-8", errors="replace"
                        )

                target_node = def_node or node
                line_start = target_node.start_point[0] + 1  # 1-based
                line_end = target_node.end_point[0] + 1

                # Extract signature (first line of the definition)
                sig_line = line_start - 1  # 0-based index
                if sig_line < len(source_lines):
                    signature = source_lines[sig_line].strip()
                else:
                    signature = name

                # Extract docstring (line after signature for most langs)
                docstring = self._extract_docstring(
                    source_lines, line_start, language
                )

                symbols.append(
                    SymbolInfo(
                        name=name,
                        kind=default_kind,
                        file_path=rel_path,
                        line_start=line_start,
                        line_end=line_end,
                        signature=signature,
                        docstring=docstring,
                        language=language,
                        parent=parent_name,
                    )
                )

            i += 1

    def _extract_docstring(
        self,
        source_lines: list[str],
        def_line: int,
        language: str,
    ) -> Optional[str]:
        """Extract the docstring/doc comment following a definition.

        Args:
            source_lines: All source lines.
            def_line: 1-based line number of the definition.
            language: Language name.

        Returns:
            Extracted docstring text, or None.
        """
        # Look at the line(s) after the definition
        idx = def_line  # 0-based index of line after def
        if idx >= len(source_lines):
            return None

        if language == "python":
            # Python: look for triple-quoted string
            return self._extract_python_docstring(source_lines, idx)
        elif language in ("javascript", "typescript", "tsx", "java", "go"):
            # JSDoc / Javadoc / Go doc: look for /** ... */ or // comments
            # above the definition
            return self._extract_block_comment(source_lines, def_line - 2)

        return None

    def _extract_python_docstring(
        self, lines: list[str], start_idx: int
    ) -> Optional[str]:
        """Extract a Python triple-quoted docstring."""
        # Scan forward from start_idx for a triple-quoted string
        for i in range(start_idx, min(start_idx + 3, len(lines))):
            stripped = lines[i].strip()
            if stripped.startswith(('"""', "'''")):
                quote = stripped[:3]
                # Single-line docstring
                if stripped.endswith(quote) and len(stripped) > 6:
                    return stripped[3:-3].strip()
                # Multi-line — collect until closing quote
                doc_lines = [stripped[3:]]
                for j in range(i + 1, len(lines)):
                    line = lines[j].strip()
                    if line.endswith(quote):
                        doc_lines.append(line[: -3])
                        return "\n".join(
                            l.strip() for l in doc_lines
                        ).strip()
                    doc_lines.append(line)
                    # Cap at 20 lines to avoid runaway
                    if j - i > 20:
                        break
                return None
        return None

    def _extract_block_comment(
        self, lines: list[str], end_idx: int
    ) -> Optional[str]:
        """Extract a JSDoc/Javadoc/Go doc comment block above a def."""
        if end_idx < 0 or end_idx >= len(lines):
            return None

        # Check for */ ending (block comment)
        stripped = lines[end_idx].strip()
        if stripped.endswith("*/"):
            doc_lines = []
            for i in range(end_idx, max(end_idx - 30, -1), -1):
                line = lines[i].strip()
                doc_lines.insert(0, line)
                if line.startswith("/**") or line.startswith("/*"):
                    # Clean up: remove /**, */, leading *
                    cleaned = []
                    for dl in doc_lines:
                        dl = dl.lstrip("/* ").rstrip("*/").strip()
                        if dl:
                            cleaned.append(dl)
                    return "\n".join(cleaned) if cleaned else None
            return None

        # Check for // line comments (Go style)
        if stripped.startswith("//"):
            doc_lines = []
            for i in range(end_idx, max(end_idx - 20, -1), -1):
                line = lines[i].strip()
                if line.startswith("//"):
                    doc_lines.insert(0, line.lstrip("/ ").strip())
                else:
                    break
            return "\n".join(doc_lines) if doc_lines else None

        return None

    # ──────────────────────────────────────────────
    # Query methods (called by MCP command handlers)
    # ──────────────────────────────────────────────

    def list_files(self) -> list[FileInfo]:
        """Return metadata for all indexed files.

        Returns:
            List of FileInfo for every file in the index.
        """
        with self._lock:
            return [entry.to_file_info() for entry in self._index.values()]

    def list_symbols(
        self, target: str
    ) -> list[SymbolInfo]:
        """List all symbols in a file or module (directory).

        Args:
            target: Relative file path or directory path.

        Returns:
            All symbols in the target file or directory.
        """
        with self._lock:
            # Exact file match
            if target in self._index:
                return list(self._index[target].symbols)

            # Directory/module match — return symbols from all files
            # under the target path
            results: list[SymbolInfo] = []
            prefix = target.rstrip("/") + "/"
            for rel_path, entry in self._index.items():
                if rel_path.startswith(prefix):
                    results.extend(entry.symbols)
            return results

    def get_function(
        self, name: str, file_path: Optional[str] = None
    ) -> Optional[tuple[SymbolInfo, str]]:
        """Get the full source code of a function.

        Args:
            name: Function name.
            file_path: Optional file path to narrow the search.

        Returns:
            Tuple of (symbol_info, source_code) or None if not found.
        """
        symbol = self._find_symbol(name, file_path)
        if symbol is None:
            return None

        # Read the source file and extract the function body
        abs_path = self.workspace_root / symbol.file_path
        try:
            source_lines = abs_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            return None

        # Extract lines from line_start to line_end (1-based)
        start = symbol.line_start - 1  # 0-based
        end = symbol.line_end  # inclusive, but Python slice is exclusive
        source = "\n".join(source_lines[start:end])

        return (symbol, source)

    def search_symbol(self, name: str) -> list[SymbolInfo]:
        """Find all symbols matching a name across the codebase.

        Performs case-insensitive substring matching.

        Args:
            name: Symbol name or partial name to search for.

        Returns:
            All matching symbols.
        """
        name_lower = name.lower()
        results: list[SymbolInfo] = []

        with self._lock:
            for entry in self._index.values():
                for symbol in entry.symbols:
                    if name_lower in symbol.name.lower():
                        results.append(symbol)

        return results

    def describe_symbol(self, name: str) -> list[SymbolInfo]:
        """Find symbols by name and return signature + docstring only.

        Same search as search_symbol, but the caller uses this when
        they only need the signature and docstring — no full body.

        Args:
            name: Symbol name or partial name.

        Returns:
            Matching symbols (with signature and docstring populated).
        """
        return self.search_symbol(name)

    def _find_symbol(
        self, name: str, file_path: Optional[str] = None
    ) -> Optional[SymbolInfo]:
        """Find a single symbol by exact name.

        If file_path is provided, only searches that file.
        Otherwise searches the entire index and returns the first
        exact match.

        Args:
            name: Exact symbol name.
            file_path: Optional file path to narrow search.

        Returns:
            The matching SymbolInfo or None.
        """
        with self._lock:
            if file_path and file_path in self._index:
                for symbol in self._index[file_path].symbols:
                    if symbol.name == name:
                        return symbol
                return None

            for entry in self._index.values():
                for symbol in entry.symbols:
                    if symbol.name == name:
                        return symbol

        return None

    @property
    def file_count(self) -> int:
        """Number of files currently indexed."""
        with self._lock:
            return len(self._index)

    @property
    def symbol_count(self) -> int:
        """Total number of symbols across all indexed files."""
        with self._lock:
            return sum(len(e.symbols) for e in self._index.values())
```

### 4. `faith/tools/code_index/watcher.py`

```python
"""Filesystem event watcher for real-time index updates.

Watches the workspace mount for file changes using the `watchfiles`
library (which uses OS-native mechanisms: inotify on Linux, FSEvents
on macOS, ReadDirectoryChangesW on Windows). When a supported source
file is created, modified, or deleted, the corresponding index entry
is updated immediately.

FRS Reference: Section 4.8.5
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from watchfiles import Change, awatch

from faith.tools.code_index.indexer import CodeIndexer
from faith.tools.code_index.languages import LanguageRegistry

logger = logging.getLogger("faith.tools.code_index.watcher")


class FileWatcher:
    """Watches the workspace for file changes and triggers re-indexing.

    Runs as an async background task. Debounces rapid changes to the
    same file (e.g. editor save + format in quick succession).

    Attributes:
        workspace_root: Path to the watched workspace.
        indexer: The CodeIndexer to update.
        language_registry: For checking file support.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        indexer: CodeIndexer,
        language_registry: LanguageRegistry,
        debounce_ms: int = 200,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.indexer = indexer
        self.lang = language_registry
        self.debounce_ms = debounce_ms
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start watching for file changes in the background."""
        if self._running:
            logger.warning("FileWatcher already running")
            return

        self._running = True
        self._task = asyncio.create_task(
            self._watch_loop(), name="code-index-watcher"
        )
        logger.info(
            f"FileWatcher started for {self.workspace_root} "
            f"(debounce: {self.debounce_ms}ms)"
        )

    async def stop(self) -> None:
        """Stop the file watcher."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("FileWatcher stopped")

    async def _watch_loop(self) -> None:
        """Main watch loop using watchfiles.awatch."""
        try:
            async for changes in awatch(
                self.workspace_root,
                debounce=self.debounce_ms,
                step=50,
                stop_event=self._make_stop_event(),
                watch_filter=self._filter_change,
            ):
                if not self._running:
                    break

                for change_type, path_str in changes:
                    await self._handle_change(change_type, path_str)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"FileWatcher error: {e}", exc_info=True)

    def _make_stop_event(self) -> asyncio.Event:
        """Create an event that signals when watching should stop."""
        event = asyncio.Event()
        # The event will be set when _running becomes False
        # (checked in the loop body)
        return event

    def _filter_change(
        self, change: Change, path: str
    ) -> bool:
        """Filter out changes to non-supported files and skip dirs.

        This is called by watchfiles for every detected change.
        Returning False skips the change.

        Args:
            change: Type of change (added, modified, deleted).
            path: Absolute path to the changed file.

        Returns:
            True if the change should be processed.
        """
        p = Path(path)

        # Skip directories
        if p.is_dir():
            return False

        # Skip files in ignored directories
        for part in p.parts:
            if self.lang.should_skip_dir(part):
                return False

        # For deletions, check extension only (file may not exist)
        if change == Change.deleted:
            return self.lang.is_supported(p)

        # For adds/modifications, use full check
        return not self.lang.should_skip_file(p)

    async def _handle_change(
        self, change_type: Change, path_str: str
    ) -> None:
        """Process a single file change event.

        Args:
            change_type: The type of change.
            path_str: Absolute path to the changed file.
        """
        path = Path(path_str)

        try:
            if change_type == Change.deleted:
                self.indexer.remove_file(path)
                logger.debug(f"File deleted, removed from index: {path}")

            elif change_type in (Change.added, Change.modified):
                self.indexer.index_file(path)
                logger.debug(
                    f"File {'added' if change_type == Change.added else 'modified'}, "
                    f"re-indexed: {path}"
                )

        except Exception as e:
            logger.warning(
                f"Failed to handle {change_type.name} for {path}: {e}"
            )
```

### 5. `faith/tools/code_index/server.py`

```python
"""MCP server entry point for the Code Index tool.

Exposes five commands via the Model Context Protocol:
- list_files: Full file tree of the workspace
- list_symbols: All symbols in a file or module
- get_function: Full source of a specific function
- search_symbol: Find all occurrences of a symbol name
- describe_symbol: Signature + docstring only (no full body)

Runs as a long-lived process inside the code-index Docker container.
On startup, performs a full scan of the workspace. Then watches for
file changes and updates the index in real time.

FRS Reference: Section 4.8
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from faith.tools.code_index.indexer import CodeIndexer
from faith.tools.code_index.languages import LanguageRegistry
from faith.tools.code_index.models import (
    DescribeSymbolResponse,
    ErrorResponse,
    GetFunctionResponse,
    ListFilesResponse,
    ListSymbolsResponse,
    SearchSymbolResponse,
)
from faith.tools.code_index.watcher import FileWatcher

logger = logging.getLogger("faith.tools.code_index.server")

# ──────────────────────────────────────────────────
# Configuration from environment variables
# ──────────────────────────────────────────────────

WORKSPACE_ROOT = os.environ.get("WORKSPACE_ROOT", "/workspace")
DEBOUNCE_MS = int(os.environ.get("CODE_INDEX_DEBOUNCE_MS", "200"))

# ──────────────────────────────────────────────────
# Server setup
# ──────────────────────────────────────────────────

app = Server("faith-code-index")
language_registry = LanguageRegistry()
indexer = CodeIndexer(WORKSPACE_ROOT, language_registry)
watcher = FileWatcher(
    WORKSPACE_ROOT, indexer, language_registry, debounce_ms=DEBOUNCE_MS
)


def _json_response(model) -> list[TextContent]:
    """Convert a Pydantic model to an MCP TextContent JSON response."""
    return [TextContent(type="text", text=model.model_dump_json(indent=2))]


def _error_response(error: str, detail: str = None) -> list[TextContent]:
    """Return a structured error response."""
    return _json_response(ErrorResponse(error=error, detail=detail))


# ──────────────────────────────────────────────────
# Tool definitions
# ──────────────────────────────────────────────────


@app.list_tools()
async def list_tools() -> list[Tool]:
    """Declare the available MCP tools."""
    return [
        Tool(
            name="list_files",
            description=(
                "List all indexed source files in the workspace. "
                "Returns file paths, languages, sizes, and symbol counts."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="list_symbols",
            description=(
                "List all functions, classes, and other symbols in a "
                "file or module directory. Returns names, kinds, "
                "signatures, line numbers, and docstrings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Relative file path (e.g. 'src/auth.py') or "
                            "module directory (e.g. 'src/auth/'). "
                            "Directory targets return symbols from all "
                            "files under that path."
                        ),
                    },
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_function",
            description=(
                "Get the full source code of a specific function or "
                "method. Use this only when you need to read or modify "
                "the actual implementation — prefer describe_symbol for "
                "signatures only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact function or method name.",
                    },
                    "file": {
                        "type": "string",
                        "description": (
                            "Optional file path to narrow the search. "
                            "If omitted, searches all indexed files."
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="search_symbol",
            description=(
                "Search for all symbols matching a name across the "
                "entire codebase. Case-insensitive substring match. "
                "Returns all matches with file paths and line numbers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Symbol name or partial name to search for."
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="describe_symbol",
            description=(
                "Get the signature and docstring of symbols matching "
                "a name. Does NOT return the full function body — use "
                "get_function for that. This is the cheapest way to "
                "understand what a function does."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Symbol name or partial name to describe."
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
    ]


# ──────────────────────────────────────────────────
# Tool handlers
# ──────────────────────────────────────────────────


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route MCP tool calls to the appropriate handler."""

    if name == "list_files":
        return _handle_list_files()

    elif name == "list_symbols":
        target = arguments.get("target")
        if not target:
            return _error_response(
                "Missing required parameter: target",
                "Provide a file path (e.g. 'src/auth.py') or "
                "module directory (e.g. 'src/auth/').",
            )
        return _handle_list_symbols(target)

    elif name == "get_function":
        func_name = arguments.get("name")
        if not func_name:
            return _error_response("Missing required parameter: name")
        file_path = arguments.get("file")
        return _handle_get_function(func_name, file_path)

    elif name == "search_symbol":
        sym_name = arguments.get("name")
        if not sym_name:
            return _error_response("Missing required parameter: name")
        return _handle_search_symbol(sym_name)

    elif name == "describe_symbol":
        sym_name = arguments.get("name")
        if not sym_name:
            return _error_response("Missing required parameter: name")
        return _handle_describe_symbol(sym_name)

    else:
        return _error_response(f"Unknown tool: {name}")


def _handle_list_files() -> list[TextContent]:
    """Handle the list_files command."""
    files = indexer.list_files()
    response = ListFilesResponse(
        workspace_root=str(indexer.workspace_root),
        total_files=len(files),
        files=files,
    )
    return _json_response(response)


def _handle_list_symbols(target: str) -> list[TextContent]:
    """Handle the list_symbols command."""
    symbols = indexer.list_symbols(target)
    response = ListSymbolsResponse(
        target=target,
        total_symbols=len(symbols),
        symbols=symbols,
    )
    return _json_response(response)


def _handle_get_function(
    name: str, file_path: str | None
) -> list[TextContent]:
    """Handle the get_function command."""
    result = indexer.get_function(name, file_path)
    if result is None:
        return _error_response(
            f"Symbol '{name}' not found",
            f"Searched in: {file_path or 'all files'}. "
            "Check the name is exact (case-sensitive).",
        )

    symbol, source = result
    response = GetFunctionResponse(symbol=symbol, source=source)
    return _json_response(response)


def _handle_search_symbol(name: str) -> list[TextContent]:
    """Handle the search_symbol command."""
    matches = indexer.search_symbol(name)
    response = SearchSymbolResponse(
        query=name,
        total_matches=len(matches),
        matches=matches,
    )
    return _json_response(response)


def _handle_describe_symbol(name: str) -> list[TextContent]:
    """Handle the describe_symbol command."""
    matches = indexer.describe_symbol(name)
    response = DescribeSymbolResponse(
        query=name,
        total_matches=len(matches),
        descriptions=matches,
    )
    return _json_response(response)


# ──────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────


async def main() -> None:
    """Start the Code Index MCP server.

    1. Initialise language grammars.
    2. Perform a full workspace scan.
    3. Start the file watcher for real-time updates.
    4. Run the MCP server via stdio transport.
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("Code Index MCP server starting")
    logger.info(f"Workspace root: {WORKSPACE_ROOT}")

    # Step 1: Load language grammars
    language_registry.initialise()
    logger.info(
        f"Languages available: {language_registry.supported_languages}"
    )

    # Step 2: Full workspace scan
    file_count = indexer.full_scan()
    logger.info(
        f"Initial scan complete: {file_count} files, "
        f"{indexer.symbol_count} symbols"
    )

    # Step 3: Start file watcher
    await watcher.start()

    # Step 4: Run MCP server
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )
    finally:
        await watcher.stop()
        logger.info("Code Index MCP server stopped")


if __name__ == "__main__":
    asyncio.run(main())
```

### 6. `faith/tools/code_index/__init__.py`

```python
"""FAITH Code Index MCP Tool — AST-based code indexing via tree-sitter."""

from faith.tools.code_index.indexer import CodeIndexer
from faith.tools.code_index.languages import LanguageRegistry
from faith.tools.code_index.watcher import FileWatcher

__all__ = [
    "CodeIndexer",
    "LanguageRegistry",
    "FileWatcher",
]
```

### 7. `containers/code-index/Dockerfile`

```dockerfile
# FAITH Code Index MCP Server
# AST-based code indexing using tree-sitter.
#
# Build context: repository root
# Usage: docker build -f containers/code-index/Dockerfile -t faith-code-index .

FROM python:3.12-slim

LABEL maintainer="FAITH Framework"
LABEL description="Code Index MCP server — tree-sitter AST indexing"

# Install build dependencies for tree-sitter (needed for native extensions)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY containers/code-index/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy FAITH package
COPY faith/ /app/faith/

# Default workspace mount point
ENV WORKSPACE_ROOT=/workspace
ENV LOG_LEVEL=INFO
ENV CODE_INDEX_DEBOUNCE_MS=200

# Health check: verify the process is running
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD pgrep -f "code_index.server" || exit 1

ENTRYPOINT ["python", "-m", "faith.tools.code_index.server"]
```

### 8. `containers/code-index/requirements.txt`

```
tree-sitter>=0.21.0,<1.0
tree-sitter-languages>=1.10.0,<2.0
watchfiles>=0.21.0,<1.0
mcp>=1.0.0,<2.0
pydantic>=2.0.0,<3.0
```

### 9. `tests/test_code_index.py`

```python
"""Tests for the FAITH Code Index MCP server.

Covers language detection, symbol extraction, index queries,
file watching, and MCP command handlers.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.tools.code_index.indexer import CodeIndexer
from faith.tools.code_index.languages import (
    LanguageRegistry,
    SKIP_DIRS,
    MAX_FILE_SIZE,
)
from faith.tools.code_index.models import (
    SymbolKind,
    SymbolInfo,
    FileInfo,
    ListFilesResponse,
    ListSymbolsResponse,
    SearchSymbolResponse,
    ErrorResponse,
)
from faith.tools.code_index.watcher import FileWatcher


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def lang_registry():
    """A language registry (may or may not have grammars depending
    on test environment — tests that need parsing should check)."""
    reg = LanguageRegistry()
    reg.initialise()
    return reg


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace with sample source files."""
    # Python file
    py_dir = tmp_path / "src"
    py_dir.mkdir()
    (py_dir / "__init__.py").write_text("", encoding="utf-8")
    (py_dir / "auth.py").write_text(
        '''"""Authentication module."""

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    import bcrypt
    return bcrypt.checkpw(password.encode(), hashed.encode())


class TokenManager:
    """Manages JWT token creation and validation."""

    def __init__(self, secret: str, algorithm: str = "HS256"):
        self.secret = secret
        self.algorithm = algorithm

    def create_token(self, payload: dict) -> str:
        """Create a signed JWT token."""
        import jwt
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def verify_token(self, token: str) -> dict:
        """Verify and decode a JWT token."""
        import jwt
        return jwt.decode(token, self.secret, algorithms=[self.algorithm])
''',
        encoding="utf-8",
    )

    # JavaScript file
    (py_dir / "utils.js").write_text(
        '''/**
 * Format a date to ISO string.
 */
function formatDate(date) {
    return date.toISOString();
}

class Logger {
    constructor(name) {
        this.name = name;
    }

    log(message) {
        console.log(`[${this.name}] ${message}`);
    }
}

export function parseConfig(path) {
    return JSON.parse(readFileSync(path, "utf-8"));
}
''',
        encoding="utf-8",
    )

    # Nested directory with Python
    nested = tmp_path / "src" / "models"
    nested.mkdir()
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "user.py").write_text(
        '''class User:
    """A user in the system."""

    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email

    def display_name(self) -> str:
        """Return the user display name."""
        return self.name
''',
        encoding="utf-8",
    )

    # Ignored directory
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "lodash.js").write_text(
        "function noop() {}", encoding="utf-8"
    )

    return tmp_path


@pytest.fixture
def indexer(workspace, lang_registry):
    """A CodeIndexer pointed at the test workspace."""
    return CodeIndexer(workspace, lang_registry)


# ──────────────────────────────────────────────────
# Language Registry tests
# ──────────────────────────────────────────────────


def test_language_detection_python(lang_registry):
    """Python files are detected by extension."""
    assert lang_registry.get_language_for_file("src/auth.py") == "python"
    assert lang_registry.get_language_for_file("main.pyw") == "python"


def test_language_detection_javascript(lang_registry):
    """JavaScript files are detected by extension."""
    assert lang_registry.get_language_for_file("app.js") == "javascript"
    assert lang_registry.get_language_for_file("app.mjs") == "javascript"


def test_language_detection_typescript(lang_registry):
    """TypeScript files are detected by extension."""
    assert lang_registry.get_language_for_file("app.ts") == "typescript"
    assert lang_registry.get_language_for_file("app.tsx") == "tsx"


def test_language_detection_java(lang_registry):
    """Java files are detected by extension."""
    assert lang_registry.get_language_for_file("Main.java") == "java"


def test_language_detection_go(lang_registry):
    """Go files are detected by extension."""
    assert lang_registry.get_language_for_file("main.go") == "go"


def test_language_detection_unsupported(lang_registry):
    """Unsupported extensions return None."""
    assert lang_registry.get_language_for_file("data.csv") is None
    assert lang_registry.get_language_for_file("image.png") is None
    assert lang_registry.get_language_for_file("README.md") is None


def test_skip_dirs():
    """Standard skip directories are in the set."""
    assert "node_modules" in SKIP_DIRS
    assert "__pycache__" in SKIP_DIRS
    assert ".git" in SKIP_DIRS
    assert "vendor" in SKIP_DIRS


def test_should_skip_dir(lang_registry):
    """Known skip directories are correctly identified."""
    assert lang_registry.should_skip_dir("node_modules") is True
    assert lang_registry.should_skip_dir("__pycache__") is True
    assert lang_registry.should_skip_dir(".hidden") is True
    assert lang_registry.should_skip_dir("src") is False


def test_supported_extensions(lang_registry):
    """Registry reports all supported extensions."""
    exts = lang_registry.supported_extensions
    assert ".py" in exts
    assert ".js" in exts
    assert ".ts" in exts
    assert ".java" in exts
    assert ".go" in exts


# ──────────────────────────────────────────────────
# Indexer: Full scan tests
# ──────────────────────────────────────────────────


def test_full_scan_indexes_files(indexer, workspace, lang_registry):
    """Full scan finds all supported files in the workspace."""
    if not lang_registry.supported_languages:
        pytest.skip("No tree-sitter grammars available")

    count = indexer.full_scan()

    # Should index: auth.py, __init__.py (x2), utils.js, user.py
    # Should NOT index: node_modules/lodash.js
    assert count >= 3  # At minimum the non-empty files
    assert indexer.file_count >= 3


def test_full_scan_skips_node_modules(indexer, workspace, lang_registry):
    """Files in node_modules are not indexed."""
    if not lang_registry.supported_languages:
        pytest.skip("No tree-sitter grammars available")

    indexer.full_scan()
    files = indexer.list_files()
    file_paths = [f.path for f in files]

    for path in file_paths:
        assert "node_modules" not in path


# ──────────────────────────────────────────────────
# Indexer: Symbol extraction tests
# ──────────────────────────────────────────────────


def test_extract_python_functions(indexer, workspace, lang_registry):
    """Python functions are extracted with correct metadata."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    symbols = indexer.list_symbols("src/auth.py")

    func_names = [s.name for s in symbols]
    assert "hash_password" in func_names
    assert "verify_password" in func_names


def test_extract_python_classes(indexer, workspace, lang_registry):
    """Python classes are extracted."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    symbols = indexer.list_symbols("src/auth.py")

    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    class_names = [c.name for c in classes]
    assert "TokenManager" in class_names


def test_extract_python_methods(indexer, workspace, lang_registry):
    """Python methods within classes are extracted."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    symbols = indexer.list_symbols("src/auth.py")

    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    method_names = [m.name for m in methods]
    assert "create_token" in method_names
    assert "verify_token" in method_names


def test_symbol_has_line_numbers(indexer, workspace, lang_registry):
    """Extracted symbols have correct line number ranges."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    symbols = indexer.list_symbols("src/auth.py")

    hash_pw = next(s for s in symbols if s.name == "hash_password")
    assert hash_pw.line_start >= 1
    assert hash_pw.line_end >= hash_pw.line_start


def test_symbol_has_signature(indexer, workspace, lang_registry):
    """Extracted symbols include their signature."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    symbols = indexer.list_symbols("src/auth.py")

    hash_pw = next(s for s in symbols if s.name == "hash_password")
    assert "def hash_password" in hash_pw.signature
    assert "password" in hash_pw.signature


def test_symbol_has_docstring(indexer, workspace, lang_registry):
    """Python symbols with docstrings have them extracted."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    symbols = indexer.list_symbols("src/auth.py")

    hash_pw = next(s for s in symbols if s.name == "hash_password")
    assert hash_pw.docstring is not None
    assert "bcrypt" in hash_pw.docstring.lower()


# ──────────────────────────────────────────────────
# Indexer: Query method tests
# ──────────────────────────────────────────────────


def test_list_files(indexer, lang_registry):
    """list_files returns FileInfo for all indexed files."""
    if not lang_registry.supported_languages:
        pytest.skip("No tree-sitter grammars available")

    indexer.full_scan()
    files = indexer.list_files()

    assert len(files) > 0
    assert all(isinstance(f, FileInfo) for f in files)
    assert all(f.language in lang_registry.supported_languages for f in files)


def test_list_symbols_by_directory(indexer, lang_registry):
    """list_symbols with a directory returns symbols from all files."""
    if not lang_registry.supported_languages:
        pytest.skip("No tree-sitter grammars available")

    indexer.full_scan()
    symbols = indexer.list_symbols("src")

    # Should include symbols from auth.py, utils.js, and models/user.py
    names = [s.name for s in symbols]
    assert len(names) > 0


def test_get_function_returns_source(indexer, lang_registry):
    """get_function returns the full source code."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    result = indexer.get_function("hash_password", "src/auth.py")

    assert result is not None
    symbol, source = result
    assert symbol.name == "hash_password"
    assert "bcrypt" in source
    assert "return" in source


def test_get_function_not_found(indexer, lang_registry):
    """get_function returns None for nonexistent symbols."""
    if not lang_registry.supported_languages:
        pytest.skip("No tree-sitter grammars available")

    indexer.full_scan()
    result = indexer.get_function("nonexistent_function")
    assert result is None


def test_search_symbol(indexer, lang_registry):
    """search_symbol finds symbols by substring match."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    results = indexer.search_symbol("password")

    names = [s.name for s in results]
    assert "hash_password" in names
    assert "verify_password" in names


def test_search_symbol_case_insensitive(indexer, lang_registry):
    """search_symbol is case-insensitive."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    results_lower = indexer.search_symbol("tokenmanager")
    results_upper = indexer.search_symbol("TokenManager")

    assert len(results_lower) == len(results_upper)


def test_describe_symbol(indexer, lang_registry):
    """describe_symbol returns signature and docstring."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    results = indexer.describe_symbol("TokenManager")

    assert len(results) > 0
    tm = results[0]
    assert tm.name == "TokenManager"
    assert tm.signature is not None


# ──────────────────────────────────────────────────
# Indexer: Incremental update tests
# ──────────────────────────────────────────────────


def test_index_file_updates_existing(indexer, workspace, lang_registry):
    """Re-indexing a file replaces the previous entry."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    initial_symbols = len(indexer.list_symbols("src/auth.py"))

    # Add a new function to auth.py
    auth_path = workspace / "src" / "auth.py"
    content = auth_path.read_text(encoding="utf-8")
    content += "\n\ndef new_function():\n    pass\n"
    auth_path.write_text(content, encoding="utf-8")

    indexer.index_file(auth_path)
    updated_symbols = len(indexer.list_symbols("src/auth.py"))

    assert updated_symbols > initial_symbols


def test_remove_file(indexer, workspace, lang_registry):
    """Removing a file clears it from the index."""
    if "python" not in lang_registry.supported_languages:
        pytest.skip("Python grammar not available")

    indexer.full_scan()
    assert indexer.file_count > 0

    auth_path = workspace / "src" / "auth.py"
    indexer.remove_file(auth_path)

    files = indexer.list_files()
    paths = [f.path for f in files]
    assert "src/auth.py" not in paths


# ──────────────────────────────────────────────────
# FileWatcher tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_watcher_start_stop(workspace, lang_registry):
    """FileWatcher starts and stops without error."""
    idx = CodeIndexer(workspace, lang_registry)
    fw = FileWatcher(workspace, idx, lang_registry, debounce_ms=50)

    await fw.start()
    assert fw._running is True

    await fw.stop()
    assert fw._running is False


# ──────────────────────────────────────────────────
# Response model tests
# ──────────────────────────────────────────────────


def test_list_files_response_serialisation():
    """ListFilesResponse serialises to valid JSON."""
    response = ListFilesResponse(
        workspace_root="/workspace",
        total_files=1,
        files=[
            FileInfo(
                path="src/auth.py",
                language="python",
                size_bytes=1234,
                symbol_count=5,
                last_indexed="2026-03-24T12:00:00+00:00",
            )
        ],
    )
    data = json.loads(response.model_dump_json())
    assert data["total_files"] == 1
    assert data["files"][0]["path"] == "src/auth.py"


def test_error_response_serialisation():
    """ErrorResponse serialises to valid JSON."""
    response = ErrorResponse(
        error="Symbol not found",
        detail="Searched all files",
    )
    data = json.loads(response.model_dump_json())
    assert data["error"] == "Symbol not found"


def test_symbol_info_serialisation():
    """SymbolInfo serialises with all fields."""
    symbol = SymbolInfo(
        name="hash_password",
        kind=SymbolKind.FUNCTION,
        file_path="src/auth.py",
        line_start=3,
        line_end=6,
        signature="def hash_password(password: str) -> str:",
        docstring="Hash a password using bcrypt.",
        language="python",
        parent=None,
    )
    data = json.loads(symbol.model_dump_json())
    assert data["name"] == "hash_password"
    assert data["kind"] == "function"
    assert data["docstring"] == "Hash a password using bcrypt."
```

---

## Integration Points

### Docker Compose Service

Add to the project's `docker-compose.yml` (managed by the PA at session start):

```yaml
  code-index:
    build:
      context: .
      dockerfile: containers/code-index/Dockerfile
    container_name: faith-code-index
    networks:
      - maf-network
    volumes:
      - ${PROJECT_WORKSPACE}:/workspace:ro
    environment:
      - WORKSPACE_ROOT=/workspace
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
      - CODE_INDEX_DEBOUNCE_MS=200
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "pgrep", "-f", "code_index.server"]
      interval: 30s
      timeout: 5s
      retries: 3
```

### Tool Configuration

The PA creates `.faith/tools/code-index.yaml` during project setup:

```yaml
# Code Index Tool configuration
# FRS Reference: Section 4.8

enabled: true
container: faith-code-index

# Languages to index (empty = all supported)
languages: []

# Directories to skip in addition to defaults
extra_skip_dirs: []

# Maximum file size to index (bytes)
max_file_size: 1048576

# File watcher debounce (milliseconds)
debounce_ms: 200
```

### Agent Usage (System Prompt Pattern)

As specified in FRS 4.8.4, agents are instructed via their system prompt:

```markdown
## Code Index Tool

Before writing any new function or class:
1. Call `search_symbol` to check if it already exists.
2. Use `describe_symbol` to read signatures without loading full bodies.
3. Only use `get_function` when you need to read or modify the implementation.

Example: to understand a 2,000-line module, call `list_symbols` (~100 tokens)
rather than loading the full file (~12,000 tokens).
```

### Integration with Filesystem MCP (FAITH-022)

The Code Index server depends on FAITH-022 for the established MCP tool container pattern (Dockerfile structure, network configuration, workspace mount conventions). It watches the same workspace volume that the Filesystem MCP server writes to. When an agent writes a file via the Filesystem tool, the Code Index watcher detects the change and re-indexes within the debounce window.

```
Agent writes file via Filesystem MCP (FAITH-022)
    ↓
File change detected by watchfiles (inotify/FSEvents)
    ↓
Code Index re-parses the file with tree-sitter
    ↓
Next agent query returns updated symbols
```

---

## Acceptance Criteria

1. **Language support:** Python, JavaScript, TypeScript (including TSX), Java, and Go files are correctly parsed and indexed. Unsupported file types are silently skipped.

2. **Symbol extraction:** Functions, classes, methods, structs (Go), and interfaces (Go) are extracted with correct name, kind, file path, line range, signature, and docstring.

3. **MCP commands:** All five commands (`list_files`, `list_symbols`, `get_function`, `search_symbol`, `describe_symbol`) return valid structured JSON matching the Pydantic response models.

4. **Directory/module queries:** `list_symbols` accepts both a file path and a directory path. Directory queries return symbols from all files under that path.

5. **Real-time updates:** File creates, modifications, and deletions in the workspace are detected and the index is updated within 1 second (plus debounce window).

6. **Skip directories:** `node_modules`, `__pycache__`, `.git`, `vendor`, `build`, `dist`, and other standard output directories are never indexed.

7. **File size limit:** Files exceeding 1 MB are skipped to avoid memory issues with generated or minified code.

8. **Error handling:** Invalid files, parse errors, and missing symbols return structured `ErrorResponse` JSON — never crash the server or return raw exceptions.

9. **Container health:** The Docker container starts, performs a full scan, begins watching, and passes health checks. Graceful shutdown on SIGTERM.

10. **All tests pass:** Unit tests for language detection, symbol extraction, index queries, incremental updates, file watcher lifecycle, and response model serialisation.

