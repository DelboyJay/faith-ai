"""Description:
    Manage approval requests, decisions, and learned rule persistence for FAITH.

Requirements:
    - Create structured approval requests for user-facing approval flows.
    - Persist learned approval rules back into ``security.yaml`` when required.
    - Publish approval request and decision events for the rest of the runtime.
    - Record audit entries when approval decisions are resolved.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from faith_pa.security.approval_engine import ApprovalEngine
from faith_pa.security.audit_log import AuditLogger
from faith_shared.protocol.events import EventType, FaithEvent


class UserApprovalDecision(str, Enum):
    """Description:
        Enumerate the user decisions supported by the approval flow.

    Requirements:
        - Cover one-off, session, persistent allow, persistent ask, and denial outcomes.
    """

    ALLOW_ONCE = "allow_once"
    APPROVE_SESSION = "approve_session"
    ALWAYS_ALLOW = "always_allow"
    ALWAYS_ASK = "always_ask"
    DENY_ONCE = "deny_once"
    DENY_PERMANENTLY = "deny_permanently"


PERMANENT_RULE_SECTION = {
    UserApprovalDecision.ALWAYS_ALLOW: "always_allow_learned",
    UserApprovalDecision.ALWAYS_ASK: "always_ask_learned",
    UserApprovalDecision.DENY_PERMANENTLY: "always_deny_learned",
}


@dataclass(slots=True)
class ApprovalRequest:
    """Description:
        Represent one pending approval request awaiting a user decision.

    Requirements:
        - Preserve request identity, action details, routing metadata, and generated rules.

    :param request_id: Unique approval request identifier.
    :param agent_id: Agent requesting approval.
    :param tool: Tool involved in the request.
    :param action: Action verb being requested.
    :param target: Optional action target such as a path or command.
    :param detail: Optional human-readable detail text.
    :param channel: Optional event channel associated with the request.
    :param msg_id: Optional message identifier associated with the request.
    :param created_at: Request creation timestamp.
    :param resolved: Whether the request has been resolved.
    :param decision: Final user decision when resolved.
    :param generated_rule: Generated persistent rule, when relevant.
    """

    request_id: str
    agent_id: str
    tool: str
    action: str
    target: str = ""
    detail: str = ""
    channel: str | None = None
    msg_id: int | None = None
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    decision: UserApprovalDecision | None = None
    generated_rule: str | None = None


class ApprovalFlow:
    """Description:
        Coordinate approval requests, user decisions, and learned-rule persistence.

    Requirements:
        - Track pending approval requests and waiting futures.
        - Publish request and decision events through the configured event publisher.
        - Persist learned rules and reload the approval engine when permanent decisions are made.
        - Write audit entries for resolved decisions when an audit logger is configured.

    :param approval_engine: Approval engine used for session-memory and rule reload support.
    :param security_yaml_path: Path to the project ``security.yaml`` file.
    :param event_publisher: Optional runtime event publisher.
    :param audit_logger: Optional audit logger for decision recording.
    """

    def __init__(
        self,
        *,
        approval_engine: ApprovalEngine,
        security_yaml_path: Path,
        event_publisher: Any = None,
        audit_logger: AuditLogger | None = None,
    ):
        """Description:
            Initialise the approval flow state.

        Requirements:
            - Start with empty pending-request and future maps.
            - Preserve the supplied approval engine, security file path, and optional integrations.

        :param approval_engine: Approval engine used for session-memory and rule reload support.
        :param security_yaml_path: Path to the project ``security.yaml`` file.
        :param event_publisher: Optional runtime event publisher.
        :param audit_logger: Optional audit logger for decision recording.
        """

        self.approval_engine = approval_engine
        self.security_yaml_path = Path(security_yaml_path)
        self.event_publisher = event_publisher
        self.audit_logger = audit_logger
        self.pending: dict[str, ApprovalRequest] = {}
        self._futures: dict[str, asyncio.Future[UserApprovalDecision]] = {}
        self._counter = 0

    async def request_approval(
        self,
        *,
        agent_id: str,
        tool: str,
        action: str,
        target: str = "",
        detail: str = "",
        channel: str | None = None,
        msg_id: int | None = None,
        timeout: float | None = None,
    ) -> UserApprovalDecision:
        """Description:
            Create one approval request and wait for the user decision.

        Requirements:
            - Publish an ``approval:requested`` event for the new request.
            - Track the pending request and corresponding future until the request resolves.
            - Support optional timeout handling via ``asyncio.wait_for``.

        :param agent_id: Agent requesting approval.
        :param tool: Tool involved in the request.
        :param action: Action verb being requested.
        :param target: Optional action target such as a path or command.
        :param detail: Optional human-readable detail text.
        :param channel: Optional event channel associated with the request.
        :param msg_id: Optional message identifier associated with the request.
        :param timeout: Optional timeout in seconds.
        :returns: Final user decision.
        """

        request = ApprovalRequest(
            request_id=self._next_request_id(),
            agent_id=agent_id,
            tool=tool,
            action=action,
            target=target,
            detail=detail,
            channel=channel,
            msg_id=msg_id,
        )
        self.pending[request.request_id] = request

        loop = asyncio.get_running_loop()
        future: asyncio.Future[UserApprovalDecision] = loop.create_future()
        self._futures[request.request_id] = future

        payload = {
            "request_id": request.request_id,
            "agent_id": agent_id,
            "tool": tool,
            "action": action,
            "target": target,
            "detail": detail,
            "channel": channel,
            "msg_id": msg_id,
        }
        await self._publish("approval:requested", payload)

        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._futures.pop(request.request_id, None)
            self.pending.pop(request.request_id, None)

    async def resolve_request(
        self,
        request_id: str,
        decision: UserApprovalDecision | str,
        *,
        scope: str | None = None,
        rule_override: str | None = None,
    ) -> ApprovalRequest:
        """Description:
            Resolve one pending approval request with the supplied decision.

        Requirements:
            - Update request state and persist learned rules for permanent decisions.
            - Record session-memory approvals for ``approve_session`` decisions.
            - Publish an ``approval:decision`` event and resolve the waiting future.
            - Write an audit log entry when an audit logger is configured.

        :param request_id: Approval request identifier.
        :param decision: User decision to apply.
        :param scope: Optional rule-generation scope such as ``exact`` or ``folder``.
        :param rule_override: Optional explicit regex override to persist instead of the generated rule.
        :returns: Resolved approval request payload.
        :raises KeyError: If the request identifier is unknown.
        """

        request = self.pending.get(request_id)
        if request is None:
            raise KeyError(f"Unknown approval request: {request_id}")

        decision_enum = UserApprovalDecision(decision)
        request.decision = decision_enum
        request.resolved = True

        if decision_enum == UserApprovalDecision.APPROVE_SESSION:
            self.approval_engine.record_session_approval(
                request.agent_id, self._action_key(request)
            )
        elif decision_enum in PERMANENT_RULE_SECTION:
            request.generated_rule = rule_override or self.generate_rule(request, scope=scope)
            self._write_learned_rule(
                request.agent_id, PERMANENT_RULE_SECTION[decision_enum], request.generated_rule
            )
            self.approval_engine.reload_rules()

        payload = {
            "request_id": request.request_id,
            "agent_id": request.agent_id,
            "tool": request.tool,
            "action": request.action,
            "target": request.target,
            "decision": decision_enum.value,
            "rule_matched": request.generated_rule,
            "channel": request.channel,
            "msg_id": request.msg_id,
        }
        await self._publish("approval:decision", payload)

        if self.audit_logger is not None:
            self.audit_logger.log_approval_decision(
                agent=request.agent_id,
                tool=request.tool,
                action=request.action,
                target=request.target or request.action,
                approval_tier=decision_enum.value,
                rule_matched=request.generated_rule,
                decision=(
                    "approved"
                    if decision_enum
                    in {
                        UserApprovalDecision.ALLOW_ONCE,
                        UserApprovalDecision.APPROVE_SESSION,
                        UserApprovalDecision.ALWAYS_ALLOW,
                        UserApprovalDecision.ALWAYS_ASK,
                    }
                    else "denied"
                ),
                channel=request.channel,
                msg_id=request.msg_id,
            )

        future = self._futures.get(request_id)
        if future is not None and not future.done():
            future.set_result(decision_enum)

        return request

    @classmethod
    def generate_rule(cls, request: ApprovalRequest, *, scope: str | None = None) -> str:
        """Description:
            Generate a regex rule for persisting a learned approval decision.

        Requirements:
            - Generate exact-match rules by default.
            - Support folder and glob filesystem scopes when a target path is present.

        :param request: Approval request to generate a rule for.
        :param scope: Optional scope hint such as ``exact``, ``folder``, or ``glob``.
        :returns: Generated regex rule string.
        """

        action_key = cls._action_key(request)
        scope_kind = (scope or "exact").lower()

        if not request.target or scope_kind == "exact":
            return rf"^{re.escape(action_key)}$"

        if request.tool == "filesystem":
            prefix = re.escape(f"{request.tool}:{request.action}:")
            normalized_target = cls._normalize_target(request.target)
            if scope_kind == "folder":
                folder = normalized_target.rstrip("/")
                return rf"^{prefix}{re.escape(folder)}(?:/.*)?$"
            if scope_kind == "glob":
                return rf"^{prefix}{cls._glob_to_regex(normalized_target)}$"

        return rf"^{re.escape(action_key)}$"

    def _write_learned_rule(self, agent_id: str, section: str, rule: str) -> None:
        """Description:
            Persist one learned rule into the configured security YAML file.

        Requirements:
            - Create the parent directory when necessary.
            - Avoid writing duplicate rules for the same agent and section.

        :param agent_id: Agent identifier that owns the learned rule.
        :param section: Security YAML section to update.
        :param rule: Regex rule string to persist.
        """

        data: dict[str, Any] = {}
        if self.security_yaml_path.exists():
            with self.security_yaml_path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}

        section_data = data.setdefault(section, {})
        agent_rules = section_data.setdefault(agent_id, [])
        if rule not in agent_rules:
            agent_rules.append(rule)

        self.security_yaml_path.parent.mkdir(parents=True, exist_ok=True)
        with self.security_yaml_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False)

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Description:
            Publish one approval-flow event through the configured event publisher.

        Requirements:
            - Prefer explicit helper methods when the publisher exposes them.
            - Fall back to the generic ``publish`` method otherwise.
            - Support both awaitable and synchronous publisher return values.

        :param event_type: Event type name to publish.
        :param payload: Structured event payload.
        """

        if self.event_publisher is None:
            return

        helper_name = event_type.replace(":", "_")
        helper = getattr(self.event_publisher, helper_name, None)
        if callable(helper):
            helper_kwargs: dict[str, Any]
            if event_type == "approval:requested":
                helper_kwargs = {
                    "request_id": payload["request_id"],
                    "agent": payload["agent_id"],
                    "action": payload["action"],
                    "detail": payload.get("detail", ""),
                    "channel": payload.get("channel"),
                }
            else:
                helper_kwargs = {
                    "request_id": payload["request_id"],
                    "decision": payload["decision"],
                    "agent": payload["agent_id"],
                }
            result = helper(**helper_kwargs)
            if inspect.isawaitable(result):
                await result
            return

        publish = getattr(self.event_publisher, "publish", None)
        if not callable(publish):
            return

        event = FaithEvent(
            event=EventType(event_type),
            source="approval_flow",
            channel=payload.get("channel"),
            data=payload,
        )
        try:
            result = publish(event)
        except TypeError:
            result = publish(event_type, payload)
        if inspect.isawaitable(result):
            await result

    def _next_request_id(self) -> str:
        """Description:
            Generate the next sequential approval request identifier.

        Requirements:
            - Produce stable, zero-padded identifiers with the ``apr-`` prefix.

        :returns: Next approval request identifier.
        """

        self._counter += 1
        return f"apr-{self._counter:04d}"

    @staticmethod
    def _action_key(request: ApprovalRequest) -> str:
        """Description:
            Build the canonical action key for one approval request.

        Requirements:
            - Include the normalised target when a target is present.

        :param request: Approval request to convert.
        :returns: Canonical action key string.
        """

        if request.target:
            return (
                f"{request.tool}:{request.action}:{ApprovalFlow._normalize_target(request.target)}"
            )
        return f"{request.tool}:{request.action}"

    @staticmethod
    def _normalize_target(target: str) -> str:
        """Description:
            Normalise one target path for rule generation.

        Requirements:
            - Convert Windows path separators to forward slashes.

        :param target: Raw target string.
        :returns: Normalised target string.
        """

        return target.replace("\\", "/")

    @staticmethod
    def _glob_to_regex(pattern: str) -> str:
        """Description:
            Convert a simple glob pattern into a regex fragment.

        Requirements:
            - Support ``*`` and ``?`` wildcards while escaping all other characters.

        :param pattern: Glob-style pattern to convert.
        :returns: Regex fragment representing the glob pattern.
        """

        parts: list[str] = []
        for char in pattern:
            if char == "*":
                parts.append(".*")
            elif char == "?":
                parts.append(".")
            else:
                parts.append(re.escape(char))
        return "".join(parts)
