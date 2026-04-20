"""Description:
    Manage the optional user-scoped FAITH host worker process.

Requirements:
    - Keep the worker user-scoped and never elevate privileges automatically.
    - Allow the CLI to start, stop, and inspect the worker when enabled.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from faith_cli.paths import host_worker_log_file, host_worker_pid_file, logs_dir


@dataclass(slots=True)
class HostWorkerStatus:
    """Description:
        Represent the current host-worker runtime status.

    Requirements:
        - Report whether the worker is enabled and currently running.
        - Preserve the current operating mode for CLI status output.

    :param enabled: Whether the host worker is enabled.
    :param running: Whether the host worker is currently running.
    :param mode: Human-readable mode string.
    :param pid: Worker process identifier when available.
    """

    enabled: bool = False
    running: bool = False
    mode: str = "disabled"
    pid: int | None = None


def _enabled() -> bool:
    """Description:
        Return whether the optional host worker is enabled.

    Requirements:
        - Treat common truthy strings as enabled.
        - Default to disabled when no explicit flag is present.

    :returns: ``True`` when host-worker execution is enabled.
    """

    return os.environ.get("FAITH_ENABLE_HOST_WORKER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _pid_file() -> Path:
    """Description:
        Return the pid file used by the host worker.

    Requirements:
        - Keep worker state under the extracted FAITH home directory.

    :returns: Host-worker pid file path.
    """

    return host_worker_pid_file()


def _read_pid() -> int | None:
    """Description:
        Read the current host-worker pid from disk.

    Requirements:
        - Return ``None`` when the pid file does not exist or is invalid.

    :returns: Worker pid when present, otherwise ``None``.
    """

    pid_path = _pid_file()
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _is_running(pid: int | None) -> bool:
    """Description:
        Return whether a process identifier still appears to be alive.

    Requirements:
        - Return ``False`` when no pid is available.
        - Treat permission errors as evidence that the process still exists.
        - Treat other Windows probe errors as stale or unprobeable pid values.

    :param pid: Candidate process identifier.
    :returns: ``True`` when the process appears to be alive.
    """

    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False
    return True


def get_host_worker_status() -> HostWorkerStatus:
    """Description:
        Return the current host-worker status for CLI reporting.

    Requirements:
        - Reflect the enabled flag even when the worker is not running.
        - Remove stale pid files when the recorded process is gone.

    :returns: Host-worker status payload.
    """

    enabled = _enabled()
    pid = _read_pid()
    running = _is_running(pid)
    if pid is not None and not running and _pid_file().exists():
        _pid_file().unlink(missing_ok=True)
        pid = None
    mode = "user-scoped" if enabled else "disabled"
    return HostWorkerStatus(enabled=enabled, running=running, mode=mode, pid=pid)


def start_host_worker() -> HostWorkerStatus:
    """Description:
        Start the optional persistent host worker when enabled.

    Requirements:
        - Leave the worker disabled when the feature flag is not enabled.
        - Reuse the existing worker when one is already running.
        - Launch the worker as the current user with no elevation.

    :returns: Updated host-worker status.
    """

    status = get_host_worker_status()
    if not status.enabled or status.running:
        return status

    logs_dir().mkdir(parents=True, exist_ok=True)
    log_handle = host_worker_log_file().open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [sys.executable, "-m", "faith_cli.host_worker", "--run"],
        stdout=log_handle,
        stderr=log_handle,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=False,
    )
    _pid_file().write_text(str(process.pid), encoding="utf-8")
    time.sleep(0.2)
    return get_host_worker_status()


def stop_host_worker() -> HostWorkerStatus:
    """Description:
        Stop the optional persistent host worker when it is running.

    Requirements:
        - Tolerate already-stopped workers and stale pid files.
        - Remove the pid file after the worker exits.

    :returns: Updated host-worker status after the stop attempt.
    """

    pid = _read_pid()
    if pid is None:
        return get_host_worker_status()

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError, SystemError):
        _pid_file().unlink(missing_ok=True)
        return get_host_worker_status()

    for _ in range(20):
        if not _is_running(pid):
            break
        time.sleep(0.1)

    _pid_file().unlink(missing_ok=True)
    return get_host_worker_status()


def _worker_loop() -> None:
    """Description:
        Run the persistent host-worker loop for the current user session.

    Requirements:
        - Keep the worker alive until a termination signal is received.
        - Clean up the pid file on shutdown.
    """

    pid_path = _pid_file()
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    active = True

    def _stop(*_args) -> None:
        """Description:
            Mark the host-worker loop for shutdown after a signal is received.

        Requirements:
            - Stop the outer loop without raising from the signal handler.
        """

        nonlocal active
        active = False

    signal.signal(signal.SIGTERM, _stop)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _stop)

    try:
        while active:
            time.sleep(1)
    finally:
        pid_path.unlink(missing_ok=True)


if __name__ == "__main__" and "--run" in sys.argv:
    _worker_loop()
