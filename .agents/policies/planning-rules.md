# Planning Rules

Apply this file whenever a task involves the FRS, epic, task documents, phase
status, planning updates, or completion claims.

## Completion Rules

- Do not report any task as complete unless the full task scope is implemented,
  verification has been attempted, and the resulting code has been compared
  against the FRS.
- If any material gap remains after either FRS comparison pass, report the task
  as not complete and continue working or describe the exact blocker.
- Do not treat partial scaffolding, partial fixes, or inferred future work as
  task completion.
- Do not mark epic/task status as `DONE`, describe a phase/task as complete, or
  use a completion-style commit message when the implementation is only
  partial. Partial work must remain `IN PROGRESS` and be described as partial.

## Phase and Task Rules

- Never add a new task to a closed or completed phase.
- If a new task appears to fit into an existing phase that is still
  `IN PROGRESS`, ask the user first whether they want it added to that phase or
  placed into a new phase instead.
- When asking that phase-placement question, always offer a proposed name for
  the new phase so the user can choose explicitly.
- If a phase has already been completed, do not add new tasks to that completed
  phase later even if the task would otherwise fit there. If a new task does
  not clearly belong to another suitable incomplete phase, create a new phase
  for it instead.

## Requirements Hierarchy

- The Functional Requirements Specification at
  `.agents/FRS-Multi-Agent-AI-Framework.md` is the source of truth for product
  behaviour, architecture, and acceptance intent.
- Task documents under `.agents/tasks/` are derived implementation briefs only.
- If a task document conflicts with the FRS, the FRS takes precedence.
- If such a conflict is found, the task document must be updated to match the
  FRS before the task is treated as fully implementable or complete.

