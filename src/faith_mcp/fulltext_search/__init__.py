"""
Description:
    Export the public full-text search MCP helpers used by the FAITH proof of
    concept.

Requirements:
    - Re-export the full-text search server facade from a stable package surface.
    - Re-export the ripgrep runner used by tests and PA integration code.
"""

from faith_mcp.fulltext_search.ripgrep import RipgrepRunner
from faith_mcp.fulltext_search.server import FullTextSearchServer

__all__ = ["FullTextSearchServer", "RipgrepRunner"]
