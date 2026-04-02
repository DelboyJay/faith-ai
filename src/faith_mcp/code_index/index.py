"""Project code indexing helpers for FAITH.

This is a practical POC for FAITH-027: it can scan a repository, extract
basic Python symbols, persist an index snapshot, and perform lightweight
keyword search over file names, symbols, and text content.
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
    name: str
    kind: str
    line: int


@dataclass(slots=True)
class CodeDocument:
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
    relative_path: str
    path: str
    score: int
    snippet: str
    matches: list[str] = field(default_factory=list)
    symbols: list[CodeSymbol] = field(default_factory=list)


@dataclass(slots=True)
class CodeIndex:
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
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    def to_dict(self) -> dict:
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
        return self.build(
            Path(self.root),
            excluded_dirs=self.excluded_dirs,
            max_file_size_bytes=max_file_size_bytes,
        )

    def search(self, query: str, *, limit: int = 10) -> list[CodeSearchHit]:
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
        relative_path = relative_path.replace("\\", "/")
        for document in self.documents:
            if document.relative_path == relative_path:
                return document
        return None


def _iter_source_files(root: Path, excluded_dirs: set[str]) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in excluded_dirs for part in path.parts):
            continue
        yield path


def _read_text(path: Path) -> str | None:
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
    return [token for token in re.split(r"\W+", value.lower()) if token]


def _symbol_search_text(name: str) -> str:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return " ".join(_tokenize(spaced))


def _score_document(document: CodeDocument, tokens: list[str]) -> tuple[int, list[str]]:
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
    lines = text.splitlines()
    return lines[:limit]
