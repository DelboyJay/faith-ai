"""Description:
    Verify the FAITH Python execution MCP helpers load configuration, execute
    code, and publish lifecycle events correctly.

Requirements:
    - Prove python tool config falls back to safe defaults.
    - Prove sandbox execution captures stdout, stderr, return values, and timeouts.
    - Prove the executor enforces working-directory boundaries and publishes events.
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from faith_mcp.python_exec import (
    PythonExecutionServer,
    PythonExecutor,
    PythonToolConfig,
    SandboxConfig,
    execute_code,
    load_python_config,
)


class DummyPublisher:
    """Description:
        Record published Python-tool lifecycle events for later assertions.

    Requirements:
        - Preserve events in call order without depending on Redis.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the dummy publisher with an empty event list.

        Requirements:
            - Start with no recorded events.
        """

        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        """Description:
            Record one published event payload.

        Requirements:
            - Preserve the raw event object for later inspection.

        :param event: Event payload published by the executor.
        """

        self.events.append(event)


def test_load_python_config_defaults_when_file_missing(tmp_path: Path) -> None:
    """Description:
        Verify Python tool configuration falls back to defaults when the config
        file is absent.

    Requirements:
        - This test is needed to prove missing ``python.yaml`` does not break the server.
        - Verify the default internet and timeout settings are returned.

    :param tmp_path: Temporary pytest directory fixture.
    """

    config = load_python_config(tmp_path / ".faith")
    assert config.internet_access is True
    assert config.timeout_seconds == 60


def test_execute_code_captures_stdout_and_return_value(tmp_path: Path) -> None:
    """Description:
        Verify sandbox execution captures stdout and the ``result`` variable separately.

        Requirements:
            - This test is needed to prove user code output is structured for the PA.
            - Verify stdout stays separate from the returned JSON-serialisable value.

        :param tmp_path: Temporary pytest directory fixture.
    """

    code = textwrap.dedent(
        """
        print("hello")
        result = {"value": 42}
        """
    )
    result = execute_code(code, SandboxConfig(timeout_seconds=5, working_directory=tmp_path))
    assert result.stdout == "hello\n"
    assert result.return_value == {"value": 42}
    assert result.traceback is None
    assert result.timed_out is False


def test_execute_code_times_out_long_running_code(tmp_path: Path) -> None:
    """Description:
        Verify sandbox execution reports timeouts for overlong code.

        Requirements:
            - This test is needed to prove runaway code is stopped deterministically.
            - Verify the timed-out result is flagged and does not report a normal success.

        :param tmp_path: Temporary pytest directory fixture.
    """

    code = "import time\ntime.sleep(2)\n"
    result = execute_code(code, SandboxConfig(timeout_seconds=1, working_directory=tmp_path))
    assert result.timed_out is True
    assert result.success is False
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_python_executor_publishes_started_and_complete_events(tmp_path: Path) -> None:
    """Description:
        Verify the executor publishes lifecycle events around a successful code run.

        Requirements:
            - This test is needed to prove the PA can observe Python tool execution.
            - Verify both start and completion events are emitted for a successful run.

        :param tmp_path: Temporary pytest directory fixture.
    """

    publisher = DummyPublisher()
    executor = PythonExecutor(
        config=PythonToolConfig(timeout_seconds=5),
        event_publisher=publisher,
        allowed_paths=[tmp_path],
    )

    result = await executor.run_code(
        "print('ok')\nresult = 1\n",
        agent_id="dev",
        working_directory=tmp_path,
    )

    assert result.stdout == "ok\n"
    assert len(publisher.events) == 2
    assert publisher.events[0].event.value == "tool:call_started"
    assert publisher.events[1].event.value == "tool:call_complete"


@pytest.mark.asyncio
async def test_python_executor_rejects_disallowed_working_directory(tmp_path: Path) -> None:
    """Description:
        Verify the executor refuses to run code outside the allowed workspace paths.

        Requirements:
            - This test is needed to prove the Python tool respects filesystem boundaries.
            - Verify a disallowed working directory raises a permission error before execution.

        :param tmp_path: Temporary pytest directory fixture.
    """

    allowed = tmp_path / "allowed"
    denied = tmp_path / "denied"
    allowed.mkdir()
    denied.mkdir()
    executor = PythonExecutor(
        config=PythonToolConfig(timeout_seconds=5),
        allowed_paths=[allowed],
    )

    with pytest.raises(PermissionError):
        await executor.run_code("print('nope')\n", agent_id="dev", working_directory=denied)


def test_python_server_formats_execute_response(tmp_path: Path) -> None:
    """Description:
        Verify the Python server exposes a structured execution response.

        Requirements:
            - This test is needed to prove the MCP-facing server returns stable result fields.
            - Verify the response includes stdout, success state, and return value.

        :param tmp_path: Temporary pytest directory fixture.
    """

    server = PythonExecutionServer(
        config=PythonToolConfig(timeout_seconds=5),
        allowed_paths=[tmp_path],
    )

    response = asyncio.run(
        server.execute_python("print('hello')\nresult = ['a']\n", agent_id="dev", working_directory=tmp_path)
    )

    assert response["success"] is True
    assert response["stdout"] == "hello\n"
    assert response["return_value"] == ["a"]
