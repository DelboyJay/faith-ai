from faith_pa.agent import AgentMessage, BaseAgent
from faith_pa.config.models import AgentConfig, SystemConfig
from faith_pa.utils.tokens import (
    FALLBACK_CHARS_PER_TOKEN,
    context_threshold,
    count_message_tokens,
    count_text_tokens,
    truncate_text_to_token_limit,
)


def build_agent_config(**overrides):
    payload = {
        "name": "Software Developer",
        "role": "software-developer",
        "tools": ["filesystem", "python"],
        "cag_documents": [],
    }
    payload.update(overrides)
    return AgentConfig.model_validate(payload)


def build_system_config(**overrides):
    payload = {
        "pa": {"model": "gpt-5.4"},
        "default_agent_model": "gpt-5.4-mini",
    }
    payload.update(overrides)
    return SystemConfig.model_validate(payload)


def test_count_text_tokens_fallback(monkeypatch):
    monkeypatch.setattr("faith.utils.tokens.tiktoken", None)
    assert count_text_tokens("abcd") == 1
    assert count_text_tokens("abcde") == 2
    assert FALLBACK_CHARS_PER_TOKEN == 4


def test_message_token_count_is_non_zero():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Do the task."},
    ]
    assert count_message_tokens(messages) > 0


def test_truncate_text_to_token_limit_fallback(monkeypatch):
    monkeypatch.setattr("faith.utils.tokens.tiktoken", None)
    text = "abcdefghij"
    assert truncate_text_to_token_limit(text, 2) == text[: 2 * FALLBACK_CHARS_PER_TOKEN]


def test_context_threshold_applies_safety_margin():
    assert context_threshold(1000, 50) == 450


def test_base_agent_assembles_context_in_expected_order(tmp_path):
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
        "# CAG Document: docs/frs.md"
    )
    assert assembly.recent_messages[0].content == "Previous reply"
    assert assembly.current_task == "Implement the feature"


def test_base_agent_uses_agent_model_override():
    agent = BaseAgent(
        agent_id="agent-2",
        config=build_agent_config(model="claude-sonnet"),
        system_config=build_system_config(default_agent_model="gpt-default"),
        prompt_text="Prompt",
    )
    assert agent.model_name == "claude-sonnet"


def test_base_agent_falls_back_to_system_model():
    agent = BaseAgent(
        agent_id="agent-3",
        config=build_agent_config(),
        system_config=build_system_config(default_agent_model="gpt-default"),
        prompt_text="Prompt",
    )
    assert agent.model_name == "gpt-default"


def test_base_agent_limits_recent_messages():
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


def test_base_agent_cag_documents_obey_token_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("faith.agent.base.count_text_tokens", lambda text, model=None: len(text))
    monkeypatch.setattr(
        "faith.agent.base.truncate_text_to_token_limit",
        lambda text, token_limit, model=None: text[:token_limit],
    )

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

    docs = agent.load_cag_documents()
    assert len(docs) == 1
    assert len(docs[0]) == 30


def test_context_needs_compaction_when_threshold_exceeded(monkeypatch):
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
    parsed = BaseAgent.parse_llm_response({"message": {"content": "done"}})
    assert parsed.content == "done"


def test_agent_message_to_chat_message():
    message = AgentMessage(role="user", content="hello", name="dev")
    assert message.to_chat_message() == {"role": "user", "content": "hello", "name": "dev"}



