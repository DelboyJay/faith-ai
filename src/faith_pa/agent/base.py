"""Description:
    Provide the base agent runtime primitives used by FAITH specialist agents.

Requirements:
    - Assemble system prompts, recent messages, and current-task context into chat payloads.
    - Track recent message history with bounded retention.
    - Load persisted context summaries and optional CAG documents.
    - Compact context through the summariser when required.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faith_pa.agent.llm_client import LLMClient
from faith_pa.agent.summariser import ContextSummariser
from faith_pa.config.models import AgentConfig, SystemConfig
from faith_pa.utils.tokens import (
    context_threshold,
    count_message_tokens,
    count_text_tokens,
    over_context_threshold,
    truncate_text_to_token_limit,
)

DEFAULT_CONTEXT_WINDOW = 128_000


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
        """

        self.agent_id = agent_id
        self.config = config
        self.system_config = system_config
        self.prompt_text = prompt_text.strip()
        self.project_root = project_root.resolve() if project_root else None
        self.context_window_tokens = context_window_tokens
        self.recent_messages: list[AgentMessage] = []

        faith_dir = (self.project_root / ".faith") if self.project_root else Path(".faith")
        self.summariser = ContextSummariser(
            agent_id=agent_id,
            model_name=self.model_name,
            context_window_tokens=self.context_window_tokens,
            context_config=self.config,
            faith_dir=faith_dir,
        )
        self.context_summary = context_summary.strip() or self.summariser.load_summary()
        self.llm_client = LLMClient(
            model=self.model_name,
            fallback_model=self.system_config.pa.fallback_model,
        )

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

    def build_role_reminder(self) -> str:
        """Description:
            Build the short role-reminder block included in the system prompt.

        Requirements:
            - Include the agent name, role, and configured tool list.

        :returns: Role-reminder block.
        """

        tools = ", ".join(self.config.tools) if self.config.tools else "none"
        return f"Agent: {self.config.name} ({self.config.role})\nTools: {tools}"

    def load_cag_documents(self) -> list[str]:
        """Description:
            Load CAG documents from the project root within the configured token budget.

        Requirements:
            - Skip missing or non-file CAG paths.
            - Truncate the final included document when it would exceed the remaining budget.

        :returns: Loaded CAG document blocks.
        """

        if not self.project_root:
            return []

        loaded: list[str] = []
        remaining = self.config.cag_max_tokens
        for relative_path in self.config.cag_documents:
            path = (self.project_root / relative_path).resolve()
            if not path.exists() or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            doc_header = f"# CAG Document: {relative_path}\n"
            doc_text = doc_header + text.strip()
            doc_tokens = count_text_tokens(doc_text, self.model_name)
            if remaining <= 0:
                break
            if doc_tokens > remaining:
                doc_text = truncate_text_to_token_limit(doc_text, remaining, self.model_name)
                if not doc_text.strip():
                    break
                loaded.append(doc_text)
                break
            loaded.append(doc_text)
            remaining -= doc_tokens
        return loaded

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

        cag_documents = self.load_cag_documents()
        if cag_documents:
            parts.append("\n\n".join(cag_documents))

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
        response = await self.llm_client.chat(assembly.to_messages(), temperature=temperature)
        return AgentResponse(
            content=response.content,
            raw_response=response,
            token_usage=response.input_tokens + response.output_tokens,
        )

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
