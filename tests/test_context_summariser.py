import asyncio

from faith.agent.base import AgentMessage
from faith.agent.summariser import ContextSummariser


def test_should_summarise_on_message_count(tmp_path):
    summariser = ContextSummariser(
        agent_id="dev",
        model_name="gpt-5.4",
        context_window_tokens=1000,
        context_config={"summary_threshold_pct": 90, "max_messages": 2},
        faith_dir=tmp_path,
    )
    messages = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}]
    assert summariser.should_summarise(messages) is True


def test_persist_and_load_summary_roundtrip(tmp_path):
    summariser = ContextSummariser(
        agent_id="dev",
        model_name="gpt-5.4",
        context_window_tokens=1000,
        faith_dir=tmp_path,
    )
    summariser.persist_summary("Important summary")
    assert summariser.context_md_path.exists()
    assert summariser.load_summary() == "Important summary"


def test_compact_retains_recent_messages_and_compacts_disposable(tmp_path):
    summariser = ContextSummariser(
        agent_id="dev",
        model_name="gpt-5.4",
        context_window_tokens=1000,
        faith_dir=tmp_path,
        retain_recent_messages=2,
    )

    messages = [
        AgentMessage(role="user", content="old-1"),
        AgentMessage(role="assistant", content="old-2", disposable=True),
        AgentMessage(role="user", content="keep-1"),
        AgentMessage(role="assistant", content="keep-2"),
    ]

    async def fake_llm(prompt: str) -> str:
        assert "old-1" in prompt
        assert "keep-2" in prompt
        return "Summarised context"

    result = asyncio.run(summariser.compact(messages, existing_summary="", llm_call=fake_llm))

    assert result.summary == "Summarised context"
    assert result.compacted_messages >= 2
    assert result.remaining_messages[0]["role"] == "system"
    assert [item["content"] for item in result.remaining_messages[1:]] == ["keep-1", "keep-2"]
    assert summariser.load_summary() == "Summarised context"
