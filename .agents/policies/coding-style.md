# Coding Style Policy

**Purpose:** Define the coding-style standards, implementation rules, and test discipline used by this repository.

This file is the code-style authority referenced by `AGENTS.md`.

## 1 Hard Rules

- Hard rules MUST ALWAYS be run after code is modified.
- Always run linting after modifying code.
- Always apply commenting rules when writing code.
- Do not use the sandbox for project work. Use the host to run commands.

## 2 Linting

- Use pre-commit or Ruff to check for linting errors and to auto-format code.
- Keep the repository passing the configured linting and formatting checks after each code change.

## 3 Commenting

- All classes, functions, and test cases must be documented.
- Python code must use reStructuredText-style docstrings/comments.
- JavaScript and TypeScript code must use JSDoc-style comments.
- Every new or updated class and function must include a docstring.
- Document all non-`self` Python function parameters with reST `:param <name>:` entries.
- Document JavaScript and TypeScript parameters and return values with JSDoc tags such as `@param`, `@returns`, and `@throws`.
- Use typed function signatures for arguments and return types.
- The comment or docstring must contain a description and requirements explaining why the object is needed and what it does.
- Use reST field tags where applicable: `:param:`, `:keyword:`/`:kwarg:`, `:return:`/`:returns:`, `:raises:`, `:var:`, `:ivar:`, `:cvar:`, and `:yield:`/`:yields:`.
- Do not use `:type:`, `:rtype:`, `:kwtype:`, `:vartype:`, or `:ytype:`.
- For test functions, the `Requirements` section must explain why the test is needed and what behaviour is being verified at a high level.
- Write comments and docstrings in plain language.
- When returning structured payloads, document the key structure in the docstring or with a short inline comment.
- When an `if` condition is complex, add a short comment above it explaining why the branch exists and what it protects.

### Example Python docstring

```python
"""
Description:
    Generate a deterministic hash of the active session key for structured
    logging without leaking raw identifiers.

Requirements:
    - Return `None` when the request carries no session key.
    - Use SHA-256 to keep the hash stable yet irreversible.

:param request: Incoming request that may carry a session key.
:returns: Stable SHA-256 hash of the session key, or `None` when unavailable.
"""
```

### Example test docstring

```python
"""
Description:
    Verify the lookup endpoint rejects requests without the required OAuth scope.

Requirements:
    - This test is needed to prove the endpoint does not accept under-scoped tokens.
    - Verify the endpoint returns HTTP 403 when the caller lacks the lookup scope.

:param drf_client: DRF test client fixture.
:param user: Baseline user fixture used by the lookup endpoint tests.
"""
```

## 4 Testing

- Use TDD style development where you write the test first, prove that it fails, write the code, and then prove the test passes.
- A correct test case MUST NOT be weakened or rewritten later just to make the implementation pass.
- Tests must verify the intended behaviour explicitly, including expected failures, exceptions, and validation errors where relevant.
- Avoid creating trivial or ineffective tests that would pass regardless of whether the implementation is correct.

### 4.1 Use of Python Mocking in Tests

- Mocking should NOT be used unless there is no other alternative.
- Obtain approval from the user before introducing mocking, including a short explanation of what is being mocked and why it is needed.

## 5 Design Philosophy

- Prefer object-oriented design over procedural functions when it improves clarity, testability, and maintainability.
- This is not a hard mandate: use procedural style when it is simpler and fits the existing module better.
- Use composition over inheritance when practical.
- Prioritize readability over terseness.
- When two solutions are equally clear, prefer the one that reduces code size meaningfully.

## 6 Consistency & Maintainability

- Match the existing codebase style before introducing new patterns.
- Keep code clean, modular, and self-explanatory.
- Refactor repetitive logic into helpers or methods only when it improves maintainability or clarity.
- Refactor only when it makes code shorter, clearer, or more maintainable.
- Avoid pattern-chasing or abstraction that increases indirection without clear benefit.

## 7 Import Style

- Use absolute imports anchored at the project root.
- Do not use relative imports in production code.
- Do not use wildcard imports.
- Group imports as stdlib, third-party, then first-party.
- Avoid `sys.path` manipulation and runtime import hacks.

## 8 Frontend JavaScript Placement

- Keep JavaScript out of templates where possible.
- Move inline scripts into dedicated `.js` files unless minimal inline bootstrapping is unavoidable.
