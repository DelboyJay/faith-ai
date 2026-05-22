"""
Description:
    Execute ripgrep-backed searches for the FAITH full-text search MCP server.

Requirements:
    - Validate requested paths against the workspace root.
    - Support regex, literal, file-name, discovery, and excerpt retrieval
      operations with bounded result sets.
"""

from __future__ import annotations

import asyncio
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from faith_mcp.code_index import CodeIndex
from faith_mcp.code_index.models import SymbolInfo, SymbolKind
from faith_mcp.fulltext_search.models import (
    ExcerptBlock,
    ExcerptDiscoveryResult,
    ExcerptFileSummary,
    ExcerptMatch,
    ExcerptRetrievalResult,
    FileMatch,
    SearchMatch,
    SearchResult,
)

DEFAULT_MAX_MATCHES = 500
DEFAULT_TIMEOUT_SECONDS = 30.0

_FILE_GROUP_DOCUMENT = "document"
_FILE_GROUP_CODE = "code"
_FILE_GROUP_CONFIG = "config_data"
_FILE_GROUP_MARKUP = "markup"

_DOCUMENT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".pdf",
    ".docx",
    ".odt",
    ".xlsx",
    ".ods",
}
_CODE_EXTENSIONS = {
    ".py",
    ".pyw",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
}
_CONFIG_EXTENSIONS = {
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".csv",
    ".tsv",
    ".jsonl",
}
_MARKUP_EXTENSIONS = {
    ".html",
    ".htm",
    ".xml",
}

_CONFIG_ENTRY_RE = re.compile(r'^\s*(?:"[^"]+"|\'[^\']+\'|[A-Za-z0-9_.-]+)\s*[:=].+$')
_MARKDOWN_HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+\S")

_CODE_SYMBOL_KIND_TO_BLOCK_TYPE = {
    SymbolKind.FUNCTION: "function",
    SymbolKind.METHOD: "function",
    SymbolKind.CLASS: "class",
    SymbolKind.STRUCT: "class",
    SymbolKind.INTERFACE: "class",
    SymbolKind.ENUM: "class",
    SymbolKind.TYPE_ALIAS: "class",
}

_DEFAULT_BLOCK_PRIORITIES = {
    _FILE_GROUP_DOCUMENT: ["paragraph", "section", "line"],
    _FILE_GROUP_CODE: ["function", "class", "line", "module"],
    _FILE_GROUP_CONFIG: ["entry", "section", "line"],
    _FILE_GROUP_MARKUP: ["paragraph", "section", "line"],
}


class RipgrepRunner:
    """
    Description:
        Run ripgrep commands safely against one workspace root.

    Requirements:
        - Constrain all search paths to the configured workspace root.
        - Enforce match-count and timeout limits.

    :param workspace_root: Root directory that may be searched.
    :param max_matches: Maximum number of matches to return.
    :param timeout_seconds: Maximum allowed runtime for one ripgrep process.
    :param rg_binary: Ripgrep executable name or path.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        max_matches: int = DEFAULT_MAX_MATCHES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        rg_binary: str = "rg",
    ):
        """
        Description:
            Configure the runner for one workspace and one ripgrep binary.

        Requirements:
            - Resolve the workspace root once during initialisation.
            - Preserve result and timeout limits for later search operations.

        :param workspace_root: Root directory that may be searched.
        :param max_matches: Maximum number of matches to return.
        :param timeout_seconds: Maximum allowed runtime for one ripgrep process.
        :param rg_binary: Ripgrep executable name or path.
        """
        self.workspace_root = Path(workspace_root).resolve()
        self.max_matches = max_matches
        self.timeout_seconds = timeout_seconds
        self.rg_binary = rg_binary
        self._code_index: CodeIndex | None = None

    def resolve_file_group(self, path: str | Path) -> str:
        """
        Description:
            Resolve one file into a deterministic resolver group.

        Requirements:
            - Classify document, code, config/data, and markup files without
              guessing a fallback group.
            - Raise a clear error for unsupported files.

        :param path: Absolute or workspace-relative file path.
        :returns: Deterministic file-group label.
        :raises ValueError: If the file does not belong to a supported group.
        """
        file_path = self._resolve_workspace_path(path)
        name = file_path.name.lower()
        suffix = file_path.suffix.lower()

        if name == "dockerfile" or name.startswith("dockerfile."):
            return _FILE_GROUP_CONFIG
        if suffix in _DOCUMENT_EXTENSIONS:
            return _FILE_GROUP_DOCUMENT
        if suffix in _CODE_EXTENSIONS:
            return _FILE_GROUP_CODE
        if suffix in _CONFIG_EXTENSIONS:
            return _FILE_GROUP_CONFIG
        if suffix in _MARKUP_EXTENSIONS:
            return _FILE_GROUP_MARKUP
        raise ValueError(f"Unsupported file group for '{file_path}'")

    def supported_block_types(self, path: str | Path) -> list[str]:
        """
        Description:
            Return the supported block types for one file.

        Requirements:
            - Preserve the group-specific block-type contract.
            - Keep the list ordered from most specific to broadest block.

        :param path: Absolute or workspace-relative file path.
        :returns: Ordered list of supported excerpt block types.
        """
        file_group = self.resolve_file_group(path)
        resolved = self._resolve_workspace_path(path)
        if file_group == _FILE_GROUP_DOCUMENT:
            return ["line", "sentence", "paragraph", "section"]
        if file_group == _FILE_GROUP_CODE:
            return ["line", "module", "class", "function"]
        if file_group == _FILE_GROUP_CONFIG:
            if resolved.suffix.lower() == ".json":
                return ["line", "entry", "object", "section"]
            return ["line", "entry", "section"]
        return ["line", "sentence", "paragraph", "section"]

    async def discover_excerpts(
        self,
        terms: str | list[str],
        *,
        paths: list[str | Path],
        block_types: list[str] | None = None,
        ignore_case: bool = False,
    ) -> ExcerptDiscoveryResult:
        """
        Description:
            Return a compact per-file discovery summary for one or more files.

        Requirements:
            - Accept one or more search terms plus one or more file paths.
            - Return stable references and line spans without dumping full text.
            - Reject unsupported block types clearly instead of falling back.

        :param terms: One search term or a list of literal search terms.
        :param paths: Files to inspect for the supplied terms.
        :param block_types: Optional block types to restrict the discovery to.
        :param ignore_case: Whether literal matching should ignore case.
        :returns: Compact excerpt discovery payload.
        """
        try:
            normalized_terms = self._normalise_terms(terms)
            requested_block_types = self._normalise_block_types(block_types)
            if not normalized_terms:
                raise ValueError("At least one search term is required")
            if not paths:
                raise ValueError("At least one file path is required")

            file_summaries: list[ExcerptFileSummary] = []
            truncated = False
            for path in paths:
                file_summary = await self._discover_file_excerpts(
                    path,
                    normalized_terms,
                    requested_block_types=requested_block_types,
                    ignore_case=ignore_case,
                )
                if file_summary.match_count:
                    file_summaries.append(file_summary)
                truncated = truncated or file_summary.match_count >= self.max_matches
            return ExcerptDiscoveryResult(files=file_summaries, truncated=truncated)
        except (ValueError, FileNotFoundError, TimeoutError, RuntimeError) as exc:
            return ExcerptDiscoveryResult(error=str(exc))

    async def retrieve_excerpts(
        self,
        references: list[str],
    ) -> ExcerptRetrievalResult:
        """
        Description:
            Resolve stable excerpt references into the requested text blocks.

        Requirements:
            - Return the exact block text previously identified during discovery.
            - Fail clearly when a reference is malformed or unsupported.

        :param references: Stable excerpt references returned by discovery.
        :returns: Materialised excerpt payload for the requested references.
        """
        try:
            if not references:
                raise ValueError("At least one excerpt reference is required")

            blocks: list[ExcerptBlock] = []
            for reference in references:
                blocks.append(self._materialize_reference(reference))
            return ExcerptRetrievalResult(blocks=blocks, truncated=False)
        except (ValueError, FileNotFoundError, TimeoutError, RuntimeError) as exc:
            return ExcerptRetrievalResult(error=str(exc))

    async def search(
        self,
        pattern: str,
        *,
        path: str | None = None,
        file_glob: str | None = None,
        ignore_case: bool = False,
    ) -> SearchResult:
        """
        Description:
            Run a regex-based search against the workspace.

        Requirements:
            - Validate the optional path filter before invoking ripgrep.
            - Parse the JSON output into structured search results.

        :param pattern: Regular-expression pattern to search for.
        :param path: Optional relative path filter.
        :param ignore_case: Whether ripgrep should ignore case.
        :returns: Structured search result payload.
        """
        try:
            target = self._validate_path(path)
            args = [
                self.rg_binary,
                "--json",
                "--max-count",
                str(self.max_matches),
                pattern,
                str(target),
            ]
            if file_glob:
                args[1:1] = ["--glob", file_glob]
            if ignore_case:
                args.insert(1, "-i")
            payload = await self._run_rg(args)
        except (ValueError, FileNotFoundError, TimeoutError, RuntimeError) as exc:
            return SearchResult(error=str(exc))
        return self._parse_search_output(payload)

    async def search_literal(
        self,
        text: str,
        *,
        path: str | None = None,
        file_glob: str | None = None,
        ignore_case: bool = False,
    ) -> SearchResult:
        """
        Description:
            Run a literal-text search against the workspace.

        Requirements:
            - Validate the optional path filter before invoking ripgrep.
            - Parse the JSON output into structured search results.

        :param text: Literal text to search for.
        :param path: Optional relative path filter.
        :param ignore_case: Whether ripgrep should ignore case.
        :returns: Structured search result payload.
        """
        try:
            target = self._validate_path(path)
            args = [
                self.rg_binary,
                "--json",
                "--fixed-strings",
                "--max-count",
                str(self.max_matches),
                text,
                str(target),
            ]
            if file_glob:
                args[1:1] = ["--glob", file_glob]
            if ignore_case:
                args.insert(1, "-i")
            payload = await self._run_rg(args)
        except (ValueError, FileNotFoundError, TimeoutError, RuntimeError) as exc:
            return SearchResult(error=str(exc))
        return self._parse_search_output(payload)

    async def search_files(
        self,
        filename_pattern: str,
        *,
        path: str | None = None,
    ) -> SearchResult:
        """
        Description:
            Search for matching file names under the workspace.

        Requirements:
            - Validate the optional path filter before invoking ripgrep.
            - Apply the configured match limit to the returned file list.

        :param pattern: Case-insensitive file-name pattern to search for.
        :param path: Optional relative path filter.
        :returns: Structured file-search result payload.
        """
        try:
            target = self._validate_path(path)
            args = [
                self.rg_binary,
                "--files",
                "--glob",
                filename_pattern,
                str(target),
            ]
            payload = await self._run_rg(args)
        except (ValueError, FileNotFoundError, TimeoutError, RuntimeError) as exc:
            return SearchResult(error=str(exc))
        matches: list[FileMatch] = []
        for line in payload.splitlines():
            entry = line.strip()
            if not entry:
                continue
            entry_path = self._coerce_match_path(entry, target)
            size = entry_path.stat().st_size if entry_path.exists() else None
            matches.append(FileMatch(path=str(entry_path), size_bytes=size))
            if len(matches) >= self.max_matches:
                return SearchResult(matches=matches, truncated=True, match_count=len(matches))
        return SearchResult(matches=matches, truncated=False, match_count=len(matches))

    async def _discover_file_excerpts(
        self,
        path: str | Path,
        terms: list[str],
        *,
        requested_block_types: list[str],
        ignore_case: bool,
    ) -> ExcerptFileSummary:
        """
        Description:
            Build one compact discovery summary for a single file.

        Requirements:
            - Group repeated hits under stable block references.
            - Apply the requested block-type filter before returning matches.

        :param path: Absolute or workspace-relative file path.
        :param terms: Literal search terms to match in the file.
        :param requested_block_types: Block types the caller explicitly asked for.
        :param ignore_case: Whether literal matching should ignore case.
        :returns: Per-file discovery summary.
        """
        resolved = self._resolve_workspace_path(path)
        file_group = self.resolve_file_group(resolved)
        supported_block_types = self.supported_block_types(resolved)
        active_block_types = requested_block_types or self._default_block_types(file_group)

        for block_type in active_block_types:
            if block_type not in supported_block_types:
                raise ValueError(
                    f"Unsupported block type '{block_type}' for file group '{file_group}'"
                )

        lines = self._read_lines(resolved)
        line_numbers = self._find_matching_line_numbers(
            lines,
            terms,
            ignore_case=ignore_case,
        )

        if not line_numbers:
            return ExcerptFileSummary(
                path=str(resolved),
                file_group=file_group,
                supported_block_types=supported_block_types,
                block_type_counts={},
                matches=[],
                match_count=0,
            )

        matches: dict[str, ExcerptMatch] = {}
        block_type_counts: dict[str, int] = {}
        for line_number in sorted(line_numbers):
            for block_type in active_block_types:
                span = self._resolve_block_span(resolved, file_group, block_type, line_number)
                if span is None:
                    continue
                line_start, line_end = span
                reference = self._build_reference(
                    resolved.relative_to(self.workspace_root).as_posix(),
                    block_type,
                    line_start,
                    line_end,
                )
                if reference not in matches:
                    matches[reference] = ExcerptMatch(
                        path=str(resolved),
                        file_group=file_group,
                        block_type=block_type,
                        reference=reference,
                        line_start=line_start,
                        line_end=line_end,
                        match_count=0,
                    )
                matches[reference].match_count += 1
                block_type_counts[block_type] = block_type_counts.get(block_type, 0) + 1
                break

        ordered_matches = sorted(
            matches.values(),
            key=lambda item: (item.line_start, item.line_end, item.block_type, item.reference),
        )
        return ExcerptFileSummary(
            path=str(resolved),
            file_group=file_group,
            supported_block_types=supported_block_types,
            block_type_counts=block_type_counts,
            matches=ordered_matches,
            match_count=sum(match.match_count for match in ordered_matches),
        )

    def _materialize_reference(self, reference: str) -> ExcerptBlock:
        """
        Description:
            Materialise one stable excerpt reference into text.

        Requirements:
            - Keep the reference format deterministic and self-describing.
            - Reject malformed references with a clear error.

        :param reference: Stable reference produced by discovery.
        :returns: Materialised excerpt block.
        :raises ValueError: If the reference cannot be parsed or resolved.
        """
        relative_path, block_type, line_start, line_end = self._parse_reference(reference)
        resolved = self._resolve_workspace_path(relative_path)
        file_group = self.resolve_file_group(resolved)
        if block_type not in self.supported_block_types(resolved):
            raise ValueError(f"Unsupported block type '{block_type}' for file group '{file_group}'")

        lines = self._read_lines(resolved)
        if line_start < 1 or line_end > len(lines) or line_start > line_end:
            raise ValueError(f"Reference '{reference}' points outside the file bounds")
        text = "\n".join(lines[line_start - 1 : line_end])
        return ExcerptBlock(
            path=str(resolved),
            file_group=file_group,
            block_type=block_type,
            reference=reference,
            line_start=line_start,
            line_end=line_end,
            text=text,
        )

    def _resolve_block_span(
        self,
        path: Path,
        file_group: str,
        block_type: str,
        line_number: int,
    ) -> tuple[int, int] | None:
        """
        Description:
            Resolve one line number to a deterministic block span.

        Requirements:
            - Use file-group-aware boundaries rather than a single fallback.
            - Reuse code-index symbol spans for code files when available.

        :param path: Absolute file path inside the workspace.
        :param file_group: Deterministic resolver group for the file.
        :param block_type: Requested excerpt block type.
        :param line_number: 1-based line number containing the search hit.
        :returns: Start and end line numbers when the block exists, else `None`.
        """
        lines = self._read_lines(path)
        if block_type == "line":
            return line_number, line_number
        if file_group == _FILE_GROUP_CODE:
            return self._resolve_code_block_span(path, line_number, block_type, lines)
        if file_group == _FILE_GROUP_DOCUMENT:
            return self._resolve_document_block_span(lines, line_number, block_type)
        if file_group == _FILE_GROUP_CONFIG:
            return self._resolve_config_block_span(path, lines, line_number, block_type)
        if file_group == _FILE_GROUP_MARKUP:
            return self._resolve_document_block_span(lines, line_number, block_type)
        return None

    def _resolve_code_block_span(
        self,
        path: Path,
        line_number: int,
        block_type: str,
        lines: list[str],
    ) -> tuple[int, int] | None:
        """
        Description:
            Resolve a code excerpt span using the code-index symbol tree.

        Requirements:
            - Prefer tree-sitter-backed symbol boundaries for functions and
              classes.
            - Treat module excerpts as the whole file when requested.

        :param path: Absolute file path inside the workspace.
        :param line_number: 1-based line number containing the search hit.
        :param block_type: Requested excerpt block type.
        :param lines: Cached source lines for the target file.
        :returns: Start and end line numbers when the block exists, else `None`.
        """
        if block_type == "module":
            return 1, len(lines)

        symbols = self._code_symbols_for_file(path)
        candidates: list[SymbolInfo] = []
        for symbol in symbols:
            symbol_block_type = _CODE_SYMBOL_KIND_TO_BLOCK_TYPE.get(symbol.kind)
            if symbol_block_type != block_type:
                continue
            if symbol.line_start <= line_number <= symbol.line_end:
                candidates.append(symbol)
        if not candidates:
            return None
        symbol = min(
            candidates, key=lambda item: (item.line_end - item.line_start, item.line_start)
        )
        return symbol.line_start, symbol.line_end

    def _resolve_document_block_span(
        self,
        lines: list[str],
        line_number: int,
        block_type: str,
    ) -> tuple[int, int] | None:
        """
        Description:
            Resolve a document excerpt span using line, paragraph, and section
            boundaries.

        Requirements:
            - Keep spans deterministic and based on visible source boundaries.
            - Avoid synthetic semantic grouping beyond simple structural cues.

        :param lines: Cached source lines for the target file.
        :param line_number: 1-based line number containing the search hit.
        :param block_type: Requested excerpt block type.
        :returns: Start and end line numbers when the block exists, else `None`.
        """
        if block_type == "paragraph":
            return self._find_nonblank_span(lines, line_number)
        if block_type == "sentence":
            return line_number, line_number
        if block_type == "section":
            return self._find_document_section_span(lines, line_number)
        return None

    def _resolve_config_block_span(
        self,
        path: Path,
        lines: list[str],
        line_number: int,
        block_type: str,
    ) -> tuple[int, int] | None:
        """
        Description:
            Resolve a config/data excerpt span using deterministic text rules.

        Requirements:
            - Treat entries as single-line blocks for compact retrieval.
            - Support JSON object boundaries when the file is clearly JSON.

        :param path: Absolute file path inside the workspace.
        :param lines: Cached source lines for the target file.
        :param line_number: 1-based line number containing the search hit.
        :param block_type: Requested excerpt block type.
        :returns: Start and end line numbers when the block exists, else `None`.
        """
        if block_type == "entry":
            line = lines[line_number - 1].rstrip("\n")
            if line.strip() and _CONFIG_ENTRY_RE.match(line):
                return line_number, line_number
            if line.strip():
                return line_number, line_number
            return None
        if block_type == "object":
            if path.suffix.lower() != ".json":
                return None
            text = "\n".join(lines)
            stripped = text.strip()
            if not stripped:
                return None
            if not (
                (stripped.startswith("{") and stripped.endswith("}"))
                or (stripped.startswith("[") and stripped.endswith("]"))
            ):
                return None
            return 1, len(lines)
        if block_type == "section":
            return self._find_nonblank_span(lines, line_number)
        return None

    def _find_nonblank_span(self, lines: list[str], line_number: int) -> tuple[int, int] | None:
        """
        Description:
            Expand one line into the surrounding non-blank paragraph.

        Requirements:
            - Use blank lines as the paragraph boundary for deterministic
              retrieval.

        :param lines: Cached source lines for the target file.
        :param line_number: 1-based line number containing the search hit.
        :returns: Paragraph line span, or `None` for blank lines.
        """
        if line_number < 1 or line_number > len(lines):
            return None
        if not lines[line_number - 1].strip():
            return None
        start = line_number
        while start > 1 and lines[start - 2].strip():
            start -= 1
        end = line_number
        while end < len(lines) and lines[end].strip():
            end += 1
        return start, end

    def _find_document_section_span(
        self, lines: list[str], line_number: int
    ) -> tuple[int, int] | None:
        """
        Description:
            Expand one line into a deterministic document section span.

        Requirements:
            - Use markdown heading lines as stable section anchors.
            - Fall back to the full file when no headings exist.

        :param lines: Cached source lines for the target file.
        :param line_number: 1-based line number containing the search hit.
        :returns: Section line span, or `None` when the file is empty.
        """
        if line_number < 1 or line_number > len(lines):
            return None

        headings: list[tuple[int, int]] = []
        for index, line in enumerate(lines, start=1):
            match = _MARKDOWN_HEADING_RE.match(line.strip())
            if match is None:
                continue
            headings.append((index, len(match.group("level"))))

        if not headings:
            return 1, len(lines)

        current_heading: tuple[int, int] | None = None
        for heading_line, heading_level in headings:
            if heading_line > line_number:
                break
            current_heading = (heading_line, heading_level)

        if current_heading is None:
            return 1, headings[0][0] - 1 if headings[0][0] > 1 else len(lines)

        start = current_heading[0]
        current_level = current_heading[1]
        end = len(lines)
        for heading_line, heading_level in headings:
            if heading_line <= start:
                continue
            if heading_level <= current_level:
                end = heading_line - 1
                break
        return start, end

    def _code_symbols_for_file(self, path: Path) -> list[SymbolInfo]:
        """
        Description:
            Return the cached code-index symbols for one file.

        Requirements:
            - Build the code index lazily so code excerpts can reuse tree-sitter
              boundaries without indexing every time.

        :param path: Absolute file path inside the workspace.
        :returns: Sorted symbol list for the file.
        """
        index = self._code_index_for_workspace()
        relative_path = path.relative_to(self.workspace_root).as_posix()
        return index.list_symbols(relative_path)

    def _code_index_for_workspace(self) -> CodeIndex:
        """
        Description:
            Return the cached code index for the current workspace.

        Requirements:
            - Build the index on first use only.

        :returns: Cached code index snapshot.
        """
        if self._code_index is None:
            self._code_index = CodeIndex.build(self.workspace_root)
        return self._code_index

    def _materialize_reference(self, reference: str) -> ExcerptBlock:
        """
        Description:
            Materialise one stable excerpt reference into text.

        Requirements:
            - Keep the reference format deterministic and self-describing.
            - Reject malformed references with a clear error.

        :param reference: Stable reference produced by discovery.
        :returns: Materialised excerpt block.
        :raises ValueError: If the reference cannot be parsed or resolved.
        """
        relative_path, block_type, line_start, line_end = self._parse_reference(reference)
        resolved = self._resolve_workspace_path(relative_path)
        file_group = self.resolve_file_group(resolved)
        if block_type not in self.supported_block_types(resolved):
            raise ValueError(f"Unsupported block type '{block_type}' for file group '{file_group}'")

        lines = self._read_lines(resolved)
        if line_start < 1 or line_end > len(lines) or line_start > line_end:
            raise ValueError(f"Reference '{reference}' points outside the file bounds")

        return ExcerptBlock(
            path=str(resolved),
            file_group=file_group,
            block_type=block_type,
            reference=reference,
            line_start=line_start,
            line_end=line_end,
            text="\n".join(lines[line_start - 1 : line_end]),
        )

    def _normalise_terms(self, terms: str | list[str]) -> list[str]:
        """
        Description:
            Normalise the discovery search terms into a deterministic list.

        Requirements:
            - Support a single term or multiple terms without changing search
              semantics.

        :param terms: One search term or a list of search terms.
        :returns: List of cleaned literal search terms.
        """
        if isinstance(terms, str):
            return [terms.strip()] if terms.strip() else []
        return [term.strip() for term in terms if term.strip()]

    def _normalise_block_types(self, block_types: list[str] | None) -> list[str]:
        """
        Description:
            Normalise the caller requested block types.

        Requirements:
            - Preserve caller ordering so the preference is deterministic.

        :param block_types: Optional requested block-type list.
        :returns: Cleaned block-type list.
        """
        if block_types is None:
            return []
        cleaned = [block_type.strip() for block_type in block_types if block_type.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Duplicate block types are not allowed")
        return cleaned

    def _default_block_types(self, file_group: str) -> list[str]:
        """
        Description:
            Return the default block-type preference for one file group.

        Requirements:
            - Prefer the smallest deterministic blocks first.

        :param file_group: Deterministic resolver group for the file.
        :returns: Ordered block-type preference list.
        """
        return list(_DEFAULT_BLOCK_PRIORITIES[file_group])

    def _build_reference(
        self, relative_path: str, block_type: str, line_start: int, line_end: int
    ) -> str:
        """
        Description:
            Build a stable excerpt reference string.

        Requirements:
            - Encode enough structure to round-trip the excerpt deterministically.

        :param relative_path: Workspace-relative file path.
        :param block_type: Excerpt block type.
        :param line_start: 1-based start line.
        :param line_end: 1-based end line.
        :returns: Stable excerpt reference string.
        """
        return f"{relative_path}::{block_type}::{line_start}-{line_end}"

    def _parse_reference(self, reference: str) -> tuple[str, str, int, int]:
        """
        Description:
            Parse one stable excerpt reference back into structured parts.

        Requirements:
            - Reject malformed references with a clear error message.

        :param reference: Stable reference produced by discovery.
        :returns: Tuple of relative path, block type, start line, and end line.
        :raises ValueError: If the reference format is invalid.
        """
        parts = reference.split("::")
        if len(parts) != 3:
            raise ValueError(f"Invalid excerpt reference '{reference}'")
        relative_path, block_type, span = parts
        try:
            start_text, end_text = span.split("-", 1)
            return relative_path, block_type, int(start_text), int(end_text)
        except ValueError as exc:
            raise ValueError(f"Invalid excerpt reference '{reference}'") from exc

    def _resolve_workspace_path(self, path: str | Path) -> Path:
        """
        Description:
            Resolve one absolute or workspace-relative path safely.

        Requirements:
            - Reject any path that escapes the configured workspace root.

        :param path: Absolute or workspace-relative path.
        :returns: Validated absolute path inside the workspace root.
        :raises ValueError: If the resolved path is outside the workspace root.
        """
        candidate = Path(path)
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (self.workspace_root / candidate).resolve()
        )
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError(f"Path '{path}' resolves outside the workspace root") from exc
        return resolved

    def _read_lines(self, path: Path) -> list[str]:
        """
        Description:
            Read one workspace file into a list of lines.

        Requirements:
            - Preserve deterministic line ordering for excerpt slicing.

        :param path: Absolute file path inside the workspace.
        :returns: File content split into lines without trailing newlines.
        """
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._read_pdf_lines(path)
        if suffix == ".docx":
            return self._read_docx_lines(path)
        if suffix == ".xlsx":
            return self._read_xlsx_lines(path)
        if suffix in {".odt", ".ods"}:
            return self._read_open_document_lines(path)
        return path.read_text(encoding="utf-8", errors="replace").splitlines()

    def _find_matching_line_numbers(
        self,
        lines: list[str],
        terms: list[str],
        *,
        ignore_case: bool,
    ) -> set[int]:
        """Description:
            Return the 1-based line numbers that contain any requested term.

        Requirements:
            - Use deterministic in-memory matching so binary-derived document formats can participate in excerpt discovery.

        :param lines: Materialised source lines for one file.
        :param terms: Literal search terms to match.
        :param ignore_case: Whether matching should ignore case.
        :returns: Matching 1-based line numbers.
        """

        normalized_terms = [term.casefold() if ignore_case else term for term in terms]
        matches: set[int] = set()
        for line_number, raw_line in enumerate(lines, start=1):
            candidate_line = raw_line.casefold() if ignore_case else raw_line
            if any(term in candidate_line for term in normalized_terms):
                matches.add(line_number)
        return matches

    def _read_pdf_lines(self, path: Path) -> list[str]:
        """Description:
            Extract simple text lines from one PDF-like file.

        Requirements:
            - Prefer literal text chunks embedded in the PDF content streams.
            - Fall back to a best-effort binary decode when no literal chunks are found.

        :param path: Absolute PDF path.
        :returns: Best-effort extracted text lines.
        """

        raw_text = path.read_bytes().decode("latin-1", errors="replace")
        literal_chunks = [
            match.strip() for match in re.findall(r"\(([^()]*)\)", raw_text) if match.strip()
        ]
        if literal_chunks:
            return literal_chunks
        return [line.strip() for line in raw_text.splitlines() if line.strip()]

    def _read_docx_lines(self, path: Path) -> list[str]:
        """Description:
            Extract paragraph lines from one DOCX file.

        Requirements:
            - Read deterministic paragraph text from `word/document.xml`.

        :param path: Absolute DOCX path.
        :returns: Extracted paragraph lines.
        """

        try:
            with zipfile.ZipFile(path) as archive:
                document_xml = archive.read("word/document.xml")
        except (KeyError, zipfile.BadZipFile):
            return []
        return self._extract_wordprocessingml_paragraphs(document_xml)

    def _read_xlsx_lines(self, path: Path) -> list[str]:
        """Description:
            Extract row text from one XLSX workbook.

        Requirements:
            - Resolve shared strings deterministically when worksheets reference them.

        :param path: Absolute XLSX path.
        :returns: Extracted worksheet row lines.
        """

        try:
            with zipfile.ZipFile(path) as archive:
                shared_strings: list[str] = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    shared_strings = self._extract_shared_strings(
                        archive.read("xl/sharedStrings.xml")
                    )
                row_lines: list[str] = []
                for member_name in sorted(
                    name for name in archive.namelist() if name.startswith("xl/worksheets/sheet")
                ):
                    row_lines.extend(
                        self._extract_xlsx_rows(archive.read(member_name), shared_strings)
                    )
                return row_lines
        except zipfile.BadZipFile:
            return []

    def _read_open_document_lines(self, path: Path) -> list[str]:
        """Description:
            Extract textual lines from one OpenDocument text or spreadsheet file.

        Requirements:
            - Read deterministic text content from `content.xml`.

        :param path: Absolute ODT or ODS path.
        :returns: Extracted textual lines.
        """

        try:
            with zipfile.ZipFile(path) as archive:
                content_xml = archive.read("content.xml")
        except (KeyError, zipfile.BadZipFile):
            return []
        return self._extract_open_document_lines(content_xml)

    def _extract_wordprocessingml_paragraphs(self, xml_bytes: bytes) -> list[str]:
        """Description:
            Extract visible paragraph text from WordprocessingML content.

        Requirements:
            - Return one line per non-empty paragraph.

        :param xml_bytes: Raw WordprocessingML bytes.
        :returns: Extracted paragraph lines.
        """

        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = ElementTree.fromstring(xml_bytes)
        paragraphs: list[str] = []
        for paragraph in root.iter(f"{namespace}p"):
            chunks = [text.text or "" for text in paragraph.iter(f"{namespace}t")]
            combined = "".join(chunks).strip()
            if combined:
                paragraphs.append(combined)
        return paragraphs

    def _extract_shared_strings(self, xml_bytes: bytes) -> list[str]:
        """Description:
            Extract the shared-string table from one XLSX workbook.

        Requirements:
            - Preserve workbook string ordering so worksheet references stay stable.

        :param xml_bytes: Raw shared-strings XML bytes.
        :returns: Ordered shared-string values.
        """

        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        root = ElementTree.fromstring(xml_bytes)
        values: list[str] = []
        for item in root.iter(f"{namespace}si"):
            chunks = [text.text or "" for text in item.iter(f"{namespace}t")]
            values.append("".join(chunks).strip())
        return values

    def _extract_xlsx_rows(self, xml_bytes: bytes, shared_strings: list[str]) -> list[str]:
        """Description:
            Extract worksheet row text from one XLSX worksheet XML file.

        Requirements:
            - Resolve shared-string cells when the worksheet stores them by index.

        :param xml_bytes: Raw worksheet XML bytes.
        :param shared_strings: Ordered workbook shared strings.
        :returns: Extracted row lines.
        """

        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        root = ElementTree.fromstring(xml_bytes)
        lines: list[str] = []
        for row in root.iter(f"{namespace}row"):
            row_values: list[str] = []
            for cell in row.iter(f"{namespace}c"):
                cell_type = cell.get("t")
                value_node = cell.find(f"{namespace}v")
                if value_node is None or value_node.text is None:
                    continue
                value_text = value_node.text.strip()
                if cell_type == "s" and value_text.isdigit():
                    string_index = int(value_text)
                    if 0 <= string_index < len(shared_strings):
                        row_values.append(shared_strings[string_index])
                elif value_text:
                    row_values.append(value_text)
            combined = "\t".join(value for value in row_values if value).strip()
            if combined:
                lines.append(combined)
        return lines

    def _extract_open_document_lines(self, xml_bytes: bytes) -> list[str]:
        """Description:
            Extract visible paragraph and table-row text from one OpenDocument content file.

        Requirements:
            - Return deterministic text lines for text and spreadsheet-style OpenDocument formats.

        :param xml_bytes: Raw OpenDocument content XML bytes.
        :returns: Extracted text lines.
        """

        root = ElementTree.fromstring(xml_bytes)
        lines: list[str] = []
        paragraph_tags = {
            "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p",
            "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}h",
        }
        row_tag = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row"
        cell_tag = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-cell"
        for node in root.iter():
            if node.tag in paragraph_tags:
                combined = "".join(node.itertext()).strip()
                if combined:
                    lines.append(combined)
            elif node.tag == row_tag:
                row_values: list[str] = []
                for cell in node.iter(cell_tag):
                    combined = "".join(cell.itertext()).strip()
                    if combined:
                        row_values.append(combined)
                if row_values:
                    lines.append("\t".join(row_values))
        return lines

    def _validate_path(self, path: str | None) -> Path:
        """
        Description:
            Resolve an optional relative path and ensure it stays inside the
            workspace root.

        Requirements:
            - Return the workspace root when no path filter is supplied.
            - Raise `ValueError` when the resolved path escapes the workspace.

        :param path: Optional relative path filter.
        :returns: Validated absolute target path for ripgrep.
        :raises ValueError: If the resolved path is outside the workspace root.
        """
        if path is None:
            return self.workspace_root
        resolved = (self.workspace_root / path).resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ValueError(f"Path '{path}' resolves outside the workspace root") from exc
        return resolved

    async def _run_rg(self, args: list[str]) -> str:
        """
        Description:
            Execute one ripgrep command and return its standard output.

        Requirements:
            - Enforce the configured timeout.
            - Treat ripgrep exit codes `0` and `1` as non-fatal.

        :param args: Full ripgrep command-line argument list.
        :returns: Standard-output payload produced by ripgrep.
        :raises TimeoutError: If ripgrep exceeds the configured timeout.
        :raises RuntimeError: If ripgrep exits with an unexpected error.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace_root),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"ripgrep binary '{self.rg_binary}' was not found") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
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
        """
        Description:
            Parse ripgrep JSON output into structured content matches.

        Requirements:
            - Ignore non-match records.
            - Stop early and mark the result truncated once the configured match
              limit is reached.

        :param payload: JSON-lines output emitted by ripgrep.
        :returns: Structured search result payload.
        """
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
            line_text = data.get("lines", {}).get("text", "").strip()
            submatches = data.get("submatches", [])
            column_start = submatches[0].get("start") if submatches else None
            column_end = submatches[0].get("end") if submatches else None
            absolute_path = self._coerce_match_path(path_text, self.workspace_root)
            matches.append(
                SearchMatch(
                    path=str(absolute_path),
                    line_number=line_number,
                    line_text=line_text,
                    column_start=column_start,
                    column_end=column_end,
                )
            )
            if len(matches) >= self.max_matches:
                return SearchResult(matches=matches, truncated=True, match_count=len(matches))
        return SearchResult(matches=matches, truncated=False, match_count=len(matches))

    def _coerce_match_path(self, raw_path: str, base_path: Path) -> Path:
        """
        Convert one ripgrep-reported path into an absolute workspace path.

        Requirements:
            - Preserve already-absolute paths unchanged.
            - Resolve relative paths from the validated search target's root.

        :param raw_path: Raw path string emitted by ripgrep.
        :param base_path: Validated search target used to launch ripgrep.
        :returns: Absolute path for the reported match.
        """
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return candidate
        anchor = base_path if base_path.is_dir() else base_path.parent
        return (anchor / candidate).resolve()
