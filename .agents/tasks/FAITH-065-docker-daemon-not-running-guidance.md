# FAITH-065 — Docker Daemon Not Running Guidance

**Phase:** 10 — First-Run Wizard & Setup
**Complexity:** S
**Model:** Haiku / GPT-5.4-mini
**Status:** TODO
**Dependencies:** FAITH-005
**FRS Reference:** Section 9.2.1, 9.2.2

---

## Objective

Improve the CLI startup UX so `faith init` and related bootstrap checks distinguish between Docker not being installed and Docker being installed but not currently running. When Docker is present but unavailable, the CLI must detect the host OS and show the user the most helpful next step instead of failing with a generic daemon error.

---

## Required Scope

1. Detect the difference between:
- Docker CLI missing
- Docker Compose missing
- Docker installed but daemon/Desktop not running
2. When the daemon/Desktop is not running, detect the current OS and print targeted guidance:
- Windows: tell the user to start Docker Desktop, then rerun `faith init`
- macOS: tell the user to open/start Docker Desktop, then rerun `faith init`
- Linux: print the expected `systemctl` start command for the Docker service and note that distro-specific alternatives may apply, then rerun `faith init`
3. Keep v1 behaviour manual:
- do not attempt to auto-start Docker
- do not attempt privilege elevation
- do not hide the failure cause
4. Reuse the same guidance path anywhere the CLI blocks on an unavailable Docker daemon during bootstrap/startup checks.

---

## Files to Create or Update

- `src/faith_cli/checks.py`
- `src/faith_cli/cli.py`
- `tests/test_faith_cli.py`

---

## Testing Requirements

Minimum coverage:
- Docker missing path
- Docker Compose missing path
- Docker installed but daemon not running on Windows
- Docker installed but daemon not running on macOS
- Docker installed but daemon not running on Linux
- user-facing guidance includes rerun instructions for `faith init`

---

## Acceptance Criteria

1. The CLI clearly distinguishes missing Docker from a non-running Docker daemon/Desktop.
2. OS-specific remediation guidance is printed for Windows, macOS, and Linux.
3. The CLI tells the user to rerun `faith init` after starting Docker.
4. The implementation does not try to auto-start Docker or perform elevated actions in v1.
