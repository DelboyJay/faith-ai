"""
Description:
    Export the public filesystem MCP helpers used by the FAITH proof of concept.

Requirements:
    - Re-export the filesystem server facade from a stable package surface.
"""

from faith_mcp.filesystem.server import FilesystemServer

__all__ = ["FilesystemServer"]
