"""Description:
    Re-export the primary runtime services used by the Project Agent package.

Requirements:
    - Provide a stable import surface for PA orchestration components.
    - Avoid eager imports that can trigger circular dependencies during module loading.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AgentState",
    "ContainerManager",
    "FRSManager",
    "InterventionHandler",
    "LoopDetectionResult",
    "LoopDetector",
    "MCPAdapter",
    "PAEventDispatcher",
    "ProjectSwitcher",
    "SandboxManager",
    "SecretResolver",
    "Session",
    "SessionManager",
    "Task",
    "ToolRouter",
]

_EXPORTS: dict[str, str] = {
    "AgentState": "faith_pa.pa.session",
    "ContainerManager": "faith_pa.pa.container_manager",
    "FRSManager": "faith_pa.pa.frs_manager",
    "InterventionHandler": "faith_pa.pa.intervention",
    "LoopDetectionResult": "faith_pa.pa.loop_detector",
    "LoopDetector": "faith_pa.pa.loop_detector",
    "MCPAdapter": "faith_pa.pa.mcp_adapter",
    "PAEventDispatcher": "faith_pa.pa.event_dispatcher",
    "ProjectSwitcher": "faith_pa.pa.project_switcher",
    "SandboxManager": "faith_pa.pa.sandbox_manager",
    "SecretResolver": "faith_pa.pa.secret_resolver",
    "Session": "faith_pa.pa.session",
    "SessionManager": "faith_pa.pa.session",
    "Task": "faith_pa.pa.session",
    "ToolRouter": "faith_pa.pa.tool_router",
}


def __getattr__(name: str) -> Any:
    """Description:
        Lazily import one PA runtime export on first access.

    Requirements:
        - Avoid eager import chains that can create circular dependencies.

    :param name: Exported attribute name.
    :returns: Requested PA runtime object.
    :raises AttributeError: If the requested name is not exported.
    """

    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'faith_pa.pa' has no attribute '{name}'")
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
