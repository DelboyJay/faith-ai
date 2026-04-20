"""Description:
    Expose the FAITH Ollama MCP management server package.

Requirements:
    - Keep Ollama model-management helpers importable from one package root.
"""

from faith_mcp.ollama.server import OllamaMCPServer

__all__ = ["OllamaMCPServer"]
