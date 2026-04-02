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
    RUNNING = "running"
    RESETTING = "resetting"
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
    """

    max_concurrent: int = 4


ResourceQuota = SandboxQuota


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
    """

    session_id: str
    task_id: str
    agent_id: str
    workspace: str = ""
    mode: SandboxMode | None = None
    purpose: str = "workspace"
    requires_isolation: bool = False
    destructive: bool = False

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
