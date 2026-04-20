"""Description:
    Provide the base agent runtime primitives used by FAITH specialist agents.

Requirements:
    - Assemble system prompts, recent messages, and current-task context into chat payloads.
    - Track recent message history with bounded retention.
    - Load persisted context summaries and optional CAG documents.
    - Compact context through the summariser when required.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faith_pa.agent.caching import apply_cache_hints, detect_provider
from faith_pa.agent.cag import CAGManager, CAGValidationResult
from faith_pa.agent.llm_client import LLMClient
from faith_pa.agent.summariser import ContextSummariser
from faith_pa.config.models import AgentConfig, SystemConfig
from faith_pa.utils.tokens import (
    context_threshold,
    count_message_tokens,
    over_context_threshold,
)
from faith_shared.protocol.compact import ChannelMessageStore, CompactMessage
from faith_shared.protocol.events import EventPublisher, EventType, FaithEvent

DEFAULT_CONTEXT_WINDOW = 128_000
logger = logging.getLogger("faith_pa.agent.base")


@dataclass(slots=True)
class AgentMessage:
    """Description:
        Represent one chat-style message stored in an agent context.

    Requirements:
        - Preserve the role, content, disposable flag, and optional name.

    :param role: Chat role for the message.
    :param content: Message content.
    :param disposable: Whether the message may be discarded during compaction.
    :param name: Optional participant name.
    """

    role: str
    content: str
    disposable: bool = False
    name: str | None = None

    def to_chat_message(self) -> dict[str, str]:
        """Description:
            Convert the agent message into the provider chat-message payload format.

        Requirements:
            - Include the ``name`` field only when it is present.

        :returns: Chat-message payload mapping.
        """

        payload = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass(slots=True)
class ContextAssembly:
    """Description:
        Represent the fully assembled context for one agent invocation.

    Requirements:
        - Preserve the system prompt, recent messages, and current task together.

    :param system_prompt: Fully assembled system prompt.
    :param recent_messages: Recent conversation history.
    :param current_task: Current task text.
    """

    system_prompt: str
    recent_messages: list[AgentMessage] = field(default_factory=list)
    current_task: str = ""

    def to_messages(self) -> list[dict[str, str]]:
        """Description:
            Convert the assembled context into a chat-completion message list.

        Requirements:
            - Emit the system prompt first.
            - Append recent messages in order.
            - Place the current task as the final user message.

        :returns: Chat-completion message payload list.
        """

        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(message.to_chat_message() for message in self.recent_messages)
        messages.append({"role": "user", "content": self.current_task})
        return messages


@dataclass(slots=True)
class AgentResponse:
    """Description:
        Represent the normalised response returned by the base agent runtime.

    Requirements:
        - Preserve content, raw response payload, and aggregate token usage.

    :param content: Assistant response text.
    :param raw_response: Raw provider response payload.
    :param token_usage: Aggregate token usage when known.
    """

    content: str
    raw_response: Any = None
    token_usage: int | None = None


class BaseAgent:
    """Description:
        Provide the shared runtime behaviour for FAITH specialist agents.

    Requirements:
        - Build prompts from the base prompt, role reminder, context summary, and optional CAG documents.
        - Maintain bounded recent-message history.
        - Expose context-budget and compaction helpers.
        - Execute chat completions through the shared LLM client.

    :param agent_id: Agent identifier.
    :param config: Agent configuration payload.
    :param system_config: System configuration payload.
    :param prompt_text: Base agent prompt text.
    :param project_root: Optional project root used for CAG document loading.
    :param context_summary: Optional preloaded context summary.
    :param context_window_tokens: Total context window available to the model.
    :param redis_client: Optional Redis client used for channel subscriptions and event publishing.
    :param llm_client: Optional LLM client override used for chat completions.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        config: AgentConfig,
        system_config: SystemConfig,
        prompt_text: str,
        project_root: Path | None = None,
        context_summary: str = "",
        context_window_tokens: int = DEFAULT_CONTEXT_WINDOW,
        redis_client: Any | None = None,
        llm_client: Any | None = None,
    ) -> None:
        """Description:
            Initialise the base agent runtime.

        Requirements:
            - Load the persisted context summary when an explicit one is not supplied.
            - Initialise the context summariser and LLM client from the agent and system configuration.

        :param agent_id: Agent identifier.
        :param config: Agent configuration payload.
        :param system_config: System configuration payload.
        :param prompt_text: Base agent prompt text.
        :param project_root: Optional project root used for CAG document loading.
        :param context_summary: Optional preloaded context summary.
        :param context_window_tokens: Total context window available to the model.
        :param redis_client: Optional Redis client used for channel subscriptions and event publishing.
        :param llm_client: Optional LLM client override used for chat completions.
        """

        self.agent_id = agent_id
        self.config = config
        self.system_config = system_config
        self.prompt_text = prompt_text.strip()
        self.project_root = project_root.resolve() if project_root else None
        self.context_window_tokens = context_window_tokens
        self.recent_messages: list[AgentMessage] = []
        self.redis = redis_client

        faith_dir = (self.project_root / ".faith") if self.project_root else Path(".faith")
        self.summariser = ContextSummariser(
            agent_id=agent_id,
            model_name=self.model_name,
            context_window_tokens=self.context_window_tokens,
            context_config=self.config,
            faith_dir=faith_dir,
        )
        self.context_summary = context_summary.strip() or self.summariser.load_summary()
        self.llm_client = llm_client or LLMClient(
            model=self.model_name,
            fallback_model=self.system_config.pa.fallback_model,
            ollama_host=self.system_config.ollama.endpoint
            if self.system_config.ollama.enabled
            else None,
        )
        self.cag_manager = CAGManager(
            project_root=self.project_root,
            model_name=self.model_name,
            document_paths=list(self.config.cag_documents),
            max_tokens=self.config.cag_max_tokens,
        )
        self.cag_validation = CAGValidationResult(
            success=True,
            total_tokens=0,
            max_tokens=self.config.cag_max_tokens,
            document_count=len(self.config.cag_documents),
            loaded_count=0,
        )
        llm_api_hint = self.system_config.ollama.endpoint or getattr(
            self.llm_client,
            "ollama_host",
            "",
        )
        self._llm_provider = detect_provider(
            self.model_name,
            llm_api_hint,
        )
        self.event_publisher = (
            EventPublisher(self.redis, source=self.agent_id) if self.redis is not None else None
        )
        self._channel_stores: dict[str, ChannelMessageStore] = {}
        self._subscribed_channels: set[str] = set()
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None
        self._listener_task: asyncio.Task | None = None
        self._pubsub: Any | None = None

    @property
    def model_name(self) -> str:
        """Description:
            Return the effective model name for the agent.

        Requirements:
            - Prefer the agent-specific model when configured.
            - Fall back to the system default agent model otherwise.

        :returns: Effective model name.
        """

        return self.config.model or self.system_config.default_agent_model

    def add_message(
        self, role: str, content: str, *, disposable: bool = False, name: str | None = None
    ) -> None:
        """Description:
            Append one message to the recent agent history.

        Requirements:
            - Respect the configured maximum message count by trimming the oldest messages.

        :param role: Chat role for the message.
        :param content: Message content.
        :param disposable: Whether the message may be discarded during compaction.
        :param name: Optional participant name.
        """

        self.recent_messages.append(
            AgentMessage(role=role, content=content, disposable=disposable, name=name)
        )
        max_messages = self.config.context.max_messages
        if len(self.recent_messages) > max_messages:
            self.recent_messages = self.recent_messages[-max_messages:]

    async def run(self) -> None:
        """Description:
            Start the asynchronous base-agent runtime loop.

        Requirements:
            - Require a Redis client before starting runtime subscriptions.
            - Subscribe to the personal PA channel for the agent.
            - Start the heartbeat and listener loops.
            - Shut down gracefully when signalled or cancelled.

        :raises RuntimeError: If no Redis client is configured.
        """

        if self.redis is None:
            raise RuntimeError("BaseAgent runtime requires a Redis client.")

        self._running = True
        self._install_signal_handlers()
        self._pubsub = self.redis.pubsub()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"{self.agent_id}-heartbeat"
        )
        await self.subscribe_channel(f"pa-{self.agent_id}")
        self._listener_task = asyncio.create_task(
            self._listen_loop(), name=f"{self.agent_id}-listener"
        )

        try:
            await self._listener_task
        except asyncio.CancelledError:
            logger.debug("Listener task cancelled for %s.", self.agent_id)
        finally:
            await self._shutdown()

    def _install_signal_handlers(self) -> None:
        """Description:
            Register graceful-shutdown signal handlers when the platform supports them.

        Requirements:
            - Ignore unsupported signal-handler registration environments.
        """

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._signal_shutdown)
            except NotImplementedError:
                continue

    async def subscribe_channel(self, channel: str) -> None:
        """Description:
            Subscribe the runtime to one Redis channel and ensure a message store exists.

        Requirements:
            - Ignore duplicate channel subscriptions.
            - Require an active Redis pubsub object before subscribing.

        :param channel: Redis channel name to subscribe.
        :raises RuntimeError: If the pubsub connection has not been initialised.
        """

        if channel in self._subscribed_channels:
            return
        if self._pubsub is None:
            raise RuntimeError("Pubsub connection is not initialised.")
        await self._pubsub.subscribe(channel)
        self._subscribed_channels.add(channel)
        self._channel_stores.setdefault(channel, ChannelMessageStore(channel))

    async def unsubscribe_channel(self, channel: str) -> None:
        """Description:
            Unsubscribe the runtime from one Redis channel.

        Requirements:
            - Ignore requests for channels that are not currently subscribed.
            - Preserve any accumulated channel store for later inspection.

        :param channel: Redis channel name to unsubscribe.
        """

        if channel not in self._subscribed_channels or self._pubsub is None:
            return
        await self._pubsub.unsubscribe(channel)
        self._subscribed_channels.discard(channel)

    def build_role_reminder(self) -> str:
        """Description:
            Build the short role-reminder block included in the system prompt.

        Requirements:
            - Include the agent name, role, and configured tool list.

        :returns: Role-reminder block.
        """

        tools = ", ".join(self.config.tools) if self.config.tools else "none"
        return (
            f"Agent: {self.config.name} ({self.config.role})\n"
            f"Tools: {tools}\n"
            "Use the FAITH compact protocol when replying to channel work."
        )

    def load_cag_documents(self) -> CAGValidationResult:
        """Description:
            Load and validate the configured CAG documents for the agent.

        Requirements:
            - Preserve the last validation result for PA session-start reporting.
            - Delegate path loading and token-budget validation to the dedicated CAG manager.

        :returns: Aggregate validation result for the configured CAG documents.
        """

        self.cag_validation = self.cag_manager.load_all()
        return self.cag_validation

    def handle_cag_file_changed(self, changed_path: str | Path) -> bool:
        """Description:
            Reload a CAG document after a matching file-change notification.

        Requirements:
            - Ignore file changes that do not belong to the configured CAG set.
            - Append a system note when a CAG document was successfully reloaded.

        :param changed_path: Changed file path received from the filesystem watch flow.
        :returns: ``True`` when a configured CAG document was reloaded.
        """

        updated = self.cag_manager.reload_document(changed_path)
        if updated is None or not updated.loaded:
            return False
        self.add_message(
            "system",
            (
                f"CAG document '{updated.relative_path}' was updated on disk and "
                "reloaded into your reference context."
            ),
            disposable=True,
        )
        return True

    def build_system_prompt(self) -> str:
        """Description:
            Build the full system prompt for the agent.

        Requirements:
            - Include the base prompt, role reminder, persisted context summary, and loaded CAG documents.
            - Omit empty sections.

        :returns: Fully assembled system prompt.
        """

        parts: list[str] = [self.prompt_text]

        role_reminder = self.build_role_reminder().strip()
        if role_reminder:
            parts.append(role_reminder)

        if self.context_summary:
            parts.append(f"Context Summary:\n{self.context_summary}")

        if self.config.cag_documents and not self.cag_manager.documents:
            self.load_cag_documents()
        formatted_cag = self.cag_manager.format_for_context()
        if formatted_cag:
            parts.append(formatted_cag)

        return "\n\n".join(part for part in parts if part.strip())

    def assemble_context(self, current_task: str) -> ContextAssembly:
        """Description:
            Assemble the full invocation context for one task.

        Requirements:
            - Preserve the current recent-message history in order.
            - Strip the current task text before storing it in the assembly.

        :param current_task: Current task text.
        :returns: Assembled invocation context.
        """

        return ContextAssembly(
            system_prompt=self.build_system_prompt(),
            recent_messages=list(self.recent_messages),
            current_task=current_task.strip(),
        )

    def build_completion_payload(self, current_task: str) -> dict[str, Any]:
        """Description:
            Build the provider payload for one completion request.

        Requirements:
            - Include the effective model name, chat messages, and agent metadata.

        :param current_task: Current task text.
        :returns: Completion payload mapping.
        """

        assembly = self.assemble_context(current_task)
        return {
            "model": self.model_name,
            "messages": assembly.to_messages(),
            "agent_id": self.agent_id,
            "agent_role": self.config.role,
            "mcp_native": self.config.mcp_native,
        }

    def count_context_tokens(self, current_task: str) -> int:
        """Description:
            Count the current context token usage for one task.

        Requirements:
            - Count the token usage of the assembled chat-message payload.

        :param current_task: Current task text.
        :returns: Estimated context token count.
        """

        assembly = self.assemble_context(current_task)
        return count_message_tokens(assembly.to_messages(), self.model_name)

    def context_needs_compaction(self, current_task: str) -> bool:
        """Description:
            Return whether the current context should be compacted.

        Requirements:
            - Trigger compaction when the token threshold is exceeded.
            - Also trigger compaction when the recent-message count reaches its configured maximum.

        :param current_task: Current task text.
        :returns: ``True`` when context compaction should run.
        """

        token_count = self.count_context_tokens(current_task)
        return (
            over_context_threshold(
                token_count,
                self.context_window_tokens,
                self.config.context.summary_threshold_pct,
            )
            or len(self.recent_messages) >= self.config.context.max_messages
        )

    def context_budget(self) -> int:
        """Description:
            Return the safe context-token budget for the agent.

        Requirements:
            - Derive the budget from the configured summary-threshold percentage.

        :returns: Safe context-token budget.
        """

        return context_threshold(
            self.context_window_tokens,
            self.config.context.summary_threshold_pct,
        )

    async def _call_llm(self, current_task: str, *, temperature: float = 0.7) -> AgentResponse:
        """Description:
            Execute the assembled context through the shared LLM client.

        Requirements:
            - Convert the normalised LLM response into the agent response model.

        :param current_task: Current task text.
        :param temperature: Sampling temperature.
        :returns: Normalised agent response.
        """

        assembly = self.assemble_context(current_task)
        messages = assembly.to_messages()
        cached_messages = apply_cache_hints(
            messages,
            provider=self._llm_provider,
            cag_present=bool(self.cag_manager.documents),
        )
        response = await self.llm_client.chat(cached_messages, temperature=temperature)
        return AgentResponse(
            content=response.content,
            raw_response=response,
            token_usage=response.input_tokens + response.output_tokens,
        )

    async def _listen_loop(self) -> None:
        """Description:
            Poll subscribed Redis channels and dispatch compact-protocol messages.

        Requirements:
            - Ignore non-message pubsub frames.
            - Decode byte payloads before parsing.
            - Continue running after non-fatal handler errors.
        """

        while self._running and self._pubsub is not None:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if not message or message.get("type") != "message":
                    continue
                raw = message.get("data")
                channel = message.get("channel", "")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8")
                await self._handle_message(str(raw), str(channel))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Agent listener loop failed for %s.", self.agent_id)

    async def _handle_message(self, raw_message: str, channel: str) -> None:
        """Description:
            Parse one compact-protocol message, store it, and execute the LLM path.

        Requirements:
            - Ignore malformed compact messages without crashing the runtime.
            - Store valid messages under the originating channel.
            - Use the compact message summary as the current task text for the LLM call.

        :param raw_message: Raw compact-protocol JSON payload.
        :param channel: Channel on which the message arrived.
        """

        try:
            message = CompactMessage.from_json(raw_message)
        except Exception:
            logger.exception("Failed to parse compact message on %s.", channel)
            return

        self._channel_stores.setdefault(channel, ChannelMessageStore(channel)).add(message)
        self.add_message(
            "user",
            message.to_compact_summary(),
            disposable=message.disposable,
            name=message.from_agent,
        )
        response = await self._call_llm(message.summary)
        await self._handle_llm_response(message, response, channel)

    async def _handle_llm_response(
        self,
        original_message: CompactMessage,
        response: AgentResponse,
        channel: str,
    ) -> None:
        """Description:
            Record the normalised LLM response and emit a task-complete event.

        Requirements:
            - Append the assistant response to recent-message history.
            - Publish an ``agent:task_complete`` event when an event publisher is available.

        :param original_message: Incoming compact message that triggered the LLM call.
        :param response: Normalised agent response.
        :param channel: Channel associated with the response.
        """

        del original_message
        self.add_message("assistant", response.content, name=self.agent_id)
        if self.event_publisher is not None:
            await self.event_publisher.agent_task_complete(
                channel=channel,
                task=response.content or "completed",
            )

    async def _heartbeat_loop(self, *, interval_seconds: float | None = None) -> None:
        """Description:
            Publish periodic heartbeat events while the runtime is active.

        Requirements:
            - Use the configured system heartbeat interval when no override is supplied.
            - Exit cleanly when cancelled or when the runtime stops.

        :param interval_seconds: Optional heartbeat interval override.
        """

        if self.event_publisher is None:
            return

        interval = interval_seconds or float(self.system_config.heartbeat_interval_seconds)
        try:
            while self._running:
                await self.event_publisher.publish(
                    FaithEvent(
                        event=EventType.AGENT_HEARTBEAT,
                        source=self.agent_id,
                        data=self.heartbeat_payload(),
                    )
                )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise

    def _signal_shutdown(self) -> None:
        """Description:
            Mark the runtime for shutdown and cancel the active listener task.

        Requirements:
            - Leave the final cleanup to ``_shutdown()``.
        """

        self._running = False
        if self._listener_task is not None and not self._listener_task.done():
            self._listener_task.cancel()

    async def _shutdown(self) -> None:
        """Description:
            Stop background tasks, unsubscribe channels, and close the pubsub connection.

        Requirements:
            - Cancel the heartbeat task when it is still running.
            - Unsubscribe every active channel before closing the pubsub object.
            - Support both ``aclose()`` and ``close()`` pubsub shutdown APIs.
        """

        self._running = False
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._pubsub is not None:
            for channel in list(self._subscribed_channels):
                try:
                    await self._pubsub.unsubscribe(channel)
                except Exception:
                    logger.exception("Failed to unsubscribe %s for %s.", channel, self.agent_id)
            close = getattr(self._pubsub, "aclose", None)
            if callable(close):
                await close()
            else:
                await self._pubsub.close()

        self._subscribed_channels.clear()

    async def compact_context(self, llm_call: Callable[[str], Awaitable[Any]] | None = None) -> str:
        """Description:
            Compact the current recent-message history into the persisted context summary.

        Requirements:
            - Use a default summariser call through the LLM client when no callback is supplied.
            - Replace the in-memory recent-message history with the retained compacted messages.

        :param llm_call: Optional async summariser callback override.
        :returns: Updated context summary text.
        """

        if llm_call is None:

            async def llm_call(prompt: str) -> str:
                """Description:
                    Generate a concise summary for context compaction.

                Requirements:
                    - Route the summarisation through the shared LLM client with deterministic temperature.

                :param prompt: Summary prompt text.
                :returns: Summary text.
                """

                result = await self.llm_client.chat(
                    [
                        {"role": "system", "content": "You are a concise summariser."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                )
                return result.content

        result = await self.summariser.compact(
            self.recent_messages,
            existing_summary=self.context_summary,
            llm_call=llm_call,
        )
        self.context_summary = result.summary
        self.recent_messages = [
            AgentMessage(
                role=message.get("role", "user"),
                content=str(message.get("content", "")),
                disposable=bool(message.get("disposable", False)),
                name=message.get("name"),
            )
            for message in result.remaining_messages
        ]
        return result.summary

    def heartbeat_payload(self, *, channel: str | None = None) -> dict[str, Any]:
        """Description:
            Build the standard heartbeat payload for the agent.

        Requirements:
            - Include the agent identity, role, model, channel, and timestamp.

        :param channel: Optional channel associated with the heartbeat.
        :returns: Heartbeat payload mapping.
        """

        return {
            "event": "agent:heartbeat",
            "agent_id": self.agent_id,
            "agent_name": self.config.name,
            "role": self.config.role,
            "model": self.model_name,
            "channel": channel,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def parse_llm_response(response: Any) -> AgentResponse:
        """Description:
            Normalise a provider or helper response into the ``AgentResponse`` model.

        Requirements:
            - Accept existing ``AgentResponse`` instances unchanged.
            - Support dict-style responses, structured response objects, and plain stringable values.

        :param response: Response object to normalise.
        :returns: Normalised agent response.
        """

        if isinstance(response, AgentResponse):
            return response
        if isinstance(response, dict):
            if "content" in response:
                content = str(response.get("content", ""))
            elif "message" in response and isinstance(response["message"], dict):
                content = str(response["message"].get("content", ""))
            else:
                content = str(response)
            token_usage = response.get("usage") if isinstance(response.get("usage"), int) else None
            return AgentResponse(content=content, raw_response=response, token_usage=token_usage)
        if hasattr(response, "content"):
            content = str(getattr(response, "content", ""))
            input_tokens = int(getattr(response, "input_tokens", 0) or 0)
            output_tokens = int(getattr(response, "output_tokens", 0) or 0)
            return AgentResponse(
                content=content,
                raw_response=response,
                token_usage=input_tokens + output_tokens,
            )
        return AgentResponse(content=str(response), raw_response=response)


__all__ = [
    "AgentMessage",
    "AgentResponse",
    "BaseAgent",
    "ContextAssembly",
]
