"""Description:
    Cover the base agent runtime helpers and context assembly flow.

Requirements:
    - Verify token helpers and base-agent context assembly stay stable.
    - Exercise the behaviour using request-style unit tests rather than internal implementation shortcuts.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from faith_pa.agent import AgentMessage, BaseAgent
from faith_pa.config.models import AgentConfig, SystemConfig
from faith_pa.utils.tokens import (
    FALLBACK_CHARS_PER_TOKEN,
    context_threshold,
    count_message_tokens,
    count_text_tokens,
    truncate_text_to_token_limit,
)
from faith_shared.protocol.compact import CompactMessage, MessageType
from faith_shared.protocol.events import SYSTEM_EVENTS_CHANNEL, EventType, FaithEvent


def build_agent_config(**overrides):
    """Description:
        Build a valid agent configuration payload for runtime tests.

    Requirements:
        - Provide the minimum valid agent configuration used by the base-agent tests.
        - Allow callers to override individual fields per scenario.

    :param overrides: Field overrides merged into the baseline agent payload.
    :returns: Validated agent configuration model.
    """

    payload = {
        "name": "Software Developer",
        "role": "software-developer",
        "tools": ["filesystem", "python"],
        "cag_documents": [],
    }
    payload.update(overrides)
    return AgentConfig.model_validate(payload)


def build_system_config(**overrides):
    """Description:
        Build a valid system configuration payload for runtime tests.

    Requirements:
        - Provide the minimum valid system configuration used by the base-agent tests.
        - Allow callers to override individual fields per scenario.

    :param overrides: Field overrides merged into the baseline system payload.
    :returns: Validated system configuration model.
    """

    payload = {
        "pa": {"model": "gpt-5.4"},
        "default_agent_model": "gpt-5.4-mini",
    }
    payload.update(overrides)
    return SystemConfig.model_validate(payload)


class FakePubSub:
    """Description:
        Provide a minimal async Redis pubsub stand-in for agent runtime tests.

    Requirements:
        - Record subscribe and unsubscribe calls for lifecycle assertions.
        - Replay queued pubsub messages in order.

    :param messages: Optional initial queue of pubsub messages.
    """

    def __init__(self, messages: list[dict] | None = None) -> None:
        """Description:
            Initialise the fake pubsub with an optional queued-message list.

        Requirements:
            - Start with deterministic message order and open state.

        :param messages: Optional initial queue of pubsub messages.
        """

        self.messages = list(messages or [])
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        """Description:
            Record requested channel subscriptions.

        Requirements:
            - Preserve subscription order for later assertions.

        :param channels: Channel names being subscribed.
        """

        self.subscribed.extend(channels)

    async def unsubscribe(self, *channels: str) -> None:
        """Description:
            Record requested channel unsubscriptions.

        Requirements:
            - Preserve unsubscription order for later assertions.

        :param channels: Channel names being unsubscribed.
        """

        self.unsubscribed.extend(channels)

    async def get_message(
        self,
        ignore_subscribe_messages: bool = True,
        timeout: float = 1.0,
    ) -> dict | None:
        """Description:
            Return the next queued pubsub message or ``None`` when empty.

        Requirements:
            - Consume queued messages in order.
            - Yield to the event loop when the queue is empty.

        :param ignore_subscribe_messages: Compatibility flag matching the Redis API.
        :param timeout: Requested polling timeout.
        :returns: Next queued pubsub message, or ``None`` when none remain.
        """

        del ignore_subscribe_messages, timeout
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0)
        return None

    async def aclose(self) -> None:
        """Description:
            Mark the fake pubsub as closed.

        Requirements:
            - Allow shutdown tests to verify the pubsub was closed.
        """

        self.closed = True


class FakeRedis:
    """Description:
        Provide the minimal Redis interface needed by the base-agent runtime tests.

    Requirements:
        - Expose ``publish()`` and ``pubsub()`` with deterministic captured state.

    :param pubsub: Fake pubsub instance returned by ``pubsub()``.
    """

    def __init__(self, pubsub: FakePubSub | None = None) -> None:
        """Description:
            Initialise the fake Redis client.

        Requirements:
            - Start with an empty published-message log.

        :param pubsub: Fake pubsub instance returned by ``pubsub()``.
        """

        self._pubsub = pubsub or FakePubSub()
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        """Description:
            Record one published Redis message.

        Requirements:
            - Preserve publish order for later assertions.

        :param channel: Redis channel name.
        :param message: Published message payload.
        """

        self.published.append((channel, message))

    def pubsub(self) -> FakePubSub:
        """Description:
            Return the fake pubsub dependency used by the runtime.

        Requirements:
            - Return the same fake pubsub instance on every call.

        :returns: Fake pubsub instance.
        """

        return self._pubsub


class FakeLLMClient:
    """Description:
        Provide a deterministic LLM client stand-in for agent runtime tests.

    Requirements:
        - Capture chat requests for later assertions.
        - Return a configured response body without network access.

    :param content: Response content returned by ``chat()``.
    """

    def __init__(self, content: str = "Acknowledged.") -> None:
        """Description:
            Initialise the fake LLM client.

        Requirements:
            - Start with an empty request log.

        :param content: Response content returned by ``chat()``.
        """

        self.content = content
        self.calls: list[dict[str, object]] = []

    async def chat(
        self,
        messages: list[dict[str, object]],
        *,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        """Description:
            Record one chat request and return a deterministic structured response.

        Requirements:
            - Preserve the request payload for later assertions.
            - Return an object matching the shared ``LLMResponse`` surface.

        :param messages: Chat-message payload.
        :param model: Optional model override.
        :param fallback_model: Optional fallback-model override.
        :param temperature: Optional temperature override.
        :param max_tokens: Optional output-token cap.
        :returns: Structured fake response object.
        """

        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "fallback_model": fallback_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return type(
            "FakeLLMResponse",
            (),
            {
                "content": self.content,
                "input_tokens": 10,
                "output_tokens": 5,
            },
        )()


def test_count_text_tokens_fallback(monkeypatch):
    """Description:
        Verify the fallback text-token counter works when ``tiktoken`` is unavailable.

    Requirements:
        - This test is needed to prove FAITH still has deterministic token estimates without tokenizer support.
        - Verify the fallback rounds by the configured characters-per-token constant.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    monkeypatch.setattr("faith_pa.utils.tokens.tiktoken", None)
    assert count_text_tokens("abcd") == 1
    assert count_text_tokens("abcde") == 2
    assert FALLBACK_CHARS_PER_TOKEN == 4


def test_message_token_count_is_non_zero():
    """Description:
        Verify message token counting returns a positive value for normal messages.

    Requirements:
        - This test is needed to prove multi-message payloads produce usable token estimates.
        - Verify a simple system-plus-user message list yields a non-zero count.
    """

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Do the task."},
    ]
    assert count_message_tokens(messages) > 0


def test_truncate_text_to_token_limit_fallback(monkeypatch):
    """Description:
        Verify fallback truncation respects the requested token limit.

    Requirements:
        - This test is needed to prove text truncation still works without tokenizer support.
        - Verify the fallback truncates by the configured characters-per-token constant.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    monkeypatch.setattr("faith_pa.utils.tokens.tiktoken", None)
    text = "abcdefghij"
    assert truncate_text_to_token_limit(text, 2) == text[: 2 * FALLBACK_CHARS_PER_TOKEN]


def test_context_threshold_applies_safety_margin():
    """Description:
        Verify context thresholds reserve the configured safety margin.

    Requirements:
        - This test is needed to prove compaction thresholds leave headroom below the hard model limit.
        - Verify a 50 percent threshold on a 1000-token window yields 450 usable tokens after safety margin.
    """

    assert context_threshold(1000, 50) == 450


def test_base_agent_assembles_context_in_expected_order(tmp_path):
    """Description:
        Verify the base agent assembles prompt context in the expected order.

    Requirements:
        - This test is needed to prove the runtime prompt layout stays stable across prompt, summary, CAG, and current-task sections.
        - Verify recent messages and current task are placed into the assembled context payload.

    :param tmp_path: Temporary project workspace.
    """

    doc = tmp_path / "docs" / "frs.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("FRS content", encoding="utf-8")

    config = build_agent_config(cag_documents=["docs/frs.md"])
    system_config = build_system_config()
    agent = BaseAgent(
        agent_id="agent-1",
        config=config,
        system_config=system_config,
        prompt_text="System prompt",
        project_root=tmp_path,
        context_summary="Summary text",
    )
    agent.add_message("assistant", "Previous reply")

    assembly = agent.assemble_context("Implement the feature")
    assert assembly.system_prompt.index("System prompt") < assembly.system_prompt.index(
        "Agent: Software Developer"
    )
    assert assembly.system_prompt.index("Agent: Software Developer") < assembly.system_prompt.index(
        "Context Summary:"
    )
    assert assembly.system_prompt.index("Context Summary:") < assembly.system_prompt.index(
        "--- CAG Reference: docs/frs.md ---"
    )
    assert assembly.recent_messages[0].content == "Previous reply"
    assert assembly.current_task == "Implement the feature"


def test_base_agent_uses_agent_model_override():
    """Description:
        Verify the base agent prefers its own model override when configured.

    Requirements:
        - This test is needed to prove per-agent model selection overrides the system default.
        - Verify the runtime model name matches the explicit agent configuration.
    """

    agent = BaseAgent(
        agent_id="agent-2",
        config=build_agent_config(model="claude-sonnet"),
        system_config=build_system_config(default_agent_model="gpt-default"),
        prompt_text="Prompt",
    )
    assert agent.model_name == "claude-sonnet"


def test_base_agent_falls_back_to_system_model():
    """Description:
        Verify the base agent uses the system default model when no agent override exists.

    Requirements:
        - This test is needed to prove agents inherit the configured default model cleanly.
        - Verify the runtime model name matches the system default.
    """

    agent = BaseAgent(
        agent_id="agent-3",
        config=build_agent_config(),
        system_config=build_system_config(default_agent_model="gpt-default"),
        prompt_text="Prompt",
    )
    assert agent.model_name == "gpt-default"


def test_base_agent_passes_ollama_endpoint_override():
    """Description:
        Verify the base agent passes the configured Ollama endpoint override into the LLM client.

    Requirements:
        - This test is needed to prove agent runtime config can direct local-model traffic to a resolved Ollama endpoint.
        - Verify the LLM client receives the system-configured Ollama endpoint.
    """

    agent = BaseAgent(
        agent_id="agent-override",
        config=build_agent_config(model="ollama/llama3:8b"),
        system_config=build_system_config(
            default_agent_model="ollama/llama3:8b",
            ollama={
                "enabled": True,
                "mode": "external",
                "endpoint": "http://external-ollama:11434",
            },
        ),
        prompt_text="Prompt",
    )
    assert agent.llm_client.ollama_host == "http://external-ollama:11434"


def test_base_agent_limits_recent_messages():
    """Description:
        Verify the base agent enforces the configured recent-message limit.

    Requirements:
        - This test is needed to prove older messages drop off once the configured rolling window is full.
        - Verify only the newest configured messages remain.
    """

    config = build_agent_config(context={"max_messages": 5, "summary_threshold_pct": 50})
    agent = BaseAgent(
        agent_id="agent-4",
        config=config,
        system_config=build_system_config(),
        prompt_text="Prompt",
    )
    agent.add_message("user", "one")
    agent.add_message("assistant", "two")
    agent.add_message("user", "three")
    agent.add_message("assistant", "four")
    agent.add_message("user", "five")
    agent.add_message("assistant", "six")
    assert [message.content for message in agent.recent_messages] == [
        "two",
        "three",
        "four",
        "five",
        "six",
    ]


def test_base_agent_cag_documents_report_budget_validation(tmp_path, monkeypatch):
    """Description:
        Verify CAG document loading reports budget overruns through the validation result.

    Requirements:
        - This test is needed to prove oversized CAG documents are flagged during session-start validation.
        - Verify the validation result is unsuccessful and includes a warning when the budget is exceeded.

    :param tmp_path: Temporary project workspace.
    :param monkeypatch: Pytest monkeypatch fixture.
    """

    monkeypatch.setattr("faith_pa.agent.cag.count_text_tokens", lambda text, model=None: len(text))

    doc = tmp_path / "docs" / "big.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("x" * 200, encoding="utf-8")

    config = build_agent_config(cag_documents=["docs/big.md"], cag_max_tokens=30)
    agent = BaseAgent(
        agent_id="agent-5",
        config=config,
        system_config=build_system_config(),
        prompt_text="Prompt",
        project_root=tmp_path,
    )

    result = agent.load_cag_documents()
    assert result.success is False
    assert len(result.warnings) == 1
    assert "rag" in result.warnings[0].lower()


def test_context_needs_compaction_when_threshold_exceeded(monkeypatch):
    """Description:
        Verify the base agent requests compaction once the context threshold is exceeded.

    Requirements:
        - This test is needed to prove context compaction triggers before the hard window limit is reached.
        - Verify the helper reports ``True`` when the counted tokens exceed the configured threshold.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    monkeypatch.setattr(BaseAgent, "count_context_tokens", lambda self, current_task: 500)
    config = build_agent_config(context={"max_messages": 50, "summary_threshold_pct": 50})
    agent = BaseAgent(
        agent_id="agent-6",
        config=config,
        system_config=build_system_config(),
        prompt_text="Prompt",
        context_window_tokens=900,
    )
    assert agent.context_needs_compaction("Task") is True


def test_build_completion_payload_contains_messages():
    """Description:
        Verify completion payload construction includes the expected model and messages.

    Requirements:
        - This test is needed to prove the LLM client receives a complete chat payload.
        - Verify the payload includes the system message and current task text.
    """

    agent = BaseAgent(
        agent_id="agent-7",
        config=build_agent_config(),
        system_config=build_system_config(),
        prompt_text="Prompt",
    )
    payload = agent.build_completion_payload("Do work")
    assert payload["model"] == "gpt-5.4-mini"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][-1]["content"] == "Do work"


def test_heartbeat_payload_contains_identity():
    """Description:
        Verify heartbeat payload generation includes the expected identity fields.

    Requirements:
        - This test is needed to prove heartbeat events carry enough data for monitoring.
        - Verify the payload includes the event type, agent ID, and channel.
    """

    agent = BaseAgent(
        agent_id="agent-8",
        config=build_agent_config(),
        system_config=build_system_config(),
        prompt_text="Prompt",
    )
    payload = agent.heartbeat_payload(channel="ch-1")
    assert payload["event"] == "agent:heartbeat"
    assert payload["agent_id"] == "agent-8"
    assert payload["channel"] == "ch-1"


def test_parse_llm_response_accepts_dict_message_shape():
    """Description:
        Verify LLM response parsing accepts the dictionary message shape returned by some providers.

    Requirements:
        - This test is needed to prove provider response normalization handles the nested message form.
        - Verify the parsed content matches the nested response payload.
    """

    parsed = BaseAgent.parse_llm_response({"message": {"content": "done"}})
    assert parsed.content == "done"


def test_agent_message_to_chat_message():
    """Description:
        Verify agent messages convert cleanly into chat-message payloads.

    Requirements:
        - This test is needed to prove message serialization keeps role, content, and optional name intact.
        - Verify the emitted dictionary matches the expected chat message shape.
    """

    message = AgentMessage(role="user", content="hello", name="dev")
    assert message.to_chat_message() == {"role": "user", "content": "hello", "name": "dev"}


@pytest.mark.asyncio
async def test_base_agent_subscribe_and_unsubscribe_channel():
    """Description:
        Verify the base agent manages Redis channel subscriptions and channel stores.

    Requirements:
        - This test is needed to prove the runtime can join and leave task channels cleanly.
        - Verify subscribing creates a store and unsubscribing removes the channel from the active set.
    """

    redis = FakeRedis()
    agent = BaseAgent(
        agent_id="agent-subscribe",
        config=build_agent_config(),
        system_config=build_system_config(),
        prompt_text="Prompt",
        redis_client=redis,
    )
    agent._pubsub = redis.pubsub()

    await agent.subscribe_channel("ch-auth")
    await agent.unsubscribe_channel("ch-auth")

    assert redis._pubsub.subscribed == ["ch-auth"]
    assert redis._pubsub.unsubscribed == ["ch-auth"]
    assert "ch-auth" not in agent._subscribed_channels
    assert "ch-auth" in agent._channel_stores


@pytest.mark.asyncio
async def test_base_agent_handle_message_stores_message_and_calls_llm():
    """Description:
        Verify incoming compact-protocol messages are stored and passed through the LLM path.

    Requirements:
        - This test is needed to prove the runtime parses channel traffic into message history before calling the LLM.
        - Verify the stored channel message count and the LLM invocation both occur for one inbound task message.
    """

    redis = FakeRedis()
    llm_client = FakeLLMClient(content="Task acknowledged.")
    agent = BaseAgent(
        agent_id="agent-handle",
        config=build_agent_config(),
        system_config=build_system_config(),
        prompt_text="Prompt",
        redis_client=redis,
        llm_client=llm_client,
    )
    message = CompactMessage(
        from_agent="pa",
        to_agent="agent-handle",
        channel="ch-auth",
        msg_id=1,
        type=MessageType.TASK,
        tags=["code"],
        summary="Implement login form.",
    )

    await agent._handle_message(message.to_json(), "ch-auth")

    assert agent._channel_stores["ch-auth"].count() == 1
    assert llm_client.calls
    assert llm_client.calls[0]["messages"][-1]["content"] == "Implement login form."
    published_events = [
        FaithEvent.from_json(payload)
        for channel_name, payload in redis.published
        if channel_name == SYSTEM_EVENTS_CHANNEL
    ]
    assert any(event.event == EventType.AGENT_TASK_COMPLETE for event in published_events)


@pytest.mark.asyncio
async def test_base_agent_heartbeat_loop_publishes_agent_events():
    """Description:
        Verify the heartbeat loop emits canonical agent-heartbeat events on the system event bus.

    Requirements:
        - This test is needed to prove specialist agents remain observable while running.
        - Verify the runtime publishes at least one ``agent:heartbeat`` event through Redis.
    """

    redis = FakeRedis()
    agent = BaseAgent(
        agent_id="agent-heartbeat",
        config=build_agent_config(),
        system_config=build_system_config(heartbeat_interval_seconds=5),
        prompt_text="Prompt",
        redis_client=redis,
    )
    agent._running = True

    task = asyncio.create_task(agent._heartbeat_loop(interval_seconds=0.01))
    await asyncio.sleep(0.03)
    agent._running = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    published_events = [
        FaithEvent.from_json(payload)
        for channel, payload in redis.published
        if channel == SYSTEM_EVENTS_CHANNEL
    ]
    assert published_events
    assert published_events[0].event == EventType.AGENT_HEARTBEAT
    assert published_events[0].source == "agent-heartbeat"


@pytest.mark.asyncio
async def test_base_agent_run_subscribes_personal_channel_and_shuts_down_cleanly():
    """Description:
        Verify the async runtime subscribes its personal channel, starts, and shuts down cleanly.

    Requirements:
        - This test is needed to prove the base-agent runtime executes the Redis listener lifecycle defined by Phase 3.
        - Verify the personal channel subscription occurs and the pubsub closes on shutdown.
    """

    redis = FakeRedis()
    agent = BaseAgent(
        agent_id="agent-run",
        config=build_agent_config(),
        system_config=build_system_config(),
        prompt_text="Prompt",
        redis_client=redis,
        llm_client=FakeLLMClient(),
    )

    task = asyncio.create_task(agent.run())
    await asyncio.sleep(0.03)
    agent._signal_shutdown()
    await asyncio.wait_for(task, timeout=1.0)

    assert "pa-agent-run" in redis._pubsub.subscribed
    assert "pa-agent-run" in redis._pubsub.unsubscribed
    assert redis._pubsub.closed is True
