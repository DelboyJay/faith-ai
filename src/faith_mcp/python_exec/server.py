"""
Description:
    Provide a high-level FAITH Python execution MCP server facade.

Requirements:
    - Expose structured execution and package-install entry points.
    - Format sandbox execution results as stable dictionaries for MCP responses.
    - Support host-routing hints, OS package install, and config reload.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from faith_mcp.python_exec.executor import PythonExecutor
from faith_mcp.python_exec.sandbox import ExecutionResult
from faith_shared.config.models import PythonToolConfig


class PythonExecutionServer:
    """
    Description:
        Coordinate Python execution requests through the underlying executor.

    Requirements:
        - Reuse the executor for both code execution and package installation.
        - Return stable JSON-safe payloads for MCP-facing callers.

    :param config: Python tool configuration.
    :param allowed_paths: Working-directory roots allowed for execution.
    :param event_publisher: Optional lifecycle event publisher.
    """

    def __init__(
        self,
        *,
        config: PythonToolConfig,
        allowed_paths: list[Path] | None = None,
        event_publisher: Any | None = None,
        host_runner: Any | None = None,
        host_worker_enabled: bool = False,
        host_allowed_paths: list[Path] | None = None,
    ) -> None:
        """
        Description:
            Initialise the Python server facade and its executor dependency.

        Requirements:
            - Preserve the supplied configuration and allowed path boundary.

        :param config: Python tool configuration.
        :param allowed_paths: Working-directory roots allowed for execution.
        :param event_publisher: Optional lifecycle event publisher.
        :param host_runner: Optional host-runner callback for host-bound execution.
        :param host_worker_enabled: Whether host routing is enabled.
        :param host_allowed_paths: Host path roots allowed for host-bound execution.
        """

        self.config = config
        self.executor = PythonExecutor(
            config=config,
            event_publisher=event_publisher,
            allowed_paths=allowed_paths,
            host_runner=host_runner,
            host_worker_enabled=host_worker_enabled,
            host_allowed_paths=host_allowed_paths,
        )

    async def execute_python(
        self,
        code: str,
        *,
        agent_id: str,
        working_directory: Path,
        require_host: bool = False,
    ) -> dict[str, Any]:
        """
        Description:
            Execute Python code and return a structured response payload.

        Requirements:
            - Delegate execution to the shared executor.
            - Format the result for stable MCP consumption.

        :param code: Python source code to execute.
        :param agent_id: Agent requesting the execution.
        :param working_directory: Working directory to use during execution.
        :param require_host: Whether the PA has already decided this request is host-bound.
        :returns: Structured execution response payload.
        """

        return self._format_result(
            await self.executor.run_code(
                code,
                agent_id=agent_id,
                working_directory=working_directory,
                require_host=require_host,
            )
        )

    async def pip_install(
        self,
        packages: list[str],
        *,
        agent_id: str,
        working_directory: Path,
        require_host: bool = False,
    ) -> dict[str, Any]:
        """
        Description:
            Install Python packages and return a structured response payload.

        Requirements:
            - Delegate installation to the shared executor.
            - Format the result for stable MCP consumption.

        :param packages: Package specifiers to install with ``pip``.
        :param agent_id: Agent requesting the installation.
        :param working_directory: Working directory to use during installation.
        :param require_host: Whether the PA has already decided this request is host-bound.
        :returns: Structured package-install response payload.
        """

        return self._format_result(
            await self.executor.install_packages(
                packages,
                agent_id=agent_id,
                working_directory=working_directory,
                require_host=require_host,
            )
        )

    async def os_package_install(
        self,
        packages: list[str],
        *,
        agent_id: str,
        working_directory: Path,
        require_host: bool = False,
    ) -> dict[str, Any]:
        """
        Description:
            Install OS packages and return a structured response payload.

        Requirements:
            - Delegate OS package installation to the shared executor.
            - Format the result for stable MCP consumption.

        :param packages: OS package names to install.
        :param agent_id: Agent requesting the installation.
        :param working_directory: Working directory to use during installation.
        :param require_host: Whether the PA has already decided this request is host-bound.
        :returns: Structured OS-package-install response payload.
        """

        return self._format_result(
            await self.executor.install_os_packages(
                packages,
                agent_id=agent_id,
                working_directory=working_directory,
                require_host=require_host,
            )
        )

    def reload_config(self, config: PythonToolConfig) -> None:
        """
        Description:
            Replace the active Python tool config and rebuild the executor around it.

        Requirements:
            - Preserve the executor's current routing collaborators and path allow-lists.

        :param config: Replacement Python tool config.
        """

        self.config = config
        self.executor = PythonExecutor(
            config=config,
            event_publisher=self.executor.event_publisher,
            allowed_paths=self.executor.allowed_paths,
            host_runner=self.executor.host_runner,
            host_worker_enabled=self.executor.host_worker_enabled,
            host_allowed_paths=self.executor.host_allowed_paths,
        )

    async def handle_tool_call(
        self,
        action: str,
        args: dict[str, Any],
        *,
        agent_id: str,
        working_directory: Path,
    ) -> dict[str, Any]:
        """
        Description:
            Dispatch one MCP-style Python tool call by action name.

        Requirements:
            - Support code execution, Python package install, and OS package install.
            - Raise a clear error for unknown actions.

        :param action: Python tool action name to dispatch.
        :param args: Action arguments supplied by the caller.
        :param agent_id: Agent performing the action.
        :param working_directory: Working directory to use during execution.
        :returns: Structured Python tool response payload.
        :raises ValueError: If the action is unknown.
        """

        require_host = bool(args.get("require_host", False))
        if action == "execute_python":
            return await self.execute_python(
                str(args.get("code", "")),
                agent_id=agent_id,
                working_directory=working_directory,
                require_host=require_host,
            )
        if action == "pip_install":
            return await self.pip_install(
                list(args.get("packages", [])),
                agent_id=agent_id,
                working_directory=working_directory,
                require_host=require_host,
            )
        if action == "os_package_install":
            return await self.os_package_install(
                list(args.get("packages", [])),
                agent_id=agent_id,
                working_directory=working_directory,
                require_host=require_host,
            )
        raise ValueError(f"Unknown python tool action '{action}'")

    @staticmethod
    def _format_result(result: ExecutionResult) -> dict[str, Any]:
        """
        Description:
            Convert an execution result into a stable response payload.

        Requirements:
            - Preserve stdout, stderr, return value, traceback, timeout, and success state.

        :param result: Structured execution result to format.
        :returns: JSON-safe response payload.
        """

        return result.to_dict()


def load_server_from_faith_dir(faith_dir: Path, *, allowed_paths: list[Path] | None = None) -> PythonExecutionServer:
    """
    Description:
        Build a Python execution server from the on-disk FAITH tool config.

    Requirements:
        - Load `.faith/tools/python.yaml` when it exists.
        - Fall back to the shared config defaults when the file is missing.

    :param faith_dir: Root FAITH data directory.
    :param allowed_paths: Optional allowed working-directory roots.
    :returns: Configured Python execution server.
    """

    config_path = Path(faith_dir) / "tools" / "python.yaml"
    if config_path.exists():
        config = PythonToolConfig.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")) or {})
    else:
        config = PythonToolConfig()
    return PythonExecutionServer(config=config, allowed_paths=allowed_paths)


def main() -> None:
    """
    Description:
        Start the Python execution MCP container entry point.

    Requirements:
        - Provide a stable line-oriented stdio process for the dedicated Python container.
        - Keep the request protocol simple: one JSON object per line in, one JSON object per line out.
    """

    faith_dir = Path(os.environ.get("FAITH_DIR", ".faith"))
    server = load_server_from_faith_dir(faith_dir, allowed_paths=[Path.cwd()])
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        try:
            result = asyncio.run(
                server.handle_tool_call(
                    str(request.get("action", "")),
                    dict(request.get("args", {})),
                    agent_id=str(request.get("agent_id", "unknown")),
                    working_directory=Path(request.get("working_directory", Path.cwd())),
                )
            )
            response = {"ok": True, "result": result}
        except Exception as exc:  # pragma: no cover - stdio guard rail
            response = {"ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
