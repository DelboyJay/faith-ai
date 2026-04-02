"""
Description:
    Build and query a lightweight code index for FAITH workspaces.

Requirements:
    - Scan text files under a workspace while excluding generated directories.
    - Extract basic Python symbols, persist snapshots, and support simple
      keyword search over paths, symbols, and preview content.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
DEFAULT_TEXT_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".ps1",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".html",
    ".htm",
    ".sql",
    ".graphql",
    ".env",
}


@dataclass(slots=True)
class CodeSymbol:
    """
    Description:
        Represent one extracted symbol from an indexed source file.

    Requirements:
        - Preserve the symbol name, symbol kind, and source line number.
    """

    name: str
    kind: str
    line: int


@dataclass(slots=True)
class CodeDocument:
    """
    Description:
        Represent one indexed text document inside the workspace.

    Requirements:
        - Preserve path, language, checksum, size, line count, symbols, and
          preview lines for search and inspection.
    """

    path: str
    relative_path: str
    language: str
    checksum: str
    line_count: int
    size_bytes: int
    symbols: list[CodeSymbol] = field(default_factory=list)
    preview_lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CodeSearchHit:
    """
    Description:
        Represent one ranked search hit returned by the code index.

    Requirements:
        - Preserve the relative path, absolute path, score, snippet, matched
          categories, and extracted symbols.
    """

    relative_path: str
    path: str
    score: int
    snippet: str
    matches: list[str] = field(default_factory=list)
    symbols: list[CodeSymbol] = field(default_factory=list)


@dataclass(slots=True)
class CodeIndex:
    """
    Description:
        Represent one persisted or in-memory snapshot of an indexed workspace.

    Requirements:
        - Preserve the workspace root, generation timestamp, excluded
          directories, and indexed documents.
    """

    root: str
    generated_at: str
    excluded_dirs: set[str]
    documents: list[CodeDocument] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        root: Path,
        *,
        excluded_dirs: Iterable[str] | None = None,
        max_file_size_bytes: int = 1_000_000,
    ) -> CodeIndex:
        """
        Description:
            Build a fresh code index snapshot for the supplied workspace root.

        Requirements:
            - Skip excluded directories and oversize or non-text files.
            - Extract checksums, preview lines, symbols, and language metadata
              for each included file.

        :param root: Workspace root to scan.
        :param excluded_dirs: Optional override for excluded directory names.
        :param max_file_size_bytes: Maximum file size to index.
        :returns: Newly built code index snapshot.
        """
        root = Path(root).resolve()
        exclude = set(excluded_dirs or DEFAULT_EXCLUDED_DIRS)
        documents: list[CodeDocument] = []

        for file_path in _iter_source_files(root, exclude):
            stat = file_path.stat()
            if stat.st_size > max_file_size_bytes:
                continue

            text = _read_text(file_path)
            if text is None:
                continue

            relative_path = file_path.relative_to(root).as_posix()
            checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
            preview_lines = _preview_lines(text)
            symbols = _extract_symbols(file_path, text)
            language = _detect_language(file_path)

            documents.append(
                CodeDocument(
                    path=str(file_path),
                    relative_path=relative_path,
                    language=language,
                    checksum=checksum,
                    line_count=text.count("\n") + (0 if text.endswith("\n") or not text else 1),
                    size_bytes=stat.st_size,
                    symbols=symbols,
                    preview_lines=preview_lines,
                )
            )

        return cls(
            root=str(root),
            generated_at=datetime.now(timezone.utc).isoformat(),
            excluded_dirs=exclude,
            documents=documents,
        )

    @classmethod
    def load(cls, path: Path) -> CodeIndex:
        """
        Description:
            Load a code index snapshot from disk.

        Requirements:
            - Rebuild document and symbol objects from the saved JSON structure.

        :param path: Path to the saved index JSON file.
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
                symbols=[CodeSymbol(**symbol) for symbol in item.get("symbols", [])],
                preview_lines=list(item.get("preview_lines", [])),
            )
            for item in raw.get("documents", [])
        ]
        return cls(
            root=raw["root"],
            generated_at=raw.get("generated_at", datetime.now(timezone.utc).isoformat()),
            excluded_dirs=set(raw.get("excluded_dirs", [])),
            documents=documents,
        )

    def save(self, path: Path) -> Path:
        """
        Description:
            Save the code index snapshot to disk.

        Requirements:
            - Create missing parent directories before writing the JSON file.

        :param path: Destination JSON file path.
        :returns: Written snapshot path.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the code index snapshot into a serialisable dictionary.

        Requirements:
            - Serialize document and symbol objects into JSON-safe structures.

        :returns: Serializable code index payload.
        """
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "excluded_dirs": sorted(self.excluded_dirs),
            "documents": [
                {
                    **asdict(document),
                    "symbols": [asdict(symbol) for symbol in document.symbols],
                }
                for document in self.documents
            ],
        }

    def refresh(self, *, max_file_size_bytes: int = 1_000_000) -> CodeIndex:
        """
        Description:
            Build a fresh snapshot using the current index settings.

        Requirements:
            - Reuse the stored root and excluded-directory set.

        :param max_file_size_bytes: Maximum file size to index.
        :returns: Refreshed code index snapshot.
        """
        return self.build(
            Path(self.root),
            excluded_dirs=self.excluded_dirs,
            max_file_size_bytes=max_file_size_bytes,
        )

    def search(self, query: str, *, limit: int = 10) -> list[CodeSearchHit]:
        """
        Description:
            Search the indexed documents using simple token scoring.

        Requirements:
            - Return no hits for empty queries.
            - Rank hits by score descending, then by relative path.

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
        Description:
            Return one indexed document by its relative path.

        Requirements:
            - Treat backslashes and forward slashes equivalently.

        :param relative_path: Relative path of the indexed document to find.
        :returns: Matching indexed document or `None`.
        """
        relative_path = relative_path.replace("\\", "/")
        for document in self.documents:
            if document.relative_path == relative_path:
                return document
        return None


def _iter_source_files(root: Path, excluded_dirs: set[str]) -> Iterable[Path]:
    """
    Description:
        Yield candidate files under the workspace root for indexing.

    Requirements:
        - Skip directories whose path parts contain excluded directory names.
        - Yield files in deterministic sorted order.

    :param root: Workspace root to scan.
    :param excluded_dirs: Directory names that must be skipped.
    :returns: Iterable of candidate file paths.
    """
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in excluded_dirs for part in path.parts):
            continue
        yield path


def _read_text(path: Path) -> str | None:
    """
    Description:
        Read a file as text when it matches the supported indexable formats.

    Requirements:
        - Skip unsupported extensions and binary files.
        - Try the supported text encodings in order until one succeeds.

    :param path: Candidate file path to read.
    :returns: Decoded text content or `None` when the file should be skipped.
    """
    suffix = path.suffix.lower()
    if suffix not in DEFAULT_TEXT_EXTENSIONS and path.name not in {
        "LICENSE",
        "README",
        "README.md",
    }:
        return None

    try:
        data = path.read_bytes()
    except OSError:
        return None

    if b"\x00" in data:
        return None

    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _detect_language(path: Path) -> str:
    """
    Description:
        Infer a simple language label from the file name or suffix.

    Requirements:
        - Treat README files as markdown.
        - Fall back to `text` for unknown suffixes.

    :param path: File path whose language should be inferred.
    :returns: Simple language label for the file.
    """
    mapping = {
        ".py": "python",
        ".md": "markdown",
        ".txt": "text",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".ini": "ini",
        ".cfg": "ini",
        ".sh": "shell",
        ".ps1": "powershell",
        ".ts": "typescript",
        ".tsx": "typescript-react",
        ".js": "javascript",
        ".jsx": "javascript-react",
        ".css": "css",
        ".html": "html",
        ".htm": "html",
        ".sql": "sql",
        ".graphql": "graphql",
        ".env": "dotenv",
    }
    if path.name in {"README", "README.md"}:
        return "markdown"
    return mapping.get(path.suffix.lower(), "text")


def _normalise_python_source(text: str) -> str:
    """
    Description:
        Normalise embedded or oddly indented Python source before parsing.

    Requirements:
        - Remove common indentation from non-empty trailing lines.
        - Preserve blank lines where possible.

    :param text: Python source text to normalise.
    :returns: Normalised Python source text.
    """
    lines = text.splitlines()
    if not lines:
        return text

    trailing_lines = [line for line in lines[1:] if line.strip()]
    if not trailing_lines:
        return text.strip()

    min_indent = min(len(line) - len(line.lstrip()) for line in trailing_lines)
    normalised = [lines[0].lstrip()]
    for line in lines[1:]:
        if not line.strip():
            normalised.append("")
        else:
            normalised.append(line[min_indent:])
    return "\n".join(normalised).strip()


def _extract_symbols(path: Path, text: str) -> list[CodeSymbol]:
    """
    Description:
        Extract top-level Python symbols from a text document when applicable.

    Requirements:
        - Return no symbols for non-Python files.
        - Retry parsing with normalised source when the raw parse fails.

    :param path: File path being indexed.
    :param text: Text content of the file.
    :returns: Extracted top-level code symbols.
    """
    if path.suffix.lower() != ".py":
        return []

    try:
        tree = ast.parse(text)
    except SyntaxError:
        try:
            tree = ast.parse(_normalise_python_source(text))
        except SyntaxError:
            return []

    symbols: list[CodeSymbol] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(CodeSymbol(name=node.name, kind="class", line=node.lineno))
        elif isinstance(node, ast.FunctionDef):
            symbols.append(CodeSymbol(name=node.name, kind="function", line=node.lineno))
        elif isinstance(node, ast.AsyncFunctionDef):
            symbols.append(CodeSymbol(name=node.name, kind="async_function", line=node.lineno))
    return symbols


def _tokenize(value: str) -> list[str]:
    """
    Description:
        Split a search string into lowercase word tokens.

    Requirements:
        - Drop empty tokens from the output.

    :param value: Input text to tokenize.
    :returns: Lowercase non-empty search tokens.
    """
    return [token for token in re.split(r"\W+", value.lower()) if token]


def _symbol_search_text(name: str) -> str:
    """
    Description:
        Expand a symbol name into searchable token text.

    Requirements:
        - Split CamelCase boundaries before tokenization.

    :param name: Symbol name to expand.
    :returns: Space-delimited searchable token text.
    """
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return " ".join(_tokenize(spaced))


def _score_document(document: CodeDocument, tokens: list[str]) -> tuple[int, list[str]]:
    """
    Description:
        Score one indexed document against a tokenised search query.

    Requirements:
        - Consider path, file name, preview content, and symbol names.
        - Return both the cumulative score and the matched categories.

    :param document: Indexed document to score.
    :param tokens: Lowercase query tokens.
    :returns: Tuple of score and matched-category labels.
    """
    haystacks = [
        document.relative_path.lower(),
        Path(document.relative_path).name.lower(),
        "\n".join(document.preview_lines).lower(),
        " ".join(_symbol_search_text(symbol.name) for symbol in document.symbols),
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
    """
    Description:
        Choose a preview snippet from the indexed document for a search result.

    Requirements:
        - Prefer the first preview line that matches any query token.
        - Fall back to the first three preview lines when nothing matches.

    :param document: Indexed document being rendered into a search hit.
    :param tokens: Lowercase query tokens.
    :returns: Search-result snippet text.
    """
    if not document.preview_lines:
        return ""

    lowered = [line.lower() for line in document.preview_lines]
    for idx, line in enumerate(lowered):
        if any(token in line for token in tokens):
            start = max(0, idx - 1)
            end = min(len(document.preview_lines), idx + 2)
            return "\n".join(document.preview_lines[start:end]).strip()

    return "\n".join(document.preview_lines[:3]).strip()


def _preview_lines(text: str, limit: int = 20) -> list[str]:
    """
    Description:
        Return the leading preview lines stored for one indexed document.

    Requirements:
        - Preserve the original line order.
        - Limit the preview to the requested number of lines.

    :param text: Full text content of the document.
    :param limit: Maximum number of preview lines to keep.
    :returns: Leading preview lines for the document.
    """
    lines = text.splitlines()
    return lines[:limit]
