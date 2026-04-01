"""Data models for full-text search results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SearchMatch:
    path: str
    line_number: int
    line_text: str
    column_start: int | None = None
    column_end: int | None = None

    def to_dict(self) -> dict:
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
    path: str
    size_bytes: int | None = None

    def to_dict(self) -> dict:
        data = {"path": self.path}
        if self.size_bytes is not None:
            data["size_bytes"] = self.size_bytes
        return data


@dataclass(slots=True)
class SearchResult:
    matches: list[SearchMatch | FileMatch] = field(default_factory=list)
    truncated: bool = False
    match_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "matches": [match.to_dict() for match in self.matches],
            "truncated": self.truncated,
            "match_count": self.match_count,
            "error": self.error,
        }
