"""
Description:
    Define the structured result models used by the full-text search MCP server.

Requirements:
    - Represent both content matches and file-only matches.
    - Provide dictionary conversion helpers for MCP-friendly payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SearchMatch:
    """
    Description:
        Represent one content-level full-text search hit.

    Requirements:
        - Preserve the file path, line information, and optional column range
          for the match.
    """

    path: str
    line_number: int
    line_text: str
    column_start: int | None = None
    column_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the search match into a serialisable dictionary.

        Requirements:
            - Include column fields only when they are present.

        :returns: Serializable content-match payload.
        """
        data = {
            "path": self.path,
            "line_number": self.line_number,
            "line_text": self.line_text,
        }
        if self.column_start is not None:
            data["column_start"] = self.column_start
        if self.column_end is not None:
            data["column_end"] = self.column_end
        return data


@dataclass(slots=True)
class FileMatch:
    """
    Description:
        Represent one file-path-only full-text search hit.

    Requirements:
        - Preserve the matched file path and optional file size.
    """

    path: str
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the file match into a serialisable dictionary.

        Requirements:
            - Include the size field only when it is known.

        :returns: Serializable file-match payload.
        """
        data = {"path": self.path}
        if self.size_bytes is not None:
            data["size_bytes"] = self.size_bytes
        return data


@dataclass(slots=True)
class SearchResult:
    """
    Description:
        Represent one structured search result payload.

    Requirements:
        - Preserve the match list, truncation flag, count, and optional error.
    """

    matches: list[SearchMatch | FileMatch] = field(default_factory=list)
    truncated: bool = False
    match_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the search result into a serialisable dictionary.

        Requirements:
            - Serialize every contained match through its own dictionary helper.

        :returns: Serializable search-result payload.
        """
        return {
            "matches": [match.to_dict() for match in self.matches],
            "truncated": self.truncated,
            "match_count": self.match_count,
            "error": self.error,
        }


@dataclass(slots=True)
class ExcerptMatch:
    """
    Description:
        Represent one deterministic excerpt discovery hit.

    Requirements:
        - Preserve the file group, block type, and stable reference for a
          follow-up retrieval request.
        - Keep the payload compact by omitting the full excerpt text.

    :param path: Absolute file path for the source file.
    :param file_group: Deterministic file group such as ``document`` or ``code``.
    :param block_type: Supported block boundary type used for the match.
    :param reference: Stable excerpt reference suitable for retrieval.
    :param line_start: 1-based start line for the excerpt block.
    :param line_end: 1-based end line for the excerpt block.
    :param match_count: Number of literal search hits represented by the block.
    """

    path: str
    file_group: str
    block_type: str
    reference: str
    line_start: int
    line_end: int
    match_count: int

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the discovery match into a serialisable dictionary.

        Requirements:
            - Keep the stable reference and line span in the payload.

        :returns: Serializable discovery-match payload.
        """
        return {
            "path": self.path,
            "file_group": self.file_group,
            "block_type": self.block_type,
            "reference": self.reference,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "match_count": self.match_count,
        }


@dataclass(slots=True)
class ExcerptFileSummary:
    """
    Description:
        Represent one file-level discovery summary.

    Requirements:
        - Preserve the supported block types and compact match list for the
          file.

    :param path: Absolute file path for the source file.
    :param file_group: Deterministic file group such as ``document`` or ``code``.
    :param supported_block_types: Block types accepted by this file group.
    :param matches: Compact excerpt matches found in the file.
    :param match_count: Number of matches represented in the summary.
    """

    path: str
    file_group: str
    supported_block_types: list[str]
    block_type_counts: dict[str, int] = field(default_factory=dict)
    matches: list[ExcerptMatch] = field(default_factory=list)
    match_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the file summary into a serialisable dictionary.

        Requirements:
            - Serialise the nested match list through the match helper.

        :returns: Serializable file-summary payload.
        """
        return {
            "path": self.path,
            "file_group": self.file_group,
            "supported_block_types": list(self.supported_block_types),
            "block_type_counts": dict(self.block_type_counts),
            "matches": [match.to_dict() for match in self.matches],
            "match_count": self.match_count,
        }


@dataclass(slots=True)
class ExcerptDiscoveryResult:
    """
    Description:
        Represent the compact discovery payload for a query across files.

    Requirements:
        - Preserve per-file summaries and a structured error field.
        - Support a truncation flag so callers can detect bounded responses.

    :param files: Per-file discovery summaries.
    :param truncated: Whether the result set hit the configured match cap.
    :param error: Error message when discovery fails.
    """

    files: list[ExcerptFileSummary] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the discovery result into a serialisable dictionary.

        Requirements:
            - Serialise every nested file summary through its own helper.

        :returns: Serializable discovery-result payload.
        """
        return {
            "files": [file_summary.to_dict() for file_summary in self.files],
            "truncated": self.truncated,
            "error": self.error,
        }


@dataclass(slots=True)
class ExcerptBlock:
    """
    Description:
        Represent one fully materialised excerpt block.

    Requirements:
        - Preserve the stable reference, line range, and text body for the
          requested block.

    :param path: Absolute file path for the source file.
    :param file_group: Deterministic file group such as ``document`` or ``code``.
    :param block_type: Supported block boundary type used for the excerpt.
    :param reference: Stable excerpt reference suitable for retrieval.
    :param line_start: 1-based start line for the excerpt block.
    :param line_end: 1-based end line for the excerpt block.
    :param text: Materialised excerpt text.
    """

    path: str
    file_group: str
    block_type: str
    reference: str
    line_start: int
    line_end: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the excerpt block into a serialisable dictionary.

        Requirements:
            - Return the exact excerpt text requested by the caller.

        :returns: Serializable excerpt-block payload.
        """
        return {
            "path": self.path,
            "file_group": self.file_group,
            "block_type": self.block_type,
            "reference": self.reference,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "text": self.text,
        }


@dataclass(slots=True)
class ExcerptRetrievalResult:
    """
    Description:
        Represent the retrieval payload returned for stable excerpt references.

    Requirements:
        - Preserve the ordered block list and structured error field.
        - Support a truncation flag so callers can detect bounded responses.

    :param blocks: Retrieved excerpt blocks.
    :param truncated: Whether the result set hit the configured match cap.
    :param error: Error message when retrieval fails.
    """

    blocks: list[ExcerptBlock] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the retrieval result into a serialisable dictionary.

        Requirements:
            - Serialise every nested block through its own helper.

        :returns: Serializable retrieval-result payload.
        """
        return {
            "blocks": [block.to_dict() for block in self.blocks],
            "truncated": self.truncated,
            "error": self.error,
        }
