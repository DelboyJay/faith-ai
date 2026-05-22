# Workflow Rules

Apply this file whenever a task involves implementation, debugging, tests, git
operations, runtime/service changes, or validation of a fix.

## Core Workflow Rules

- Think first before implementing anything and actively check for ambiguity,
  requirement gaps, or hidden assumptions.
- If ambiguity or requirement gaps are found, discuss them with the user fully
  before implementation, one at a time.
- Once ambiguity and requirement gaps have been resolved, ask the user
  explicitly whether they are happy for implementation to go ahead before
  starting the implementation work.
- Keep all code changes tightly focused and minimal. Change only what is
  required for the current task and do not alter existing code unnecessarily.
- If unrelated problems or weaknesses are noticed in existing code, do not fold
  them into the current task silently. Raise them separately with the user as a
  new discussion or task.
- If a user request actually contains multiple distinct tasks, stop and call
  that out clearly instead of silently bundling them together.
- If the user already made a clear decision earlier in the thread, follow that
  decision unless the user explicitly reopens it.

## Test and Verification Rules

- Do not change existing test cases unless they directly conflict with the
  current task requirements.
- If an existing test must be changed because it conflicts with the current
  task, inform the user before changing it and obtain approval first.
- Do not assume a failing test is wrong until it has been checked against the
  FRS and the agreed user requirements.
- Before claiming a fix works, rerun the specific failing test or check first,
  then run the broader verification suite.
- When reporting a failure, conflict, or regression, always explain whether the
  requirement, the test, or the implementation is correct, which side is wrong,
  and why.
- When possible, give a focused recommendation for how to fix a failure,
  conflict, or regression so the user can make an informed decision.

## Git and Commit Rules

- Always run `git status` before starting work and again before committing so
  the current worktree state is known and accidental scope creep is caught
  early.
- Never run Git write operations that can contend for the index in parallel.
  Commands such as `git add`, `git commit`, `git merge`, `git rebase`,
  `git cherry-pick`, and similar index-writing operations must always be run
  sequentially.
- If a `.git/index.lock` issue appears, first assume it may be caused by
  concurrent or interrupted Git activity started during the current task. Check
  whether a live Git process is still running before taking action.
- If no Git process is running and `.git/index.lock` is confirmed to be stale,
  remove the stale lock and then retry the Git command sequentially.
- Always run `pre-commit` before creating a Git commit that will be pushed to
  GitHub so avoidable hook failures are caught locally first and tokens are not
  wasted on preventable retry cycles.

## Runtime and Audit Rules

- Do not restart, recreate, or tear down running services unless the current
  task truly requires it or the user explicitly asks for that action.
- If local audit tools and GitHub or another remote system disagree, report the
  mismatch plainly and inspect the source of truth instead of making blind
  changes.

