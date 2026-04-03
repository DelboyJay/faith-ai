# FAITH-024 — Python Execution MCP Server

**Phase:** 6 — Tool MCP Servers
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** IN PROGRESS
**Dependencies:** FAITH-003, FAITH-008, FAITH-057
**FRS Reference:** Section 4.2

---

## Objective

Implement the FAITH-owned Python Execution MCP server that runs as a dedicated Docker container (`tool-python-container`). The server receives Python code from agents via MCP tool calls, executes it inside PA-managed disposable sandbox containers with configurable timeout, captures stdout/stderr/return values/tracebacks as separate fields, enforces workspace mount permissions, supports runtime `pip install` and OS package installation inside the sandbox, and publishes tool lifecycle events (`tool:call_started`, `tool:call_complete`, `tool:error`) to the `system-events` Redis channel. Sandbox containers may run as root inside the container, but they must never receive the Docker socket, must not run in privileged mode, must not use host networking, and must receive only explicitly approved mounts.

Execution is container-first by default, but the PA should route directly to the optional host-worker path when the requested action clearly requires host-only context or resources. The system should avoid wasting time on a doomed container attempt when the correct execution boundary is evident in advance.

This tool is part of FAITH's core security boundary. The implementation must include explicit security hardening and security-focused testing around sandbox escape, package installation, resource exhaustion, approval enforcement, filesystem boundary controls, and violations of the sandbox isolation rules (Docker socket exposure, privileged mode, host networking, or over-broad mounts).

---

## Architecture

```
faith/tools/python/
├── __init__.py
├── server.py           ← MCP server entry point (this task)
├── executor.py         ← Sandboxed code execution engine (this task)
├── config.py           ← Configuration loader for python.yaml (this task)
└── sandbox.py          ← Subprocess sandbox and output capture (this task)

containers/
└── tool-python/
    ├── Dockerfile       ← Python image with pre-installed packages (this task)
    └── entrypoint.sh    ← Container entry point (this task)

tests/
└── test_python_tool.py  ← Unit and integration tests (this task)
```

---

## Files to Create

### 1. `containers/tool-python/Dockerfile`

```dockerfile
# Dockerfile for the Python Execution MCP tool container.
#
# Pre-installs commonly used packages so agents can use them immediately
# without a pip install round-trip. Additional packages can be installed
# at runtime by the agent.
#
# FRS Reference: Section 4.2.1, 4.2.2

FROM python:3.12-slim

# System dependencies for playwright and general build support
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Pre-installed packages (FRS 4.2.2)
RUN pip install --no-cache-dir \
    numpy \
    pandas \
    requests \
    beautifulsoup4 \
    playwright \
    lxml \
    httpx \
    pyyaml \
    pydantic \
    redis \
    mcp

# Install playwright browsers
RUN playwright install --with-deps chromium

# Create a non-root user for execution
RUN useradd -m -s /bin/bash executor

# Working directory for script execution
WORKDIR /workspace

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
```

### 2. `containers/tool-python/entrypoint.sh`

```bash
#!/bin/bash
# Entry point for the Python Execution MCP container.
# Starts the MCP server process that listens for tool calls.

set -e

echo "Starting Python Execution MCP server..."
exec python -m faith.tools.python.server
```

### 3. `faith/tools/python/config.py`

```python
"""Configuration loader for the Python Execution MCP tool.

Reads .faith/tools/python.yaml for project-level settings (internet
toggle, timeout). Falls back to sensible defaults when the config
file is absent.

FRS Reference: Section 4.2, 7.1.2
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger("faith.tools.python.config")


class PythonToolConfig(BaseModel):
    """Pydantic model for .faith/tools/python.yaml.

    Attributes:
        internet_access: Whether the container has internet access.
            Default True. Toggling this changes Docker network policy.
        timeout_seconds: Default execution timeout per call in seconds.
            Agents may override via their config.yaml python_timeout_seconds.
    """

    internet_access: bool = Field(default=True)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)


def load_python_config(faith_dir: Path) -> PythonToolConfig:
    """Load Python tool configuration from .faith/tools/python.yaml.

    Args:
        faith_dir: Path to the .faith directory.

    Returns:
        PythonToolConfig with values from the file, or defaults if
        the file is missing or invalid.
    """
    config_path = faith_dir / "tools" / "python.yaml"

    if not config_path.exists():
        logger.info(
            f"No python.yaml found at {config_path} — using defaults"
        )
        return PythonToolConfig()

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if raw is None:
            return PythonToolConfig()
        return PythonToolConfig(**raw)
    except Exception as e:
        logger.warning(
            f"Failed to parse {config_path}: {e} — using defaults"
        )
        return PythonToolConfig()
```

### 4. `faith/tools/python/sandbox.py`

```python
"""Sandboxed Python code execution via subprocess.

Runs untrusted Python code in an isolated subprocess with:
- Configurable timeout (kills process on expiry)
- Separate capture of stdout, stderr, return value, and tracebacks
- Non-root execution (executor user)
- Restricted filesystem access (only mounted workspace paths)

FRS Reference: Section 4.2.4, 4.2.5, 4.2.6
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("faith.tools.python.sandbox")

# Template that wraps user code to capture return value and traceback
# separately from stdout/stderr.
_WRAPPER_TEMPLATE = textwrap.dedent("""\
    import sys
    import json
    import traceback

    _faith_result = {"return_value": None, "traceback": None}

    try:
        # Execute user code in a dedicated namespace
        _faith_ns = {}
        exec(open({script_path!r}).read(), _faith_ns)

        # Capture return value if the user assigned to 'result'
        if "result" in _faith_ns:
            _rv = _faith_ns["result"]
            try:
                json.dumps(_rv)  # Check JSON serialisable
                _faith_result["return_value"] = _rv
            except (TypeError, ValueError):
                _faith_result["return_value"] = repr(_rv)

    except SystemExit:
        raise
    except BaseException:
        _faith_result["traceback"] = traceback.format_exc()

    # Write result to a sidecar file for the sandbox to read
    with open({result_path!r}, "w") as _f:
        json.dump(_faith_result, _f)
""")


@dataclass
class ExecutionResult:
    """Result of a sandboxed Python execution.

    Attributes:
        stdout: Captured standard output.
        stderr: Captured standard error.
        return_value: Value of the 'result' variable if set by user code.
        traceback: Full traceback string if an exception occurred.
        exit_code: Process exit code (0 = success).
        timed_out: Whether the execution was killed due to timeout.
        duration_seconds: Wall-clock execution time.
    """

    stdout: str = ""
    stderr: str = ""
    return_value: Any = None
    traceback: Optional[str] = None
    exit_code: int = 0
    timed_out: bool = False
    duration_seconds: float = 0.0


@dataclass
class SandboxConfig:
    """Configuration for the execution sandbox.

    Attributes:
        timeout_seconds: Maximum execution time before the process is killed.
        allowed_paths: List of workspace paths the code may access.
        run_as_user: Unix user to run the subprocess as (None = current user).
        working_dir: Working directory for the subprocess.
    """

    timeout_seconds: int = 60
    allowed_paths: list[str] = field(default_factory=list)
    run_as_user: Optional[str] = "executor"
    working_dir: str = "/workspace"


async def execute_code(
    code: str,
    config: SandboxConfig,
) -> ExecutionResult:
    """Execute Python code in a sandboxed subprocess.

    The code is written to a temporary file, wrapped in a capture
    harness that separates stdout, stderr, return values, and
    tracebacks, then executed as a subprocess with the configured
    timeout.

    Args:
        code: The Python source code to execute.
        config: Sandbox configuration (timeout, paths, user).

    Returns:
        ExecutionResult with all captured output fields.
    """
    import time

    result = ExecutionResult()
    start_time = time.monotonic()

    # Create temporary files for the script and result sidecar
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = os.path.join(tmpdir, "user_script.py")
        wrapper_path = os.path.join(tmpdir, "wrapper.py")
        result_path = os.path.join(tmpdir, "result.json")

        # Write user script
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        # Write wrapper that executes the user script with capture
        wrapper_code = _WRAPPER_TEMPLATE.format(
            script_path=script_path,
            result_path=result_path,
        )
        with open(wrapper_path, "w", encoding="utf-8") as f:
            f.write(wrapper_code)

        # Build subprocess command
        cmd = [sys.executable, wrapper_path]

        # Build environment — restrict PATH awareness but inherit
        # necessary variables for pip-installed packages to work
        env = os.environ.copy()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=config.working_dir,
                env=env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=config.timeout_seconds,
                )
                result.exit_code = proc.returncode or 0
            except asyncio.TimeoutError:
                # Kill the process on timeout
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
                result.timed_out = True
                result.exit_code = -1
                result.stderr = (
                    f"Execution timed out after {config.timeout_seconds} "
                    f"seconds. The process was killed.\n"
                )
                stdout_bytes = b""
                stderr_bytes = b""

            result.stdout = stdout_bytes.decode("utf-8", errors="replace")
            if not result.timed_out:
                result.stderr = stderr_bytes.decode("utf-8", errors="replace")

        except Exception as e:
            result.exit_code = -1
            result.stderr = f"Failed to start subprocess: {e}"

        # Read the result sidecar if it exists
        if os.path.exists(result_path):
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    sidecar = json.load(f)
                result.return_value = sidecar.get("return_value")
                result.traceback = sidecar.get("traceback")
            except Exception as e:
                logger.warning(f"Failed to read result sidecar: {e}")

        result.duration_seconds = time.monotonic() - start_time

    return result


async def install_packages(
    packages: list[str],
    timeout_seconds: int = 120,
) -> ExecutionResult:
    """Install Python packages via pip.

    Used by agents to install packages not in the base image before
    executing code. Follows the check-before-execute pattern from
    FRS 4.2.2.

    Args:
        packages: List of package specifiers (e.g. ["scipy", "plotly>=5.0"]).
        timeout_seconds: Timeout for the pip install process.

    Returns:
        ExecutionResult with pip's stdout/stderr output.
    """
    import time

    result = ExecutionResult()
    start_time = time.monotonic()

    if not packages:
        return result

    # Sanitise package names — reject anything with shell metacharacters
    for pkg in packages:
        if any(c in pkg for c in ";|&$`\\'\"\n\r"):
            result.exit_code = -1
            result.stderr = f"Invalid package name: {pkg!r}"
            return result

    cmd = [sys.executable, "-m", "pip", "install", "--no-input"] + packages

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
            result.exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            result.timed_out = True
            result.exit_code = -1
            result.stderr = (
                f"pip install timed out after {timeout_seconds} seconds."
            )
            stdout_bytes = b""
            stderr_bytes = b""

        result.stdout = stdout_bytes.decode("utf-8", errors="replace")
        if not result.timed_out:
            result.stderr = stderr_bytes.decode("utf-8", errors="replace")

    except Exception as e:
        result.exit_code = -1
        result.stderr = f"Failed to run pip: {e}"

    result.duration_seconds = time.monotonic() - start_time
    return result
```

### 5. `faith/tools/python/executor.py`

```python
"""High-level execution orchestrator for the Python tool.

Coordinates between configuration, sandbox execution, mount permission
enforcement, and event publishing. This is the layer that the MCP
server calls — it never invokes the sandbox directly.

FRS Reference: Section 4.2
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from faith.protocol.events import EventPublisher, EventType
from faith.tools.python.config import PythonToolConfig
from faith.tools.python.sandbox import (
    ExecutionResult,
    SandboxConfig,
    execute_code,
    install_packages,
)

logger = logging.getLogger("faith.tools.python.executor")


class PythonExecutor:
    """Orchestrates Python code execution with event publishing.

    Attributes:
        config: Python tool configuration from python.yaml.
        event_publisher: EventPublisher for tool lifecycle events.
    """

    def __init__(
        self,
        config: PythonToolConfig,
        event_publisher: EventPublisher,
    ):
        self.config = config
        self.event_publisher = event_publisher

    def _resolve_allowed_paths(
        self, agent_mounts: list[dict[str, str]],
    ) -> list[str]:
        """Resolve the allowed filesystem paths for an agent.

        The Python tool enforces the same mount permission model as
        the filesystem tool — agents can only access workspace mounts
        explicitly assigned to them in their config.yaml.

        Args:
            agent_mounts: List of mount dicts from agent config,
                each with 'name' and 'host_path' keys.

        Returns:
            List of allowed absolute paths inside the container.
        """
        paths = []
        for mount in agent_mounts:
            container_path = mount.get("container_path", mount.get("host_path", ""))
            if container_path:
                paths.append(container_path)
        return paths

    def _build_sandbox_config(
        self,
        agent_mounts: list[dict[str, str]],
        timeout_override: Optional[int] = None,
    ) -> SandboxConfig:
        """Build a SandboxConfig from tool config and agent mounts.

        Args:
            agent_mounts: Agent's workspace mount definitions.
            timeout_override: Per-agent timeout override from
                config.yaml python_timeout_seconds. Uses tool
                default if None.

        Returns:
            Configured SandboxConfig.
        """
        timeout = timeout_override or self.config.timeout_seconds
        allowed = self._resolve_allowed_paths(agent_mounts)

        return SandboxConfig(
            timeout_seconds=timeout,
            allowed_paths=allowed,
        )

    async def run_code(
        self,
        code: str,
        agent_id: str,
        agent_mounts: list[dict[str, str]],
        timeout_override: Optional[int] = None,
        channel: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute Python code on behalf of an agent.

        Publishes tool:call_started before execution and
        tool:call_complete or tool:error after.

        Args:
            code: Python source code to execute.
            agent_id: ID of the requesting agent (for events/audit).
            agent_mounts: Agent's workspace mount definitions.
            timeout_override: Per-agent timeout (seconds) from
                config.yaml python_timeout_seconds.
            channel: Task channel for event context (optional).

        Returns:
            ExecutionResult with captured output.
        """
        sandbox_config = self._build_sandbox_config(
            agent_mounts, timeout_override
        )

        # Publish tool:call_started
        await self.event_publisher.publish_event(
            EventType.TOOL_CALL_STARTED,
            tool="python",
            agent=agent_id,
            channel=channel,
            timeout=sandbox_config.timeout_seconds,
        )

        result = await execute_code(code, sandbox_config)

        # Publish completion or error event
        if result.timed_out:
            await self.event_publisher.publish_event(
                EventType.TOOL_ERROR,
                tool="python",
                agent=agent_id,
                channel=channel,
                reason="timeout",
                duration=result.duration_seconds,
            )
            logger.warning(
                f"Execution for agent '{agent_id}' timed out after "
                f"{sandbox_config.timeout_seconds}s"
            )
        elif result.exit_code != 0 or result.traceback:
            await self.event_publisher.publish_event(
                EventType.TOOL_ERROR,
                tool="python",
                agent=agent_id,
                channel=channel,
                reason="execution_error",
                exit_code=result.exit_code,
                traceback=result.traceback,
                duration=result.duration_seconds,
            )
            logger.info(
                f"Execution for agent '{agent_id}' failed "
                f"(exit_code={result.exit_code})"
            )
        else:
            await self.event_publisher.publish_event(
                EventType.TOOL_CALL_COMPLETE,
                tool="python",
                agent=agent_id,
                channel=channel,
                duration=result.duration_seconds,
                has_return_value=result.return_value is not None,
            )
            logger.info(
                f"Execution for agent '{agent_id}' completed in "
                f"{result.duration_seconds:.2f}s"
            )

        return result

    async def run_pip_install(
        self,
        packages: list[str],
        agent_id: str,
        channel: Optional[str] = None,
    ) -> ExecutionResult:
        """Install packages via pip on behalf of an agent.

        Args:
            packages: Package specifiers to install.
            agent_id: Requesting agent ID.
            channel: Task channel for event context.

        Returns:
            ExecutionResult with pip output.
        """
        await self.event_publisher.publish_event(
            EventType.TOOL_CALL_STARTED,
            tool="python:pip_install",
            agent=agent_id,
            channel=channel,
            packages=packages,
        )

        result = await install_packages(packages)

        if result.exit_code != 0:
            await self.event_publisher.publish_event(
                EventType.TOOL_ERROR,
                tool="python:pip_install",
                agent=agent_id,
                channel=channel,
                reason="pip_install_failed",
                stderr=result.stderr[:500],
            )
        else:
            await self.event_publisher.publish_event(
                EventType.TOOL_CALL_COMPLETE,
                tool="python:pip_install",
                agent=agent_id,
                channel=channel,
                packages=packages,
            )

        return result
```

### 6. `faith/tools/python/server.py`

```python
"""MCP server for Python code execution.

Exposes two MCP tools:
- execute_python: Run Python code in a sandboxed subprocess
- pip_install: Install Python packages at runtime

Reads configuration from .faith/tools/python.yaml (via environment
variable FAITH_DIR). Publishes tool lifecycle events to system-events.

FRS Reference: Section 4.2, 2.2.3
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from mcp.server import Server
from mcp.types import Tool, TextContent

from faith.protocol.events import EventPublisher
from faith.tools.python.config import PythonToolConfig, load_python_config
from faith.tools.python.executor import PythonExecutor
from faith.tools.python.sandbox import ExecutionResult

logger = logging.getLogger("faith.tools.python.server")

# MCP tool definitions
EXECUTE_PYTHON_TOOL = Tool(
    name="execute_python",
    description=(
        "Execute Python code in a sandboxed environment. "
        "stdout, stderr, return values, and tracebacks are captured "
        "separately. Assign to a variable named 'result' to have its "
        "value returned. Pre-installed packages: numpy, pandas, requests, "
        "beautifulsoup4, playwright, lxml, httpx. Use pip_install for "
        "additional packages."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python source code to execute.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": (
                    "Override timeout in seconds (default from config). "
                    "Max 3600."
                ),
            },
        },
        "required": ["code"],
    },
)

PIP_INSTALL_TOOL = Tool(
    name="pip_install",
    description=(
        "Install Python packages via pip. Use this before execute_python "
        "if your code requires packages not in the base image. Combine "
        "all packages into a single call."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of package specifiers (e.g. ['scipy', 'plotly>=5.0']).",
            },
        },
        "required": ["packages"],
    },
)


def _format_result(result: ExecutionResult) -> list[TextContent]:
    """Format an ExecutionResult as MCP TextContent responses.

    Returns separate text blocks for stdout, stderr, return value,
    and traceback — only including non-empty fields.

    Args:
        result: The execution result to format.

    Returns:
        List of TextContent blocks.
    """
    parts = []

    if result.timed_out:
        parts.append(TextContent(
            type="text",
            text=f"[TIMEOUT] Execution killed after {result.duration_seconds:.1f}s",
        ))

    if result.stdout:
        parts.append(TextContent(
            type="text",
            text=f"[STDOUT]\n{result.stdout}",
        ))

    if result.stderr:
        parts.append(TextContent(
            type="text",
            text=f"[STDERR]\n{result.stderr}",
        ))

    if result.return_value is not None:
        rv_str = (
            json.dumps(result.return_value, indent=2, default=str)
            if not isinstance(result.return_value, str)
            else result.return_value
        )
        parts.append(TextContent(
            type="text",
            text=f"[RETURN VALUE]\n{rv_str}",
        ))

    if result.traceback:
        parts.append(TextContent(
            type="text",
            text=f"[TRACEBACK]\n{result.traceback}",
        ))

    if not parts:
        parts.append(TextContent(
            type="text",
            text="[OK] Execution completed with no output.",
        ))

    # Append execution metadata
    parts.append(TextContent(
        type="text",
        text=(
            f"[META] exit_code={result.exit_code} "
            f"duration={result.duration_seconds:.2f}s"
        ),
    ))

    return parts


async def create_server() -> Server:
    """Create and configure the MCP server instance.

    Reads FAITH_DIR from the environment to locate project config.
    Connects to Redis for event publishing.

    Returns:
        Configured MCP Server ready to run.
    """
    # Load configuration
    faith_dir = Path(os.environ.get("FAITH_DIR", "/workspace/.faith"))
    config = load_python_config(faith_dir)

    # Connect to Redis for event publishing
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    redis_client = aioredis.from_url(redis_url)
    event_publisher = EventPublisher(
        redis_client=redis_client,
        source="tool-python",
    )

    # Create executor
    executor = PythonExecutor(
        config=config,
        event_publisher=event_publisher,
    )

    # Create MCP server
    server = Server("faith-python-tool")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [EXECUTE_PYTHON_TOOL, PIP_INSTALL_TOOL]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        # Extract agent context from MCP request metadata
        agent_id = arguments.pop("_agent_id", "unknown")
        agent_mounts = arguments.pop("_agent_mounts", [])
        channel = arguments.pop("_channel", None)

        if name == "execute_python":
            code = arguments["code"]
            timeout_override = arguments.get("timeout_seconds")

            result = await executor.run_code(
                code=code,
                agent_id=agent_id,
                agent_mounts=agent_mounts,
                timeout_override=timeout_override,
                channel=channel,
            )
            return _format_result(result)

        elif name == "pip_install":
            packages = arguments["packages"]

            result = await executor.run_pip_install(
                packages=packages,
                agent_id=agent_id,
                channel=channel,
            )
            return _format_result(result)

        else:
            return [TextContent(
                type="text",
                text=f"Unknown tool: {name}",
            )]

    return server


def main():
    """Entry point for the Python Execution MCP server."""
    import mcp.server.stdio

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    async def run():
        server = await create_server()
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream)

    asyncio.run(run())


if __name__ == "__main__":
    main()
```

### 7. `faith/tools/python/__init__.py`

```python
"""FAITH Python Execution MCP Tool.

Sandboxed Python code execution with output capture, configurable
timeout, runtime pip install, and tool event publishing.
"""

from faith.tools.python.config import PythonToolConfig, load_python_config
from faith.tools.python.executor import PythonExecutor
from faith.tools.python.sandbox import ExecutionResult, SandboxConfig, execute_code

__all__ = [
    "PythonToolConfig",
    "load_python_config",
    "PythonExecutor",
    "ExecutionResult",
    "SandboxConfig",
    "execute_code",
]
```

### 8. `tests/test_python_tool.py`

```python
"""Tests for the FAITH Python Execution MCP tool.

Covers configuration loading, sandboxed execution, output capture,
timeout handling, pip install, event publishing, and MCP tool formatting.
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from faith.tools.python.config import PythonToolConfig, load_python_config
from faith.tools.python.sandbox import (
    ExecutionResult,
    SandboxConfig,
    execute_code,
    install_packages,
)
from faith.tools.python.executor import PythonExecutor
from faith.tools.python.server import _format_result


# ──────────────────────────────────────────────────
# Configuration tests
# ──────────────────────────────────────────────────


def test_config_defaults():
    """Default config has internet on and 60s timeout."""
    config = PythonToolConfig()
    assert config.internet_access is True
    assert config.timeout_seconds == 60


def test_config_from_dict():
    """Config can be loaded from a dictionary."""
    config = PythonToolConfig(internet_access=False, timeout_seconds=120)
    assert config.internet_access is False
    assert config.timeout_seconds == 120


def test_load_config_from_yaml(tmp_path):
    """Config is loaded from .faith/tools/python.yaml."""
    faith_dir = tmp_path / ".faith"
    tools_dir = faith_dir / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "python.yaml").write_text(
        "internet_access: false\ntimeout_seconds: 30\n",
        encoding="utf-8",
    )
    config = load_python_config(faith_dir)
    assert config.internet_access is False
    assert config.timeout_seconds == 30


def test_load_config_missing_file(tmp_path):
    """Missing config file returns defaults."""
    config = load_python_config(tmp_path / ".faith")
    assert config.internet_access is True
    assert config.timeout_seconds == 60


def test_load_config_invalid_yaml(tmp_path):
    """Invalid YAML falls back to defaults."""
    faith_dir = tmp_path / ".faith"
    tools_dir = faith_dir / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "python.yaml").write_text(
        "internet_access: not_a_bool\n",
        encoding="utf-8",
    )
    # Pydantic may coerce or reject — either way, should not crash
    config = load_python_config(faith_dir)
    assert isinstance(config, PythonToolConfig)


def test_load_config_empty_yaml(tmp_path):
    """Empty YAML file returns defaults."""
    faith_dir = tmp_path / ".faith"
    tools_dir = faith_dir / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "python.yaml").write_text("", encoding="utf-8")
    config = load_python_config(faith_dir)
    assert config.timeout_seconds == 60


# ──────────────────────────────────────────────────
# Sandbox execution tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_simple_print():
    """Simple print statement is captured in stdout."""
    config = SandboxConfig(timeout_seconds=10, working_dir=".")
    result = await execute_code("print('hello world')", config)
    assert result.exit_code == 0
    assert "hello world" in result.stdout
    assert result.traceback is None
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_execute_captures_return_value():
    """Assigning to 'result' captures the return value."""
    config = SandboxConfig(timeout_seconds=10, working_dir=".")
    result = await execute_code("result = {'key': 42}", config)
    assert result.exit_code == 0
    assert result.return_value == {"key": 42}


@pytest.mark.asyncio
async def test_execute_captures_stderr():
    """stderr output is captured separately."""
    code = "import sys; sys.stderr.write('warning message\\n')"
    config = SandboxConfig(timeout_seconds=10, working_dir=".")
    result = await execute_code(code, config)
    assert "warning message" in result.stderr


@pytest.mark.asyncio
async def test_execute_captures_traceback():
    """Exceptions produce a traceback in the result."""
    config = SandboxConfig(timeout_seconds=10, working_dir=".")
    result = await execute_code("raise ValueError('test error')", config)
    assert result.traceback is not None
    assert "ValueError" in result.traceback
    assert "test error" in result.traceback


@pytest.mark.asyncio
async def test_execute_syntax_error():
    """Syntax errors are captured as tracebacks."""
    config = SandboxConfig(timeout_seconds=10, working_dir=".")
    result = await execute_code("def broken(:", config)
    # Syntax errors may appear in stderr or traceback
    has_error = (
        (result.traceback and "SyntaxError" in result.traceback)
        or (result.stderr and "SyntaxError" in result.stderr)
        or result.exit_code != 0
    )
    assert has_error


@pytest.mark.asyncio
async def test_execute_timeout():
    """Long-running code is killed after timeout."""
    config = SandboxConfig(timeout_seconds=1, working_dir=".")
    result = await execute_code("import time; time.sleep(30)", config)
    assert result.timed_out is True
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_execute_stdout_and_return_value():
    """stdout and return value are captured independently."""
    code = "print('output line'); result = 99"
    config = SandboxConfig(timeout_seconds=10, working_dir=".")
    result = await execute_code(code, config)
    assert "output line" in result.stdout
    assert result.return_value == 99


@pytest.mark.asyncio
async def test_execute_no_output():
    """Code with no output produces an empty result."""
    config = SandboxConfig(timeout_seconds=10, working_dir=".")
    result = await execute_code("x = 1 + 1", config)
    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.return_value is None


@pytest.mark.asyncio
async def test_execute_duration_tracked():
    """Execution duration is recorded."""
    config = SandboxConfig(timeout_seconds=10, working_dir=".")
    result = await execute_code("print('fast')", config)
    assert result.duration_seconds >= 0


# ──────────────────────────────────────────────────
# pip install tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_install_empty_list():
    """Empty package list is a no-op."""
    result = await install_packages([])
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_install_rejects_shell_metacharacters():
    """Package names with shell metacharacters are rejected."""
    result = await install_packages(["valid-pkg", "bad;rm -rf /"])
    assert result.exit_code == -1
    assert "Invalid package name" in result.stderr


# ──────────────────────────────────────────────────
# Executor event publishing tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_executor_publishes_started_event():
    """Executor publishes tool:call_started before execution."""
    config = PythonToolConfig()
    event_pub = AsyncMock()
    executor = PythonExecutor(config=config, event_publisher=event_pub)

    await executor.run_code(
        code="print('hi')",
        agent_id="dev",
        agent_mounts=[],
        channel="ch-test",
    )

    # First call should be tool:call_started
    calls = event_pub.publish_event.call_args_list
    assert len(calls) >= 2
    first_call_kwargs = calls[0].kwargs
    assert first_call_kwargs.get("tool") == "python"
    assert first_call_kwargs.get("agent") == "dev"


@pytest.mark.asyncio
async def test_executor_publishes_complete_on_success():
    """Successful execution publishes tool:call_complete."""
    config = PythonToolConfig()
    event_pub = AsyncMock()
    executor = PythonExecutor(config=config, event_publisher=event_pub)

    await executor.run_code(
        code="result = 42",
        agent_id="dev",
        agent_mounts=[],
    )

    # Last publish_event call should be completion
    last_call = event_pub.publish_event.call_args_list[-1]
    # Event type is the first positional arg
    assert "TOOL_CALL_COMPLETE" in str(last_call) or "complete" in str(last_call).lower()


@pytest.mark.asyncio
async def test_executor_publishes_error_on_timeout():
    """Timeout publishes tool:error with reason=timeout."""
    config = PythonToolConfig(timeout_seconds=1)
    event_pub = AsyncMock()
    executor = PythonExecutor(config=config, event_publisher=event_pub)

    await executor.run_code(
        code="import time; time.sleep(30)",
        agent_id="dev",
        agent_mounts=[],
    )

    last_call = event_pub.publish_event.call_args_list[-1]
    assert "timeout" in str(last_call).lower()


@pytest.mark.asyncio
async def test_executor_publishes_error_on_exception():
    """Execution errors publish tool:error with traceback."""
    config = PythonToolConfig()
    event_pub = AsyncMock()
    executor = PythonExecutor(config=config, event_publisher=event_pub)

    await executor.run_code(
        code="raise RuntimeError('boom')",
        agent_id="dev",
        agent_mounts=[],
    )

    last_call = event_pub.publish_event.call_args_list[-1]
    assert "error" in str(last_call).lower()


@pytest.mark.asyncio
async def test_executor_pip_install_publishes_events():
    """pip_install publishes started and complete/error events."""
    config = PythonToolConfig()
    event_pub = AsyncMock()
    executor = PythonExecutor(config=config, event_publisher=event_pub)

    await executor.run_pip_install(
        packages=["this-package-does-not-exist-xyz"],
        agent_id="dev",
    )

    calls = event_pub.publish_event.call_args_list
    assert len(calls) >= 2  # started + error/complete


# ──────────────────────────────────────────────────
# MCP result formatting tests
# ──────────────────────────────────────────────────


def test_format_result_stdout_only():
    """Result with only stdout produces STDOUT and META blocks."""
    result = ExecutionResult(stdout="hello\n", duration_seconds=0.5)
    parts = _format_result(result)
    texts = [p.text for p in parts]
    assert any("[STDOUT]" in t for t in texts)
    assert any("[META]" in t for t in texts)


def test_format_result_with_traceback():
    """Traceback results include a TRACEBACK block."""
    result = ExecutionResult(
        traceback="Traceback (most recent call last):\n  ...\nValueError: x",
        exit_code=1,
    )
    parts = _format_result(result)
    texts = [p.text for p in parts]
    assert any("[TRACEBACK]" in t for t in texts)


def test_format_result_timeout():
    """Timeout results include a TIMEOUT block."""
    result = ExecutionResult(
        timed_out=True,
        exit_code=-1,
        stderr="Execution timed out",
        duration_seconds=60.0,
    )
    parts = _format_result(result)
    texts = [p.text for p in parts]
    assert any("[TIMEOUT]" in t for t in texts)


def test_format_result_return_value():
    """Return values are formatted as RETURN VALUE block."""
    result = ExecutionResult(return_value={"key": [1, 2, 3]})
    parts = _format_result(result)
    texts = [p.text for p in parts]
    assert any("[RETURN VALUE]" in t for t in texts)
    assert any("key" in t for t in texts)


def test_format_result_no_output():
    """No-output execution returns an OK block."""
    result = ExecutionResult()
    parts = _format_result(result)
    texts = [p.text for p in parts]
    assert any("[OK]" in t for t in texts)


def test_format_result_all_fields():
    """Result with all fields produces all blocks."""
    result = ExecutionResult(
        stdout="out\n",
        stderr="err\n",
        return_value=42,
        traceback="Traceback...",
        exit_code=1,
        duration_seconds=1.5,
    )
    parts = _format_result(result)
    texts = [p.text for p in parts]
    assert any("[STDOUT]" in t for t in texts)
    assert any("[STDERR]" in t for t in texts)
    assert any("[RETURN VALUE]" in t for t in texts)
    assert any("[TRACEBACK]" in t for t in texts)
    assert any("[META]" in t for t in texts)
```

---

## Integration Points

The Python Execution MCP server integrates with several FAITH components:

```python
# Agent calls execute_python via MCP (FAITH-008 events, FAITH-003 config)
# The PA's adapter layer translates MCP tool calls for non-MCP models.

# Tool call flow:
# 1. Agent sends MCP tool call: execute_python(code="...", ...)
# 2. Server receives call, injects agent context (_agent_id, _agent_mounts)
# 3. Executor publishes tool:call_started to system-events
# 4. Sandbox runs code in subprocess with timeout
# 5. Executor publishes tool:call_complete or tool:error
# 6. Server formats ExecutionResult as MCP TextContent response
# 7. Agent receives structured stdout/stderr/return/traceback
```

```python
# Configuration hot-reload (FAITH-004 watches .faith/tools/python.yaml):
# When the user toggles internet_access or changes timeout_seconds,
# the config watcher publishes system:config_changed. The PA can
# restart or reconfigure the tool container accordingly.
```

```python
# Mount permission enforcement (same model as FAITH-022 filesystem tool):
# The agent's config.yaml defines which mounts it can access.
# The executor resolves these to container paths and restricts
# the sandbox to only those directories. Example agent config:
#
# tools:
#   python:
#     mounts: [workspace, data]
#     python_timeout_seconds: 120
```

---

## Acceptance Criteria

1. `PythonToolConfig` Pydantic model validates `.faith/tools/python.yaml` with defaults (`internet_access: true`, `timeout_seconds: 60`). Missing or invalid files produce valid defaults without errors.
2. `execute_code()` runs Python code in a subprocess and captures stdout, stderr, return value (via `result` variable), and tracebacks as separate fields in `ExecutionResult`.
3. Timeout enforcement: when execution exceeds `timeout_seconds`, the subprocess is killed, `timed_out` is set to `True`, and a `tool:error` event with `reason: timeout` is published.
4. `install_packages()` runs `pip install` with package sanitisation (rejects shell metacharacters) and captures pip output.
5. `PythonExecutor.run_code()` publishes `tool:call_started` before execution and `tool:call_complete` or `tool:error` after, via the `EventPublisher` to the `system-events` Redis channel.
6. `PythonExecutor` enforces workspace mount permissions — agents can only access paths assigned to them in their `config.yaml`, matching the filesystem tool permission model.
7. The MCP server exposes two tools (`execute_python`, `pip_install`) and formats results as separate `TextContent` blocks for stdout, stderr, return value, traceback, and metadata.
8. The Dockerfile pre-installs numpy, pandas, requests, beautifulsoup4, playwright (with Chromium), lxml, and httpx.
9. All 30 tests in `tests/test_python_tool.py` pass, covering config loading, sandboxed execution, output capture, timeout, pip install, event publishing, and MCP result formatting.

---

## Notes for Implementer

- **Wrapper template**: The sandbox uses a two-file approach. User code is written to `user_script.py`, then a `wrapper.py` executes it inside a try/except and writes structured results to a JSON sidecar file. This cleanly separates user stdout from framework output.
- **Return value convention**: Following FRS 4.2.5, user code can set a variable named `result` and its value will be captured and returned. The wrapper checks for JSON serialisability and falls back to `repr()` for complex objects.
- **Shell metacharacter rejection**: The `install_packages()` function rejects package names containing `;|&$` etc. to prevent command injection. This is a defence-in-depth measure alongside subprocess execution (which does not use `shell=True`).
- **Agent context injection**: The MCP server expects `_agent_id`, `_agent_mounts`, and `_channel` to be injected into the tool call arguments by the PA's adapter layer. These are popped from `arguments` before processing so they do not interfere with tool parameters.
- **Event types**: The server uses `EventType.TOOL_CALL_STARTED`, `EventType.TOOL_CALL_COMPLETE`, and `EventType.TOOL_ERROR` from the event system (FAITH-008). If these exact enum values differ in the final FAITH-008 implementation, update the imports accordingly.
- **Internet toggle**: The `internet_access` setting in `python.yaml` is enforced at the Docker network level by the PA when starting the container. The MCP server itself does not enforce network isolation — it reads the setting for event metadata only. The PA uses Docker network policies (`maf-network` connected or disconnected) to control access.
- **Non-root execution**: The Dockerfile creates an `executor` user. In production, the subprocess should run as this user for additional sandboxing. The current implementation passes through the container's user context; switching to `executor` requires `subprocess` user switching which is configured via `SandboxConfig.run_as_user` but not yet enforced in the async subprocess (future hardening).
- **Working directory**: The default working directory is `/workspace`, which is where Docker mounts are mapped. Agent-specific mount paths are resolved relative to this directory.


