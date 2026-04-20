# FAITH-069 - PA MCP Inventory Grounding

## Summary

Ground Project Agent answers about available FAITH MCP servers and tools in a canonical PA-owned inventory so local models do not hallucinate fake MCP servers or confuse MCP with Microsoft Configuration Manager.

## Scope

- Define MCP as Model Context Protocol in the Project Agent chat tool manifest.
- Explicitly instruct the model not to interpret MCP as Microsoft Configuration Manager.
- Add a canonical PA-visible MCP tool inventory for the interactive chat loop.
- Expose `mcp.list_tools` as the inventory surface.
- Answer user questions such as "what MCP servers are available to FAITH?" directly from the PA inventory without calling the LLM.
- Include the currently exposed filesystem chat actions: `filesystem.read`, `filesystem.list`, and `filesystem.stat`.

## Acceptance Criteria

1. The Project Agent tool manifest defines MCP as Model Context Protocol.
2. The manifest includes `mcp.list_tools` and the filesystem chat actions.
3. MCP inventory questions do not call the LLM.
4. MCP inventory answers mention the filesystem MCP server and its available actions.
5. MCP inventory answers do not mention Microsoft Configuration Manager or invented placeholder servers.
6. Regression tests cover the manifest grounding and the deterministic inventory response.

## Dependencies

- FAITH-068 - PA Chat MCP Tool-Calling Loop

## Notes

- This task does not add new external MCP servers.
- It prevents a UX failure where a local model invents fake MCP servers from general knowledge instead of reporting the FAITH runtime inventory.
