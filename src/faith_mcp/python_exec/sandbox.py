"""
Description:
    Execute Python code in a subprocess with structured output capture.

Requirements:
    - Capture stdout, stderr, return values, and tracebacks separately.
    - Enforce execution timeouts deterministically.
    - Keep execution rooted in an explicit working directory.
    - Support package-install helpers with input sanitisation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_WRAPPER = textwrap.dedent(
    """\
    import json
    import traceback
    from pathlib import Path

    payload_path = Path(__import__("sys").argv[1])
    source_path = Path(__import__("sys").argv[2])
    namespace = {}
    payload = {"return_value": None, "traceback": None}

    try:
        code = source_path.read_text(encoding="utf-8")
        exec(compile(code, str(source_path), "exec"), namespace)
        if "result" in namespace:
            try:
                json.dumps(namespace["result"])
                payload["return_value"] = namespace["result"]
            except TypeError:
                payload["return_value"] = repr(namespace["result"])
    except Exception:
        payload["traceback"] = traceback.format_exc()

    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    if payload["traceback"] is not None:
        raise SystemExit(1)
    """
)

_INVALID_PACKAGE_CHARS = set(";|&$`'\"\n\r")


@dataclass(slots=True)
class SandboxConfig:
    """
    Description:
        Configure one subprocess-backed Python execution sandbox.

    Requirements:
        - Preserve timeout, executable, working directory, and environment overrides.
        - Preserve the execution target label used for audit and debugging.

    :param timeout_seconds: Maximum execution time before the subprocess is killed.
    :param python_executable: Python interpreter used for execution.
    :param working_directory: Working directory for the subprocess.
    :param environment: Optional environment overrides.
    """

    timeout_seconds: int = 60
    python_executable: str = sys.executable
    working_directory: Path | None = None
    environment: dict[str, str] = field(default_factory=dict)
    execution_target: str = "sandbox"


@dataclass(slots=True)
class ExecutionResult:
    """
    Description:
        Capture the structured result of one Python execution request.

    Requirements:
        - Preserve stdout, stderr, return value, traceback, exit status, duration,
          and execution target.
        - Report whether execution timed out.
    """

    stdout: str = ""
    stderr: str = ""
    return_value: Any = None
    traceback: str | None = None
    exit_code: int = 0
    timed_out: bool = False
    duration_seconds: float = 0.0
    execution_target: str = "sandbox"

    @property
    def success(self) -> bool:
        """
        Description:
            Report whether the execution completed successfully.

        Requirements:
            - Return ``True`` only when the subprocess did not time out, did not
              raise a traceback, and exited with status ``0``.

        :returns: ``True`` when execution succeeded, otherwise ``False``.
        """

        return not self.timed_out and self.traceback is None and self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        """
        Description:
            Convert the execution result to a serialisable dictionary.

        Requirements:
            - Preserve all structured result fields in the output.

        :returns: JSON-safe representation of the execution result.
        """

        payload = asdict(self)
        payload["success"] = self.success
        return payload


def execute_code(code: str, config: SandboxConfig) -> ExecutionResult:
    """
    Description:
        Execute Python code in a subprocess and capture structured results.

    Requirements:
        - Capture stdout and stderr independently.
        - Surface the ``result`` variable separately from stream output.
        - Kill the subprocess when the configured timeout expires.

    :param code: Python source code to execute.
    :param config: Subprocess sandbox configuration.
    :returns: Structured execution result.
    """

    result = ExecutionResult(execution_target=config.execution_target)
    start = time.perf_counter()
    working_directory = Path(config.working_directory or Path.cwd())
    working_directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="faith-python-") as temp_dir:
        temp_root = Path(temp_dir)
        source_path = temp_root / "user_code.py"
        payload_path = temp_root / "result.json"
        wrapper_path = temp_root / "wrapper.py"
        source_path.write_text(code, encoding="utf-8")
        wrapper_path.write_text(_WRAPPER, encoding="utf-8")

        env = None
        if config.environment:
            env = dict(os.environ)
            env.update(config.environment)

        try:
            completed = subprocess.run(
                [config.python_executable, str(wrapper_path), str(payload_path), str(source_path)],
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
                env=env,
            )
            result.stdout = completed.stdout
            result.stderr = completed.stderr
            result.exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            result.stdout = exc.stdout or ""
            result.stderr = exc.stderr or ""
            result.exit_code = -1
            result.timed_out = True
            result.duration_seconds = time.perf_counter() - start
            return result

        if payload_path.exists():
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            result.return_value = payload.get("return_value")
            result.traceback = payload.get("traceback")

    result.duration_seconds = time.perf_counter() - start
    return result


def install_packages(packages: list[str], config: SandboxConfig) -> ExecutionResult:
    """
    Description:
        Install Python packages with ``pip`` inside the selected execution target.

    Requirements:
        - Reject package specifiers containing shell-metacharacter input.
        - Return a structured subprocess result without invoking a shell.

    :param packages: Python package specifiers to install.
    :param config: Subprocess sandbox configuration.
    :returns: Structured package-install execution result.
    """

    if not packages:
        return ExecutionResult(execution_target=config.execution_target)
    _validate_package_inputs(packages, "package")
    return _run_subprocess(
        [config.python_executable, "-m", "pip", "install", "--no-input", *packages],
        config,
    )


def install_os_packages(packages: list[str], config: SandboxConfig) -> ExecutionResult:
    """
    Description:
        Install OS packages inside the selected execution target.

    Requirements:
        - Reject package specifiers containing shell-metacharacter input.
        - Run package-manager commands without invoking a shell.

    :param packages: OS package names to install.
    :param config: Subprocess sandbox configuration.
    :returns: Structured OS-package-install execution result.
    """

    if not packages:
        return ExecutionResult(execution_target=config.execution_target)
    _validate_package_inputs(packages, "OS package")
    update_result = _run_subprocess(
        ["apt-get", "update"],
        config,
        timeout_seconds=max(config.timeout_seconds, 120),
    )
    if not update_result.success:
        return update_result
    install_result = _run_subprocess(
        ["apt-get", "install", "-y", "--no-install-recommends", *packages],
        config,
        timeout_seconds=max(config.timeout_seconds, 120),
    )
    install_result.stdout = f"{update_result.stdout}{install_result.stdout}"
    install_result.stderr = f"{update_result.stderr}{install_result.stderr}"
    return install_result


def _run_subprocess(
    args: list[str],
    config: SandboxConfig,
    *,
    timeout_seconds: int | None = None,
) -> ExecutionResult:
    """
    Description:
        Run one subprocess command and capture its structured result.

    Requirements:
        - Capture stdout and stderr separately.
        - Honour the configured timeout without invoking a shell.

    :param args: Subprocess argument vector.
    :param config: Subprocess sandbox configuration.
    :param timeout_seconds: Optional timeout override for the subprocess.
    :returns: Structured subprocess execution result.
    """

    result = ExecutionResult(execution_target=config.execution_target)
    start = time.perf_counter()
    working_directory = Path(config.working_directory or Path.cwd())
    working_directory.mkdir(parents=True, exist_ok=True)
    env = None
    if config.environment:
        env = dict(os.environ)
        env.update(config.environment)
    try:
        completed = subprocess.run(
            args,
            cwd=working_directory,
            capture_output=True,
            text=True,
            timeout=timeout_seconds or config.timeout_seconds,
            env=env,
        )
        result.stdout = completed.stdout
        result.stderr = completed.stderr
        result.exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        result.stdout = exc.stdout or ""
        result.stderr = exc.stderr or ""
        result.exit_code = -1
        result.timed_out = True
        result.duration_seconds = time.perf_counter() - start
        return result
    except FileNotFoundError as exc:
        result.exit_code = -1
        result.stderr = str(exc)
        result.duration_seconds = time.perf_counter() - start
        return result
    result.duration_seconds = time.perf_counter() - start
    return result


def _validate_package_inputs(values: list[str], label: str) -> None:
    """
    Description:
        Validate package names before they are passed to a subprocess command.

    Requirements:
        - Reject shell-metacharacter input rather than attempting to escape it.

    :param values: Package names or package specifiers to validate.
    :param label: Human-readable item label used in validation errors.
    :raises ValueError: If any value contains shell-metacharacter input.
    """

    for value in values:
        if any(character in value for character in _INVALID_PACKAGE_CHARS):
            raise ValueError(f"Invalid {label} name: {value!r}")
