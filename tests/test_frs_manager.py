from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from faith_pa.pa.frs_manager import FRSManager, InputClassification


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


@pytest.fixture
def event_publisher() -> object:
    from faith_shared.protocol.events import EventPublisher

    return EventPublisher(FakeRedis(), "pa")


@pytest.mark.asyncio
async def test_handle_new_requirement_updates_frs(tmp_path: Path, event_publisher) -> None:
    llm = AsyncMock(side_effect=["new_requirement", "The API shall support rate limiting", "NONE"])
    manager = FRSManager(tmp_path / ".faith", event_publisher, llm, project_name="Demo")

    result = await manager.handle_user_input(
        "Add rate limiting", ["developer"], {"task-1": "Implement API"}
    )

    content = manager.read_frs()
    assert result.entry_id == "REQ-001"
    assert "REQ-001" in content
    assert "rate limiting" in content


@pytest.mark.asyncio
async def test_handle_correction_adds_decision(tmp_path: Path, event_publisher) -> None:
    frs_dir = tmp_path / ".faith"
    docs_dir = frs_dir / "docs"
    docs_dir.mkdir(parents=True)
    docs_dir.joinpath("frs.md").write_text(
        "# Project FRS — Demo\n## Last Updated: 2026-01-01 00:00 UTC by PA\n\n### Requirements\n- REQ-001: Use JWT\n\n### Decisions\n\n### Out of Scope\n\n### Open Questions\n",
        encoding="utf-8",
    )
    llm = AsyncMock(
        side_effect=[
            "correction",
            "ENTRY_ID: REQ-001\nUPDATED_TEXT: Use session cookies\nDECISION: JWT was overkill",
            "NONE",
        ]
    )
    manager = FRSManager(frs_dir, event_publisher, llm, project_name="Demo")

    result = await manager.handle_user_input("Use session cookies instead", ["developer"], {})

    content = manager.read_frs()
    assert result.classification is InputClassification.CORRECTION
    assert "Use session cookies" in content
    assert "DEC-001" in content


@pytest.mark.asyncio
async def test_handle_question_records_open_question(tmp_path: Path, event_publisher) -> None:
    llm = AsyncMock(side_effect=["question", "Should we support SSO?"])
    manager = FRSManager(tmp_path / ".faith", event_publisher, llm, project_name="Demo")

    result = await manager.handle_user_input("Do we need SSO?", [], {})

    assert result.entry_id is None
    assert "Should we support SSO?" in manager.read_frs()

