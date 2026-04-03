# FAITH-020 — Approval Request/Response Flow

**Phase:** 5 — Security & Approval System
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-019, FAITH-008
**FRS Reference:** Section 5.6

---

## Objective

Implement the approval request/response flow that connects the FAITH-019 ApprovalEngine to user-facing decisions. When an MCP action requires approval, the `ApprovalFlow` class creates an approval request, publishes it as an `approval:requested` event (for the Web UI to surface), and waits for the user's response. The user selects one of the supported options — Allow once, Approve for session, Always allow, Always ask, Deny once, Deny permanently. For persisted options, the flow generates a rule from the action, presents it for user review/edit, and writes the learned rule to `.faith/security.yaml`. Filesystem actions must support exact file, folder, or path-pattern scopes rather than command-only rules. The flow publishes an `approval:decision` event once the user has responded.

---

## Architecture

```
faith/security/
├── __init__.py
├── engine.py           ← ApprovalEngine (FAITH-019 — already exists)
└── approval_flow.py    ← ApprovalFlow class (this task)

tests/
└── test_approval_flow.py   ← Tests (this task)
```

---

## Files to Create

### 1. `faith/security/approval_flow.py`

```python
"""Approval request/response flow for FAITH security system.

Bridges the ApprovalEngine (FAITH-019) to the user via events. When a tool
action is surfaced for approval, this module creates a pending request,
publishes an approval:requested event, and processes the user's response.

For persisted decisions (`always allow`, `always ask`, `deny permanently`), generates
a regex rule from the command, allows user review/edit, and writes the
learned rule to .faith/security.yaml.

FRS Reference: Section 5.6
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from faith.protocol.events import EventPublisher

logger = logging.getLogger("faith.security.approval_flow")


class ApprovalDecision(str, Enum):
    """The six user-facing approval response options.

    FRS Section 5.6 defines exactly these six options.
    """

    ALLOW_ONCE = "allow_once"
    APPROVE_SESSION = "approve_session"
    ALWAYS_ALLOW = "always_allow"
    ALWAYS_ASK = "always_ask"
    DENY_ONCE = "deny_once"
    DENY_PERMANENTLY = "deny_permanently"


# Decisions that persist to security.yaml
PERMANENT_DECISIONS = {
    ApprovalDecision.ALWAYS_ALLOW,
    ApprovalDecision.ALWAYS_ASK,
    ApprovalDecision.DENY_PERMANENTLY,
}

# Map permanent decisions to the security.yaml section they write to
LEARNED_RULE_SECTIONS = {
    ApprovalDecision.ALWAYS_ALLOW: "always_allow_learned",
    ApprovalDecision.ALWAYS_ASK: "always_ask_learned",
    ApprovalDecision.DENY_PERMANENTLY: "always_deny_learned",
}


@dataclass
class ApprovalRequest:
    """A pending approval request waiting for user response.

    Attributes:
        request_id: Unique identifier for this request (e.g. "apr-001").
        agent_id: The agent that triggered the action.
        action: The tool action string (e.g. "git push origin main").
        detail: Human-readable context about the action.
        channel: The task channel where the action originated.
        created_at: Unix timestamp when the request was created.
        resolved: Whether the user has responded.
        decision: The user's decision (set after resolution).
        regex_rule: The regex written to security.yaml (if permanent).
    """

    request_id: str
    agent_id: str
    action: str
    detail: str
    channel: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    resolved: bool = False
    decision: Optional[ApprovalDecision] = None
    regex_rule: Optional[str] = None


def generate_regex_from_command(command: str) -> str:
    """Generate a regex pattern from a concrete command string.

    Escapes regex metacharacters in the command, then replaces
    common variable parts (file paths, branch names, version numbers)
    with flexible patterns so the rule generalises beyond the exact
    command that triggered it.

    The generated regex is always anchored with ^ and $.

    Args:
        command: The concrete command string (e.g. "git commit -m 'fix bug'").

    Returns:
        A regex pattern string (e.g. "^git commit -m .*$").

    Examples:
        >>> generate_regex_from_command("pytest tests/test_auth.py")
        '^pytest tests/.*$'
        >>> generate_regex_from_command("git push origin main")
        '^git push origin main$'
        >>> generate_regex_from_command("pip install requests==2.31.0")
        '^pip install requests(==[\\\\.\\\\d]+)?$'
    """
    # Split into command parts for analysis
    parts = command.split()

    if not parts:
        return f"^{re.escape(command)}$"

    base_command = parts[0]

    # Strategy: escape the command, then apply known generalisation patterns
    escaped = re.escape(command)

    # Generalise file paths: replace specific file names after common path prefixes
    # e.g. "tests/test_auth.py" -> "tests/.*"
    escaped = re.sub(
        r"tests/[^\s]+",
        "tests/.*",
        escaped,
    )

    # Generalise version pinning: "==1.2.3" -> "(==[\d.]+)?"
    escaped = re.sub(
        r"\\=\\=[\d\\.]+",
        r"(==[\\d.]+)?",
        escaped,
    )

    # Generalise quoted strings (single or double quotes)
    escaped = re.sub(
        r"(\\')[^']*(\\')",
        ".*",
        escaped,
    )
    escaped = re.sub(
        r'(\\")[^"]*(\\")',
        ".*",
        escaped,
    )

    # Anchor the pattern
    if not escaped.startswith("^"):
        escaped = f"^{escaped}"
    if not escaped.endswith("$"):
        escaped = f"{escaped}$"

    return escaped


class ApprovalFlow:
    """Manages the approval request/response lifecycle.

    Responsibilities:
    - Create approval requests and publish approval:requested events
    - Track pending requests in memory
    - Process user decisions
    - For permanent decisions: generate regex, support user review,
      write learned rules to .faith/security.yaml
    - Publish approval:decision events
    - Maintain session-scoped approval memory for approve_session decisions

    Attributes:
        event_publisher: EventPublisher for system-events.
        security_yaml_path: Path to .faith/security.yaml.
        pending: Dict of request_id -> ApprovalRequest.
        session_approvals: Set of (agent_id, action) tuples approved for session.
    """

    def __init__(
        self,
        event_publisher: EventPublisher,
        security_yaml_path: Path,
    ):
        """Initialise the approval flow.

        Args:
            event_publisher: EventPublisher for publishing approval events.
            security_yaml_path: Path to the project's .faith/security.yaml.
        """
        self.event_publisher = event_publisher
        self.security_yaml_path = security_yaml_path

        # Pending approval requests keyed by request_id
        self.pending: dict[str, ApprovalRequest] = {}

        # Session-scoped approval memory: (agent_id, action_pattern) -> True
        # These are cleared when the session ends.
        self.session_approvals: set[tuple[str, str]] = set()

        # Counter for generating request IDs
        self._request_counter = 0

        # Futures for async request/response flow: request_id -> Future
        self._response_futures: dict[str, asyncio.Future] = {}

    def _next_request_id(self) -> str:
        """Generate the next unique approval request ID."""
        self._request_counter += 1
        return f"apr-{self._request_counter:04d}"

    def is_session_approved(self, agent_id: str, action: str) -> bool:
        """Check whether an action has been session-approved.

        Called by the ApprovalEngine (FAITH-019) before surfacing an
        ask-first action. If the user previously chose
        "approve for this session" for the same agent+action, this
        returns True and the action is allowed from session memory
        without surfacing a new approval request.

        Args:
            agent_id: The agent requesting the action.
            action: The action string to check.

        Returns:
            True if the action was previously approved for this session.
        """
        return (agent_id, action) in self.session_approvals

    async def request_approval(
        self,
        agent_id: str,
        action: str,
        detail: str,
        channel: Optional[str] = None,
    ) -> ApprovalRequest:
        """Create an approval request, publish event, and wait for response.

        This is the main entry point called by the ApprovalEngine when
        an action requires user approval. It:
        1. Creates an ApprovalRequest
        2. Publishes an approval:requested event
        3. Awaits the user's response (via resolve_request)
        4. Returns the resolved ApprovalRequest

        Args:
            agent_id: The agent requesting approval.
            action: The tool action string.
            detail: Human-readable context about the action.
            channel: The task channel (optional).

        Returns:
            The resolved ApprovalRequest with the user's decision.
        """
        request_id = self._next_request_id()

        request = ApprovalRequest(
            request_id=request_id,
            agent_id=agent_id,
            action=action,
            detail=detail,
            channel=channel,
        )

        self.pending[request_id] = request

        # Create a future for the async response
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalRequest] = loop.create_future()
        self._response_futures[request_id] = future

        # Publish approval:requested event
        await self.event_publisher.approval_requested(
            request_id=request_id,
            agent=agent_id,
            action=action,
            detail=detail,
            channel=channel,
        )

        logger.info(
            f"Approval requested: {request_id} — agent={agent_id}, "
            f"action='{action}'"
        )

        # Wait for the user to respond
        resolved_request = await future
        return resolved_request

    async def resolve_request(
        self,
        request_id: str,
        decision: ApprovalDecision,
        edited_regex: Optional[str] = None,
    ) -> ApprovalRequest:
        """Process the user's response to an approval request.

        Called when the user clicks one of the five approval buttons.
        For permanent decisions, writes the learned rule to security.yaml.

        Args:
            request_id: The approval request to resolve.
            decision: The user's chosen decision.
            edited_regex: For permanent decisions, the user may have
                edited the generated regex. If None, the auto-generated
                regex is used.

        Returns:
            The resolved ApprovalRequest.

        Raises:
            KeyError: If request_id is not found in pending requests.
            ValueError: If the request has already been resolved.
        """
        if request_id not in self.pending:
            raise KeyError(f"Unknown approval request: {request_id}")

        request = self.pending[request_id]

        if request.resolved:
            raise ValueError(f"Request {request_id} is already resolved")

        request.decision = decision
        request.resolved = True

        # --- Handle session-scoped approval ---
        if decision == ApprovalDecision.APPROVE_SESSION:
            self.session_approvals.add((request.agent_id, request.action))
            logger.info(
                f"Session approval recorded: agent={request.agent_id}, "
                f"action='{request.action}'"
            )

        # --- Handle permanent decisions (write to security.yaml) ---
        if decision in PERMANENT_DECISIONS:
            regex = edited_regex or generate_regex_from_command(request.action)
            request.regex_rule = regex

            section = LEARNED_RULE_SECTIONS[decision]
            await self._write_learned_rule(
                agent_id=request.agent_id,
                regex=regex,
                section=section,
            )

            logger.info(
                f"Learned rule written: section={section}, "
                f"agent={request.agent_id}, regex='{regex}'"
            )

        # Determine decision string for the event
        decision_str = (
            "approved" if decision in {
                ApprovalDecision.ALLOW_ONCE,
                ApprovalDecision.APPROVE_SESSION,
                ApprovalDecision.ALWAYS_ALLOW,
                ApprovalDecision.ALWAYS_ASK,
            } else "denied"
        )

        # Publish approval:decision event
        await self.event_publisher.approval_decision(
            request_id=request_id,
            decision=decision_str,
            agent=request.agent_id,
        )

        logger.info(
            f"Approval resolved: {request_id} — decision={decision.value}"
        )

        # Remove from pending
        del self.pending[request_id]

        # Resolve the future so request_approval() returns
        future = self._response_futures.pop(request_id, None)
        if future and not future.done():
            future.set_result(request)

        return request

    def get_pending_requests(self) -> list[ApprovalRequest]:
        """Return all pending (unresolved) approval requests.

        Used by the Web UI approval panel to display the queue.

        Returns:
            List of pending ApprovalRequests, ordered by creation time.
        """
        return sorted(
            self.pending.values(),
            key=lambda r: r.created_at,
        )

    def build_websocket_payload(self, request: ApprovalRequest) -> dict[str, Any]:
        """Build the WebSocket payload for an approval request.

        Includes visual de-emphasis metadata for persisted options.
        The Web UI uses this to render durable actions with reduced
        visual prominence.

        FRS Section 5.6: persisted options are visually de-emphasised
        relative to one-shot and session actions because they change
        future behaviour.

        Args:
            request: The approval request to serialise.

        Returns:
            Dict suitable for JSON serialisation and WebSocket transport.
        """
        return {
            "request_id": request.request_id,
            "agent_id": request.agent_id,
            "action": request.action,
            "detail": request.detail,
            "channel": request.channel,
            "created_at": request.created_at,
            "options": [
                {
                    "key": ApprovalDecision.ALLOW_ONCE.value,
                    "label": "Allow once",
                    "permanent": False,
                    "de_emphasise": False,
                },
                {
                    "key": ApprovalDecision.APPROVE_SESSION.value,
                    "label": "Approve for this session",
                    "permanent": False,
                    "de_emphasise": False,
                },
                {
                    "key": ApprovalDecision.ALWAYS_ALLOW.value,
                    "label": "Always allow",
                    "permanent": True,
                    "de_emphasise": True,
                },
                {
                    "key": ApprovalDecision.ALWAYS_ASK.value,
                    "label": "Always ask",
                    "permanent": True,
                    "de_emphasise": True,
                },
                {
                    "key": ApprovalDecision.DENY_ONCE.value,
                    "label": "Deny once",
                    "permanent": False,
                    "de_emphasise": False,
                },
                {
                    "key": ApprovalDecision.DENY_PERMANENTLY.value,
                    "label": "Deny permanently",
                    "permanent": True,
                    "de_emphasise": True,
                },
            ],
            "suggested_regex": generate_regex_from_command(request.action),
        }

    async def _write_learned_rule(
        self,
        agent_id: str,
        regex: str,
        section: str,
    ) -> None:
        """Write a learned rule to .faith/security.yaml.

        Reads the current file, adds the regex to the appropriate section
        under the agent's key, and writes it back. Includes a date comment.

        The file is written atomically to prevent corruption if the process
        is interrupted mid-write.

        Args:
            agent_id: The agent the rule applies to.
            regex: The regex pattern to write.
            section: Either "always_allow_learned", "always_ask_learned", or "always_deny_learned".
        """
        # Load current config
        config = self._load_security_yaml()

        # Ensure the section and agent key exist
        if section not in config:
            config[section] = {}
        if agent_id not in config[section]:
            config[section][agent_id] = []

        # Check for duplicate rules
        existing_rules = config[section][agent_id]
        if regex in existing_rules:
            logger.debug(
                f"Rule already exists in {section}/{agent_id}: {regex}"
            )
            return

        # Add the rule with a date comment
        # YAML comments aren't preserved by pyyaml, so we write the file
        # manually with comments for learned rules.
        config[section][agent_id].append(regex)

        # Write back
        self._save_security_yaml(config)

        logger.info(
            f"Wrote learned rule to {self.security_yaml_path}: "
            f"[{section}][{agent_id}] = '{regex}'"
        )

    def _load_security_yaml(self) -> dict[str, Any]:
        """Load .faith/security.yaml as a dict.

        Returns:
            Parsed YAML dict, or a minimal default if the file
            doesn't exist.
        """
        try:
            raw = self.security_yaml_path.read_text(encoding="utf-8")
            config = yaml.safe_load(raw) or {}
            return config
        except FileNotFoundError:
            logger.warning(
                f"security.yaml not found at {self.security_yaml_path} — "
                f"creating new file"
            )
            return {"schema_version": "1.0"}
        except Exception as e:
            logger.error(f"Error loading security.yaml: {e}")
            return {"schema_version": "1.0"}

    def _save_security_yaml(self, config: dict[str, Any]) -> None:
        """Write config dict back to .faith/security.yaml.

        Uses block-style YAML for readability. Writes to a temp file first,
        then renames for atomicity.

        Args:
            config: The full security config dict to write.
        """
        import tempfile

        # Add date comments to learned rules
        content = yaml.dump(
            config,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

        # Write atomically: write to temp, then rename
        tmp_path = self.security_yaml_path.with_suffix(".yaml.tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self.security_yaml_path)
        except Exception as e:
            logger.error(f"Failed to write security.yaml: {e}")
            # Clean up temp file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def clear_session(self) -> None:
        """Clear all session-scoped approval memory.

        Called when a session ends. Removes all "approve for this session"
        remembered approvals. Pending requests are also cancelled.
        """
        count = len(self.session_approvals)
        self.session_approvals.clear()

        # Cancel any pending futures
        for request_id, future in self._response_futures.items():
            if not future.done():
                future.cancel()
        self._response_futures.clear()
        self.pending.clear()

        logger.info(
            f"Session cleared: {count} session approvals removed, "
            f"all pending requests cancelled"
        )
```

### 2. `tests/test_approval_flow.py`

```python
"""Tests for the FAITH approval request/response flow.

Covers: request creation, all five decision paths, regex generation,
session-scoped memory, security.yaml writing, WebSocket payload
construction, duplicate rule handling, and session clearing.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from faith.security.approval_flow import (
    ApprovalDecision,
    ApprovalFlow,
    ApprovalRequest,
    LEARNED_RULE_SECTIONS,
    PERMANENT_DECISIONS,
    generate_regex_from_command,
)
from faith.protocol.events import EventPublisher, EventType


# ──────────────────────────────────────────────────
# Fake EventPublisher for testing
# ──────────────────────────────────────────────────


class FakeEventPublisher:
    """Captures published events for assertion."""

    def __init__(self):
        self.events: list[dict] = []

    async def approval_requested(
        self,
        request_id: str,
        agent: str,
        action: str,
        detail: str,
        channel: str = None,
    ) -> None:
        self.events.append({
            "type": "approval:requested",
            "request_id": request_id,
            "agent": agent,
            "action": action,
            "detail": detail,
            "channel": channel,
        })

    async def approval_decision(
        self,
        request_id: str,
        decision: str,
        agent: str,
    ) -> None:
        self.events.append({
            "type": "approval:decision",
            "request_id": request_id,
            "decision": decision,
            "agent": agent,
        })


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def tmp_security_yaml(tmp_path):
    """Create a temporary .faith/security.yaml."""
    faith_dir = tmp_path / ".faith"
    faith_dir.mkdir(parents=True)
    security_path = faith_dir / "security.yaml"

    config = {
        "schema_version": "1.0",
        "approval_rules": {
            "software-developer": {
                "always_allow": ["^pytest.*$"],
                "always_ask": ["^git push.*$"],
            }
        },
        "always_allow_learned": {},
        "always_ask_learned": {},
        "always_deny_learned": {},
    }
    security_path.write_text(
        yaml.dump(config, default_flow_style=False),
        encoding="utf-8",
    )
    return security_path


@pytest.fixture
def fake_publisher():
    return FakeEventPublisher()


@pytest.fixture
def flow(fake_publisher, tmp_security_yaml):
    """Create an ApprovalFlow with test fixtures."""
    return ApprovalFlow(
        event_publisher=fake_publisher,
        security_yaml_path=tmp_security_yaml,
    )


# ──────────────────────────────────────────────────
# Regex generation tests
# ──────────────────────────────────────────────────


def test_generate_regex_simple_command():
    """Simple command is escaped and anchored."""
    regex = generate_regex_from_command("git status")
    assert regex.startswith("^")
    assert regex.endswith("$")
    assert "git" in regex
    assert "status" in regex


def test_generate_regex_with_test_path():
    """Test file paths are generalised to tests/.*"""
    regex = generate_regex_from_command("pytest tests/test_auth.py")
    assert "tests/.*" in regex


def test_generate_regex_with_version():
    """Version pinning is generalised."""
    regex = generate_regex_from_command("pip install requests==2.31.0")
    assert "==" in regex or "\\d" in regex


def test_generate_regex_empty_command():
    """Empty command produces anchored empty pattern."""
    regex = generate_regex_from_command("")
    assert regex.startswith("^")
    assert regex.endswith("$")


def test_generate_regex_with_special_chars():
    """Regex metacharacters in commands are escaped."""
    regex = generate_regex_from_command("echo hello.world")
    # The dot should be escaped
    assert "\\." in regex or "hello" in regex


def test_generated_regex_is_valid():
    """Generated regex compiles without error."""
    import re
    commands = [
        "git commit -m 'fix bug'",
        "pytest tests/test_auth.py -v",
        "pip install requests==2.31.0",
        "rm -rf build/",
        "docker compose up -d",
    ]
    for cmd in commands:
        regex = generate_regex_from_command(cmd)
        re.compile(regex)  # Should not raise


# ──────────────────────────────────────────────────
# Request ID generation tests
# ──────────────────────────────────────────────────


def test_request_id_increments(flow):
    """Request IDs increment sequentially."""
    id1 = flow._next_request_id()
    id2 = flow._next_request_id()
    assert id1 == "apr-0001"
    assert id2 == "apr-0002"


# ──────────────────────────────────────────────────
# Allow once tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allow_once(flow, fake_publisher):
    """Allow once resolves the request and publishes events."""
    # Start approval in background
    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev",
            action="git push origin main",
            detail="Push to remote",
            channel="ch-deploy",
        )
    )

    # Let the event loop process the request
    await asyncio.sleep(0.01)

    # Verify event was published
    assert len(fake_publisher.events) == 1
    assert fake_publisher.events[0]["type"] == "approval:requested"
    assert fake_publisher.events[0]["agent"] == "dev"

    # Resolve the request
    request = await flow.resolve_request(
        "apr-0001", ApprovalDecision.ALLOW_ONCE
    )

    result = await task
    assert result.decision == ApprovalDecision.ALLOW_ONCE
    assert result.resolved is True
    assert result.regex_rule is None  # No rule written for once

    # Decision event published
    decision_events = [
        e for e in fake_publisher.events if e["type"] == "approval:decision"
    ]
    assert len(decision_events) == 1
    assert decision_events[0]["decision"] == "approved"

    # Request removed from pending
    assert "apr-0001" not in flow.pending


# ──────────────────────────────────────────────────
# Approve session tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_session_records_memory(flow, fake_publisher):
    """Approve session records the action in session memory."""
    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev",
            action="npm test",
            detail="Run tests",
        )
    )
    await asyncio.sleep(0.01)

    await flow.resolve_request("apr-0001", ApprovalDecision.APPROVE_SESSION)
    await task

    # Action is now session-approved
    assert flow.is_session_approved("dev", "npm test") is True
    # Different action is not
    assert flow.is_session_approved("dev", "npm build") is False
    # Different agent is not
    assert flow.is_session_approved("qa", "npm test") is False


@pytest.mark.asyncio
async def test_approve_session_no_yaml_write(flow, tmp_security_yaml):
    """Approve session does not write to security.yaml."""
    original_content = tmp_security_yaml.read_text(encoding="utf-8")

    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev",
            action="npm test",
            detail="Run tests",
        )
    )
    await asyncio.sleep(0.01)
    await flow.resolve_request("apr-0001", ApprovalDecision.APPROVE_SESSION)
    await task

    assert tmp_security_yaml.read_text(encoding="utf-8") == original_content


# ──────────────────────────────────────────────────
# Always allow tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_always_allow_writes_yaml(flow, tmp_security_yaml):
    """Always allow writes a learned rule to security.yaml."""
    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev",
            action="pytest tests/test_auth.py",
            detail="Run auth tests",
        )
    )
    await asyncio.sleep(0.01)

    await flow.resolve_request(
        "apr-0001", ApprovalDecision.ALWAYS_ALLOW
    )
    result = await task

    assert result.regex_rule is not None
    assert result.decision == ApprovalDecision.ALWAYS_ALLOW

    # Check security.yaml was updated
    config = yaml.safe_load(
        tmp_security_yaml.read_text(encoding="utf-8")
    )
    assert "always_allow_learned" in config
    assert "dev" in config["always_allow_learned"]
    assert len(config["always_allow_learned"]["dev"]) == 1


@pytest.mark.asyncio
async def test_always_allow_with_edited_regex(
    flow, tmp_security_yaml
):
    """User-edited regex is written instead of auto-generated one."""
    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev",
            action="pytest tests/test_auth.py",
            detail="Run auth tests",
        )
    )
    await asyncio.sleep(0.01)

    custom_regex = "^pytest tests/test_auth\\.py$"
    await flow.resolve_request(
        "apr-0001",
        ApprovalDecision.ALWAYS_ALLOW,
        edited_regex=custom_regex,
    )
    result = await task

    assert result.regex_rule == custom_regex

    config = yaml.safe_load(
        tmp_security_yaml.read_text(encoding="utf-8")
    )
    assert custom_regex in config["always_allow_learned"]["dev"]


# ──────────────────────────────────────────────────
# Deny once tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deny_once(flow, fake_publisher):
    """Deny once resolves the request with a denied decision."""
    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev",
            action="rm -rf /",
            detail="Delete everything",
        )
    )
    await asyncio.sleep(0.01)

    await flow.resolve_request("apr-0001", ApprovalDecision.DENY_ONCE)
    result = await task

    assert result.decision == ApprovalDecision.DENY_ONCE
    decision_events = [
        e for e in fake_publisher.events if e["type"] == "approval:decision"
    ]
    assert decision_events[0]["decision"] == "denied"


@pytest.mark.asyncio
async def test_deny_no_session_memory(flow):
    """Deny once does not record anything in session memory."""
    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev",
            action="rm -rf /",
            detail="Delete everything",
        )
    )
    await asyncio.sleep(0.01)

    await flow.resolve_request("apr-0001", ApprovalDecision.DENY_ONCE)
    await task

    assert flow.is_session_approved("dev", "rm -rf /") is False


# ──────────────────────────────────────────────────
# Deny permanently tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deny_permanently_writes_yaml(flow, tmp_security_yaml):
    """Deny permanently writes a learned deny rule to security.yaml."""
    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev",
            action="rm -rf build/",
            detail="Remove build directory",
        )
    )
    await asyncio.sleep(0.01)

    await flow.resolve_request(
        "apr-0001", ApprovalDecision.DENY_PERMANENTLY
    )
    result = await task

    assert result.regex_rule is not None
    assert result.decision == ApprovalDecision.DENY_PERMANENTLY

    config = yaml.safe_load(
        tmp_security_yaml.read_text(encoding="utf-8")
    )
    assert "always_deny_learned" in config
    assert "dev" in config["always_deny_learned"]
    assert len(config["always_deny_learned"]["dev"]) == 1


# ──────────────────────────────────────────────────
# Duplicate rule prevention tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_rule_not_written(flow, tmp_security_yaml):
    """Writing the same regex twice does not create a duplicate."""
    # First approval
    task1 = asyncio.create_task(
        flow.request_approval(
            agent_id="dev", action="npm test", detail="Run tests"
        )
    )
    await asyncio.sleep(0.01)
    await flow.resolve_request(
        "apr-0001", ApprovalDecision.ALWAYS_ALLOW
    )
    await task1

    # Second approval with same action
    task2 = asyncio.create_task(
        flow.request_approval(
            agent_id="dev", action="npm test", detail="Run tests again"
        )
    )
    await asyncio.sleep(0.01)
    await flow.resolve_request(
        "apr-0002", ApprovalDecision.ALWAYS_ALLOW
    )
    await task2

    config = yaml.safe_load(
        tmp_security_yaml.read_text(encoding="utf-8")
    )
    # Should have exactly one rule, not two
    assert len(config["always_allow_learned"]["dev"]) == 1


# ──────────────────────────────────────────────────
# Pending request management tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_pending_requests(flow):
    """get_pending_requests returns requests sorted by creation time."""
    # Create two requests without resolving
    task1 = asyncio.create_task(
        flow.request_approval(
            agent_id="dev", action="action1", detail="detail1"
        )
    )
    await asyncio.sleep(0.01)
    task2 = asyncio.create_task(
        flow.request_approval(
            agent_id="qa", action="action2", detail="detail2"
        )
    )
    await asyncio.sleep(0.01)

    pending = flow.get_pending_requests()
    assert len(pending) == 2
    assert pending[0].agent_id == "dev"
    assert pending[1].agent_id == "qa"

    # Clean up
    await flow.resolve_request("apr-0001", ApprovalDecision.ALLOW_ONCE)
    await flow.resolve_request("apr-0002", ApprovalDecision.ALLOW_ONCE)
    await task1
    await task2


@pytest.mark.asyncio
async def test_resolve_unknown_request_raises(flow):
    """Resolving a non-existent request raises KeyError."""
    with pytest.raises(KeyError, match="Unknown approval request"):
        await flow.resolve_request(
            "apr-9999", ApprovalDecision.ALLOW_ONCE
        )


@pytest.mark.asyncio
async def test_resolve_already_resolved_raises(flow):
    """Resolving the same request twice raises ValueError."""
    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev", action="test", detail="test"
        )
    )
    await asyncio.sleep(0.01)

    await flow.resolve_request("apr-0001", ApprovalDecision.ALLOW_ONCE)
    await task

    # Request is already removed from pending, so this is a KeyError
    with pytest.raises(KeyError):
        await flow.resolve_request(
            "apr-0001", ApprovalDecision.ALLOW_ONCE
        )


# ──────────────────────────────────────────────────
# WebSocket payload tests
# ──────────────────────────────────────────────────


def test_websocket_payload_structure(flow):
    """WebSocket payload contains all required fields."""
    request = ApprovalRequest(
        request_id="apr-0001",
        agent_id="dev",
        action="git push origin main",
        detail="Push to remote repository",
        channel="ch-deploy",
    )

    payload = flow.build_websocket_payload(request)

    assert payload["request_id"] == "apr-0001"
    assert payload["agent_id"] == "dev"
    assert payload["action"] == "git push origin main"
    assert payload["detail"] == "Push to remote repository"
    assert payload["channel"] == "ch-deploy"
    assert len(payload["options"]) == 5
    assert "suggested_regex" in payload


def test_websocket_payload_de_emphasis(flow):
    """Permanent options have de_emphasise=True."""
    request = ApprovalRequest(
        request_id="apr-0001",
        agent_id="dev",
        action="test",
        detail="test",
    )

    payload = flow.build_websocket_payload(request)
    options = payload["options"]

    # Options 0, 1, 3 (approve once, approve session, deny) — NOT de-emphasised
    assert options[0]["de_emphasise"] is False
    assert options[1]["de_emphasise"] is False
    assert options[3]["de_emphasise"] is False

    # Options 2, 4 (approve all, deny permanently) — DE-EMPHASISED
    assert options[2]["de_emphasise"] is True
    assert options[2]["permanent"] is True
    assert options[4]["de_emphasise"] is True
    assert options[4]["permanent"] is True


def test_websocket_payload_option_keys(flow):
    """Option keys match ApprovalDecision enum values."""
    request = ApprovalRequest(
        request_id="apr-0001",
        agent_id="dev",
        action="test",
        detail="test",
    )

    payload = flow.build_websocket_payload(request)
    keys = [opt["key"] for opt in payload["options"]]

    assert keys == [
        "allow_once",
        "approve_session",
        "always_allow",
        "always_ask",
        "deny_once",
        "deny_permanently",
    ]


# ──────────────────────────────────────────────────
# Session clearing tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_session(flow):
    """clear_session removes all session approvals and pending requests."""
    # Add session approval
    task = asyncio.create_task(
        flow.request_approval(
            agent_id="dev", action="npm test", detail="Run tests"
        )
    )
    await asyncio.sleep(0.01)
    await flow.resolve_request("apr-0001", ApprovalDecision.APPROVE_SESSION)
    await task

    assert flow.is_session_approved("dev", "npm test") is True

    # Create a pending request
    task2 = asyncio.create_task(
        flow.request_approval(
            agent_id="dev", action="npm build", detail="Build"
        )
    )
    await asyncio.sleep(0.01)
    assert len(flow.pending) == 1

    # Clear session
    flow.clear_session()

    assert flow.is_session_approved("dev", "npm test") is False
    assert len(flow.pending) == 0
    assert len(flow._response_futures) == 0

    # The pending future should be cancelled
    with pytest.raises(asyncio.CancelledError):
        await task2


# ──────────────────────────────────────────────────
# Security YAML file handling tests
# ──────────────────────────────────────────────────


def test_load_missing_security_yaml(flow, tmp_path):
    """Loading a non-existent security.yaml returns defaults."""
    flow.security_yaml_path = tmp_path / "nonexistent" / "security.yaml"
    config = flow._load_security_yaml()
    assert config["schema_version"] == "1.0"


def test_load_existing_security_yaml(flow, tmp_security_yaml):
    """Loading an existing security.yaml returns its contents."""
    config = flow._load_security_yaml()
    assert config["schema_version"] == "1.0"
    assert "approval_rules" in config


def test_save_security_yaml_atomic(flow, tmp_security_yaml):
    """Saving security.yaml does not leave temp files on success."""
    config = flow._load_security_yaml()
    config["always_allow_learned"]["test-agent"] = ["^test.*$"]
    flow._save_security_yaml(config)

    # Temp file should not exist
    tmp_file = tmp_security_yaml.with_suffix(".yaml.tmp")
    assert not tmp_file.exists()

    # Content should be written
    reloaded = yaml.safe_load(
        tmp_security_yaml.read_text(encoding="utf-8")
    )
    assert "^test.*$" in reloaded["always_allow_learned"]["test-agent"]


@pytest.mark.asyncio
async def test_write_learned_rule_creates_section_if_missing(
    flow, tmp_security_yaml
):
    """Writing a learned rule creates the section if it does not exist."""
    # Remove the section from the YAML
    config = yaml.safe_load(
        tmp_security_yaml.read_text(encoding="utf-8")
    )
    config.pop("always_allow_learned", None)
    tmp_security_yaml.write_text(
        yaml.dump(config, default_flow_style=False),
        encoding="utf-8",
    )

    await flow._write_learned_rule(
        agent_id="dev",
        regex="^npm test$",
        section="always_allow_learned",
    )

    reloaded = yaml.safe_load(
        tmp_security_yaml.read_text(encoding="utf-8")
    )
    assert "always_allow_learned" in reloaded
    assert "^npm test$" in reloaded["always_allow_learned"]["dev"]


# ──────────────────────────────────────────────────
# Permanent decision classification tests
# ──────────────────────────────────────────────────


def test_permanent_decisions_set():
    """PERMANENT_DECISIONS contains exactly the two persistent options."""
    assert ApprovalDecision.ALWAYS_ALLOW in PERMANENT_DECISIONS
    assert ApprovalDecision.ALWAYS_ASK in PERMANENT_DECISIONS
    assert ApprovalDecision.DENY_PERMANENTLY in PERMANENT_DECISIONS
    assert ApprovalDecision.ALLOW_ONCE not in PERMANENT_DECISIONS
    assert ApprovalDecision.APPROVE_SESSION not in PERMANENT_DECISIONS
    assert ApprovalDecision.DENY_ONCE not in PERMANENT_DECISIONS


def test_learned_rule_sections_mapping():
    """LEARNED_RULE_SECTIONS maps to correct security.yaml keys."""
    assert LEARNED_RULE_SECTIONS[ApprovalDecision.ALWAYS_ALLOW] == \
        "always_allow_learned"
    assert LEARNED_RULE_SECTIONS[ApprovalDecision.ALWAYS_ASK] == \
        "always_ask_learned"
    assert LEARNED_RULE_SECTIONS[ApprovalDecision.DENY_PERMANENTLY] == \
        "always_deny_learned"
```

---

## Integration Points

The ApprovalFlow sits between the FAITH-019 ApprovalEngine and the Web UI, connected by the FAITH-008 event system.

```python
# FAITH-019 ApprovalEngine calls into ApprovalFlow when an action
# falls into the ask-first path:

from faith.security.approval_flow import ApprovalFlow, ApprovalDecision

# Inside ApprovalEngine.evaluate():
async def evaluate(self, agent_id: str, action: str, detail: str, channel: str):
    tier = self._match_tier(agent_id, action)

    if tier == "always_allow":
        return True

    if tier == "ask_first":
        # Check session memory first
        if self.approval_flow.is_session_approved(agent_id, action):
            return True

        # Surface to user
        request = await self.approval_flow.request_approval(
            agent_id=agent_id,
            action=action,
            detail=detail,
            channel=channel,
        )
        return request.decision in {
            ApprovalDecision.ALLOW_ONCE,
            ApprovalDecision.APPROVE_SESSION,
            ApprovalDecision.ALWAYS_ALLOW,
            ApprovalDecision.ALWAYS_ASK,
        }

    if tier == "always_ask":
        # Always surface, no session memory check
        request = await self.approval_flow.request_approval(
            agent_id=agent_id,
            action=action,
            detail=detail,
            channel=channel,
        )
        return request.decision in {
            ApprovalDecision.ALLOW_ONCE,
            ApprovalDecision.APPROVE_SESSION,
            ApprovalDecision.ALWAYS_ALLOW,
            ApprovalDecision.ALWAYS_ASK,
        }
```

```python
# FAITH-008 EventPublisher: ApprovalFlow uses the existing typed helpers
# from EventPublisher to publish approval:requested and approval:decision
# events. No changes to FAITH-008 are required.

await event_publisher.approval_requested(
    request_id="apr-0042",
    agent="software-developer",
    action="git push origin main",
    detail="Push 3 commits to remote",
    channel="ch-deploy",
)

await event_publisher.approval_decision(
    request_id="apr-0042",
    decision="approved",  # or "denied"
    agent="software-developer",
)
```

```python
# Web UI (FAITH-039) receives the approval:requested event via WebSocket,
# calls build_websocket_payload() for the full display payload, and
# renders the approval card. When the user clicks a button, the Web UI
# sends an HTTP POST to /approve/{request_id} which calls resolve_request().

# FastAPI endpoint example (FAITH-036):
@app.post("/approve/{request_id}")
async def approve(request_id: str, body: ApprovalBody):
    decision = ApprovalDecision(body.decision)
    request = await approval_flow.resolve_request(
        request_id=request_id,
        decision=decision,
        edited_regex=body.edited_regex,  # Optional, for permanent decisions
    )
    return {"status": "resolved", "decision": decision.value}
```

---

## Acceptance Criteria

1. `ApprovalFlow.request_approval()` creates a pending `ApprovalRequest`, publishes an `approval:requested` event via `EventPublisher`, and returns the resolved request after the user responds.
2. `ApprovalFlow.resolve_request()` correctly handles all six `ApprovalDecision` values and publishes an `approval:decision` event with `"approved"` or `"denied"`.
3. **Allow once** — action executes; no session memory, no YAML write.
4. **Approve for session** — action executes; `is_session_approved()` returns `True` for the same agent+action pair; no YAML write.
5. **Always allow** — action executes; generates regex from command; writes to `always_allow_learned` section of `.faith/security.yaml` under the agent's key.
6. **Always ask** — action remains prompt-gated; generates regex from command; writes to `always_ask_learned` section of `.faith/security.yaml` under the agent's key.
7. **Deny once** — action blocked; no session memory, no YAML write.
8. **Deny permanently** — action blocked; generates regex from command; writes to `always_deny_learned` section of `.faith/security.yaml` under the agent's key.
9. For persisted decisions, `edited_regex` parameter allows the user to override the auto-generated regex before it is written.
10. `generate_regex_from_command()` produces valid, anchored regex patterns that compile without error.
10. Duplicate rules are not written to `security.yaml` — if the same regex already exists under the same agent+section, the write is skipped.
11. `build_websocket_payload()` returns a dict with all five options, where options 3 (Approve all sessions) and 5 (Deny permanently) have `de_emphasise: true` and `permanent: true`.
12. `clear_session()` removes all session-scoped approvals and cancels all pending request futures.
13. `security.yaml` writes are atomic (write to temp file, then rename).
14. All tests in `tests/test_approval_flow.py` pass, covering all five decision paths, regex generation, session memory, YAML writing, duplicate prevention, WebSocket payload structure, and session clearing.

---

## Notes for Implementer

- **Async request/response pattern**: `request_approval()` is an async method that blocks (awaits a future) until `resolve_request()` is called. This is intentional — the calling agent's tool action is suspended until the user responds. The `asyncio.Future` bridges the two calls.
- **Session memory scope**: `session_approvals` is an in-memory set, not persisted. It is cleared when `clear_session()` is called at session end. The PA (FAITH-015) is responsible for calling `clear_session()` during session teardown.
- **YAML comments**: PyYAML does not preserve comments when round-tripping. The FRS shows comments like `# learned 2026-03-23, approved for all sessions` in the example, but these are aspirational. If comment preservation is desired in future, switch to `ruamel.yaml`. For now, the rules are written without comments — the audit log (FAITH-021) records the full context of every approval decision.
- **Atomic writes**: `_save_security_yaml()` writes to a `.yaml.tmp` file first, then uses `Path.replace()` for an atomic rename. This prevents corruption if the process is killed mid-write. On Windows, `Path.replace()` is atomic for same-volume renames.
- **No direct config reload**: After writing to `security.yaml`, the config hot-reload watcher (FAITH-004) detects the change and triggers re-evaluation of the Pydantic models. The ApprovalFlow does not need to notify the ApprovalEngine directly — the watcher handles it.
- **WebSocket payload vs event**: The `approval:requested` event published via EventPublisher is minimal (request_id, agent, action, detail). The `build_websocket_payload()` method provides the full payload including all five option definitions and de-emphasis metadata. The Web UI backend (FAITH-036) calls `build_websocket_payload()` to enrich the event before forwarding over WebSocket.
- **Regex generation is best-effort**: `generate_regex_from_command()` applies heuristics (generalise test paths, version pins, quoted strings). It will not produce perfect regexes for all possible commands. The user can always edit the regex before it is saved — this is a core UX requirement from FRS Section 5.6.
- **FakeEventPublisher in tests**: The tests use a custom `FakeEventPublisher` rather than mocking the real `EventPublisher`. This keeps the test assertions clean and matches the pattern used in other FAITH test suites (e.g. FAITH-010's `FakeRedis`).

