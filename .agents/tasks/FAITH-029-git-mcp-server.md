# FAITH-029 — Git MCP Server

**Phase:** 6 — Tool Servers
**Complexity:** M
**Model:** Sonnet / GPT-5.4
**Status:** TODO
**Dependencies:** FAITH-019
**FRS Reference:** Section 4.12

---

## Objective

External-first task. Prefer a public/external local git MCP server in v1 if it can satisfy FAITH's approval and audit requirements. The GitHub MCP server is not sufficient because it operates at the remote hosting API level rather than against the local repository on disk. Only implement a FAITH-owned local git MCP server if external options do not meet FAITH's security model. If FAITH does implement a fallback later, the server would expose local git operations as structured commands with typed parameters, structured JSON output, and approval handling integrated with the FAITH security system (FAITH-019).

Implementation note: the embedded in-house implementation sketches below are fallback material only. The normative v1 path is integration of an approved external local git MCP server through FAITH-035.

---

## Architecture

```
faith/tools/git/
├── __init__.py
├── server.py          ← MCP server registration and tool dispatch
├── commands.py        ← Individual git command implementations
├── schemas.py         ← Input/output JSON schemas per command
└── approval.py        ← Default approval rule definitions + security.yaml seeding

tests/
└── test_git_mcp.py    ← Unit and integration tests
```

---

## Files to Create

### 1. `faith/tools/git/schemas.py`

```python
"""JSON schemas for Git MCP tool inputs and outputs.

Each git command defines a typed input schema (parameters the agent
provides) and a typed output schema (structured JSON the tool returns).
Raw shell text is never returned — all output is parsed into structured
fields.

FRS Reference: Section 4.12
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class GitCommand(str, Enum):
    """Supported git commands."""
    STATUS = "status"
    LOG = "log"
    DIFF = "diff"
    ADD = "add"
    COMMIT = "commit"
    BRANCH = "branch"
    CHECKOUT = "checkout"
    PUSH = "push"
    PULL = "pull"
    STASH = "stash"


@dataclass
class GitToolResult:
    """Structured result from any git tool invocation.

    Attributes:
        success: Whether the command completed without error.
        command: The git command that was executed.
        data: Structured output data (command-specific).
        error: Error message if success is False.
        raw_stderr: Raw stderr output for debugging (never shown to LLM
            by default — included only when success is False).
    """
    success: bool
    command: GitCommand
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    raw_stderr: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        result = {
            "success": self.success,
            "command": self.command.value,
            "data": self.data,
        }
        if self.error is not None:
            result["error"] = self.error
        if not self.success and self.raw_stderr:
            result["raw_stderr"] = self.raw_stderr
        return result


# --- Input schemas (used for MCP tool parameter validation) ---

STATUS_INPUT = {
    "type": "object",
    "properties": {
        "short": {
            "type": "boolean",
            "description": "Use short format output.",
            "default": False,
        },
    },
    "additionalProperties": False,
}

LOG_INPUT = {
    "type": "object",
    "properties": {
        "max_count": {
            "type": "integer",
            "description": "Maximum number of commits to return.",
            "default": 20,
            "minimum": 1,
            "maximum": 100,
        },
        "oneline": {
            "type": "boolean",
            "description": "One-line format per commit.",
            "default": False,
        },
        "branch": {
            "type": "string",
            "description": "Branch to show log for. Defaults to current branch.",
        },
        "path": {
            "type": "string",
            "description": "Limit log to commits affecting this path.",
        },
    },
    "additionalProperties": False,
}

DIFF_INPUT = {
    "type": "object",
    "properties": {
        "staged": {
            "type": "boolean",
            "description": "Show staged (cached) changes instead of unstaged.",
            "default": False,
        },
        "path": {
            "type": "string",
            "description": "Limit diff to a specific file or directory.",
        },
        "stat_only": {
            "type": "boolean",
            "description": "Show diffstat summary only (no patch).",
            "default": False,
        },
    },
    "additionalProperties": False,
}

ADD_INPUT = {
    "type": "object",
    "properties": {
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "File paths to stage. Use ['.'] for all changes.",
            "minItems": 1,
        },
    },
    "required": ["paths"],
    "additionalProperties": False,
}

COMMIT_INPUT = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": "Commit message.",
            "minLength": 1,
        },
        "amend": {
            "type": "boolean",
            "description": "Amend the previous commit.",
            "default": False,
        },
    },
    "required": ["message"],
    "additionalProperties": False,
}

BRANCH_INPUT = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "create", "delete"],
            "description": "Branch operation to perform.",
            "default": "list",
        },
        "name": {
            "type": "string",
            "description": "Branch name (required for create/delete).",
        },
    },
    "additionalProperties": False,
}

CHECKOUT_INPUT = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": "Branch name or commit hash to check out.",
        },
        "create": {
            "type": "boolean",
            "description": "Create a new branch and switch to it (-b).",
            "default": False,
        },
    },
    "required": ["target"],
    "additionalProperties": False,
}

PUSH_INPUT = {
    "type": "object",
    "properties": {
        "remote": {
            "type": "string",
            "description": "Remote name.",
            "default": "origin",
        },
        "branch": {
            "type": "string",
            "description": "Branch to push. Defaults to current branch.",
        },
        "set_upstream": {
            "type": "boolean",
            "description": "Set upstream tracking reference (-u).",
            "default": False,
        },
        "force": {
            "type": "boolean",
            "description": "Force push (--force). Requires always_ask approval.",
            "default": False,
        },
    },
    "additionalProperties": False,
}

PULL_INPUT = {
    "type": "object",
    "properties": {
        "remote": {
            "type": "string",
            "description": "Remote name.",
            "default": "origin",
        },
        "branch": {
            "type": "string",
            "description": "Branch to pull. Defaults to current branch.",
        },
        "rebase": {
            "type": "boolean",
            "description": "Pull with rebase instead of merge.",
            "default": False,
        },
    },
    "additionalProperties": False,
}

STASH_INPUT = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["push", "pop", "list", "drop", "apply"],
            "description": "Stash operation to perform.",
            "default": "push",
        },
        "message": {
            "type": "string",
            "description": "Stash message (for push action only).",
        },
        "index": {
            "type": "integer",
            "description": "Stash index (for pop/drop/apply actions).",
            "default": 0,
        },
    },
    "additionalProperties": False,
}

# Lookup table: command → input schema
COMMAND_SCHEMAS: dict[GitCommand, dict] = {
    GitCommand.STATUS: STATUS_INPUT,
    GitCommand.LOG: LOG_INPUT,
    GitCommand.DIFF: DIFF_INPUT,
    GitCommand.ADD: ADD_INPUT,
    GitCommand.COMMIT: COMMIT_INPUT,
    GitCommand.BRANCH: BRANCH_INPUT,
    GitCommand.CHECKOUT: CHECKOUT_INPUT,
    GitCommand.PUSH: PUSH_INPUT,
    GitCommand.PULL: PULL_INPUT,
    GitCommand.STASH: STASH_INPUT,
}
```

### 2. `faith/tools/git/approval.py`

```python
"""Default approval tier mappings for git commands and security.yaml seeding.

Each git command has a hardcoded default approval tier. On first run,
these defaults are written to .faith/security.yaml so users can see
and override them. The approval engine from FAITH-019 evaluates these
rules at runtime.

FRS Reference: Section 4.12, 5.1
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from faith.tools.git.schemas import GitCommand

logger = logging.getLogger("faith.tools.git.approval")


class ApprovalTier(str, Enum):
    """Canonical approval tiers used by the FAITH approval engine."""
    ALWAYS_ALLOW = "always_allow"
    APPROVE_SESSION = "approve_session"
    ALWAYS_ASK = "always_ask"


# Default approval tier for each git command (FRS Section 4.12.2)
DEFAULT_TIERS: dict[GitCommand, ApprovalTier] = {
    # Git actions default to ask-first in FAITH. Persisted allow rules are
    # stored by the shared approval engine rather than hard-coded here.
    GitCommand.STATUS: ApprovalTier.ALWAYS_ASK,
    GitCommand.LOG: ApprovalTier.ALWAYS_ASK,
    GitCommand.DIFF: ApprovalTier.ALWAYS_ASK,
    GitCommand.ADD: ApprovalTier.ALWAYS_ASK,
    GitCommand.COMMIT: ApprovalTier.ALWAYS_ASK,
    GitCommand.BRANCH: ApprovalTier.ALWAYS_ASK,
    GitCommand.CHECKOUT: ApprovalTier.ALWAYS_ASK,
    GitCommand.PULL: ApprovalTier.ALWAYS_ASK,
    GitCommand.STASH: ApprovalTier.ALWAYS_ASK,
    GitCommand.PUSH: ApprovalTier.ALWAYS_ASK,
}


def get_approval_tier(command: GitCommand) -> ApprovalTier:
    """Return the default approval tier for a git command.

    Args:
        command: The git command.

    Returns:
        The approval tier. Defaults to ALWAYS_ASK for unknown commands.
    """
    return DEFAULT_TIERS.get(command, ApprovalTier.ALWAYS_ASK)


def build_default_rules() -> dict[str, list[str]]:
    """Build the default regex rules for security.yaml.

    Returns a dict keyed by approval tier, with lists of regex patterns
    matching each git MCP command.

    Returns:
        Dict with canonical approval-tier keys and regex pattern lists.
    """
    rules: dict[str, list[str]] = {
        ApprovalTier.ALWAYS_ALLOW.value: [],
        ApprovalTier.APPROVE_SESSION.value: [],
        ApprovalTier.ALWAYS_ASK.value: [],
    }

    for cmd, tier in DEFAULT_TIERS.items():
        # Pattern matches the MCP tool name format: git_{command}
        pattern = f"^git_{cmd.value}$"
        rules[tier.value].append(pattern)

    return rules


def seed_security_yaml(faith_dir: Path) -> bool:
    """Pre-populate .faith/security.yaml with default git approval rules.

    Reads the existing security.yaml (if any), merges git tool rules
    into the approval_rules section under a 'git_tools' key, and writes
    the file back. Does not overwrite existing git_tools rules if they
    are already present.

    Args:
        faith_dir: Path to the .faith directory.

    Returns:
        True if rules were written, False if they already existed.
    """
    security_path = faith_dir / "security.yaml"

    # Load existing config or start fresh
    existing: dict[str, Any] = {}
    if security_path.exists():
        try:
            raw = security_path.read_text(encoding="utf-8")
            existing = yaml.safe_load(raw) or {}
        except Exception as e:
            logger.warning(f"Failed to read {security_path}: {e}")
            existing = {}

    # Check if git rules already exist
    approval_rules = existing.setdefault("approval_rules", {})
    git_rules = approval_rules.get("git_tools")

    if git_rules is not None:
        logger.debug("Git approval rules already present in security.yaml")
        return False

    # Build and insert default rules
    approval_rules["git_tools"] = {
        "_comment": (
            "Default approval rules for the Git MCP tool. "
            "Edit these patterns or move them between tiers to customise. "
            "See FRS Section 4.12 and 5.1."
        ),
        **build_default_rules(),
    }

    # Write back
    try:
        security_path.parent.mkdir(parents=True, exist_ok=True)
        security_path.write_text(
            yaml.dump(existing, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        logger.info(f"Seeded git approval rules in {security_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to write {security_path}: {e}")
        return False
```

### 3. `faith/tools/git/commands.py`

```python
"""Git command implementations for the Git MCP tool server.

Each function executes a git command via asyncio.create_subprocess_exec,
parses the output into structured data, and returns a GitToolResult.
All output is structured JSON — raw shell text is never returned to the
agent.

FRS Reference: Section 4.12
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

from faith.tools.git.schemas import GitCommand, GitToolResult

logger = logging.getLogger("faith.tools.git.commands")


async def _run_git(
    args: list[str],
    cwd: Path,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    """Execute a git command and return (returncode, stdout, stderr).

    Args:
        args: Git command arguments (without the 'git' prefix).
        cwd: Working directory for the git command.
        timeout: Maximum seconds to wait for the command.

    Returns:
        Tuple of (return_code, stdout_text, stderr_text).

    Raises:
        asyncio.TimeoutError: If the command exceeds the timeout.
    """
    env = os.environ.copy()
    # Prevent git from prompting for credentials or input
    env["GIT_TERMINAL_PROMPT"] = "0"

    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

    return proc.returncode, stdout, stderr


def _error_result(
    command: GitCommand, error: str, stderr: str = ""
) -> GitToolResult:
    """Build an error GitToolResult."""
    return GitToolResult(
        success=False,
        command=command,
        error=error,
        raw_stderr=stderr or None,
    )


# ──────────────────────────────────────────────────
# git status
# ──────────────────────────────────────────────────


async def git_status(cwd: Path, short: bool = False) -> GitToolResult:
    """Get working tree status as structured data.

    Returns:
        GitToolResult with data containing:
        - branch: current branch name
        - staged: list of staged file paths with status
        - unstaged: list of unstaged file paths with status
        - untracked: list of untracked file paths
        - clean: bool indicating no pending changes
    """
    args = ["status", "--porcelain=v2", "--branch"]
    if short:
        args = ["status", "--short", "--branch"]

    rc, stdout, stderr = await _run_git(args, cwd)
    if rc != 0:
        return _error_result(GitCommand.STATUS, "git status failed", stderr)

    branch = ""
    staged = []
    unstaged = []
    untracked = []

    for line in stdout.splitlines():
        if line.startswith("# branch.head"):
            branch = line.split()[-1]
        elif line.startswith("1 ") or line.startswith("2 "):
            # Changed entries: field layout is
            # 1 XY sub mH mI mW hH hI path
            parts = line.split(maxsplit=8)
            if len(parts) >= 9:
                xy = parts[1]
                filepath = parts[8]
                if xy[0] != ".":
                    staged.append({"path": filepath, "status": xy[0]})
                if xy[1] != ".":
                    unstaged.append({"path": filepath, "status": xy[1]})
        elif line.startswith("? "):
            untracked.append(line[2:])

    return GitToolResult(
        success=True,
        command=GitCommand.STATUS,
        data={
            "branch": branch,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
            "clean": len(staged) == 0
                and len(unstaged) == 0
                and len(untracked) == 0,
        },
    )


# ──────────────────────────────────────────────────
# git log
# ──────────────────────────────────────────────────


async def git_log(
    cwd: Path,
    max_count: int = 20,
    oneline: bool = False,
    branch: Optional[str] = None,
    path: Optional[str] = None,
) -> GitToolResult:
    """Get commit history as structured data.

    Returns:
        GitToolResult with data containing:
        - commits: list of dicts with hash, author, date, message
    """
    # Use a machine-parseable format
    separator = "---FAITH_COMMIT_SEP---"
    fmt = f"%H%n%an%n%ae%n%aI%n%s%n%b%n{separator}"

    args = ["log", f"--max-count={max_count}", f"--format={fmt}"]
    if branch:
        args.append(branch)
    if path:
        args.extend(["--", path])

    rc, stdout, stderr = await _run_git(args, cwd)
    if rc != 0:
        return _error_result(GitCommand.LOG, "git log failed", stderr)

    commits = []
    blocks = stdout.split(separator)
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 5:
            continue
        commit = {
            "hash": lines[0],
            "author": lines[1],
            "email": lines[2],
            "date": lines[3],
            "subject": lines[4],
            "body": "\n".join(lines[5:]).strip(),
        }
        commits.append(commit)

    return GitToolResult(
        success=True,
        command=GitCommand.LOG,
        data={"commits": commits, "count": len(commits)},
    )


# ──────────────────────────────────────────────────
# git diff
# ──────────────────────────────────────────────────


async def git_diff(
    cwd: Path,
    staged: bool = False,
    path: Optional[str] = None,
    stat_only: bool = False,
) -> GitToolResult:
    """Get diff output as structured data.

    Returns:
        GitToolResult with data containing:
        - files: list of dicts with path, insertions, deletions
        - patch: full diff text (omitted if stat_only is True)
        - stat: diffstat summary
    """
    args = ["diff"]
    if staged:
        args.append("--cached")
    if stat_only:
        args.append("--stat")
    else:
        args.append("--stat")  # Always include stat
    if path:
        args.extend(["--", path])

    rc, stdout, stderr = await _run_git(args, cwd)
    if rc != 0:
        return _error_result(GitCommand.DIFF, "git diff failed", stderr)

    stat_text = stdout

    # Also get the numstat for structured file-level data
    numstat_args = ["diff", "--numstat"]
    if staged:
        numstat_args.append("--cached")
    if path:
        numstat_args.extend(["--", path])

    rc2, numstat_out, _ = await _run_git(numstat_args, cwd)

    files = []
    if rc2 == 0:
        for line in numstat_out.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                added = parts[0] if parts[0] != "-" else 0
                removed = parts[1] if parts[1] != "-" else 0
                files.append({
                    "path": parts[2],
                    "insertions": int(added),
                    "deletions": int(removed),
                })

    data: dict[str, Any] = {
        "files": files,
        "stat": stat_text,
        "total_files": len(files),
    }

    # Include full patch unless stat_only
    if not stat_only:
        patch_args = ["diff"]
        if staged:
            patch_args.append("--cached")
        if path:
            patch_args.extend(["--", path])
        rc3, patch_out, _ = await _run_git(patch_args, cwd)
        if rc3 == 0:
            data["patch"] = patch_out

    return GitToolResult(
        success=True,
        command=GitCommand.DIFF,
        data=data,
    )


# ──────────────────────────────────────────────────
# git add
# ──────────────────────────────────────────────────


async def git_add(cwd: Path, paths: list[str]) -> GitToolResult:
    """Stage files for commit.

    Returns:
        GitToolResult with data containing:
        - staged_paths: list of paths that were staged
    """
    if not paths:
        return _error_result(GitCommand.ADD, "No paths provided")

    args = ["add", "--"] + paths

    rc, stdout, stderr = await _run_git(args, cwd)
    if rc != 0:
        return _error_result(GitCommand.ADD, "git add failed", stderr)

    return GitToolResult(
        success=True,
        command=GitCommand.ADD,
        data={"staged_paths": paths},
    )


# ──────────────────────────────────────────────────
# git commit
# ──────────────────────────────────────────────────


async def git_commit(
    cwd: Path, message: str, amend: bool = False
) -> GitToolResult:
    """Create a commit.

    Returns:
        GitToolResult with data containing:
        - hash: the new commit hash
        - message: the commit message
        - branch: the branch committed to
    """
    if not message.strip():
        return _error_result(GitCommand.COMMIT, "Commit message cannot be empty")

    args = ["commit", "-m", message]
    if amend:
        args.append("--amend")

    rc, stdout, stderr = await _run_git(args, cwd)
    if rc != 0:
        return _error_result(GitCommand.COMMIT, "git commit failed", stderr)

    # Extract commit hash from output
    commit_hash = ""
    hash_args = ["rev-parse", "HEAD"]
    rc2, hash_out, _ = await _run_git(hash_args, cwd)
    if rc2 == 0:
        commit_hash = hash_out.strip()

    # Get current branch
    branch_args = ["rev-parse", "--abbrev-ref", "HEAD"]
    rc3, branch_out, _ = await _run_git(branch_args, cwd)
    branch = branch_out.strip() if rc3 == 0 else ""

    return GitToolResult(
        success=True,
        command=GitCommand.COMMIT,
        data={
            "hash": commit_hash,
            "message": message,
            "branch": branch,
            "amended": amend,
        },
    )


# ──────────────────────────────────────────────────
# git branch
# ──────────────────────────────────────────────────


async def git_branch(
    cwd: Path,
    action: str = "list",
    name: Optional[str] = None,
) -> GitToolResult:
    """List, create, or delete branches.

    Returns:
        GitToolResult with data containing:
        - For list: branches (list), current (str)
        - For create: created (str)
        - For delete: deleted (str)
    """
    if action == "list":
        args = ["branch", "--format=%(refname:short)%(HEAD)"]
        rc, stdout, stderr = await _run_git(args, cwd)
        if rc != 0:
            return _error_result(GitCommand.BRANCH, "git branch failed", stderr)

        branches = []
        current = ""
        for line in stdout.splitlines():
            line = line.strip()
            if line.endswith("*"):
                branch_name = line[:-1].strip()
                current = branch_name
            elif line.startswith("*"):
                branch_name = line[1:].strip()
                current = branch_name
            else:
                branch_name = line.strip()
            if branch_name:
                branches.append(branch_name)

        return GitToolResult(
            success=True,
            command=GitCommand.BRANCH,
            data={"branches": branches, "current": current},
        )

    elif action == "create":
        if not name:
            return _error_result(
                GitCommand.BRANCH, "Branch name required for create"
            )
        args = ["branch", name]
        rc, stdout, stderr = await _run_git(args, cwd)
        if rc != 0:
            return _error_result(GitCommand.BRANCH, "git branch create failed", stderr)
        return GitToolResult(
            success=True,
            command=GitCommand.BRANCH,
            data={"created": name},
        )

    elif action == "delete":
        if not name:
            return _error_result(
                GitCommand.BRANCH, "Branch name required for delete"
            )
        args = ["branch", "-d", name]
        rc, stdout, stderr = await _run_git(args, cwd)
        if rc != 0:
            return _error_result(GitCommand.BRANCH, "git branch delete failed", stderr)
        return GitToolResult(
            success=True,
            command=GitCommand.BRANCH,
            data={"deleted": name},
        )

    else:
        return _error_result(
            GitCommand.BRANCH, f"Unknown branch action: {action}"
        )


# ──────────────────────────────────────────────────
# git checkout
# ──────────────────────────────────────────────────


async def git_checkout(
    cwd: Path, target: str, create: bool = False
) -> GitToolResult:
    """Switch branches or create and switch.

    Returns:
        GitToolResult with data containing:
        - branch: the branch checked out to
        - created: whether a new branch was created
    """
    args = ["checkout"]
    if create:
        args.append("-b")
    args.append(target)

    rc, stdout, stderr = await _run_git(args, cwd)
    if rc != 0:
        return _error_result(GitCommand.CHECKOUT, "git checkout failed", stderr)

    return GitToolResult(
        success=True,
        command=GitCommand.CHECKOUT,
        data={"branch": target, "created": create},
    )


# ──────────────────────────────────────────────────
# git push
# ──────────────────────────────────────────────────


async def git_push(
    cwd: Path,
    remote: str = "origin",
    branch: Optional[str] = None,
    set_upstream: bool = False,
    force: bool = False,
) -> GitToolResult:
    """Push commits to a remote.

    Returns:
        GitToolResult with data containing:
        - remote: the remote pushed to
        - branch: the branch pushed
        - forced: whether force push was used
    """
    args = ["push"]
    if set_upstream:
        args.append("-u")
    if force:
        args.append("--force")
    args.append(remote)
    if branch:
        args.append(branch)

    rc, stdout, stderr = await _run_git(args, cwd, timeout=60.0)
    if rc != 0:
        return _error_result(GitCommand.PUSH, "git push failed", stderr)

    # Determine which branch was pushed
    pushed_branch = branch or ""
    if not pushed_branch:
        rc2, branch_out, _ = await _run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd
        )
        if rc2 == 0:
            pushed_branch = branch_out.strip()

    return GitToolResult(
        success=True,
        command=GitCommand.PUSH,
        data={
            "remote": remote,
            "branch": pushed_branch,
            "forced": force,
        },
    )


# ──────────────────────────────────────────────────
# git pull
# ──────────────────────────────────────────────────


async def git_pull(
    cwd: Path,
    remote: str = "origin",
    branch: Optional[str] = None,
    rebase: bool = False,
) -> GitToolResult:
    """Pull changes from a remote.

    Returns:
        GitToolResult with data containing:
        - remote: the remote pulled from
        - branch: the branch pulled
        - rebased: whether rebase was used
        - summary: human-readable summary of what changed
    """
    args = ["pull"]
    if rebase:
        args.append("--rebase")
    args.append(remote)
    if branch:
        args.append(branch)

    rc, stdout, stderr = await _run_git(args, cwd, timeout=60.0)
    if rc != 0:
        return _error_result(GitCommand.PULL, "git pull failed", stderr)

    return GitToolResult(
        success=True,
        command=GitCommand.PULL,
        data={
            "remote": remote,
            "branch": branch or "",
            "rebased": rebase,
            "summary": stdout,
        },
    )


# ──────────────────────────────────────────────────
# git stash
# ──────────────────────────────────────────────────


async def git_stash(
    cwd: Path,
    action: str = "push",
    message: Optional[str] = None,
    index: int = 0,
) -> GitToolResult:
    """Stash or restore working directory changes.

    Returns:
        GitToolResult with data containing:
        - action: the stash action performed
        - For list: stashes (list of dicts with index and message)
        - For push: message
        - For pop/apply/drop: index
    """
    if action == "push":
        args = ["stash", "push"]
        if message:
            args.extend(["-m", message])
        rc, stdout, stderr = await _run_git(args, cwd)
        if rc != 0:
            return _error_result(GitCommand.STASH, "git stash push failed", stderr)
        return GitToolResult(
            success=True,
            command=GitCommand.STASH,
            data={"action": "push", "message": message or "", "output": stdout},
        )

    elif action == "pop":
        args = ["stash", "pop", f"stash@{{{index}}}"]
        rc, stdout, stderr = await _run_git(args, cwd)
        if rc != 0:
            return _error_result(GitCommand.STASH, "git stash pop failed", stderr)
        return GitToolResult(
            success=True,
            command=GitCommand.STASH,
            data={"action": "pop", "index": index, "output": stdout},
        )

    elif action == "apply":
        args = ["stash", "apply", f"stash@{{{index}}}"]
        rc, stdout, stderr = await _run_git(args, cwd)
        if rc != 0:
            return _error_result(GitCommand.STASH, "git stash apply failed", stderr)
        return GitToolResult(
            success=True,
            command=GitCommand.STASH,
            data={"action": "apply", "index": index, "output": stdout},
        )

    elif action == "drop":
        args = ["stash", "drop", f"stash@{{{index}}}"]
        rc, stdout, stderr = await _run_git(args, cwd)
        if rc != 0:
            return _error_result(GitCommand.STASH, "git stash drop failed", stderr)
        return GitToolResult(
            success=True,
            command=GitCommand.STASH,
            data={"action": "drop", "index": index, "output": stdout},
        )

    elif action == "list":
        args = ["stash", "list"]
        rc, stdout, stderr = await _run_git(args, cwd)
        if rc != 0:
            return _error_result(GitCommand.STASH, "git stash list failed", stderr)

        stashes = []
        for line in stdout.splitlines():
            # Format: stash@{0}: WIP on main: abc1234 commit msg
            if line.strip():
                stashes.append(line.strip())

        return GitToolResult(
            success=True,
            command=GitCommand.STASH,
            data={"action": "list", "stashes": stashes, "count": len(stashes)},
        )

    else:
        return _error_result(
            GitCommand.STASH, f"Unknown stash action: {action}"
        )
```

### 4. `faith/tools/git/server.py`

```python
"""Git MCP tool server — registers git commands as MCP tools.

This server is registered with the FAITH MCP registry at startup. Each
git command becomes a separate MCP tool (e.g. git_status, git_log) with
typed input parameters and structured JSON output.

The server integrates with the FAITH-019 approval engine: before
executing any command, it checks the approval tier and requests
approval if needed.

FRS Reference: Section 4.12
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

from faith.tools.git.approval import (
    ApprovalTier,
    get_approval_tier,
    seed_security_yaml,
)
from faith.tools.git.commands import (
    git_status,
    git_log,
    git_diff,
    git_add,
    git_commit,
    git_branch,
    git_checkout,
    git_push,
    git_pull,
    git_stash,
)
from faith.tools.git.schemas import (
    GitCommand,
    GitToolResult,
    COMMAND_SCHEMAS,
    STATUS_INPUT,
    LOG_INPUT,
    DIFF_INPUT,
    ADD_INPUT,
    COMMIT_INPUT,
    BRANCH_INPUT,
    CHECKOUT_INPUT,
    PUSH_INPUT,
    PULL_INPUT,
    STASH_INPUT,
)

logger = logging.getLogger("faith.tools.git.server")


# Type alias for the approval callback provided by the tool executor
ApprovalCallback = Callable[[str, ApprovalTier], Awaitable[bool]]


class GitMCPServer:
    """MCP tool server for local git operations.

    Attributes:
        workspace: Path to the git working directory.
        faith_dir: Path to the .faith directory.
        approval_callback: Async callback to request user approval.
            Signature: (tool_name: str, tier: ApprovalTier) -> bool
            Provided by the tool executor at runtime. If None, all
            commands require manual approval.
    """

    SERVER_NAME = "git"
    SERVER_VERSION = "1.0.0"

    def __init__(
        self,
        workspace: Path,
        faith_dir: Path,
        approval_callback: ApprovalCallback | None = None,
    ):
        self.workspace = workspace
        self.faith_dir = faith_dir
        self.approval_callback = approval_callback

        # Seed security.yaml with default git rules on first run
        seed_security_yaml(faith_dir)

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions for all git commands.

        Each tool has a name, description, and inputSchema following
        the MCP tool definition format.

        Returns:
            List of MCP tool definition dicts.
        """
        return [
            {
                "name": "git_status",
                "description": (
                    "Get the working tree status of the local git repository. "
                    "Returns branch name, staged files, unstaged changes, "
                    "and untracked files as structured JSON."
                ),
                "inputSchema": STATUS_INPUT,
            },
            {
                "name": "git_log",
                "description": (
                    "Get the commit history of the local git repository. "
                    "Returns a list of commits with hash, author, date, "
                    "and message as structured JSON."
                ),
                "inputSchema": LOG_INPUT,
            },
            {
                "name": "git_diff",
                "description": (
                    "Show changes in the working tree or staging area. "
                    "Returns per-file insertions/deletions and optional "
                    "full patch as structured JSON."
                ),
                "inputSchema": DIFF_INPUT,
            },
            {
                "name": "git_add",
                "description": (
                    "Stage files for the next commit. Accepts a list of "
                    "file paths. Use ['.'] to stage all changes."
                ),
                "inputSchema": ADD_INPUT,
            },
            {
                "name": "git_commit",
                "description": (
                    "Create a new commit with the staged changes. "
                    "Requires a commit message. Optionally amend the "
                    "previous commit."
                ),
                "inputSchema": COMMIT_INPUT,
            },
            {
                "name": "git_branch",
                "description": (
                    "List, create, or delete branches in the local "
                    "repository. Returns branch list with current "
                    "branch indicator."
                ),
                "inputSchema": BRANCH_INPUT,
            },
            {
                "name": "git_checkout",
                "description": (
                    "Switch to an existing branch or create and switch "
                    "to a new branch."
                ),
                "inputSchema": CHECKOUT_INPUT,
            },
            {
                "name": "git_push",
                "description": (
                    "Push local commits to a remote repository. "
                    "Always requires explicit user approval."
                ),
                "inputSchema": PUSH_INPUT,
            },
            {
                "name": "git_pull",
                "description": (
                    "Pull changes from a remote repository into the "
                    "current branch. Supports merge and rebase modes."
                ),
                "inputSchema": PULL_INPUT,
            },
            {
                "name": "git_stash",
                "description": (
                    "Stash working directory changes for later use. "
                    "Supports push, pop, apply, drop, and list operations."
                ),
                "inputSchema": STASH_INPUT,
            },
        ]

    async def _check_approval(self, tool_name: str, command: GitCommand) -> bool:
        """Check whether the command is approved for execution.

        Delegates to the approval callback (backed by FAITH-019). If
        no callback is registered, execution is denied because Git
        actions are ask-first by default in FAITH.

        Args:
            tool_name: The MCP tool name (e.g. "git_status").
            command: The GitCommand enum value.

        Returns:
            True if the command is approved, False otherwise.
        """
        tier = get_approval_tier(command)

        if tier == ApprovalTier.ALWAYS_ALLOW:
            logger.debug(f"{tool_name} matched always_allow — executing")
            return True

        if self.approval_callback is None:
            logger.warning(
                f"{tool_name} requires approval (tier={tier.value}) "
                f"but no approval callback is registered"
            )
            return False

        approved = await self.approval_callback(tool_name, tier)
        if not approved:
            logger.info(f"{tool_name} was denied by user (tier={tier.value})")
        return approved

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a git MCP tool call.

        This is the main entry point called by the FAITH tool executor
        when an agent invokes a git tool.

        Args:
            tool_name: The tool name (e.g. "git_status", "git_commit").
            arguments: The tool arguments matching the input schema.

        Returns:
            Structured JSON result dict from GitToolResult.to_dict().

        Raises:
            ValueError: If the tool name is not recognised.
        """
        dispatch = {
            "git_status": (GitCommand.STATUS, self._do_status),
            "git_log": (GitCommand.LOG, self._do_log),
            "git_diff": (GitCommand.DIFF, self._do_diff),
            "git_add": (GitCommand.ADD, self._do_add),
            "git_commit": (GitCommand.COMMIT, self._do_commit),
            "git_branch": (GitCommand.BRANCH, self._do_branch),
            "git_checkout": (GitCommand.CHECKOUT, self._do_checkout),
            "git_push": (GitCommand.PUSH, self._do_push),
            "git_pull": (GitCommand.PULL, self._do_pull),
            "git_stash": (GitCommand.STASH, self._do_stash),
        }

        if tool_name not in dispatch:
            raise ValueError(f"Unknown git tool: {tool_name}")

        command, handler = dispatch[tool_name]

        # Check approval
        approved = await self._check_approval(tool_name, command)
        if not approved:
            result = GitToolResult(
                success=False,
                command=command,
                error=f"Command '{tool_name}' was not approved by the user.",
            )
            return result.to_dict()

        # Execute
        try:
            result = await handler(arguments)
        except Exception as e:
            logger.error(f"Error executing {tool_name}: {e}", exc_info=True)
            result = GitToolResult(
                success=False,
                command=command,
                error=str(e),
            )

        return result.to_dict()

    # ──────────────────────────────────────────────
    # Handler methods (unpack arguments → call command)
    # ──────────────────────────────────────────────

    async def _do_status(self, args: dict) -> GitToolResult:
        return await git_status(self.workspace, short=args.get("short", False))

    async def _do_log(self, args: dict) -> GitToolResult:
        return await git_log(
            self.workspace,
            max_count=args.get("max_count", 20),
            oneline=args.get("oneline", False),
            branch=args.get("branch"),
            path=args.get("path"),
        )

    async def _do_diff(self, args: dict) -> GitToolResult:
        return await git_diff(
            self.workspace,
            staged=args.get("staged", False),
            path=args.get("path"),
            stat_only=args.get("stat_only", False),
        )

    async def _do_add(self, args: dict) -> GitToolResult:
        return await git_add(self.workspace, paths=args["paths"])

    async def _do_commit(self, args: dict) -> GitToolResult:
        return await git_commit(
            self.workspace,
            message=args["message"],
            amend=args.get("amend", False),
        )

    async def _do_branch(self, args: dict) -> GitToolResult:
        return await git_branch(
            self.workspace,
            action=args.get("action", "list"),
            name=args.get("name"),
        )

    async def _do_checkout(self, args: dict) -> GitToolResult:
        return await git_checkout(
            self.workspace,
            target=args["target"],
            create=args.get("create", False),
        )

    async def _do_push(self, args: dict) -> GitToolResult:
        return await git_push(
            self.workspace,
            remote=args.get("remote", "origin"),
            branch=args.get("branch"),
            set_upstream=args.get("set_upstream", False),
            force=args.get("force", False),
        )

    async def _do_pull(self, args: dict) -> GitToolResult:
        return await git_pull(
            self.workspace,
            remote=args.get("remote", "origin"),
            branch=args.get("branch"),
            rebase=args.get("rebase", False),
        )

    async def _do_stash(self, args: dict) -> GitToolResult:
        return await git_stash(
            self.workspace,
            action=args.get("action", "push"),
            message=args.get("message"),
            index=args.get("index", 0),
        )
```

### 5. `faith/tools/git/__init__.py`

```python
"""FAITH Git MCP Tool — local git operations as structured MCP commands."""

from faith.tools.git.server import GitMCPServer
from faith.tools.git.schemas import GitCommand, GitToolResult
from faith.tools.git.approval import ApprovalTier, get_approval_tier

__all__ = [
    "GitMCPServer",
    "GitCommand",
    "GitToolResult",
    "ApprovalTier",
    "get_approval_tier",
]
```

### 6. `tests/test_git_mcp.py`

```python
"""Tests for the FAITH Git MCP tool server.

Covers all 10 git commands, approval tier checks, security.yaml seeding,
structured output parsing, and error handling. Uses a real temporary git
repository for integration tests.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from faith.tools.git.server import GitMCPServer
from faith.tools.git.schemas import GitCommand, GitToolResult, COMMAND_SCHEMAS
from faith.tools.git.approval import (
    ApprovalTier,
    get_approval_tier,
    build_default_rules,
    seed_security_yaml,
    DEFAULT_TIERS,
)
from faith.tools.git.commands import (
    git_status,
    git_log,
    git_diff,
    git_add,
    git_commit,
    git_branch,
    git_checkout,
    git_push,
    git_pull,
    git_stash,
)


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def tmp_git_repo(tmp_path):
    """Create a temporary git repository with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()

    async def _init():
        # git init
        proc = await asyncio.create_subprocess_exec(
            "git", "init", cwd=str(repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Configure user for commits
        for key, val in [("user.email", "test@faith.dev"), ("user.name", "FAITH Test")]:
            proc = await asyncio.create_subprocess_exec(
                "git", "config", key, val, cwd=str(repo),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

        # Initial commit
        readme = repo / "README.md"
        readme.write_text("# Test Repo\n", encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            "git", "add", ".", cwd=str(repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", "Initial commit", cwd=str(repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    asyncio.get_event_loop().run_until_complete(_init())
    return repo


@pytest.fixture
def tmp_faith_dir(tmp_path):
    """Create a temporary .faith directory."""
    faith_dir = tmp_path / ".faith"
    faith_dir.mkdir()
    return faith_dir


@pytest.fixture
def git_server(tmp_git_repo, tmp_faith_dir):
    """Create a GitMCPServer with approval callback."""
    async def always_approve(tool_name, tier):
        return True

    return GitMCPServer(
        workspace=tmp_git_repo,
        faith_dir=tmp_faith_dir,
        approval_callback=always_approve,
    )


# ──────────────────────────────────────────────────
# Approval tier tests
# ──────────────────────────────────────────────────


def test_git_commands_default_to_always_ask():
    """Git actions default to ask-first until the user creates a remembered rule."""
    for cmd in [GitCommand.STATUS, GitCommand.LOG, GitCommand.DIFF, GitCommand.ADD]:
        assert get_approval_tier(cmd) == ApprovalTier.ALWAYS_ASK
    for cmd in [
        GitCommand.COMMIT, GitCommand.BRANCH, GitCommand.CHECKOUT,
        GitCommand.PULL, GitCommand.STASH,
    ]:
        assert get_approval_tier(cmd) == ApprovalTier.ALWAYS_ASK


def test_push_is_always_ask():
    """push is always_ask."""
    assert get_approval_tier(GitCommand.PUSH) == ApprovalTier.ALWAYS_ASK


def test_all_commands_have_approval_tier():
    """Every GitCommand has a defined default approval tier."""
    for cmd in GitCommand:
        tier = get_approval_tier(cmd)
        assert isinstance(tier, ApprovalTier)


def test_all_commands_have_input_schema():
    """Every GitCommand has a corresponding input schema."""
    for cmd in GitCommand:
        assert cmd in COMMAND_SCHEMAS


# ──────────────────────────────────────────────────
# Security YAML seeding tests
# ──────────────────────────────────────────────────


def test_build_default_rules_has_canonical_tiers():
    """build_default_rules returns only canonical approval tier keys."""
    rules = build_default_rules()
    assert "always_allow" in rules
    assert "approve_session" in rules
    assert "always_ask" in rules


def test_build_default_rules_covers_all_commands():
    """Every command appears in exactly one tier's rule list."""
    rules = build_default_rules()
    all_patterns = (
        rules["always_allow"]
        + rules["approve_session"]
        + rules["always_ask"]
    )
    assert len(all_patterns) == len(GitCommand)


def test_seed_security_yaml_creates_file(tmp_faith_dir):
    """Seeding creates security.yaml with git rules."""
    result = seed_security_yaml(tmp_faith_dir)
    assert result is True

    security_path = tmp_faith_dir / "security.yaml"
    assert security_path.exists()

    config = yaml.safe_load(security_path.read_text(encoding="utf-8"))
    assert "approval_rules" in config
    assert "git_tools" in config["approval_rules"]
    assert "always_allow" in config["approval_rules"]["git_tools"]
    assert "approve_session" in config["approval_rules"]["git_tools"]
    assert "always_ask" in config["approval_rules"]["git_tools"]


def test_seed_security_yaml_idempotent(tmp_faith_dir):
    """Seeding twice does not overwrite existing rules."""
    seed_security_yaml(tmp_faith_dir)
    # Modify the rules
    security_path = tmp_faith_dir / "security.yaml"
    config = yaml.safe_load(security_path.read_text(encoding="utf-8"))
    config["approval_rules"]["git_tools"]["custom_key"] = "custom_value"
    security_path.write_text(
        yaml.dump(config, default_flow_style=False), encoding="utf-8"
    )

    # Seed again — should not overwrite
    result = seed_security_yaml(tmp_faith_dir)
    assert result is False

    config2 = yaml.safe_load(security_path.read_text(encoding="utf-8"))
    assert config2["approval_rules"]["git_tools"]["custom_key"] == "custom_value"


def test_seed_security_yaml_preserves_existing(tmp_faith_dir):
    """Seeding preserves existing non-git rules in security.yaml."""
    security_path = tmp_faith_dir / "security.yaml"
    existing = {
        "approval_rules": {
            "software-developer": {
                "always_allow": ["^pytest.*$"],
            }
        }
    }
    security_path.write_text(
        yaml.dump(existing, default_flow_style=False), encoding="utf-8"
    )

    seed_security_yaml(tmp_faith_dir)

    config = yaml.safe_load(security_path.read_text(encoding="utf-8"))
    assert "software-developer" in config["approval_rules"]
    assert "git_tools" in config["approval_rules"]


# ──────────────────────────────────────────────────
# Git command tests (integration — use real git repo)
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_status_clean(tmp_git_repo):
    """Status on a clean repo returns clean=True."""
    result = await git_status(tmp_git_repo)
    assert result.success is True
    assert result.command == GitCommand.STATUS
    assert result.data["clean"] is True
    assert result.data["branch"] != ""


@pytest.mark.asyncio
async def test_git_status_with_changes(tmp_git_repo):
    """Status detects new untracked files."""
    (tmp_git_repo / "new_file.txt").write_text("hello", encoding="utf-8")
    result = await git_status(tmp_git_repo)
    assert result.success is True
    assert result.data["clean"] is False
    assert len(result.data["untracked"]) >= 1


@pytest.mark.asyncio
async def test_git_log_returns_commits(tmp_git_repo):
    """Log returns the initial commit."""
    result = await git_log(tmp_git_repo)
    assert result.success is True
    assert result.data["count"] >= 1
    commit = result.data["commits"][0]
    assert "hash" in commit
    assert "author" in commit
    assert "date" in commit
    assert "subject" in commit


@pytest.mark.asyncio
async def test_git_diff_no_changes(tmp_git_repo):
    """Diff on a clean repo returns empty file list."""
    result = await git_diff(tmp_git_repo)
    assert result.success is True
    assert result.data["total_files"] == 0


@pytest.mark.asyncio
async def test_git_diff_with_changes(tmp_git_repo):
    """Diff detects modifications to tracked files."""
    readme = tmp_git_repo / "README.md"
    readme.write_text("# Updated\nNew content\n", encoding="utf-8")
    result = await git_diff(tmp_git_repo)
    assert result.success is True
    assert result.data["total_files"] >= 1
    assert any(f["path"] == "README.md" for f in result.data["files"])


@pytest.mark.asyncio
async def test_git_add_stages_files(tmp_git_repo):
    """Add stages a new file."""
    (tmp_git_repo / "staged.txt").write_text("content", encoding="utf-8")
    result = await git_add(tmp_git_repo, paths=["staged.txt"])
    assert result.success is True
    assert "staged.txt" in result.data["staged_paths"]

    # Verify via status
    status = await git_status(tmp_git_repo)
    assert any(
        f["path"] == "staged.txt" for f in status.data["staged"]
    )


@pytest.mark.asyncio
async def test_git_commit_creates_commit(tmp_git_repo):
    """Commit creates a new commit with the given message."""
    (tmp_git_repo / "committed.txt").write_text("data", encoding="utf-8")
    await git_add(tmp_git_repo, paths=["committed.txt"])

    result = await git_commit(tmp_git_repo, message="Add committed.txt")
    assert result.success is True
    assert result.data["hash"] != ""
    assert result.data["message"] == "Add committed.txt"


@pytest.mark.asyncio
async def test_git_commit_empty_message_fails(tmp_git_repo):
    """Commit with an empty message fails."""
    result = await git_commit(tmp_git_repo, message="")
    assert result.success is False
    assert "empty" in result.error.lower()


@pytest.mark.asyncio
async def test_git_branch_list(tmp_git_repo):
    """Branch list returns at least one branch."""
    result = await git_branch(tmp_git_repo, action="list")
    assert result.success is True
    assert len(result.data["branches"]) >= 1


@pytest.mark.asyncio
async def test_git_branch_create_and_delete(tmp_git_repo):
    """Branch create and delete work correctly."""
    create_result = await git_branch(tmp_git_repo, action="create", name="feature-test")
    assert create_result.success is True
    assert create_result.data["created"] == "feature-test"

    delete_result = await git_branch(tmp_git_repo, action="delete", name="feature-test")
    assert delete_result.success is True
    assert delete_result.data["deleted"] == "feature-test"


@pytest.mark.asyncio
async def test_git_checkout_switch_branch(tmp_git_repo):
    """Checkout switches to an existing branch."""
    await git_branch(tmp_git_repo, action="create", name="dev")
    result = await git_checkout(tmp_git_repo, target="dev")
    assert result.success is True
    assert result.data["branch"] == "dev"

    # Verify via status
    status = await git_status(tmp_git_repo)
    assert status.data["branch"] == "dev"


@pytest.mark.asyncio
async def test_git_checkout_create_branch(tmp_git_repo):
    """Checkout -b creates and switches to a new branch."""
    result = await git_checkout(tmp_git_repo, target="new-feature", create=True)
    assert result.success is True
    assert result.data["created"] is True
    assert result.data["branch"] == "new-feature"


@pytest.mark.asyncio
async def test_git_stash_push_and_pop(tmp_git_repo):
    """Stash push and pop round-trip preserves changes."""
    (tmp_git_repo / "README.md").write_text("modified\n", encoding="utf-8")

    push_result = await git_stash(tmp_git_repo, action="push", message="test stash")
    assert push_result.success is True

    # Working tree should be clean after stash
    status = await git_status(tmp_git_repo)
    assert status.data["clean"] is True

    pop_result = await git_stash(tmp_git_repo, action="pop")
    assert pop_result.success is True

    # Changes should be restored
    status2 = await git_status(tmp_git_repo)
    assert status2.data["clean"] is False


@pytest.mark.asyncio
async def test_git_stash_list(tmp_git_repo):
    """Stash list returns stash entries."""
    (tmp_git_repo / "README.md").write_text("stash me\n", encoding="utf-8")
    await git_stash(tmp_git_repo, action="push", message="my stash")

    result = await git_stash(tmp_git_repo, action="list")
    assert result.success is True
    assert result.data["count"] >= 1


# ──────────────────────────────────────────────────
# Server-level tests
# ──────────────────────────────────────────────────


def test_server_tool_definitions(git_server):
    """Server provides 10 tool definitions."""
    tools = git_server.get_tool_definitions()
    assert len(tools) == 10
    names = {t["name"] for t in tools}
    expected = {
        "git_status", "git_log", "git_diff", "git_add", "git_commit",
        "git_branch", "git_checkout", "git_push", "git_pull", "git_stash",
    }
    assert names == expected


def test_server_tool_definitions_have_schemas(git_server):
    """Every tool definition includes an inputSchema."""
    for tool in git_server.get_tool_definitions():
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"


@pytest.mark.asyncio
async def test_server_call_tool_status(git_server):
    """Server dispatches git_status correctly."""
    result = await git_server.call_tool("git_status", {})
    assert result["success"] is True
    assert result["command"] == "status"


@pytest.mark.asyncio
async def test_server_call_unknown_tool(git_server):
    """Calling an unknown tool raises ValueError."""
    with pytest.raises(ValueError, match="Unknown git tool"):
        await git_server.call_tool("git_rebase", {})


@pytest.mark.asyncio
async def test_server_denied_command_returns_error(tmp_git_repo, tmp_faith_dir):
    """Denied commands return a structured error result."""
    async def always_deny(tool_name, tier):
        return False

    server = GitMCPServer(
        workspace=tmp_git_repo,
        faith_dir=tmp_faith_dir,
        approval_callback=always_deny,
    )

    # push is always_ask, so it goes through the callback
    result = await server.call_tool("git_push", {})
    assert result["success"] is False
    assert "not approved" in result["error"]


@pytest.mark.asyncio
async def test_server_ask_first_uses_callback(tmp_git_repo, tmp_faith_dir):
    """Ask-first git commands call the approval callback before execution."""
    callback = AsyncMock(return_value=True)
    server = GitMCPServer(
        workspace=tmp_git_repo,
        faith_dir=tmp_faith_dir,
        approval_callback=callback,
    )

    result = await server.call_tool("git_status", {})
    assert result["success"] is True
    callback.assert_awaited_once()


# ──────────────────────────────────────────────────
# GitToolResult serialisation tests
# ──────────────────────────────────────────────────


def test_tool_result_to_dict_success():
    """Successful result serialises without error/stderr fields."""
    result = GitToolResult(
        success=True,
        command=GitCommand.STATUS,
        data={"branch": "main", "clean": True},
    )
    d = result.to_dict()
    assert d["success"] is True
    assert d["command"] == "status"
    assert d["data"]["branch"] == "main"
    assert "error" not in d
    assert "raw_stderr" not in d


def test_tool_result_to_dict_failure():
    """Failed result includes error and raw_stderr."""
    result = GitToolResult(
        success=False,
        command=GitCommand.PUSH,
        error="git push failed",
        raw_stderr="fatal: remote rejected",
    )
    d = result.to_dict()
    assert d["success"] is False
    assert d["error"] == "git push failed"
    assert d["raw_stderr"] == "fatal: remote rejected"


def test_tool_result_json_serialisable():
    """GitToolResult.to_dict() output is JSON-serialisable."""
    result = GitToolResult(
        success=True,
        command=GitCommand.LOG,
        data={"commits": [{"hash": "abc123", "subject": "Test"}]},
    )
    serialised = json.dumps(result.to_dict())
    parsed = json.loads(serialised)
    assert parsed["data"]["commits"][0]["hash"] == "abc123"
```

---

## Integration Points

The Git MCP server integrates with the FAITH framework at several points:

```python
# Tool executor registers the Git MCP server at startup (FAITH-022 pattern)
from faith.tools.git import GitMCPServer

git_server = GitMCPServer(
    workspace=session.workspace_path,
    faith_dir=session.faith_dir,
    approval_callback=approval_engine.check,  # FAITH-019
)

# Agent invokes a git tool via the standard MCP tool call protocol
tool_result = await git_server.call_tool("git_status", {})
# Returns: {"success": true, "command": "status", "data": {"branch": "main", ...}}

# Approval flow for write commands
tool_result = await git_server.call_tool("git_commit", {"message": "Add auth module"})
# FAITH-019 approval engine evaluates the action against remembered rules.
# By default git actions use ask-first fallback.
# If the user approves for the session: later matching calls execute immediately

# Push always prompts regardless of session history
tool_result = await git_server.call_tool("git_push", {"remote": "origin"})
# ^git_push$ → always_ask tier → user prompt every time
```

```yaml
# .faith/security.yaml (auto-generated on first run)
approval_rules:
  git_tools:
    _comment: >
      Default approval rules for the Git MCP tool.
      Git actions are ask-first by default; add remembered rules here only
      when the user explicitly chooses to persist an allow decision.
      See FRS Section 4.12 and 5.1.
    always_allow: []
    approve_session: []
    always_ask:
      - "^git_status$"
      - "^git_log$"
      - "^git_diff$"
      - "^git_add$"
      - "^git_commit$"
      - "^git_branch$"
      - "^git_checkout$"
      - "^git_pull$"
      - "^git_stash$"
      - "^git_push$"
```

---

## Acceptance Criteria

1. FAITH can register and use an approved external local git MCP server through the FAITH-035 external MCP flow.
2. The selected external server operates against the local repository on disk, not just a remote hosting API.
3. FAITH maps Git operations to the ask-first approval model: `push` is always `always_ask`; other operations are ask-first unless covered by remembered rules.
4. Git tool usage is audited through FAITH's standard audit pipeline with structured action names and outcomes.
5. External git-server installation/update remains version-pinned and user-confirmed per the external MCP policy.
6. If FAITH later falls back to an in-house implementation, it exposes structured git commands and preserves the same approval and audit semantics.
11. All 30 tests in `tests/test_git_mcp.py` pass, covering approval tiers, security.yaml seeding, all 10 commands, server dispatch, error handling, and result serialisation.

---

## Notes for Implementer

- **Subprocess, not library**: Git commands are executed via `asyncio.create_subprocess_exec("git", ...)`, not via a Python git library like `gitpython`. This keeps the dependency list small and matches the exact behaviour users expect from their local git installation.
- **GIT_TERMINAL_PROMPT=0**: This environment variable prevents git from opening interactive credential prompts. If authentication is needed, the command fails with a clear error rather than hanging. Agents should instruct the user to configure SSH keys or credential helpers.
- **Porcelain v2 for status**: `git status --porcelain=v2 --branch` produces a machine-parseable format that is stable across git versions. Do not parse the human-readable `git status` output.
- **Commit separator in log**: The `---FAITH_COMMIT_SEP---` separator in the log format string is used to reliably split commits. Using `%x00` (null byte) would also work but complicates Python string handling.
- **Force push escalation**: Even though `push` is already `always_ask`, `--force` is an additional signal the approval UI can use to show a stronger warning. The schema exposes it as a boolean so the approval engine can match on it.
- **No `git init`**: The Git MCP server does not expose `git init`. Repository initialisation is a one-time setup operation that should be done by the user or by the FAITH first-run wizard (FAITH-049).
- **Workspace path**: The `workspace` path passed to `GitMCPServer` is the project root that contains the `.git` directory. This is the same path mounted into agent containers as the workspace volume.
- **FAITH-019 dependency**: The approval callback interface (`async (tool_name, tier) -> bool`) is the contract provided by the FAITH-019 security engine. This task defines the callback signature but does not implement the engine itself.


