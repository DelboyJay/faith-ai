"""Description:
    Manage external MCP server registrations and session-scoped stdio lifecycle.

Requirements:
    - Load version-pinned `external-*.yaml` registrations from the project `.faith/tools` directory.
    - Enforce enablement, privacy-profile, and agent-assignment rules before launch.
    - Start the shared `mcp-runtime` before launching external stdio subprocesses.
    - Resolve external MCP environment values and secret references through the PA secret resolver.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from faith_pa.pa.secret_resolver import SecretResolver
from faith_shared.config.models import ExternalMCPToolConfig, PrivacyProfile

DEFAULT_MCP_RUNTIME_NAME = "faith-mcp-runtime"
DEFAULT_REGISTRY_URL = "http://mcp-registry:8080"


class ProcessLauncher(Protocol):
    """Description:
        Describe the async launcher interface used to start external MCP subprocesses.

    Requirements:
        - Accept command segments and a resolved environment mapping.
        - Return an object exposing stdin, stdout, stderr, wait, terminate, and kill.
    """

    async def __call__(self, *cmd: str, env: dict[str, str]) -> Any:
        """Description:
        Launch one external MCP subprocess.

        Requirements:
            - Preserve the supplied command and environment unchanged.

        :param cmd: Command-line segments to execute.
        :param env: Fully resolved process environment.
        :returns: Async subprocess-compatible process object.
        """


class RegistryResolver(Protocol):
    """Description:
        Describe the registry-resolution interface used before launching an external MCP server.

    Requirements:
        - Resolve a registry reference and pinned version into package metadata suitable for launch.
        - Allow FAITH to swap in a self-hosted MCP Registry client without changing manager callers.
    """

    async def __call__(self, registry_ref: str, package_version: str) -> dict[str, str]:
        """Description:
        Resolve one registry-backed MCP server registration.

        Requirements:
            - Preserve the registry reference and pinned version in the returned metadata.
            - Return a package identifier suitable for `npx` launch.

        :param registry_ref: Registry reference declared in the tool config.
        :param package_version: Pinned package version declared in the tool config.
        :returns: Registry metadata including a launchable `package` identifier.
        """


@dataclass(slots=True)
class ExternalMCPServer:
    """Description:
        Represent one registered external MCP server and its current runtime state.

    Requirements:
        - Preserve config, source path, resolved environment, and optional process/session state.

    :param name: Logical server name derived from the config file name.
    :param config: Validated external MCP config.
    :param source_path: Source config file path.
    :param process: Running subprocess when started.
    :param resolved_env: Resolved environment used for the most recent start.
    :param session_id: Session currently owning the running subprocess.
    """

    name: str
    config: ExternalMCPToolConfig
    source_path: Path
    process: Any | None = None
    resolved_env: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None

    @property
    def is_running(self) -> bool:
        """Description:
            Return whether the external MCP subprocess is currently running.

        Requirements:
            - Treat any process with a `None` return code as running.

        :returns: `True` when the subprocess is still active.
        """

        return self.process is not None and getattr(self.process, "returncode", None) is None

    async def stop(self) -> None:
        """Description:
            Stop the external MCP subprocess gracefully and clear session ownership.

        Requirements:
            - Send terminate first and wait for exit.
            - Fall back to kill if the process does not exit before timeout.
            - Clear the stored process and session state on completion.
        """

        if self.process is None:
            self.session_id = None
            return
        process = self.process
        if getattr(process, "returncode", None) is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        self.process = None
        self.session_id = None


class ExternalMCPManager:
    """Description:
        Manage external MCP registration, policy checks, and session-scoped stdio lifecycle.

    Requirements:
        - Load version-pinned external MCP tool configs from `.faith/tools`.
        - Start the shared `mcp-runtime` before launching stdio subprocesses.
        - Preserve running process state across config reloads for unchanged registrations.
        - Expose a transport lookup for the MCP adapter layer.

    :param faith_dir: Project `.faith` directory containing external tool configs.
    :param secret_resolver: Secret resolver used for environment and secret-ref expansion.
    :param active_privacy_profile: Active project privacy profile.
    :param launcher: Optional async launcher used for stdio subprocess execution.
    :param container_manager: Optional container manager used to start `mcp-runtime`.
    :param registry_url: Registry base URL exposed to runtime subprocesses.
    """

    def __init__(
        self,
        *,
        faith_dir: Path,
        secret_resolver: SecretResolver,
        active_privacy_profile: PrivacyProfile | str = PrivacyProfile.INTERNAL,
        launcher: ProcessLauncher | None = None,
        container_manager: Any | None = None,
        registry_url: str = DEFAULT_REGISTRY_URL,
        registry_resolver: RegistryResolver | None = None,
    ) -> None:
        """Description:
            Initialise the external MCP manager.

        Requirements:
            - Normalise the active privacy profile into the shared enum.
            - Use the default stdio launcher when none is supplied.

        :param faith_dir: Project `.faith` directory containing external tool configs.
        :param secret_resolver: Secret resolver used for environment and secret-ref expansion.
        :param active_privacy_profile: Active project privacy profile.
        :param launcher: Optional async launcher used for stdio subprocess execution.
        :param container_manager: Optional container manager used to start `mcp-runtime`.
        :param registry_url: Registry base URL exposed to runtime subprocesses.
        :param registry_resolver: Optional registry resolver used to turn registry references into launch metadata.
        """

        self.faith_dir = Path(faith_dir).resolve()
        self.secret_resolver = secret_resolver
        self.active_privacy_profile = PrivacyProfile(active_privacy_profile)
        self.launcher = launcher or self._default_launcher
        self.container_manager = container_manager
        self.registry_url = registry_url
        self.registry_resolver = registry_resolver or self._default_registry_resolver
        self._servers: dict[str, ExternalMCPServer] = {}
        self._runtime_started = False

    def load_configs(self) -> int:
        """Description:
            Load all external MCP registrations from `.faith/tools/external-*.yaml`.

        Requirements:
            - Replace the in-memory registration table with the freshly parsed configs.
            - Use the file stem after `external-` as the stable server name.

        :returns: Number of registered external MCP servers.
        """

        loaded: dict[str, ExternalMCPServer] = {}
        for path in sorted((self.faith_dir / "tools").glob("external-*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            config = ExternalMCPToolConfig.model_validate(data)
            name = path.stem.removeprefix("external-")
            loaded[name] = ExternalMCPServer(name=name, config=config, source_path=path)
        self._servers = loaded
        return len(self._servers)

    def reload_configs(self) -> int:
        """Description:
            Reload external MCP registrations while preserving running state for unchanged names.

        Requirements:
            - Carry forward process, resolved environment, and session state for unchanged registrations.

        :returns: Number of registered external MCP servers after reload.
        """

        existing = dict(self._servers)
        self.load_configs()
        for name, server in self._servers.items():
            previous = existing.get(name)
            if previous is None:
                continue
            server.process = previous.process
            server.resolved_env = dict(previous.resolved_env)
            server.session_id = previous.session_id
        return len(self._servers)

    def get_server(self, name: str) -> ExternalMCPServer | None:
        """Description:
            Return one registered external MCP server by name.

        Requirements:
            - Return `None` when the registration does not exist.

        :param name: External MCP server name.
        :returns: Registered server state when present.
        """

        return self._servers.get(name)

    def _privacy_rank(self, profile: PrivacyProfile) -> int:
        """Description:
            Convert a privacy profile into its restrictiveness rank.

        Requirements:
            - Treat `public` as least restrictive and `confidential` as most restrictive.

        :param profile: Privacy profile to rank.
        :returns: Numeric restrictiveness rank.
        """

        ordering = {
            PrivacyProfile.PUBLIC: 0,
            PrivacyProfile.INTERNAL: 1,
            PrivacyProfile.CONFIDENTIAL: 2,
        }
        return ordering[profile]

    def is_privacy_allowed(self, name: str) -> bool:
        """Description:
            Return whether the named server is allowed under the active privacy profile.

        Requirements:
            - Allow servers only when the active profile is equally or less restrictive than the server tier.

        :param name: External MCP server name.
        :returns: `True` when the server may run under the active privacy profile.
        """

        server = self._servers.get(name)
        if server is None:
            return False
        active = self._privacy_rank(self.active_privacy_profile)
        required = self._privacy_rank(server.config.privacy_tier)
        return active <= required

    def get_servers_for_agent(self, agent_id: str) -> list[str]:
        """Description:
            Return the external MCP servers usable by one agent under the current policy state.

        Requirements:
            - Exclude disabled registrations.
            - Exclude servers blocked by the active privacy profile.
            - Return names in stable sorted order.

        :param agent_id: Agent identifier.
        :returns: Sorted list of usable server names.
        """

        allowed: list[str] = []
        for name, server in sorted(self._servers.items()):
            if not server.config.enabled:
                continue
            if agent_id not in server.config.agents:
                continue
            if not self.is_privacy_allowed(name):
                continue
            allowed.append(name)
        return allowed

    def _build_package_spec(self, package_name: str, package_version: str) -> str:
        """Description:
            Build the version-pinned package spec for one external MCP registration.

        Requirements:
            - Always append the pinned version to the registry/package reference.

        :param package_name: Launchable npm package identifier.
        :param package_version: Pinned package version.
        :returns: Version-pinned package spec for `npx`.
        """

        return f"{package_name}@{package_version}"

    def _build_command(self, package_spec: str) -> list[str]:
        """Description:
            Build the stdio launch command for one external MCP registration.

        Requirements:
            - Use `npx -y` with the version-pinned package reference for v1.

        :param package_spec: Version-pinned package specification.
        :returns: Command list used to launch the stdio subprocess.
        """

        return ["npx", "-y", package_spec]

    async def _default_registry_resolver(
        self,
        registry_ref: str,
        package_version: str,
    ) -> dict[str, str]:
        """Description:
            Resolve one registry-backed external MCP server using the current local fallback policy.

        Requirements:
            - Preserve the registry reference and pinned version for downstream audit and launch decisions.
            - Return a launchable package identifier even when no live registry client is configured yet.

        :param registry_ref: Registry reference declared in the tool config.
        :param package_version: Pinned package version declared in the tool config.
        :returns: Minimal registry metadata for launch.
        """

        return {
            "registry_ref": registry_ref,
            "package_version": package_version,
            "package": registry_ref,
        }

    async def _default_launcher(self, *cmd: str, env: dict[str, str]) -> Any:
        """Description:
            Launch one real stdio subprocess using asyncio.

        Requirements:
            - Expose stdin, stdout, and stderr pipes for MCP stdio routing.

        :param cmd: Command-line segments to execute.
        :param env: Fully resolved process environment.
        :returns: Async subprocess object.
        """

        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

    async def _ensure_runtime(self, workspace_path: Path | None) -> None:
        """Description:
            Ensure the shared `mcp-runtime` is started before launching subprocesses.

        Requirements:
            - Start the runtime at most once per manager instance.
            - Pass the full current external MCP registration table to the runtime startup call.

        :param workspace_path: Project workspace path used for runtime startup.
        """

        if self._runtime_started or self.container_manager is None or workspace_path is None:
            return
        external_tools = {
            f"external-{name}": server.config.model_dump(mode="python")
            for name, server in sorted(self._servers.items())
        }
        await self.container_manager.start_mcp_runtime(
            external_tools=external_tools,
            workspace_path=Path(workspace_path).resolve(),
        )
        self._runtime_started = True

    async def start_server(self, name: str, *, session_id: str) -> bool:
        """Description:
            Start one external MCP stdio subprocess when policy permits it.

        Requirements:
            - Reject unknown, disabled, or privacy-blocked registrations.
            - Resolve environment substitutions and secret references before launch.
            - Reuse already-running subprocesses without relaunching them.

        :param name: External MCP server name.
        :param session_id: Session taking ownership of the started server.
        :returns: `True` when the server is running after the call.
        """

        server = self._servers.get(name)
        if server is None or not server.config.enabled or not self.is_privacy_allowed(name):
            return False
        if server.is_running:
            return True

        resolved_env = self.secret_resolver.resolve_environment(
            env=server.config.env,
            env_secret_refs=server.config.env_secret_refs,
        )
        resolved_package = await self.registry_resolver(
            server.config.registry_ref,
            server.config.package_version,
        )
        process_env = dict(os.environ)
        process_env.update(resolved_env)
        process_env.setdefault("MCP_REGISTRY_URL", self.registry_url)
        package_name = resolved_package.get("package", server.config.registry_ref)
        package_spec = self._build_package_spec(package_name, server.config.package_version)
        process = await self.launcher(*self._build_command(package_spec), env=process_env)
        server.process = process
        server.resolved_env = process_env
        server.session_id = session_id
        return True

    async def start_servers_for_session(
        self,
        *,
        agent_ids: list[str],
        session_id: str,
        workspace_path: Path | None = None,
    ) -> dict[str, bool]:
        """Description:
            Start every external MCP server required by the supplied agent roster.

        Requirements:
            - Start the shared `mcp-runtime` once before launching subprocesses.
            - Launch only the servers needed by at least one of the supplied agents.

        :param agent_ids: Agent identifiers participating in the session.
        :param session_id: Session taking ownership of the started servers.
        :param workspace_path: Project workspace path used for runtime startup.
        :returns: Start result mapping keyed by external MCP server name.
        """

        needed = sorted(
            {name for agent_id in agent_ids for name in self.get_servers_for_agent(agent_id)}
        )
        if needed:
            await self._ensure_runtime(workspace_path)
        results: dict[str, bool] = {}
        for name in needed:
            results[name] = await self.start_server(name, session_id=session_id)
        return results

    async def stop_server(self, name: str) -> bool:
        """Description:
            Stop one registered external MCP subprocess.

        Requirements:
            - Return `False` when the registration is unknown.

        :param name: External MCP server name.
        :returns: `True` when the registration existed and stop logic ran.
        """

        server = self._servers.get(name)
        if server is None:
            return False
        await server.stop()
        return True

    async def stop_session_servers(self, session_id: str) -> int:
        """Description:
            Stop every running external MCP server owned by one session.

        Requirements:
            - Leave other sessions' servers untouched.

        :param session_id: Session identifier to stop.
        :returns: Number of servers stopped for the session.
        """

        stopped = 0
        for server in self._servers.values():
            if server.session_id != session_id:
                continue
            await server.stop()
            stopped += 1
        return stopped

    async def stop_all(self) -> None:
        """Description:
            Stop every running external MCP subprocess.

        Requirements:
            - Clear the runtime-started flag so a future session can restart the shared runtime.
        """

        for server in self._servers.values():
            await server.stop()
        self._runtime_started = False

    def get_server_stdio(self, name: str) -> tuple[Any, Any] | None:
        """Description:
            Return the stdin/stdout transport pair for one running external MCP server.

        Requirements:
            - Return `None` when the server is unknown or not currently running.

        :param name: External MCP server name.
        :returns: `(stdin, stdout)` transport tuple when available.
        """

        server = self._servers.get(name)
        if server is None or not server.is_running:
            return None
        return (server.process.stdin, server.process.stdout)

    def list_servers(self) -> list[dict[str, Any]]:
        """Description:
            Return a UI-friendly summary of registered external MCP servers.

        Requirements:
            - Include enablement, privacy, agent assignments, version pinning, running state, and rollback metadata fields.

        :returns: Sorted external MCP server summaries.
        """

        listing: list[dict[str, Any]] = []
        for name, server in sorted(self._servers.items()):
            listing.append(
                {
                    "name": name,
                    "registry_ref": server.config.registry_ref,
                    "package_version": server.config.package_version,
                    "previous_package_version": None,
                    "enabled": server.config.enabled,
                    "privacy_tier": server.config.privacy_tier.value,
                    "privacy_allowed": self.is_privacy_allowed(name),
                    "agents": list(server.config.agents),
                    "env_keys": sorted(
                        server.config.env.keys() | server.config.env_secret_refs.keys()
                    ),
                    "running": server.is_running,
                    "session_id": server.session_id,
                    "install_status": "running" if server.is_running else "registered",
                    "available_update_version": None,
                }
            )
        return listing


__all__ = [
    "DEFAULT_MCP_RUNTIME_NAME",
    "DEFAULT_REGISTRY_URL",
    "ExternalMCPManager",
    "ExternalMCPServer",
]
