"""
Description:
    Provide a high-level FAITH Python execution MCP server facade.

Requirements:
    - Expose structured execution and package-install entry points.
    - Format sandbox execution results as stable dictionaries for MCP responses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    ) -> None:
        """
        Description:
            Initialise the Python server facade and its executor dependency.

        Requirements:
            - Preserve the supplied configuration and allowed path boundary.

        :param config: Python tool configuration.
        :param allowed_paths: Working-directory roots allowed for execution.
        :param event_publisher: Optional lifecycle event publisher.
        """

        self.config = config
        self.executor = PythonExecutor(
            config=config,
            event_publisher=event_publisher,
            allowed_paths=allowed_paths,
        )

    async def execute_python(
        self,
        code: str,
        *,
        agent_id: str,
        working_directory: Path,
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
        :returns: Structured execution response payload.
        """

        return self._format_result(
            await self.executor.run_code(
                code,
                agent_id=agent_id,
                working_directory=working_directory,
            )
        )

    async def pip_install(
        self,
        packages: list[str],
        *,
        agent_id: str,
        working_directory: Path,
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
        :returns: Structured package-install response payload.
        """

        return self._format_result(
            await self.executor.install_packages(
                packages,
                agent_id=agent_id,
                working_directory=working_directory,
            )
        )

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
