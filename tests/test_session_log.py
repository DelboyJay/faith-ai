"""Description:
    Verify the standalone FAITH session-log writer primitives.

Requirements:
    - Prove session and task metadata round-trip with the FRS-required fields.
    - Prove channel logs and agent cross-reference indices are written in the expected markdown format.
"""

from __future__ import annotations

import json
from pathlib import Path

from faith_pa.logging.session_log import AgentIndexWriter, SessionLogWriter, SessionMeta, TaskMeta


def test_session_and_task_meta_round_trip() -> None:
    """Description:
        Verify standalone session and task metadata models round-trip through JSON.

    Requirements:
        - This test is needed to prove the log-writer metadata files preserve the FRS-required fields.
        - Verify `SessionMeta` and `TaskMeta` deserialize back to the same key identifiers and counters.
    """

    session = SessionMeta(
        session_id="sess-0042",
        task_count=2,
        agents_active=["software-developer", "qa-engineer"],
        total_input_tokens=120,
        total_output_tokens=45,
        total_estimated_cost=0.25,
    )
    task = TaskMeta(
        task_id="task-001-143201.456",
        session_id="sess-0042",
        goal="Implement JWT auth module",
        agents=["software-developer"],
        channels=["ch-auth-feature"],
        input_tokens=100,
        output_tokens=25,
        estimated_cost=0.15,
    )

    restored_session = SessionMeta.from_json(session.to_json())
    restored_task = TaskMeta.from_json(task.to_json())

    assert restored_session.session_id == "sess-0042"
    assert restored_session.task_count == 2
    assert restored_session.total_estimated_cost == 0.25
    assert restored_task.task_id == "task-001-143201.456"
    assert restored_task.channels == ["ch-auth-feature"]
    assert restored_task.estimated_cost == 0.15


def test_session_log_writer_creates_task_channel_logs_and_agent_index(tmp_path: Path) -> None:
    """Description:
        Verify the standalone session log writer creates task channel logs and agent indices.

    Requirements:
        - This test is needed to prove FAITH can persist one log per channel per task without duplicating content.
        - Verify channel markdown is written, session/task metadata is updated, and the per-agent index links back to the session.

    :param tmp_path: Temporary pytest directory fixture.
    """

    sessions_dir = tmp_path / ".faith" / "sessions"
    agents_dir = tmp_path / ".faith" / "agents"
    (agents_dir / "software-developer").mkdir(parents=True)

    session_writer = SessionLogWriter(sessions_dir=sessions_dir, session_id="sess-0042")
    task_writer = session_writer.create_task(
        goal="Implement JWT auth module",
        task_id="task-001-143201.456",
    )
    task_writer.add_agent("software-developer")
    channel_writer = task_writer.get_channel_writer("ch-auth-feature")
    channel_writer.write_message(
        timestamp="14:32:01",
        sender="software-developer",
        recipient="qa-engineer",
        msg_type="review_request",
        summary="auth module done, 3 endpoints, JWT httponly cookies",
        status="complete",
        needs="test coverage for token expiry edge case",
    )
    task_writer.update_tokens(input_tokens=1240, output_tokens=380, estimated_cost=0.0)
    session_writer.update_tokens(input_tokens=1240, output_tokens=380, estimated_cost=0.0)
    task_writer.complete()
    session_writer.complete()

    AgentIndexWriter(agents_dir=agents_dir).update_index(
        agent_name="software-developer",
        session_id="sess-0042",
        session_date="2026-03-23",
        task_id="task-001-143201.456",
        task_goal="Implement JWT auth module",
        channels=["ch-auth-feature"],
    )

    session_meta = json.loads(
        (session_writer.session_dir / "session.meta.json").read_text(encoding="utf-8")
    )
    task_meta = json.loads((task_writer.task_dir / "task.meta.json").read_text(encoding="utf-8"))
    channel_log = (task_writer.task_dir / "ch-auth-feature.log").read_text(encoding="utf-8")
    index_text = (agents_dir / "software-developer" / "sessions.index.md").read_text(
        encoding="utf-8"
    )

    assert session_meta["task_count"] == 1
    assert session_meta["total_input_tokens"] == 1240
    assert task_meta["task_id"] == "task-001-143201.456"
    assert task_meta["status"] == "complete"
    assert "# Channel: ch-auth-feature" in channel_log
    assert "software-developer → qa-engineer" in channel_log
    assert "sess-0042" in index_text
    assert "task-001-143201.456" in index_text
