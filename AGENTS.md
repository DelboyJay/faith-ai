# Project Instructions

## Hard Completion Rules

- HARD RULE: Do not report any task as complete unless the full task scope is implemented, verification has been attempted, and the resulting code has been compared against the FRS.
- HARD RULE: If any material gap remains after either FRS comparison pass, report the task as not complete and continue working or describe the exact blocker.
- HARD RULE: Do not treat partial scaffolding, partial fixes, or inferred future work as task completion.
- HARD RULE: Whenever code is generated or changed read the coding-style.md file and implement coding standard.

## Code Style Reference

When writing or modifying code, load and follow:

- [`.agents/policies/coding-style.md`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\policies\coding-style.md)

Use that file as the project code-style authority for:

- linting and formatting expectations
- commenting requirements
- testing expectations

If the task does not involve code changes, loading `coding-style.md` is not required.

## Requirements Source of Truth

The Functional Requirements Specification at
[`.agents/FRS-Multi-Agent-AI-Framework.md`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\FRS-Multi-Agent-AI-Framework.md)
is the source of truth for product behaviour, architecture, and acceptance intent.

Task documents under
[`.agents/tasks/`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\tasks)
are derived implementation briefs only.

If a task document conflicts with the FRS, the FRS takes precedence.

If such a conflict is found, the task document must be updated to match the FRS
before the task is treated as fully implementable or complete.
