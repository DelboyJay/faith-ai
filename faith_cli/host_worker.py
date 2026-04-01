"""User-scoped host worker helpers for the FAITH CLI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class HostWorkerStatus:
    enabled: bool = False
    running: bool = False
    mode: str = "disabled"


def get_host_worker_status() -> HostWorkerStatus:
    """Return the current host-worker status for the POC.

    The host worker is not wired into the current PoC yet, so the CLI reports
    a disabled user-scoped worker rather than pretending it is active.
    """

    return HostWorkerStatus()
