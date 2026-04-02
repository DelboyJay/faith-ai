"""
Description:
    Export the public full-text search MCP helpers used by the FAITH proof of
    concept.

Requirements:
    - Re-export the full-text search server facade from a stable package surface.
"""

from faith_mcp.fulltext_search.server import FullTextSearchServer

__all__ = ["FullTextSearchServer"]
