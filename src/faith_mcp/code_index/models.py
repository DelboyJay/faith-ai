"""
Description:
    Define the structured models used by the code-index MCP package.

Requirements:
    - Preserve file metadata, symbol metadata, and function source payloads in
      JSON-safe models.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SymbolKind(str, Enum):
    """Classify one indexed code symbol."""

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
    """Describe one indexed symbol."""

    name: str = Field(description="Symbol name.")
    kind: SymbolKind = Field(description="Symbol category.")
    file_path: str = Field(description="Relative path from the workspace root.")
    line_start: int = Field(description="1-based start line number.")
    line_end: int = Field(description="1-based end line number.")
    signature: str = Field(description="Symbol declaration signature.")
    docstring: str | None = Field(default=None, description="Leading docstring or comment block.")
    language: str = Field(description="Source language.")
    parent: str | None = Field(default=None, description="Parent symbol name when nested.")


class FileInfo(BaseModel):
    """Describe one indexed file."""

    path: str = Field(description="Relative path from the workspace root.")
    language: str = Field(description="Detected source language.")
    size_bytes: int = Field(description="File size in bytes.")
    symbol_count: int = Field(description="Number of extracted symbols.")
    last_indexed: str = Field(description="ISO 8601 timestamp of the last index pass.")


class FunctionResult(BaseModel):
    """Return one symbol together with its full source body."""

    symbol: SymbolInfo = Field(description="Matched function or method metadata.")
    source: str = Field(description="Full source code for the matched symbol.")
