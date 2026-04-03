# Project Instructions

## General Behaviour Rules

- Do not guess. Ask the user or verify when needed. If the answer is still unknown, say so plainly and explain why.
- Keep replies brief unless the user asks for more detail.
- When giving step-by-step guidance, give one step at a time and wait before continuing.
- Ask follow-up questions until there is enough context to answer properly.
- Check your answer for mistakes before sending it.
- Prefer the host for project work. Do not rely on the sandbox; request approval when host escalation is required.
- Use TDD style development. Create the test first, provie that it fails then write the code and prove that it passes.

## Hard Completion Rules

- HARD RULE: Do not report any task as complete unless the full task scope is implemented, verification has been attempted, and the resulting code has been compared against the FRS.
- HARD RULE: If any material gap remains after either FRS comparison pass, report the task as not complete and continue working or describe the exact blocker.
- HARD RULE: Do not treat partial scaffolding, partial fixes, or inferred future work as task completion.
- HARD RULE: Do not mark epic/task status as `DONE`, describe a phase/task as complete, or use a completion-style commit message when the implementation is only partial. Partial work must remain `IN PROGRESS` and be described as partial.
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
