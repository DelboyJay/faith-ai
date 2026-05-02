# FAITH-067 — Ollama Management MCP Server

## Summary

Implement a FAITH-owned Ollama MCP server that lets the PA manage local Ollama models through a bounded, auditable tool interface rather than embedding Ollama administration directly in the PA core.

## Scope

- List installed Ollama models.
- List currently loaded/running Ollama models, including CPU/GPU processor split where Ollama reports it.
- Pull a requested model after explicit user approval.
- Delete a local model after explicit user approval.
- Probe a model with a tiny inference request and return runtime metrics.
- Set the PA default model, specialist-agent default model, or both by updating project `.faith/system.yaml` after explicit user approval.
- Expose these operations through a generic `handle_tool_call()` dispatch surface for PA/MCP integration.

## Acceptance Criteria

1. Read-only model inspection works without approval.
2. Mutating actions (`pull_model`, `delete_model`, `set_default_model`) fail unless approval is explicitly supplied.
3. Pull requests call Ollama `/api/pull` with `stream: false` for deterministic MCP responses.
4. Delete requests call Ollama `/api/delete`.
5. Probe requests call Ollama `/api/generate` and include current `/api/ps` data in the result.
6. Default-model changes preserve unrelated `system.yaml` keys.
7. Tests cover read-only operations, approval gates, model probing, default-model updates, and generic tool dispatch.

## Dependencies

- FAITH-004 — Config Hot-Reload
- FAITH-013 — LLM API Client (Ollama + OpenRouter)
- FAITH-019 — Security Approval Engine
- FAITH-051 — Ollama Model Download Integration

## Notes

- The MCP server owns Ollama API details; the PA should treat model management as a tool call.
- UI support can be added later on top of the same tool surface.
- The approval layer must wrap mutating calls before setting `approved: true`.
