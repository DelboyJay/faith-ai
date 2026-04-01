---
name: update-dependency-graph
description: Regenerate `.agents/dependency-graph.md` from `.agents/epic.yaml` using the local generator script whenever the epic or task dependencies have changed.
---

# Update Dependency Graph

Use this skill when the user asks to refresh the dependency graph, regenerate wave ordering, or sync `.agents/dependency-graph.md` to the latest `.agents/epic.yaml`.

## Workflow

1. Treat [epic.yaml](E:\ClaudeSharedFolder\AI Agent Framework\.agents\epic.yaml) as the source of truth.
2. Prefer the platform wrapper script first:

On Windows:

```powershell
.agents\generate-dependency-graph.bat
```

On Linux/macOS:

```bash
./.agents/generate-dependency-graph.sh
```

3. If you cannot use the wrapper, discover Python first:

On Windows:

```powershell
where.exe python
```

On Linux/macOS:

```bash
whereis python
```

4. Run the generator directly only as fallback:

```powershell
py -3 .agents\generate_dependency_graph.py
```

If `py` is unavailable, try:

```powershell
python .agents\generate_dependency_graph.py
```

If neither launcher works but you discovered a full Python path, run the script with that executable directly.

5. Review the generated [dependency-graph.md](E:\ClaudeSharedFolder\AI Agent Framework\.agents\dependency-graph.md) for obvious inconsistencies.
6. If `dependency-graph.svg` was generated, keep it; if Graphviz is unavailable, markdown generation alone is acceptable.

## Expectations

- Do not edit `dependency-graph.md` manually unless the generator itself is wrong.
- If the generated graph looks wrong, fix [generate_dependency_graph.py](E:\ClaudeSharedFolder\AI Agent Framework\.agents\generate_dependency_graph.py) or [epic.yaml](E:\ClaudeSharedFolder\AI Agent Framework\.agents\epic.yaml), then rerun the script.
- Keep the graph derived from the epic, not from task docs or ad hoc assumptions.
