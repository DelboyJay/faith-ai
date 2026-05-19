---
name: efficient-subagent-delegation
description: Split suitable work across multiple sub-agents when the user asks for delegation, sub-agents, or parallel execution. Use this skill to plan bounded parallel subtasks, choose the cheapest effective model and reasoning level for each worker, keep write scopes disjoint, and minimize total token usage while still completing the task reliably.
---

# Efficient Subagent Delegation

## Purpose
Use this skill when the user explicitly asks to:
- split work across sub-agents
- delegate tasks to sub-agents
- run tasks in parallel
- hand work over to smaller or cheaper models where possible

Do not use this skill just because a task is large. Only use it when the user
has clearly asked for delegation or parallel sub-agent work.

## Core Rules
- Delegate only when it is likely to beat local execution on both token cost and elapsed time.
- If the task can be completed locally in one clean pass, do it locally instead of spawning workers.
- Default to a maximum of **2 workers** unless the user explicitly asks for broader parallelism.
- Keep one immediate blocking task local whenever possible.
- Delegate only bounded, concrete subtasks that materially advance the main goal.
- Minimize total token use by choosing the cheapest model and lowest reasoning level that can do the job reliably.
- Give every worker a clear ownership boundary, especially for files they may edit.
- Do not send two workers into the same unresolved write scope.
- Do not duplicate work between workers or between a worker and the main agent.
- Do not wait on workers unless their result is needed for the next critical-path step.

## Model Selection Policy
Default to the least expensive capable option.

### `GPT-5.4-mini`
Use for:
- narrow read-only exploration
- targeted grep/find/read tasks
- simple, localised code edits
- focused test additions with obvious scope
- documentation updates with clear source material

Reasoning:
- prefer `low` or `medium`
- use `high` only if the task is still tightly bounded but slightly tricky

### `GPT-5.4`
Use for:
- moderate implementation tasks
- cross-file but still well-scoped edits
- test/debug work with some ambiguity
- integration work that needs careful reasoning but not flagship depth

Reasoning:
- prefer `medium`
- use `high` only when the task has genuine ambiguity or integration risk

### `GPT-5.5`
Use only for:
- architecture-sensitive subtasks
- complex cross-cutting refactors
- high-ambiguity investigations
- tasks where a weaker model is likely to waste more tokens through failure

Reasoning:
- prefer `medium` or `high`
- use `xhigh` only for genuinely hard architectural or debugging work

## Delegation Workflow
1. Build a short local plan.
2. Identify:
   - the immediate local blocking task
   - the independent sidecar subtasks that can run in parallel
3. Decide whether delegation is actually worth it. If not, keep the work local.
4. Keep worker count as low as possible. Default to 1 worker when that is enough, and do not exceed 2 unless the user explicitly wants wider parallelism.
5. For ambiguous scopes, prefer a read-only explorer-style subtask before assigning a write task.
6. For each delegated subtask, specify:
   - exact outcome required
   - files or module ownership
   - whether the task is read-only or may edit files
   - model choice
   - reasoning level
7. Spawn workers only for the independent subtasks.
8. Continue local work immediately instead of idling.
9. When workers finish, integrate their results and run the necessary verification.

## Worker Prompt Requirements
Every delegated prompt should include:
- the exact task to perform
- the files or directories the worker owns
- any files the worker must not change
- only the minimum context the worker needs
- a reminder that other agents may be working in parallel
- an instruction not to revert or overwrite others' changes
- the exact output expected back

Do not send large pasted summaries or broad project restatements to workers unless
they are genuinely necessary.

## Required Worker Output Shape
Ask workers to return only:
- changed files
- key findings or key decisions
- blockers or risks

Prefer exact file references and concise bullet points over long prose.

## Good Delegation Patterns
- One worker explores backend API wiring while another inspects frontend usage.
- One worker writes tests for a clearly bounded module while the main agent implements the fix.
- One worker updates docs while another handles a non-overlapping implementation slice.

## Bad Delegation Patterns
- Multiple workers editing the same file without explicit separation.
- Sending the core blocking task to a worker and then waiting immediately.
- Using GPT-5.5 for routine grep/read or simple file edits.
- Spawning workers for vague “look into this” requests with no deliverable.
- Sending workers broad project context they do not need.
- Delegating work that the main agent could finish directly with less overhead.

## Reporting Back
When you use this skill:
- briefly state how the work was split
- state the chosen model tier only when useful
- summarize each worker's output clearly
- call out any remaining blocker or risk

## Efficiency Goal
The objective is not “use as many agents as possible.” The objective is:
- fastest reliable completion
- lowest sensible token cost
- minimal rework during integration

Use delegation sparingly and intentionally.
