# Project Instructions

## Always-On Rules

- Keep this file minimal. Put conditional or task-specific instructions in
  separate files and load them only when relevant.
- Do not guess or invent details. Ask the user or verify when needed. If the
  answer is still unknown, say so plainly and explain why.
- Keep replies brief and token-efficient unless more detail is needed for
  quality, safety, or correctness, or the user asks for it.
- When giving step-by-step guidance, give one step at a time and wait before
  continuing.
- Resolve any material ambiguity that could affect scope, safety, or outcome
  before acting. Ask follow-up questions one at a time until there is enough
  context, and confirm with the user before material changes, disruptive
  tests, or changes in agreed scope.
- Check your answer for mistakes before sending it.
- Prefer the host for project work. Do not rely on the sandbox; request
  approval when host escalation is required.
- Use TDD style development. Create the test first, prove that it fails, then
  write the code and prove that it passes.
- Do not use mocking unless there is no other practical way to achieve the
  required test outcome. Mock third-party servers when needed, but otherwise
  prefer real behavior. If a function must be mocked for observation, such as
  checking call counts, the mock must still call the original function. Avoid
  mocks that replace real return values unless there is no safer alternative.
- If there are multiple questions, state how many there are and ask them one at
  a time so each can be discussed properly.
- Use sub-agents only when they are likely to reduce time or improve results
  without costing more tokens than completing the work directly. Choose the
  lightest sufficient agent tier.

## Policy Files To Load

Load the following focused policy files only when the current task needs them:

- Always load [`.agents/policies/coding-style.md`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\policies\coding-style.md)
  when generating or changing code.
- Load [`.agents/policies/workflow-rules.md`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\policies\workflow-rules.md)
  for implementation, debugging, tests, git work, runtime changes, or fix
  verification.
- Load [`.agents/policies/planning-rules.md`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\policies\planning-rules.md)
  for FRS, epic, tasks, phases, planning updates, or completion decisions.
- Load [`.agents/policies/frontend-risk-rules.md`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\policies\frontend-risk-rules.md)
  for web UI, browser-facing JavaScript, CSS, layout, panel, or other
  user-visible frontend work.
- Load [`.agents/policies/versioning-rules.md`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\policies\versioning-rules.md)
  when shipping a user-facing product change.

## Requirements Source of Truth

The Functional Requirements Specification at
[`.agents/FRS-Multi-Agent-AI-Framework.md`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\FRS-Multi-Agent-AI-Framework.md)
is the source of truth for product behaviour, architecture, and acceptance
intent.

Task documents under
[`.agents/tasks/`](E:\ClaudeSharedFolder\AI Agent Framework\.agents\tasks)
are derived implementation briefs only.

If a task document conflicts with the FRS, the FRS takes precedence.
