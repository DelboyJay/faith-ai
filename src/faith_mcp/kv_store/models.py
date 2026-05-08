"""
Description:
    Define the structured request and response models for the KV store tool.

Requirements:
    - Validate the command payloads before Redis access.
    - Keep request and response structures JSON-safe for MCP transport.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class KVSetParams(BaseModel):
    """Parameters for the set command."""

    key: str = Field(..., min_length=1, description="The key to store.")
    value: Any = Field(..., description="The value to store.")
    ttl: int | None = Field(default=None, ge=1, description="Optional TTL in seconds.")
    persistent: bool = Field(default=False, description="Preserve the key across session cleanup.")


class KVGetParams(BaseModel):
    """Parameters for the get command."""

    key: str = Field(..., min_length=1, description="The key to retrieve.")


class KVDeleteParams(BaseModel):
    """Parameters for the delete command."""

    key: str = Field(..., min_length=1, description="The key to delete.")


class KVListParams(BaseModel):
    """Parameters for the list command."""

    prefix: str | None = Field(default=None, description="Optional key prefix filter.")


class KVExistsParams(BaseModel):
    """Parameters for the exists command."""

    key: str = Field(..., min_length=1, description="The key to check.")


class KVSetResult(BaseModel):
    """Return payload for the set command."""

    ok: bool = True
    key: str
    ttl: int | None = None
    persistent: bool = False


class KVGetResult(BaseModel):
    """Return payload for the get command."""

    key: str
    value: Any | None = None
    found: bool


class KVDeleteResult(BaseModel):
    """Return payload for the delete command."""

    key: str
    deleted: bool


class KVListResult(BaseModel):
    """Return payload for the list command."""

    keys: list[str]
    count: int


class KVExistsResult(BaseModel):
    """Return payload for the exists command."""

    key: str
    exists: bool
