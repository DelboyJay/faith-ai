"""
Description:
    Export the public code-index helpers used by the FAITH code-index MCP
    prototype.

Requirements:
    - Re-export the index and server entry points from a stable package surface.
"""

from faith_mcp.code_index.index import CodeIndex
from faith_mcp.code_index.server import CodeIndexServer

__all__ = ["CodeIndex", "CodeIndexServer"]
