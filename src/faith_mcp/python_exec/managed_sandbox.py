"""
Description:
    Execute Python-tool actions inside PA-managed disposable sandbox containers.

Requirements:
    - Allocate sandbox containers through the shared sandbox manager.
    - Reuse shared sandboxes for routine Python work when isolation is unnecessary.
    - Fall back to local subprocess execution when the runtime is not a real Docker-backed container runtime.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path, PurePosixPath
from typing import Any

from faith_mcp.python_exec.sandbox import (
    ExecutionResult,
    SandboxConfig,
    execute_code,
    install_os_packages,
    install_packages,
)

_INVALID_PACKAGE_CHARS = set(";|&$`'\"\n\r")

_CODE_EXEC_WRAPPER = """
import base64
import io
import json
import os
import signal
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

payload = {
    "stdout": "",
    "stderr": "",
    "return_value": None,
    "traceback": None,
    "exit_code": 0,
    "timed_out": False,
    "duration_seconds": 0.0,
}

stdout_buffer = io.StringIO()
stderr_buffer = io.StringIO()
namespace = {}
code = base64.b64decode(os.environ["FAITH_USER_CODE_B64"]).decode("utf-8")
timeout_seconds = int(os.environ["FAITH_TIMEOUT_SECONDS"])
start = time.perf_counter()

def _timeout_handler(signum, frame):
    raise TimeoutError("Python execution timed out")

previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(timeout_seconds)
try:
    with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
        exec(compile(code, "<faith-user-code>", "exec"), namespace)
    if "result" in namespace:
        try:
            json.dumps(namespace["result"])
            payload["return_value"] = namespace["result"]
        except TypeError:
            payload["return_value"] = repr(namespace["result"])
except TimeoutError:
    payload["timed_out"] = True
    payload["exit_code"] = -1
except Exception:
    payload["traceback"] = traceback.format_exc()
    payload["exit_code"] = 1
finally:
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous_handler)
    payload["stdout"] = stdout_buffer.getvalue()
    payload["stderr"] = stderr_buffer.getvalue()
    payload["duration_seconds"] = time.perf_counter() - start

print(json.dumps(payload))
raise SystemExit(0 if payload["exit_code"] == 0 and not payload["timed_out"] else 1)
""".strip()

_COMMAND_EXEC_WRAPPER = """
import base64
import json
import os
import subprocess
import sys
import time

payload = json.loads(base64.b64decode(os.environ["FAITH_TOOL_PAYLOAD_B64"]).decode("utf-8"))
command = list(payload["command"])
timeout_seconds = int(payload["timeout_seconds"])
start = time.perf_counter()
result = {
    "stdout": "",
    "stderr": "",
    "return_value": None,
    "traceback": None,
    "exit_code": 0,
    "timed_out": False,
    "duration_seconds": 0.0,
}

try:
    completed = subprocess.run(
        command,
        cwd=os.getcwd(),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    result["stdout"] = completed.stdout
    result["stderr"] = completed.stderr
    result["exit_code"] = completed.returncode
except subprocess.TimeoutExpired as exc:
    result["stdout"] = exc.stdout or ""
    result["stderr"] = exc.stderr or ""
    result["exit_code"] = -1
    result["timed_out"] = True
except FileNotFoundError as exc:
    result["stderr"] = str(exc)
    result["exit_code"] = -1
finally:
    result["duration_seconds"] = time.perf_counter() - start

print(json.dumps(result))
raise SystemExit(0 if result["exit_code"] == 0 and not result["timed_out"] else 1)
""".strip()


class ManagedSandboxPythonRunner:
    """
    Description:
        Run Python-tool actions through the PA-managed disposable sandbox lifecycle.

    Requirements:
        - Create or reuse a shared sandbox for routine Python execution.
        - Execute actions inside the sandbox when a Docker-backed runtime is available.
        - Fall back to local subprocess execution for in-memory or non-Docker runtimes.

    :param sandbox_manager: Sandbox manager used to allocate and track sandboxes.
    """

    def __init__(self, *, sandbox_manager: Any) -> None:
        """
        Description:
            Store the sandbox manager dependency.

        Requirements:
            - Keep the runner lightweight so the executor can delegate to it directly.

        :param sandbox_manager: Sandbox manager used to allocate and track sandboxes.
        """

        self.sandbox_manager = sandbox_manager

    async def run(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        agent_id: str,
        working_directory: Path,
        allowed_paths: list[Path],
        timeout_seconds: int,
    ) -> ExecutionResult:
        """
        Description:
            Execute one Python-tool action through the managed sandbox path.

        Requirements:
            - Allocate or reuse a sandbox before execution begins.
            - Mount the relevant allowed workspace root into the sandbox.
            - Fall back to the local subprocess helpers when the runtime is not Docker-backed.

        :param action: Python-tool action name.
        :param payload: Structured action payload.
        :param agent_id: Agent requesting the action.
        :param working_directory: Host working directory for the action.
        :param allowed_paths: Allowed host-path roots for sandbox execution.
        :param timeout_seconds: Timeout budget for the action.
        :returns: Structured execution result.
        """

        mount_root = self._select_mount_root(working_directory, allowed_paths)
        sandbox_record = await self.sandbox_manager.allocate(
            self._build_request(
                agent_id=agent_id,
                working_directory=working_directory,
                mount_root=mount_root,
            )
        )
        container_workdir = self._container_working_directory(
            working_directory=working_directory,
            mount_root=mount_root,
            mount_target=PurePosixPath("/workspace"),
        )
        docker_client = self._docker_client()
        if docker_client is None:
            return self._run_local_fallback(
                action,
                payload,
                working_directory=working_directory,
                timeout_seconds=timeout_seconds,
            )
        return self._run_in_container(
            docker_client=docker_client,
            container_name=sandbox_record.container_name,
            action=action,
            payload=payload,
            container_workdir=container_workdir,
            timeout_seconds=timeout_seconds,
        )

    def _build_request(
        self,
        *,
        agent_id: str,
        working_directory: Path,
        mount_root: Path,
    ) -> Any:
        """
        Description:
            Build one sandbox allocation request for routine Python execution.

        Requirements:
            - Mount the selected host root into the canonical sandbox workspace path.
            - Use a stable shared session/task identity so routine work can reuse the sandbox.

        :param agent_id: Agent requesting the action.
        :param working_directory: Host working directory for the action.
        :param mount_root: Host path mounted into the sandbox.
        :returns: Sandbox allocation request instance.
        """

        from faith_pa.pa.sandbox_models import SandboxRequest

        return SandboxRequest(
            session_id="python-mcp",
            task_id="python-mcp-shared",
            agent_id=agent_id,
            workspace=str(working_directory),
            purpose="python-exec",
            approved_mounts={str(mount_root): "/workspace"},
        )

    def _select_mount_root(self, working_directory: Path, allowed_paths: list[Path]) -> Path:
        """
        Description:
            Select the host path root that should be mounted into the sandbox.

        Requirements:
            - Prefer the narrowest configured allowed path that still contains the working directory.
            - Fall back to the working directory itself when no allowed paths are configured.

        :param working_directory: Host working directory for the action.
        :param allowed_paths: Allowed host-path roots for sandbox execution.
        :returns: Host path root to mount into the sandbox.
        """

        containing_paths = []
        for allowed_path in allowed_paths:
            try:
                working_directory.relative_to(allowed_path)
                containing_paths.append(allowed_path)
            except ValueError:
                continue
        if not containing_paths:
            return working_directory
        return max(containing_paths, key=lambda item: len(str(item)))

    def _container_working_directory(
        self,
        *,
        working_directory: Path,
        mount_root: Path,
        mount_target: PurePosixPath,
    ) -> str:
        """
        Description:
            Translate a host working directory into its sandbox container path.

        Requirements:
            - Preserve subdirectory structure relative to the mounted host root.

        :param working_directory: Host working directory for the action.
        :param mount_root: Host path mounted into the sandbox.
        :param mount_target: Container mount target path.
        :returns: Sandbox container working-directory path.
        """

        relative_path = working_directory.relative_to(mount_root)
        if str(relative_path) == ".":
            return mount_target.as_posix()
        return (mount_target / relative_path.as_posix()).as_posix()

    def _docker_client(self) -> Any | None:
        """
        Description:
            Return the underlying Docker SDK client when the sandbox runtime is Docker-backed.

        Requirements:
            - Return ``None`` for in-memory or non-Docker runtimes so tests can use the local fallback.

        :returns: Docker SDK client, or ``None`` when unavailable.
        """

        runtime = getattr(self.sandbox_manager, "runtime", None)
        container_runtime = getattr(runtime, "client", None)
        docker_client = getattr(container_runtime, "client", None)
        if docker_client is None or not hasattr(docker_client, "containers"):
            return None
        return docker_client

    def _run_local_fallback(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        working_directory: Path,
        timeout_seconds: int,
    ) -> ExecutionResult:
        """
        Description:
            Execute one Python-tool action locally when the runtime is not Docker-backed.

        Requirements:
            - Preserve the structured behaviour of the existing subprocess helpers.
            - Mark the execution target as ``sandbox`` because the logical routing decision is still sandbox-first.

        :param action: Python-tool action name.
        :param payload: Structured action payload.
        :param working_directory: Host working directory for the action.
        :param timeout_seconds: Timeout budget for the action.
        :returns: Structured execution result.
        """

        config = SandboxConfig(
            timeout_seconds=timeout_seconds,
            working_directory=working_directory,
            execution_target="sandbox",
        )
        if action == "execute":
            return execute_code(str(payload["code"]), config)
        if action == "pip_install":
            return install_packages(list(payload.get("packages", [])), config)
        if action == "os_package_install":
            return install_os_packages(list(payload.get("packages", [])), config)
        raise ValueError(f"Unknown python execution action '{action}'")

    def _run_in_container(
        self,
        *,
        docker_client: Any,
        container_name: str,
        action: str,
        payload: dict[str, Any],
        container_workdir: str,
        timeout_seconds: int,
    ) -> ExecutionResult:
        """
        Description:
            Execute one Python-tool action inside a running sandbox container.

        Requirements:
            - Use the Docker exec API against the allocated sandbox container.
            - Return the structured JSON payload emitted by the wrapper code.

        :param docker_client: Docker SDK client used to locate the sandbox container.
        :param container_name: Sandbox container name.
        :param action: Python-tool action name.
        :param payload: Structured action payload.
        :param container_workdir: Container working-directory path.
        :param timeout_seconds: Timeout budget for the action.
        :returns: Structured execution result.
        """

        container = docker_client.containers.get(container_name)
        if action == "execute":
            environment = {
                "FAITH_USER_CODE_B64": base64.b64encode(
                    str(payload["code"]).encode("utf-8")
                ).decode("ascii"),
                "FAITH_TIMEOUT_SECONDS": str(timeout_seconds),
            }
            command = ["python", "-c", _CODE_EXEC_WRAPPER]
        elif action == "pip_install":
            self._validate_package_inputs(list(payload.get("packages", [])), "package")
            environment = {
                "FAITH_TOOL_PAYLOAD_B64": base64.b64encode(
                    json.dumps(
                        {
                            "command": [
                                "python",
                                "-m",
                                "pip",
                                "install",
                                "--no-input",
                                *list(payload.get("packages", [])),
                            ],
                            "timeout_seconds": timeout_seconds,
                        }
                    ).encode("utf-8")
                ).decode("ascii")
            }
            command = ["python", "-c", _COMMAND_EXEC_WRAPPER]
        elif action == "os_package_install":
            packages = list(payload.get("packages", []))
            self._validate_package_inputs(packages, "OS package")
            command_chain = [
                "python",
                "-c",
                _COMMAND_EXEC_WRAPPER,
            ]
            environment = {
                "FAITH_TOOL_PAYLOAD_B64": base64.b64encode(
                    json.dumps(
                        {
                            "command": [
                                "/bin/sh",
                                "-lc",
                                "apt-get update && apt-get install -y --no-install-recommends "
                                + " ".join(packages),
                            ],
                            "timeout_seconds": max(timeout_seconds, 120),
                        }
                    ).encode("utf-8")
                ).decode("ascii")
            }
            command = command_chain
        else:
            raise ValueError(f"Unknown python execution action '{action}'")

        exit_code, output = container.exec_run(
            command,
            workdir=container_workdir,
            environment=environment,
            demux=True,
        )
        stdout_bytes, stderr_bytes = output if output is not None else (b"", b"")
        stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")
        try:
            payload_data = json.loads(stdout_text or "{}")
        except json.JSONDecodeError:
            return ExecutionResult(
                stdout=stdout_text,
                stderr=stderr_text,
                traceback=None,
                exit_code=exit_code,
                timed_out=False,
                duration_seconds=0.0,
                execution_target="sandbox",
            )
        payload_data["execution_target"] = "sandbox"
        if stderr_text:
            payload_data["stderr"] = f"{payload_data.get('stderr', '')}{stderr_text}"
        return ExecutionResult(**payload_data)

    def _validate_package_inputs(self, values: list[str], label: str) -> None:
        """
        Description:
            Validate package names before sending them into the sandbox command wrapper.

        Requirements:
            - Reject shell-metacharacter input rather than passing dangerous values to the sandbox shell.

        :param values: Package names or specifiers to validate.
        :param label: Human-readable item label used in validation errors.
        :raises ValueError: If any value contains shell-metacharacter input.
        """

        for value in values:
            if any(character in value for character in _INVALID_PACKAGE_CHARS):
                raise ValueError(f"Invalid {label} name: {value!r}")
