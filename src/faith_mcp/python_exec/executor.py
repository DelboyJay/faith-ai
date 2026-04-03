"""
Description:
    Coordinate Python execution requests, workspace checks, and lifecycle events.

Requirements:
    - Enforce allowed working-directory boundaries before execution.
    - Publish tool lifecycle events around execution and package installation.
    - Delegate subprocess execution to the sandbox layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from faith_mcp.python_exec.sandbox import ExecutionResult, SandboxConfig, execute_code
from faith_shared.config.models import PythonToolConfig
from faith_shared.protocol.events import EventPublisher, EventType, FaithEvent


class PythonExecutor:
    """
    Description:
        Execute Python tool requests under the FAITH workspace boundary rules.

    Requirements:
        - Enforce that the working directory stays inside one of the allowed paths.
        - Publish start, complete, and error events when an event publisher is configured.

    :param config: Python tool configuration.
    :param event_publisher: Optional event publisher used for lifecycle events.
    :param allowed_paths: Host paths the execution request may use as its working directory.
    """

    def __init__(
        self,
        *,
        config: PythonToolConfig,
        event_publisher: EventPublisher | Any | None = None,
        allowed_paths: list[Path] | None = None,
    ) -> None:
        """
        Description:
            Store executor dependencies and workspace boundary settings.

        Requirements:
            - Resolve all allowed paths up front for stable boundary checks.

        :param config: Python tool configuration.
        :param event_publisher: Optional event publisher used for lifecycle events.
        :param allowed_paths: Host paths the execution request may use as its working directory.
        """

        self.config = config
        self.event_publisher = event_publisher
        self.allowed_paths = [Path(path).resolve() for path in (allowed_paths or [])]

    async def run_code(
        self,
        code: str,
        *,
        agent_id: str,
        working_directory: Path,
    ) -> ExecutionResult:
        """
        Description:
            Execute Python code inside the configured working directory.

        Requirements:
            - Reject working directories outside the allowed workspace paths.
            - Publish start and completion or error events around execution.

        :param code: Python source code to execute.
        :param agent_id: Agent requesting the execution.
        :param working_directory: Working directory to use during execution.
        :returns: Structured execution result.
        :raises PermissionError: If the working directory falls outside the allowed paths.
        """

        resolved_working_directory = Path(working_directory).resolve()
        self._ensure_allowed_path(resolved_working_directory)
        await self._publish(
            EventType.TOOL_CALL_STARTED,
            tool="python",
            data={"agent": agent_id, "action": "execute", "cwd": str(resolved_working_directory)},
        )
        result = execute_code(
            code,
            SandboxConfig(
                timeout_seconds=self.config.timeout_seconds,
                working_directory=resolved_working_directory,
            ),
        )
        if result.success:
            await self._publish(
                EventType.TOOL_CALL_COMPLETE,
                tool="python",
                data={
                    "agent": agent_id,
                    "action": "execute",
                    "cwd": str(resolved_working_directory),
                    "timed_out": result.timed_out,
                },
            )
        else:
            await self._publish(
                EventType.TOOL_ERROR,
                tool="python",
                data={
                    "agent": agent_id,
                    "action": "execute",
                    "cwd": str(resolved_working_directory),
                    "reason": "timeout" if result.timed_out else "execution_failed",
                },
            )
        return result

    async def install_packages(
        self,
        packages: list[str],
        *,
        agent_id: str,
        working_directory: Path,
    ) -> ExecutionResult:
        """
        Description:
            Install Python packages for the current execution environment.

        Requirements:
            - Use ``pip install`` through the same sandboxed execution path.
            - Publish the same lifecycle events as code execution.

        :param packages: Package specifiers to install with ``pip``.
        :param agent_id: Agent requesting the installation.
        :param working_directory: Working directory to use during installation.
        :returns: Structured execution result for the pip invocation.
        """

        install_code = (
            "import subprocess, sys\n"
            f"result = subprocess.run([sys.executable, '-m', 'pip', 'install', *{packages!r}], "
            "capture_output=True, text=True)\n"
            "print(result.stdout, end='')\n"
            "print(result.stderr, end='', file=sys.stderr)\n"
            "result = {'exit_code': result.returncode}\n"
        )
        return await self.run_code(
            install_code,
            agent_id=agent_id,
            working_directory=working_directory,
        )

    def _ensure_allowed_path(self, working_directory: Path) -> None:
        """
        Description:
            Validate that the requested working directory stays inside the allowed paths.

        Requirements:
            - Allow all paths when no workspace boundary has been configured.
            - Raise a permission error when the path escapes the allowed roots.

        :param working_directory: Working directory requested for execution.
        :raises PermissionError: If the working directory falls outside the allowed paths.
        """

        if not self.allowed_paths:
            return
        for allowed in self.allowed_paths:
            try:
                working_directory.relative_to(allowed)
                return
            except ValueError:
                continue
        raise PermissionError(f"Working directory '{working_directory}' is outside the allowed paths")

    async def _publish(self, event_type: EventType, *, tool: str, data: dict[str, Any]) -> None:
        """
        Description:
            Publish one Python tool lifecycle event when a publisher is configured.

        Requirements:
            - Support both the shared ``EventPublisher`` and simpler ``publish`` adapters.

        :param event_type: Event type to publish.
        :param tool: Tool source identifier.
        :param data: Structured event payload.
        """

        if self.event_publisher is None:
            return
        event = FaithEvent(event=event_type, source=tool, data=data)
        publish = getattr(self.event_publisher, "publish", None)
        if callable(publish):
            await publish(event)
