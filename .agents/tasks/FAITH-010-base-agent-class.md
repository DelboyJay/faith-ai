# FAITH-010 — Base Agent Class

**Phase:** 3 — Base Agent Runtime
**Complexity:** L
**Model:** Opus / GPT-5.4 high reasoning
**Status:** DONE
**Dependencies:** FAITH-007, FAITH-008
**FRS Reference:** Section 3.5, 3.6, 3.7.4

---

## Objective

Implement the base agent runtime class that all specialist agents inherit from. This runs inside the `agent-base` Docker container. It handles context assembly (building the full LLM prompt from system prompt, role reminder, context summary, CAG documents, recent messages, and current task), the main async message loop via Redis pub/sub, LLM call orchestration (stubbed — actual HTTP client deferred to FAITH-013), heartbeat publishing, and graceful shutdown. Also implement a token counting utility using tiktoken with a character-based fallback.

---

## Architecture

```
faith/agent/
├── __init__.py
├── base.py          ← BaseAgent class (this task)
└── context.py       ← Context assembly logic (this task)

faith/utils/
└── tokens.py        ← Token counting utility (this task)
```

---

## Files to Create

### 1. `faith/utils/tokens.py`

```python
"""Token counting utilities for context window management.

Uses tiktoken for known model encodings with a character-based fallback
(len(text) / 4) for unsupported models. All estimates include a 10%
safety margin.

FRS Reference: Section 3.6
"""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger("faith.utils.tokens")

# Known context window sizes by model name / family.
# Values are total context window tokens (input + output).
_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    # Anthropic
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-3.5-sonnet": 200_000,
    "claude-3.5-haiku": 200_000,
    "claude-4-sonnet": 200_000,
    "claude-4-opus": 200_000,
    # Google
    "gemini-1.5-pro": 1_000_000,
    "gemini-1.5-flash": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    # DeepSeek
    "deepseek-chat": 64_000,
    "deepseek-coder": 64_000,
}

DEFAULT_CONTEXT_WINDOW = 8_192
SAFETY_MARGIN = 0.10  # 10% safety margin on all estimates


@lru_cache(maxsize=32)
def _get_encoding(model: str):
    """Attempt to load a tiktoken encoding for the given model.

    Returns the encoding object or None if tiktoken is not installed
    or the model is not supported.
    """
    try:
        import tiktoken
    except ImportError:
        logger.debug("tiktoken not installed — using character fallback")
        return None

    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        pass

    # Try common encoding names as fallback
    for encoding_name in ("cl100k_base", "o200k_base"):
        try:
            return tiktoken.get_encoding(encoding_name)
        except Exception:
            continue

    logger.debug(f"No tiktoken encoding found for model '{model}' — using fallback")
    return None


def estimate_tokens(text: str, model: str = "gpt-4o") -> int:
    """Estimate the number of tokens in a text string.

    Uses tiktoken for known encodings, falls back to len(text) / 4
    for unsupported models. Includes a 10% safety margin.

    Args:
        text: The text to estimate tokens for.
        model: The model name (used to select the tokenizer).

    Returns:
        Estimated token count including safety margin.
    """
    if not text:
        return 0

    encoding = _get_encoding(model)

    if encoding is not None:
        raw_count = len(encoding.encode(text))
    else:
        # Fallback: ~4 characters per token is a reasonable average
        raw_count = len(text) // 4

    # Apply safety margin and round up
    return int(raw_count * (1 + SAFETY_MARGIN) + 0.5)


def get_context_window(model: str) -> int:
    """Return the known context window size for a model.

    Args:
        model: The model name.

    Returns:
        Context window size in tokens. Returns DEFAULT_CONTEXT_WINDOW
        (8192) for unknown models.
    """
    # Exact match first
    if model in _CONTEXT_WINDOWS:
        return _CONTEXT_WINDOWS[model]

    # Prefix match (e.g. "gpt-4o-2024-05-13" matches "gpt-4o")
    for known_model, window in _CONTEXT_WINDOWS.items():
        if model.startswith(known_model):
            return window

    logger.debug(
        f"Unknown model '{model}' — using default context window "
        f"of {DEFAULT_CONTEXT_WINDOW} tokens"
    )
    return DEFAULT_CONTEXT_WINDOW
```

### 2. `faith/agent/context.py`

```python
"""Context assembly for FAITH agent LLM calls.

Builds the full prompt sent to the LLM on each call. The context
follows a strict layered structure:

1. System Prompt       — from prompt.md (loaded fresh each call for hot-reload)
2. Role Reminder       — 2-3 line reinforcement of role + protocol usage
3. Context Summary     — from context.md (rolling summary of past work)
4. CAG Documents       — pre-loaded static reference docs (if configured)
5. Recent Messages     — last N compact protocol messages from active channels
6. Current Task        — the current message to process

Each layer is assembled and token-counted. If the total exceeds the
model's context window, recent messages are truncated from oldest first.

FRS Reference: Section 3.5, 3.6
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from faith.protocol.compact import CompactMessage
from faith.utils.tokens import estimate_tokens, get_context_window

logger = logging.getLogger("faith.agent.context")


class ContextAssembler:
    """Assembles the full LLM context for an agent call.

    Attributes:
        agent_dir: Path to the agent's config directory
            (e.g. .faith/agents/software-developer/).
        model: The LLM model name (for token counting).
        recent_message_limit: Max number of recent messages to include.
        cag_docs: List of pre-loaded CAG document contents.
    """

    def __init__(
        self,
        agent_dir: Path,
        model: str,
        recent_message_limit: int = 20,
        cag_docs: Optional[list[str]] = None,
    ):
        self.agent_dir = agent_dir
        self.model = model
        self.recent_message_limit = recent_message_limit
        self.cag_docs = cag_docs or []

    def _load_file(self, filename: str) -> str:
        """Load a text file from the agent directory.

        Returns empty string if the file doesn't exist.
        """
        path = self.agent_dir / filename
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug(f"File not found: {path}")
            return ""
        except Exception as e:
            logger.warning(f"Error reading {path}: {e}")
            return ""

    def load_system_prompt(self) -> str:
        """Load the system prompt from prompt.md.

        Read fresh on every call to support hot-reload — if the user
        edits prompt.md while an agent is running, the next LLM call
        picks up the changes.
        """
        return self._load_file("prompt.md")

    def load_context_summary(self) -> str:
        """Load the rolling context summary from context.md."""
        return self._load_file("context.md")

    def build_role_reminder(self, agent_role: str, agent_id: str) -> str:
        """Build a short role reminder string.

        Args:
            agent_role: The agent's role description (from config.yaml).
            agent_id: The agent's identifier.

        Returns:
            A 2-3 line reminder string.
        """
        return (
            f"You are {agent_id}, a FAITH agent.\n"
            f"Role: {agent_role}\n"
            f"Always use the compact protocol format for all messages."
        )

    def format_cag_documents(self) -> str:
        """Format pre-loaded CAG documents for inclusion in context.

        Returns:
            Formatted string with all CAG documents, or empty string
            if none configured.
        """
        if not self.cag_docs:
            return ""

        sections = []
        for i, doc in enumerate(self.cag_docs, 1):
            sections.append(f"--- Reference Document {i} ---\n{doc}")
        return "\n\n".join(sections)

    def format_recent_messages(
        self, messages: list[CompactMessage], available_tokens: int
    ) -> str:
        """Format recent messages for context, respecting token budget.

        Messages are included newest-first up to the available token
        budget, then reversed to chronological order for the LLM.

        Args:
            messages: Recent messages (oldest first).
            available_tokens: Maximum tokens to use for messages.

        Returns:
            Formatted message string.
        """
        if not messages:
            return ""

        formatted_lines: list[str] = []
        tokens_used = 0

        # Work backwards from newest to oldest
        for msg in reversed(messages):
            line = msg.to_compact_summary()
            line_tokens = estimate_tokens(line, self.model)

            if tokens_used + line_tokens > available_tokens:
                break

            formatted_lines.append(line)
            tokens_used += line_tokens

        if not formatted_lines:
            return ""

        # Reverse back to chronological order
        formatted_lines.reverse()

        return "--- Recent Channel Messages ---\n" + "\n".join(formatted_lines)

    def assemble(
        self,
        agent_id: str,
        agent_role: str,
        recent_messages: list[CompactMessage],
        current_task: str,
    ) -> list[dict[str, str]]:
        """Assemble the full context for an LLM call.

        Returns a list of message dicts suitable for chat-completion APIs:
        [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]

        The system message contains layers 1-5. The user message contains
        layer 6 (current task).

        Args:
            agent_id: The agent's identifier.
            agent_role: The agent's role description.
            recent_messages: Recent compact protocol messages.
            current_task: The current message/task to process.

        Returns:
            List of message dicts for the LLM API.
        """
        context_window = get_context_window(self.model)

        # Reserve tokens for the response (25% of context window)
        response_reserve = context_window // 4
        available = context_window - response_reserve

        # --- Layer 1: System Prompt ---
        system_prompt = self.load_system_prompt()
        system_tokens = estimate_tokens(system_prompt, self.model)

        # --- Layer 2: Role Reminder ---
        role_reminder = self.build_role_reminder(agent_role, agent_id)
        role_tokens = estimate_tokens(role_reminder, self.model)

        # --- Layer 3: Context Summary ---
        context_summary = self.load_context_summary()
        summary_tokens = estimate_tokens(context_summary, self.model)

        # --- Layer 4: CAG Documents ---
        cag_text = self.format_cag_documents()
        cag_tokens = estimate_tokens(cag_text, self.model)

        # --- Layer 6: Current Task (reserved first — always included) ---
        task_tokens = estimate_tokens(current_task, self.model)

        # Calculate remaining budget for recent messages
        fixed_tokens = (
            system_tokens + role_tokens + summary_tokens + cag_tokens + task_tokens
        )
        message_budget = max(0, available - fixed_tokens)

        # --- Layer 5: Recent Messages (token-bounded) ---
        messages_text = self.format_recent_messages(
            recent_messages[-self.recent_message_limit :],
            message_budget,
        )

        # Build system content from layers 1-5
        system_parts = [system_prompt]
        if role_reminder:
            system_parts.append(role_reminder)
        if context_summary:
            system_parts.append(f"--- Context Summary ---\n{context_summary}")
        if cag_text:
            system_parts.append(cag_text)
        if messages_text:
            system_parts.append(messages_text)

        system_content = "\n\n".join(part for part in system_parts if part)

        total_tokens = estimate_tokens(system_content, self.model) + task_tokens
        logger.debug(
            f"Context assembled: ~{total_tokens} tokens "
            f"({total_tokens * 100 // context_window}% of {context_window} window)"
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": current_task},
        ]
```

### 3. `faith/agent/base.py`

```python
"""FAITH Base Agent — the runtime class all specialist agents inherit from.

Handles Redis pub/sub subscription, message dispatch, LLM call orchestration,
heartbeat publishing, and graceful shutdown. Runs inside the agent-base
Docker container.

FRS Reference: Section 3.5, 3.6, 3.7.4
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any, Optional

import yaml
import redis.asyncio as aioredis

from faith.agent.context import ContextAssembler
from faith.protocol.compact import CompactMessage, ChannelMessageStore
from faith.protocol.events import EventPublisher, EventType

logger = logging.getLogger("faith.agent.base")


class LLMClient:
    """Stub LLM client interface.

    The actual HTTP implementation is provided by FAITH-013.
    This class defines the interface and provides a no-op stub
    so that BaseAgent can be developed and tested independently.
    """

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Send a chat completion request to an LLM API.

        Args:
            messages: List of message dicts (role + content).
            model: The model identifier.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.

        Returns:
            The LLM's response text.

        Raises:
            NotImplementedError: This is a stub — FAITH-013 provides
                the real implementation.
        """
        raise NotImplementedError(
            "LLMClient is a stub. Install the real client from FAITH-013."
        )


class BaseAgent:
    """Base runtime for all FAITH specialist agents.

    Subclasses override `_handle_llm_response()` to implement
    agent-specific behaviour. The base class handles:
    - Config loading from .faith/agents/{id}/config.yaml
    - Redis pub/sub subscription (pa-{agent_id} + task channels)
    - Context assembly and LLM calls
    - Heartbeat publishing
    - Graceful SIGTERM shutdown

    Attributes:
        agent_id: Unique agent identifier (e.g. "software-developer").
        config: Parsed agent config from config.yaml.
        redis: Async Redis client.
        event_publisher: EventPublisher for system-events.
        context_assembler: ContextAssembler for building LLM prompts.
        llm_client: LLM client (stub until FAITH-013).
    """

    def __init__(
        self,
        agent_id: str,
        faith_dir: Path,
        redis_client: aioredis.Redis,
        llm_client: Optional[LLMClient] = None,
    ):
        """Initialise the base agent.

        Args:
            agent_id: The agent's unique identifier.
            faith_dir: Path to the .faith directory.
            redis_client: Connected async Redis client.
            llm_client: Optional LLM client (defaults to stub).
        """
        self.agent_id = agent_id
        self.faith_dir = faith_dir
        self.agent_dir = faith_dir / "agents" / agent_id
        self.redis = redis_client
        self.llm_client = llm_client or LLMClient()

        # Load agent config
        self.config = self._load_config()

        # Set up event publisher
        self.event_publisher = EventPublisher(self.redis, source=self.agent_id)

        # Set up context assembler
        model = self.config.get("model", "gpt-4o")
        recent_limit = self.config.get("recent_message_limit", 20)
        cag_docs = self._load_cag_documents()
        self.context_assembler = ContextAssembler(
            agent_dir=self.agent_dir,
            model=model,
            recent_message_limit=recent_limit,
            cag_docs=cag_docs,
        )

        # Channel message stores (channel_name -> ChannelMessageStore)
        self._channel_stores: dict[str, ChannelMessageStore] = {}

        # Subscribed channels
        self._subscribed_channels: set[str] = set()

        # Control flags
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None

    def _load_config(self) -> dict[str, Any]:
        """Load agent config from .faith/agents/{id}/config.yaml.

        Returns:
            Parsed config dict. Returns minimal defaults if file
            doesn't exist.
        """
        config_path = self.agent_dir / "config.yaml"
        try:
            raw = config_path.read_text(encoding="utf-8")
            config = yaml.safe_load(raw) or {}
            logger.info(f"Loaded config for agent '{self.agent_id}' from {config_path}")
            return config
        except FileNotFoundError:
            logger.warning(
                f"Config not found at {config_path} — using defaults"
            )
            return {}
        except Exception as e:
            logger.error(f"Error loading config from {config_path}: {e}")
            return {}

    def _load_cag_documents(self) -> list[str]:
        """Load CAG (Context-Augmented Generation) reference documents.

        Reads file paths from config.yaml's `cag_documents` list and
        loads their contents.

        Returns:
            List of document content strings.
        """
        doc_paths = self.config.get("cag_documents", [])
        docs = []
        for path_str in doc_paths:
            path = Path(path_str)
            if not path.is_absolute():
                path = self.faith_dir / path_str
            try:
                docs.append(path.read_text(encoding="utf-8"))
                logger.debug(f"Loaded CAG document: {path}")
            except Exception as e:
                logger.warning(f"Failed to load CAG document {path}: {e}")
        return docs

    async def run(self) -> None:
        """Main entry point — start the agent's async event loop.

        Subscribes to the personal channel (pa-{agent_id}) and any
        pre-configured task channels, starts the heartbeat, and
        processes messages until shutdown.
        """
        self._running = True
        logger.info(f"Agent '{self.agent_id}' starting up")

        # Install signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._signal_shutdown)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        # Start heartbeat
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"{self.agent_id}-heartbeat"
        )

        # Subscribe to personal channel
        personal_channel = f"pa-{self.agent_id}"
        self._pubsub = self.redis.pubsub()
        await self._pubsub.subscribe(personal_channel)
        self._subscribed_channels.add(personal_channel)
        logger.info(f"Subscribed to personal channel: {personal_channel}")

        # Subscribe to any pre-configured task channels
        task_channels = self.config.get("channels", [])
        for ch in task_channels:
            await self.subscribe_channel(ch)

        # Publish startup event
        await self.event_publisher.publish(
            __import__("faith.protocol.events", fromlist=["FaithEvent"]).FaithEvent(
                event=EventType.AGENT_HEARTBEAT,
                source=self.agent_id,
                data={"status": "started"},
            )
        )

        # Main message loop
        self._listener_task = asyncio.create_task(
            self._listen_loop(), name=f"{self.agent_id}-listener"
        )

        try:
            await self._listener_task
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def _listen_loop(self) -> None:
        """Internal loop: read messages from subscribed Redis channels."""
        while self._running:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is None:
                    continue

                if message["type"] != "message":
                    continue

                raw = message["data"]
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")

                channel = message.get("channel", b"")
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8")

                await self._handle_message(raw, channel)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in listen loop: {e}", exc_info=True)

    async def _handle_message(self, raw_message: str, channel: str) -> None:
        """Parse and process an incoming compact protocol message.

        Args:
            raw_message: Raw JSON string from Redis.
            channel: The Redis channel the message arrived on.
        """
        try:
            msg = CompactMessage.from_json(raw_message)
        except Exception as e:
            logger.warning(f"Failed to parse message on {channel}: {e}")
            return

        logger.info(
            f"Received msg #{msg.msg_id} from {msg.from_agent} on {channel}: "
            f"{msg.summary[:80]}"
        )

        # Store message
        if channel not in self._channel_stores:
            self._channel_stores[channel] = ChannelMessageStore(channel)
        self._channel_stores[channel].add(msg)

        # Call LLM to generate a response
        try:
            response = await self._call_llm(msg, channel)
            await self._handle_llm_response(msg, response, channel)
        except NotImplementedError:
            logger.warning(
                "LLM client is a stub — cannot process message. "
                "Install FAITH-013 for real LLM calls."
            )
        except Exception as e:
            logger.error(f"Error processing message #{msg.msg_id}: {e}", exc_info=True)
            await self.event_publisher.agent_error(
                error=str(e),
                channel=channel,
                recoverable=True,
            )

    async def _call_llm(self, message: CompactMessage, channel: str) -> str:
        """Assemble context and call the LLM.

        Args:
            message: The current message to process.
            channel: The channel the message came from.

        Returns:
            The LLM's response text.
        """
        # Get recent messages from the channel store
        store = self._channel_stores.get(channel)
        recent_messages = store.get_recent(self.context_assembler.recent_message_limit) if store else []

        # Get agent role from config
        agent_role = self.config.get("role", "specialist agent")

        # Assemble context
        context = self.context_assembler.assemble(
            agent_id=self.agent_id,
            agent_role=agent_role,
            recent_messages=recent_messages,
            current_task=message.to_json(),
        )

        # Call LLM
        model = self.config.get("model", "gpt-4o")
        temperature = self.config.get("temperature", 0.7)
        max_tokens = self.config.get("max_tokens", None)

        response = await self.llm_client.chat_completion(
            messages=context,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        return response

    async def _handle_llm_response(
        self,
        original_message: CompactMessage,
        response: str,
        channel: str,
    ) -> None:
        """Process the LLM's response.

        Subclasses override this to implement agent-specific behaviour
        (e.g. parsing tool calls, sending review requests, etc.).

        The default implementation attempts to parse the response as
        a compact protocol message and send it to the channel.

        Args:
            original_message: The message that triggered the LLM call.
            response: The raw LLM response text.
            channel: The channel to respond on.
        """
        try:
            # Try to parse as compact protocol message
            reply = CompactMessage.from_json(response)
            await self._send_message(channel, reply)
        except Exception:
            # If response isn't valid compact protocol, log it
            logger.debug(
                f"LLM response is not compact protocol format: "
                f"{response[:200]}"
            )

    async def _send_message(self, channel: str, message: CompactMessage) -> None:
        """Publish a compact protocol message to a Redis channel.

        Args:
            channel: Target Redis channel.
            message: The message to send.
        """
        try:
            await self.redis.publish(channel, message.to_json())
            logger.debug(f"Sent msg #{message.msg_id} to {channel}")
        except Exception as e:
            logger.error(f"Failed to send message to {channel}: {e}")

    async def _publish_event(self, event_type: EventType, **data) -> None:
        """Convenience wrapper for publishing events.

        Uses the agent's EventPublisher to send an event to
        system-events.

        Args:
            event_type: The event type to publish.
            **data: Event-specific data fields.
        """
        from faith.protocol.events import FaithEvent

        event = FaithEvent(
            event=event_type,
            source=self.agent_id,
            data=data,
        )
        await self.event_publisher.publish(event)

    async def subscribe_channel(self, channel: str) -> None:
        """Subscribe to an additional task channel.

        Called when the PA assigns this agent to a new channel.

        Args:
            channel: The channel name to subscribe to.
        """
        if channel in self._subscribed_channels:
            logger.debug(f"Already subscribed to {channel}")
            return

        if self._pubsub:
            await self._pubsub.subscribe(channel)
            self._subscribed_channels.add(channel)
            self._channel_stores[channel] = ChannelMessageStore(channel)
            logger.info(f"Subscribed to channel: {channel}")

    async def unsubscribe_channel(self, channel: str) -> None:
        """Unsubscribe from a task channel.

        Args:
            channel: The channel name to unsubscribe from.
        """
        if channel not in self._subscribed_channels:
            return

        if self._pubsub:
            await self._pubsub.unsubscribe(channel)
            self._subscribed_channels.discard(channel)
            self._channel_stores.pop(channel, None)
            logger.info(f"Unsubscribed from channel: {channel}")

    async def _heartbeat_loop(self) -> None:
        """Background task that publishes heartbeat events.

        Publishes agent:heartbeat to system-events at the configured
        interval (default 30s).
        """
        interval = self.config.get("heartbeat_interval_seconds", 30)
        logger.info(
            f"Heartbeat started for '{self.agent_id}' "
            f"(interval: {interval}s)"
        )

        try:
            while self._running:
                await asyncio.sleep(interval)
                if not self._running:
                    break
                await self.event_publisher.agent_heartbeat()
                logger.debug(f"Heartbeat published for '{self.agent_id}'")
        except asyncio.CancelledError:
            pass

    def _signal_shutdown(self) -> None:
        """Signal handler for SIGTERM/SIGINT — triggers graceful shutdown."""
        logger.info(f"Shutdown signal received for agent '{self.agent_id}'")
        self._running = False
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()

    async def _shutdown(self) -> None:
        """Perform graceful shutdown.

        Cancels heartbeat, unsubscribes from all channels, and
        closes the pubsub connection.
        """
        logger.info(f"Agent '{self.agent_id}' shutting down")
        self._running = False

        # Cancel heartbeat
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Unsubscribe from all channels
        if self._pubsub:
            for channel in list(self._subscribed_channels):
                try:
                    await self._pubsub.unsubscribe(channel)
                except Exception:
                    pass
            try:
                await self._pubsub.close()
            except Exception:
                pass

        self._subscribed_channels.clear()
        logger.info(f"Agent '{self.agent_id}' shutdown complete")
```

### 4. `faith/agent/__init__.py`

```python
"""FAITH Agent Runtime — base class and context assembly."""

from faith.agent.base import BaseAgent, LLMClient
from faith.agent.context import ContextAssembler

__all__ = [
    "BaseAgent",
    "LLMClient",
    "ContextAssembler",
]
```

### 5. `tests/test_base_agent.py`

```python
"""Tests for the FAITH base agent runtime.

Covers context assembly, token estimation, heartbeat publishing,
message handling, graceful shutdown, and channel subscription.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from faith.agent.base import BaseAgent, LLMClient
from faith.agent.context import ContextAssembler
from faith.protocol.compact import CompactMessage, MessageType, ChannelMessageStore
from faith.protocol.events import EventType, FaithEvent
from faith.utils.tokens import estimate_tokens, get_context_window


# ──────────────────────────────────────────────────
# Fake Redis for testing
# ──────────────────────────────────────────────────


class FakePubSub:
    """Minimal fake async Redis PubSub."""

    def __init__(self):
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self._messages: list[dict] = []
        self._closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str = None) -> None:
        if channel:
            self.unsubscribed.append(channel)

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self._messages:
            return self._messages.pop(0)
        return None

    async def close(self) -> None:
        self._closed = True

    def inject_message(self, channel: str, data: str) -> None:
        self._messages.append({
            "type": "message",
            "channel": channel.encode("utf-8"),
            "data": data.encode("utf-8"),
        })


class FakeRedis:
    """Minimal fake async Redis client for testing."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self._pubsub = FakePubSub()

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    def pubsub(self) -> FakePubSub:
        return self._pubsub


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def tmp_faith_dir(tmp_path):
    """Create a temporary .faith directory with agent config."""
    faith_dir = tmp_path / ".faith"
    agent_dir = faith_dir / "agents" / "test-agent"
    agent_dir.mkdir(parents=True)

    # Write config.yaml
    config = {
        "role": "test specialist",
        "model": "gpt-4o",
        "heartbeat_interval_seconds": 1,
        "recent_message_limit": 10,
        "temperature": 0.5,
        "channels": [],
    }
    (agent_dir / "config.yaml").write_text(
        __import__("yaml").dump(config), encoding="utf-8"
    )

    # Write prompt.md
    (agent_dir / "prompt.md").write_text(
        "You are a test agent for the FAITH framework.", encoding="utf-8"
    )

    # Write context.md
    (agent_dir / "context.md").write_text(
        "Previous work: set up project structure.", encoding="utf-8"
    )

    return faith_dir


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def agent(tmp_faith_dir, fake_redis):
    """Create a BaseAgent with test fixtures."""
    return BaseAgent(
        agent_id="test-agent",
        faith_dir=tmp_faith_dir,
        redis_client=fake_redis,
    )


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client that returns a valid compact message."""
    client = AsyncMock(spec=LLMClient)
    reply = CompactMessage(
        **{
            "from": "test-agent",
            "to": "pa",
            "channel": "ch-test",
            "msg_id": 2,
            "type": MessageType.STATUS_UPDATE,
            "tags": ["test"],
            "summary": "Task complete",
        }
    )
    client.chat_completion.return_value = reply.to_json()
    return client


@pytest.fixture
def sample_message():
    """A sample incoming compact protocol message."""
    return CompactMessage(
        **{
            "from": "pa",
            "to": "test-agent",
            "channel": "ch-test",
            "msg_id": 1,
            "type": MessageType.TASK,
            "tags": ["code"],
            "summary": "Implement auth module",
        }
    )


# ──────────────────────────────────────────────────
# Token estimation tests
# ──────────────────────────────────────────────────


def test_estimate_tokens_empty_string():
    """Empty text returns 0 tokens."""
    assert estimate_tokens("", "gpt-4o") == 0


def test_estimate_tokens_nonempty():
    """Non-empty text returns a positive token count."""
    tokens = estimate_tokens("Hello, world! This is a test.", "gpt-4o")
    assert tokens > 0


def test_estimate_tokens_includes_safety_margin():
    """Token estimate includes a 10% safety margin."""
    text = "a " * 100  # ~100 tokens raw
    tokens = estimate_tokens(text, "gpt-4o")
    # With safety margin, should be more than a naive count
    naive = len(text) // 4
    # The tiktoken count or fallback + 10% should be > naive
    assert tokens > 0


def test_estimate_tokens_fallback_for_unknown_model():
    """Unknown models use the character-based fallback."""
    text = "a" * 400  # Should be ~100 tokens with /4 fallback
    tokens = estimate_tokens(text, "totally-unknown-model-xyz")
    # Fallback: 400/4 = 100, + 10% = 110
    assert tokens == 110


def test_get_context_window_known_model():
    """Known models return their documented context window."""
    assert get_context_window("gpt-4o") == 128_000
    assert get_context_window("claude-3.5-sonnet") == 200_000


def test_get_context_window_unknown_model():
    """Unknown models return the default 8192."""
    assert get_context_window("totally-unknown-model") == 8_192


# ──────────────────────────────────────────────────
# Context assembly tests
# ──────────────────────────────────────────────────


def test_context_assembler_loads_prompt(tmp_faith_dir):
    """System prompt is loaded from prompt.md."""
    assembler = ContextAssembler(
        agent_dir=tmp_faith_dir / "agents" / "test-agent",
        model="gpt-4o",
    )
    prompt = assembler.load_system_prompt()
    assert "test agent" in prompt.lower()


def test_context_assembler_loads_context_summary(tmp_faith_dir):
    """Context summary is loaded from context.md."""
    assembler = ContextAssembler(
        agent_dir=tmp_faith_dir / "agents" / "test-agent",
        model="gpt-4o",
    )
    summary = assembler.load_context_summary()
    assert "project structure" in summary


def test_context_assembler_missing_file_returns_empty(tmp_path):
    """Missing files return empty string instead of raising."""
    assembler = ContextAssembler(
        agent_dir=tmp_path / "nonexistent",
        model="gpt-4o",
    )
    assert assembler.load_system_prompt() == ""
    assert assembler.load_context_summary() == ""


def test_context_assembly_returns_messages(tmp_faith_dir, sample_message):
    """Assemble returns a list with system and user messages."""
    assembler = ContextAssembler(
        agent_dir=tmp_faith_dir / "agents" / "test-agent",
        model="gpt-4o",
    )
    context = assembler.assemble(
        agent_id="test-agent",
        agent_role="test specialist",
        recent_messages=[sample_message],
        current_task="Implement the auth module",
    )
    assert len(context) == 2
    assert context[0]["role"] == "system"
    assert context[1]["role"] == "user"
    assert "test agent" in context[0]["content"].lower()
    assert "auth module" in context[1]["content"].lower()


def test_context_assembly_includes_role_reminder(tmp_faith_dir):
    """Assembled context includes the role reminder."""
    assembler = ContextAssembler(
        agent_dir=tmp_faith_dir / "agents" / "test-agent",
        model="gpt-4o",
    )
    context = assembler.assemble(
        agent_id="test-agent",
        agent_role="test specialist",
        recent_messages=[],
        current_task="test",
    )
    system_content = context[0]["content"]
    assert "test-agent" in system_content
    assert "compact protocol" in system_content.lower()


def test_context_assembly_includes_cag_documents(tmp_faith_dir):
    """CAG documents are included in the system context."""
    assembler = ContextAssembler(
        agent_dir=tmp_faith_dir / "agents" / "test-agent",
        model="gpt-4o",
        cag_docs=["This is reference doc 1.", "This is reference doc 2."],
    )
    context = assembler.assemble(
        agent_id="test-agent",
        agent_role="test specialist",
        recent_messages=[],
        current_task="test",
    )
    system_content = context[0]["content"]
    assert "Reference Document 1" in system_content
    assert "Reference Document 2" in system_content


# ──────────────────────────────────────────────────
# BaseAgent config loading tests
# ──────────────────────────────────────────────────


def test_agent_loads_config(agent):
    """Agent loads config from config.yaml on init."""
    assert agent.config["role"] == "test specialist"
    assert agent.config["model"] == "gpt-4o"


def test_agent_missing_config_uses_defaults(fake_redis, tmp_path):
    """Agent with no config file uses empty defaults."""
    faith_dir = tmp_path / ".faith"
    (faith_dir / "agents" / "ghost").mkdir(parents=True)
    a = BaseAgent(
        agent_id="ghost",
        faith_dir=faith_dir,
        redis_client=fake_redis,
    )
    assert a.config == {}


# ──────────────────────────────────────────────────
# Channel subscription tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_channel(agent, fake_redis):
    """Agent can subscribe to a new task channel."""
    agent._pubsub = fake_redis.pubsub()

    await agent.subscribe_channel("ch-auth")
    assert "ch-auth" in agent._subscribed_channels
    assert "ch-auth" in fake_redis._pubsub.subscribed
    assert "ch-auth" in agent._channel_stores


@pytest.mark.asyncio
async def test_subscribe_channel_idempotent(agent, fake_redis):
    """Subscribing to the same channel twice is a no-op."""
    agent._pubsub = fake_redis.pubsub()
    await agent.subscribe_channel("ch-auth")
    await agent.subscribe_channel("ch-auth")
    assert fake_redis._pubsub.subscribed.count("ch-auth") == 1


@pytest.mark.asyncio
async def test_unsubscribe_channel(agent, fake_redis):
    """Agent can unsubscribe from a task channel."""
    agent._pubsub = fake_redis.pubsub()
    await agent.subscribe_channel("ch-auth")
    await agent.unsubscribe_channel("ch-auth")
    assert "ch-auth" not in agent._subscribed_channels
    assert "ch-auth" in fake_redis._pubsub.unsubscribed


# ──────────────────────────────────────────────────
# Message handling tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_message_stores_message(agent, sample_message):
    """Incoming messages are stored in the channel store."""
    # Use stub LLM client — will raise NotImplementedError
    await agent._handle_message(sample_message.to_json(), "ch-test")
    assert "ch-test" in agent._channel_stores
    assert agent._channel_stores["ch-test"].count() == 1


@pytest.mark.asyncio
async def test_handle_message_calls_llm(
    agent, fake_redis, sample_message, mock_llm_client
):
    """Message handling triggers an LLM call and publishes the response."""
    agent.llm_client = mock_llm_client
    await agent._handle_message(sample_message.to_json(), "ch-test")
    mock_llm_client.chat_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_invalid_message(agent):
    """Invalid JSON messages are logged but don't crash the agent."""
    await agent._handle_message("not valid json {{{{", "ch-test")
    # Should not raise — just log a warning


# ──────────────────────────────────────────────────
# Heartbeat tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_publishes_events(agent, fake_redis):
    """Heartbeat loop publishes agent:heartbeat events."""
    agent._running = True
    agent.config["heartbeat_interval_seconds"] = 0.1

    # Run heartbeat for a short time
    task = asyncio.create_task(agent._heartbeat_loop())
    await asyncio.sleep(0.35)
    agent._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Check that heartbeat events were published
    heartbeat_events = [
        (ch, msg) for ch, msg in fake_redis.published
        if "heartbeat" in msg
    ]
    assert len(heartbeat_events) >= 2


# ──────────────────────────────────────────────────
# Graceful shutdown tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_shutdown_sets_flag(agent):
    """Signal handler sets _running to False."""
    agent._running = True
    agent._listener_task = asyncio.create_task(asyncio.sleep(10))
    agent._signal_shutdown()
    assert agent._running is False
    agent._listener_task.cancel()
    try:
        await agent._listener_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_shutdown_cleans_up(agent, fake_redis):
    """Graceful shutdown cancels heartbeat and closes pubsub."""
    agent._pubsub = fake_redis.pubsub()
    agent._subscribed_channels = {"pa-test-agent", "ch-test"}
    agent._heartbeat_task = asyncio.create_task(asyncio.sleep(10))

    await agent._shutdown()

    assert agent._running is False
    assert len(agent._subscribed_channels) == 0
    assert fake_redis._pubsub._closed is True


# ──────────────────────────────────────────────────
# LLM client stub tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_client_stub_raises():
    """The stub LLM client raises NotImplementedError."""
    client = LLMClient()
    with pytest.raises(NotImplementedError):
        await client.chat_completion(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4o",
        )


# ──────────────────────────────────────────────────
# Event publishing helper tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_event_helper(agent, fake_redis):
    """The _publish_event helper publishes to system-events."""
    await agent._publish_event(
        EventType.AGENT_TASK_COMPLETE,
        task="auth module",
        files_written=3,
    )
    assert len(fake_redis.published) == 1
    channel, msg = fake_redis.published[0]
    assert channel == "system-events"
    parsed = json.loads(msg)
    assert parsed["event"] == "agent:task_complete"
    assert parsed["source"] == "test-agent"
    assert parsed["data"]["task"] == "auth module"
```

---

## Integration Points

The BaseAgent integrates with several other FAITH components:

```python
# PA assigns an agent to a new task channel (FAITH-014)
# The PA sends a compact protocol message to pa-{agent_id}:
msg = CompactMessage(
    from_agent="pa",
    to_agent="software-developer",
    channel="ch-auth-feature",
    msg_id=1,
    type=MessageType.TASK,
    tags=["code", "auth"],
    summary="Implement JWT authentication with httponly cookies",
    needs="Full implementation with tests",
)
await redis.publish("pa-software-developer", msg.to_json())

# The agent receives the message, subscribes to the task channel,
# assembles context from prompt.md + context.md + CAG docs + recent
# messages, calls the LLM, and sends its response back to the channel.
```

```python
# Agent subclass example (future tasks):
class SoftwareDeveloper(BaseAgent):
    async def _handle_llm_response(self, original, response, channel):
        # Parse LLM response for tool calls, file writes, etc.
        # Publish task_complete event when done
        await self.event_publisher.agent_task_complete(
            channel=channel,
            task=original.summary,
            files_written=3,
        )
```

---

## Acceptance Criteria

1. `BaseAgent.__init__` loads config from `.faith/agents/{id}/config.yaml` and initialises all components (EventPublisher, ContextAssembler, channel stores).
2. `BaseAgent.run()` subscribes to `pa-{agent_id}` and starts the heartbeat background task.
3. `subscribe_channel()` / `unsubscribe_channel()` correctly manage Redis pub/sub subscriptions and channel message stores.
4. `_handle_message()` parses incoming compact protocol messages, stores them, and triggers LLM calls.
5. `_call_llm()` assembles context via `ContextAssembler` and calls the LLM client with the correct model/temperature from config.
6. `ContextAssembler.assemble()` builds a two-message list (system + user) with all six context layers, respecting the model's context window.
7. `ContextAssembler` loads `prompt.md` fresh on every call (hot-reload support) and gracefully handles missing files.
8. Heartbeat background task publishes `agent:heartbeat` events at the configured interval.
9. `_signal_shutdown()` and `_shutdown()` perform graceful cleanup: cancel heartbeat, unsubscribe all channels, close pubsub.
10. All 25 tests in `tests/test_base_agent.py` pass, covering token estimation, context assembly, config loading, channel subscription, message handling, heartbeat, shutdown, LLM stub, and event publishing.

---

## Notes for Implementer

- **Hot-reload of prompt.md**: The `ContextAssembler.load_system_prompt()` method reads `prompt.md` from disk on every LLM call. This is intentional — the config watcher (FAITH-004) detects changes to prompt files, but the agent picks up the new content automatically on its next LLM call without needing a signal.
- **LLM client stub**: `LLMClient` in `base.py` defines the interface but raises `NotImplementedError`. FAITH-013 provides the real implementation with retry logic, API key management, and provider routing. The `BaseAgent` accepts an `llm_client` parameter so the real client can be injected.
- **Token safety margin**: All `estimate_tokens()` calls include a 10% safety margin. This prevents context truncation due to tokenizer inaccuracies between the estimator and the actual LLM tokenizer.
- **No secrets access**: The agent never reads `config/secrets.yaml`. API keys are injected via environment variables by the container orchestrator. The agent's config comes exclusively from `.faith/agents/{id}/config.yaml`.
- **Windows signal handling**: `signal.SIGTERM` / `signal.SIGINT` registration via `loop.add_signal_handler` is wrapped in a try/except for `NotImplementedError` because Windows does not support this API. In production, agents run in Linux Docker containers where it works.
- **CAG documents**: These are static reference files (e.g. coding standards, API specs) loaded once at init. They are listed in `config.yaml` under `cag_documents` as file paths relative to the `.faith` directory.
- **FakeRedis in tests**: The tests use a custom `FakeRedis` / `FakePubSub` pair rather than `fakeredis` to keep dependencies minimal and match the pattern used in FAITH-004 and FAITH-009 test suites.

