"""Base agent runtime primitives for the FAITH POC."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faith.agent.llm_client import LLMClient
from faith.agent.summariser import ContextSummariser
from faith.config.models import AgentConfig, SystemConfig
from faith.utils.tokens import (
    context_threshold,
    count_message_tokens,
    count_text_tokens,
    over_context_threshold,
    truncate_text_to_token_limit,
)

DEFAULT_CONTEXT_WINDOW = 128_000


@dataclass(slots=True)
class AgentMessage:
    """Chat-style message stored in an agent context."""

    role: str
    content: str
    disposable: bool = False
    name: str | None = None

    def to_chat_message(self) -> dict[str, str]:
        payload = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass(slots=True)
class ContextAssembly:
    """Detailed context assembly output for one agent invocation."""

    system_prompt: str
    recent_messages: list[AgentMessage] = field(default_factory=list)
    current_task: str = ""

    def to_messages(self) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(message.to_chat_message() for message in self.recent_messages)
        messages.append({"role": "user", "content": self.current_task})
        return messages


@dataclass(slots=True)
class AgentResponse:
    """Normalized LLM response returned by the agent runtime."""

    content: str
    raw_response: Any = None
    token_usage: int | None = None


class BaseAgent:
    """Minimal base runtime for FAITH specialist agents."""

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
        return self.config.model or self.system_config.default_agent_model

    def add_message(
        self, role: str, content: str, *, disposable: bool = False, name: str | None = None
    ) -> None:
        self.recent_messages.append(
            AgentMessage(role=role, content=content, disposable=disposable, name=name)
        )
        max_messages = self.config.context.max_messages
        if len(self.recent_messages) > max_messages:
            self.recent_messages = self.recent_messages[-max_messages:]

    def build_role_reminder(self) -> str:
        tools = ", ".join(self.config.tools) if self.config.tools else "none"
        return f"Agent: {self.config.name} ({self.config.role})\nTools: {tools}"

    def load_cag_documents(self) -> list[str]:
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
        return ContextAssembly(
            system_prompt=self.build_system_prompt(),
            recent_messages=list(self.recent_messages),
            current_task=current_task.strip(),
        )

    def build_completion_payload(self, current_task: str) -> dict[str, Any]:
        assembly = self.assemble_context(current_task)
        return {
            "model": self.model_name,
            "messages": assembly.to_messages(),
            "agent_id": self.agent_id,
            "agent_role": self.config.role,
            "mcp_native": self.config.mcp_native,
        }

    def count_context_tokens(self, current_task: str) -> int:
        assembly = self.assemble_context(current_task)
        return count_message_tokens(assembly.to_messages(), self.model_name)

    def context_needs_compaction(self, current_task: str) -> bool:
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
        return context_threshold(
            self.context_window_tokens,
            self.config.context.summary_threshold_pct,
        )

    async def _call_llm(self, current_task: str, *, temperature: float = 0.7) -> AgentResponse:
        assembly = self.assemble_context(current_task)
        response = await self.llm_client.chat(assembly.to_messages(), temperature=temperature)
        return AgentResponse(
            content=response.content,
            raw_response=response,
            token_usage=response.input_tokens + response.output_tokens,
        )

    async def compact_context(self, llm_call: Callable[[str], Awaitable[Any]] | None = None) -> str:
        if llm_call is None:

            async def llm_call(prompt: str) -> str:
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
