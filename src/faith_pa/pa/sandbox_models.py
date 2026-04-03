"""Description:
    Define the data models used for disposable sandbox allocation and tracking.

Requirements:
    - Represent sandbox allocation mode, lifecycle state, quotas, requests, and records.
    - Keep the models lightweight so they can be shared across scheduler components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SandboxAllocationMode(str, Enum):
    """Description:
        Enumerate the supported sandbox allocation modes.

    Requirements:
        - Distinguish between shared and isolated sandbox scheduling.
    """

    SHARED = "shared"
    ISOLATED = "isolated"


class SandboxState(str, Enum):
    """Description:
        Enumerate the lifecycle states for one sandbox record.

    Requirements:
        - Cover creation, ready, running, reset, and destroyed states.
    """

    CREATING = "creating"
    READY = "ready"
    BUSY = "busy"
    RUNNING = "running"
    RESETTING = "resetting"
    FAILED = "failed"
    DESTROYED = "destroyed"


SandboxMode = SandboxAllocationMode
SandboxStatus = SandboxState


@dataclass(slots=True)
class SandboxQuota:
    """Description:
        Represent the basic quota controls for sandbox scheduling.

    Requirements:
        - Limit the number of concurrently allocated sandboxes.

    :param max_concurrent: Maximum number of concurrently allocated sandboxes.
    :param cpu_limit: Optional CPU limit applied to one sandbox.
    :param memory_mb: Optional memory limit applied to one sandbox in megabytes.
    :param disk_mb: Optional disk-budget hint applied to one sandbox in megabytes.
    """

    max_concurrent: int = 4
    cpu_limit: float | None = None
    memory_mb: int | None = None
    disk_mb: int | None = None


ResourceQuota = SandboxQuota


@dataclass(slots=True)
class SandboxPolicy:
    """Description:
        Define the hard isolation policy attached to one disposable sandbox.

    Requirements:
        - Preserve approved mounts, network mode, privilege policy, Docker socket policy, and Linux capability allow-list.
        - Provide the resource limits used when scheduling or creating the sandbox.

    :param approved_mounts: Explicitly approved host-to-container mounts.
    :param network_mode: Sandbox network mode. Host networking is never allowed for disposable sandboxes.
    :param privileged: Whether privileged mode is allowed.
    :param docker_socket_allowed: Whether the Docker socket may be mounted.
    :param linux_capabilities: Minimal Linux capability allow-list for the sandbox.
    :param cpu_limit: Optional CPU limit applied to the sandbox.
    :param memory_mb: Optional memory limit applied to the sandbox in megabytes.
    :param disk_mb: Optional disk-budget hint applied to the sandbox in megabytes.
    """

    approved_mounts: dict[str, str] = field(default_factory=dict)
    network_mode: str = "bridge"
    privileged: bool = False
    docker_socket_allowed: bool = False
    linux_capabilities: list[str] = field(default_factory=list)
    cpu_limit: float | None = None
    memory_mb: int | None = None
    disk_mb: int | None = None


@dataclass(slots=True)
class SandboxRequest:
    """Description:
        Describe one sandbox allocation request from the scheduler.

    Requirements:
        - Capture the owning session, task, and agent identity.
        - Derive the effective allocation mode when one is not supplied explicitly.

    :param session_id: Owning session identifier.
    :param task_id: Owning task identifier.
    :param agent_id: Owning agent identifier.
    :param workspace: Logical workspace label for the request.
    :param mode: Explicit sandbox mode when supplied.
    :param purpose: Logical purpose label for the sandbox.
    :param requires_isolation: Whether the request explicitly requires isolation.
    :param destructive: Whether the planned work is destructive enough to require isolation.
    :param approved_mounts: Explicitly approved host-to-container mounts for the sandbox.
    :param linux_capabilities: Minimal Linux capability allow-list for the sandbox.
    """

    session_id: str
    task_id: str
    agent_id: str
    workspace: str = ""
    mode: SandboxMode | None = None
    purpose: str = "workspace"
    requires_isolation: bool = False
    destructive: bool = False
    approved_mounts: dict[str, str] = field(default_factory=dict)
    linux_capabilities: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Description:
            Normalise the derived workspace and allocation fields after initialisation.

        Requirements:
            - Default the mode to isolated when the request is destructive or requires isolation.
            - Mark the request as isolation-required when the final mode is isolated.
            - Reuse the workspace label as the purpose when the default purpose is still in place.
        """

        if self.mode is None:
            self.mode = (
                SandboxMode.ISOLATED
                if (self.requires_isolation or self.destructive)
                else SandboxMode.SHARED
            )
        self.requires_isolation = self.requires_isolation or self.mode is SandboxMode.ISOLATED
        if not self.workspace:
            self.workspace = self.purpose
        if self.purpose == "workspace" and self.workspace:
            self.purpose = self.workspace


@dataclass(slots=True)
class SandboxRecord:
    """Description:
        Represent one tracked sandbox allocation owned by the scheduler.

    Requirements:
        - Preserve ownership, lifecycle, image, and reuse state for the sandbox.
        - Expose convenience properties for compatibility with older field names.

    :param sandbox_id: Unique sandbox identifier.
    :param session_id: Owning session identifier.
    :param task_id: Owning task identifier.
    :param workspace: Logical workspace label.
    :param purpose: Logical sandbox purpose label.
    :param allocation_mode: Effective allocation mode for the sandbox.
    :param state: Current sandbox lifecycle state.
    :param image: Sandbox image reference.
    :param container_name: Docker container name for the sandbox.
    :param agents: Agent identifiers currently attached to the sandbox.
    :param reuse_count: Number of times the sandbox has been reused.
    :param policy: Hardened sandbox isolation policy.
    """

    sandbox_id: str
    session_id: str
    task_id: str
    workspace: str
    purpose: str
    allocation_mode: SandboxAllocationMode
    state: SandboxState
    image: str
    container_name: str
    agents: set[str] = field(default_factory=set)
    reuse_count: int = 0
    policy: SandboxPolicy = field(default_factory=SandboxPolicy)

    @property
    def mode(self) -> SandboxMode:
        """Description:
            Return the effective sandbox allocation mode.

        Requirements:
            - Preserve compatibility with callers expecting ``mode``.

        :returns: Effective sandbox allocation mode.
        """

        return self.allocation_mode

    @property
    def status(self) -> SandboxStatus:
        """Description:
            Return the externally reported sandbox status value.

        Requirements:
            - Report ``running`` when the internal state is ``ready``.
            - Otherwise mirror the internal lifecycle state value.

        :returns: External sandbox status value.
        """

        return (
            SandboxStatus.RUNNING
            if self.state is SandboxState.READY
            else SandboxStatus(self.state.value)
        )

    @property
    def reused(self) -> bool:
        """Description:
            Return whether the sandbox has been reused after initial allocation.

        Requirements:
            - Treat any reuse count above zero as reused.

        :returns: ``True`` when the sandbox has been reused.
        """

        return self.reuse_count > 0
