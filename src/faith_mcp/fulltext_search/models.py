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
