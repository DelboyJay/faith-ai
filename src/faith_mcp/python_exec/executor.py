"""
Description:
    Coordinate Python execution requests, workspace checks, and lifecycle events.

Requirements:
    - Enforce allowed working-directory boundaries before execution.
    - Publish tool lifecycle events around execution and package installation.
    - Delegate subprocess execution to the sandbox layer.
    - Route explicitly host-bound work to the optional host runner when enabled.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from faith_mcp.python_exec.sandbox import (
    ExecutionResult,
    SandboxConfig,
    execute_code,
    install_os_packages,
    install_packages,
)
from faith_shared.config.models import PythonToolConfig
from faith_shared.protocol.events import EventPublisher, EventType, FaithEvent


class PythonExecutor:
    """
    Description:
        Execute Python tool requests under the FAITH workspace boundary rules.

    Requirements:
        - Enforce that the working directory stays inside one of the allowed paths.
        - Publish start, complete, and error events when an event publisher is configured.
        - Prefer sandbox execution unless host routing is explicitly required.

    :param config: Python tool configuration.
    :param event_publisher: Optional event publisher used for lifecycle events.
    :param allowed_paths: Host paths the execution request may use as its working directory.
    :param host_runner: Optional callable used for host-routed execution.
    :param host_worker_enabled: Whether host routing is currently enabled.
    :param host_allowed_paths: Host path roots the optional host runner may use.
    """

    def __init__(
        self,
        *,
        config: PythonToolConfig,
        event_publisher: EventPublisher | Any | None = None,
        allowed_paths: list[Path] | None = None,
        host_runner: Callable[[str, dict[str, Any]], ExecutionResult] | None = None,
        host_worker_enabled: bool = False,
        host_allowed_paths: list[Path] | None = None,
    ) -> None:
        """
        Description:
            Store executor dependencies and workspace boundary settings.

        Requirements:
            - Resolve all allowed paths up front for stable boundary checks.

        :param config: Python tool configuration.
        :param event_publisher: Optional event publisher used for lifecycle events.
        :param allowed_paths: Host paths the execution request may use as its working directory.
        :param host_runner: Optional callable used for host-routed execution.
        :param host_worker_enabled: Whether host routing is currently enabled.
        :param host_allowed_paths: Host path roots the optional host runner may use.
        """

        self.config = config
        self.event_publisher = event_publisher
        self.allowed_paths = [Path(path).resolve() for path in (allowed_paths or [])]
        self.host_runner = host_runner
        self.host_worker_enabled = host_worker_enabled
        self.host_allowed_paths = [Path(path).resolve() for path in (host_allowed_paths or [])]

    async def run_code(
        self,
        code: str,
        *,
        agent_id: str,
        working_directory: Path,
        require_host: bool = False,
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
        :param require_host: Whether the PA has already decided this request is host-bound.
        :returns: Structured execution result.
        :raises PermissionError: If the working directory falls outside the allowed paths.
        """

        resolved_working_directory = Path(working_directory).resolve()
        execution_target = self._resolve_execution_target(
            resolved_working_directory,
            require_host=require_host,
        )
        await self._publish(
            EventType.TOOL_CALL_STARTED,
            tool="python",
            data={
                "agent": agent_id,
                "action": "execute",
                "cwd": str(resolved_working_directory),
                "execution_target": execution_target,
            },
        )
        result = self._run_target(
            "execute",
            {
                "code": code,
                "working_directory": str(resolved_working_directory),
                "timeout_seconds": self.config.timeout_seconds,
            },
            execution_target=execution_target,
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
                    "execution_target": execution_target,
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
                    "execution_target": execution_target,
                },
            )
        return result

    async def install_packages(
        self,
        packages: list[str],
        *,
        agent_id: str,
        working_directory: Path,
        require_host: bool = False,
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

        resolved_working_directory = Path(working_directory).resolve()
        execution_target = self._resolve_execution_target(
            resolved_working_directory,
            require_host=require_host,
        )
        await self._publish(
            EventType.TOOL_CALL_STARTED,
            tool="python",
            data={
                "agent": agent_id,
                "action": "pip_install",
                "cwd": str(resolved_working_directory),
                "execution_target": execution_target,
                "packages": list(packages),
            },
        )
        result = self._run_target(
            "pip_install",
            {"packages": list(packages), "working_directory": str(resolved_working_directory)},
            execution_target=execution_target,
        )
        await self._publish(
            EventType.TOOL_CALL_COMPLETE if result.success else EventType.TOOL_ERROR,
            tool="python",
            data={
                "agent": agent_id,
                "action": "pip_install",
                "cwd": str(resolved_working_directory),
                "execution_target": execution_target,
                "packages": list(packages),
                "reason": None if result.success else "install_failed",
            },
        )
        return result

    async def install_os_packages(
        self,
        packages: list[str],
        *,
        agent_id: str,
        working_directory: Path,
        require_host: bool = False,
    ) -> ExecutionResult:
        """
        Description:
            Install OS packages for the current execution target.

        Requirements:
            - Support the same host-routing decision model as Python package installs.
            - Publish lifecycle events for OS package operations.

        :param packages: OS package names to install.
        :param agent_id: Agent requesting the installation.
        :param working_directory: Working directory to use during installation.
        :param require_host: Whether the PA has already decided this request is host-bound.
        :returns: Structured execution result for the OS package install.
        """

        resolved_working_directory = Path(working_directory).resolve()
        execution_target = self._resolve_execution_target(
            resolved_working_directory,
            require_host=require_host,
        )
        await self._publish(
            EventType.TOOL_CALL_STARTED,
            tool="python",
            data={
                "agent": agent_id,
                "action": "os_package_install",
                "cwd": str(resolved_working_directory),
                "execution_target": execution_target,
                "packages": list(packages),
            },
        )
        result = self._run_target(
            "os_package_install",
            {"packages": list(packages), "working_directory": str(resolved_working_directory)},
            execution_target=execution_target,
        )
        await self._publish(
            EventType.TOOL_CALL_COMPLETE if result.success else EventType.TOOL_ERROR,
            tool="python",
            data={
                "agent": agent_id,
                "action": "os_package_install",
                "cwd": str(resolved_working_directory),
                "execution_target": execution_target,
                "packages": list(packages),
                "reason": None if result.success else "install_failed",
            },
        )
        return result

    def _resolve_execution_target(self, working_directory: Path, *, require_host: bool) -> str:
        """
        Description:
            Decide whether a request should run in the sandbox or via the optional host worker.

        Requirements:
            - Prefer sandbox execution when the working directory is inside the allowed paths.
            - Route to the host only when host routing is enabled and explicitly required.

        :param working_directory: Working directory requested for execution.
        :param require_host: Whether the PA has already decided the request is host-bound.
        :returns: Selected execution target label.
        :raises PermissionError: If the working directory is not permitted for the chosen target.
        """

        if require_host:
            if not self.host_worker_enabled or self.host_runner is None:
                raise PermissionError("Host execution requested but the host worker is not enabled")
            self._ensure_allowed_path(
                working_directory, self.host_allowed_paths or self.allowed_paths
            )
            return "host"
        self._ensure_allowed_path(working_directory, self.allowed_paths)
        return "sandbox"

    def _run_target(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        execution_target: str,
    ) -> ExecutionResult:
        """
        Description:
            Execute one Python tool action on the selected execution target.

        Requirements:
            - Use the host runner when the target is ``host``.
            - Use the sandbox helpers for local sandbox execution.

        :param action: Tool action name to execute.
        :param payload: Structured action payload.
        :param execution_target: Selected execution target label.
        :returns: Structured execution result.
        """

        if execution_target == "host":
            assert self.host_runner is not None
            result = self.host_runner(action, payload)
            result.execution_target = "host"
            return result
        config = SandboxConfig(
            timeout_seconds=int(payload.get("timeout_seconds", self.config.timeout_seconds)),
            working_directory=Path(payload["working_directory"]),
            execution_target="sandbox",
        )
        if action == "execute":
            return execute_code(str(payload["code"]), config)
        if action == "pip_install":
            return install_packages(list(payload.get("packages", [])), config)
        if action == "os_package_install":
            return install_os_packages(list(payload.get("packages", [])), config)
        raise ValueError(f"Unknown python execution action '{action}'")

    def _ensure_allowed_path(self, working_directory: Path, allowed_paths: list[Path]) -> None:
        """
        Description:
            Validate that the requested working directory stays inside the allowed paths.

        Requirements:
            - Allow all paths when no workspace boundary has been configured.
            - Raise a permission error when the path escapes the allowed roots.

        :param working_directory: Working directory requested for execution.
        :raises PermissionError: If the working directory falls outside the allowed paths.
        """

        if not allowed_paths:
            return
        for allowed in allowed_paths:
            try:
                working_directory.relative_to(allowed)
                return
            except ValueError:
                continue
        raise PermissionError(
            f"Working directory '{working_directory}' is outside the allowed paths"
        )

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
