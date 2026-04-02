"""Models for disposable sandbox scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SandboxAllocationMode(str, Enum):
    SHARED = "shared"
    ISOLATED = "isolated"


class SandboxState(str, Enum):
    CREATING = "creating"
    READY = "ready"
    RUNNING = "running"
    RESETTING = "resetting"
    DESTROYED = "destroyed"


SandboxMode = SandboxAllocationMode
SandboxStatus = SandboxState


@dataclass(slots=True)
class SandboxQuota:
    max_concurrent: int = 4


ResourceQuota = SandboxQuota


@dataclass(slots=True)
class SandboxRequest:
    session_id: str
    task_id: str
    agent_id: str
    workspace: str = ""
    mode: SandboxMode | None = None
    purpose: str = "workspace"
    requires_isolation: bool = False
    destructive: bool = False

    def __post_init__(self) -> None:
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
        return self.allocation_mode

    @property
    def status(self) -> SandboxStatus:
        return (
            SandboxStatus.RUNNING
            if self.state is SandboxState.READY
            else SandboxStatus(self.state.value)
        )

    @property
    def reused(self) -> bool:
        return self.reuse_count > 0
