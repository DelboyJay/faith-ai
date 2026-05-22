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
- HARD RULE: Never add a new task to a closed or completed phase.
- HARD RULE: If a new task appears to fit into an existing phase that is still `IN PROGRESS`, ask the user first whether they want it added to that phase or placed into a new phase instead.
- HARD RULE: When asking that phase-placement question, always offer a proposed name for the new phase so the user can choose explicitly.
- HARD RULE: Always think first before implementing anything and actively check for ambiguity, requirement gaps, or hidden assumptions.
- HARD RULE: If ambiguity or requirement gaps are found, they must be discussed with the user fully before implementation, and those discussion points must be handled one at a time.
- HARD RULE: Once ambiguity and requirement gaps have been resolved, ask the user explicitly whether they are happy for implementation to go ahead before starting the implementation work.
- HARD RULE: Keep all code changes tightly focused and minimal. Change only what is required for the current task and do not alter existing code unnecessarily.
- HARD RULE: If unrelated problems or weaknesses are noticed in existing code, do not fold them into the current task silently. Raise them separately with the user as a new discussion or task.
- HARD RULE: Do not change existing test cases unless they directly conflict with the current task requirements.
- HARD RULE: If an existing test must be changed because it conflicts with the current task, inform the user before changing it and obtain approval first.
- HARD RULE: Treat frontend code as high risk for regressions. Do not change existing frontend code unless the current task genuinely requires it.
- HARD RULE: If a required frontend change may alter the existing UI or UX look and feel, inform the user and seek approval first unless no other implementation path exists.
- HARD RULE: Never run Git write operations that can contend for the index in parallel. Commands such as `git add`, `git commit`, `git merge`, `git rebase`, `git cherry-pick`, and similar index-writing operations must always be run sequentially.
- HARD RULE: If a `.git/index.lock` issue appears, first assume it may be caused by concurrent or interrupted Git activity started during the current task. Check whether a live Git process is still running before taking action.
- HARD RULE: If no Git process is running and `.git/index.lock` is confirmed to be stale, remove the stale lock and then retry the Git command sequentially.
- HARD RULE: When reporting a failure, conflict, or regression, always explain whether the requirement, the test, or the implementation is correct, which side is wrong, and why. When possible, also give a focused recommendation for how to fix it so the user can make an informed decision.
- HARD RULE: Always run `pre-commit` before creating a Git commit that will be pushed to GitHub so avoidable hook failures are caught locally first and tokens are not wasted on preventable retry cycles.
- HARD RULE: Always run `git status` before starting work and again before committing so the current worktree state is known and accidental scope creep is caught early.
- HARD RULE: If the task is only a focused bug fix, do not update planning docs, epic files, or visible version numbers unless the user-facing shipped behaviour genuinely changes or the user explicitly asks for those updates.
- HARD RULE: Before claiming a fix works, rerun the specific failing test or check first, then run the broader verification suite.
- HARD RULE: If a user request actually contains multiple distinct tasks, stop and call that out clearly instead of silently bundling them together.
- HARD RULE: Do not assume a failing test is wrong until it has been checked against the FRS and the agreed user requirements.
- HARD RULE: If the user already made a clear decision earlier in the thread, follow that decision unless the user explicitly reopens it.
- HARD RULE: Do not restart, recreate, or tear down running services unless the current task truly requires it or the user explicitly asks for that action.
- HARD RULE: If local audit tools and GitHub or another remote system disagree, report the mismatch plainly and inspect the source of truth instead of making blind changes.

## Hard Execution Rules

- HARD RULE: Only make focused micro changes in areas directly related to the current task. Do not change surrounding code or adjacent behavior unless it is required for the current task.
- HARD RULE: Do not immediately dive into a change or task. Think first before making changes.
- HARD RULE: Always ensure there is no ambiguity or gaps in the requirements before executing a task. Ask the user questions to resolve these issues first. You may suggest the best fix, but the user must make the final decision.
- HARD RULE: Do not hallucinate or invent details to fill gaps. Report any issues or problems to the user for discussion instead.
- HARD RULE: If there are multiple questions, state how many there are and ask them one at a time so each can be discussed properly. The user has the final say, but any issues with the user's decision must still be reported clearly so they are understood.
- HARD RULE: If a phase has already been completed, do not add new tasks to that completed phase later even if the task would otherwise fit there. If a new task does not clearly belong to another suitable incomplete phase, create a new phase for it instead.
- HARD RULE: If a UI option is greyed out, disabled, or otherwise not selectable because of its current state, it must always expose a tooltip that explains why it is unavailable and how the user can restore or enable it.

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

## Versioning Rules

- Always update the visible project version when shipping a change so the running UI proves the latest code is present.
- Use `major.minor.revision` versioning.
- Increment the `major` number for breaking changes or incompatible behaviour changes.
- Increment the `minor` number for new features or meaningful new user-facing capabilities.
- Increment the `revision` number for bug fixes, small behaviour fixes, and other minor changes.
- When a version is changed, update every source of that displayed version needed by the product so the UI and served metadata stay in sync.
