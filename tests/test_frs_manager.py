"""Description:
    Verify FRS creation, updates, and affected-agent handling.

Requirements:
    - Prove new requirements are added to the living FRS.
    - Prove corrections replace entries and record decisions.
    - Prove questions are appended to the open-questions section.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from faith_pa.pa.frs_manager import FRSManager, InputClassification


class FakeRedis:
    """Description:
        Provide a minimal Redis double for FRS manager tests.

    Requirements:
        - Record published event payloads when needed by the publisher.
    """

    def __init__(self) -> None:
        """Description:
            Initialise the fake Redis state.

        Requirements:
            - Start with an empty published-event list.
        """

        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        """Description:
            Record one published message.

        Requirements:
            - Preserve the channel and message for later assertions.

        :param channel: Published channel name.
        :param message: Published message payload.
        """

        self.published.append((channel, message))


@pytest.fixture
def event_publisher() -> object:
    """Description:
        Create a real event publisher backed by the fake Redis test double.

        Requirements:
            - Provide the FRS manager with a publisher compatible with its publish path.

        :returns: Event publisher instance.
    """

    from faith_shared.protocol.events import EventPublisher

    return EventPublisher(FakeRedis(), "pa")


@pytest.mark.asyncio
async def test_handle_new_requirement_updates_frs(tmp_path: Path, event_publisher) -> None:
    """Description:
        Verify a new requirement is added to the living FRS.

        Requirements:
            - This test is needed to prove the FRS manager can create the initial FRS and append a requirement entry.
            - Verify the generated requirement identifier and text are written to the FRS.

        :param tmp_path: Temporary pytest directory fixture.
        :param event_publisher: Event publisher fixture.
    """

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
    """Description:
        Verify a correction replaces an existing requirement and records a linked decision.

        Requirements:
            - This test is needed to prove corrections can update existing FRS content without losing traceability.
            - Verify the corrected text and generated decision entry are both present in the FRS.

        :param tmp_path: Temporary pytest directory fixture.
        :param event_publisher: Event publisher fixture.
    """

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
    """Description:
        Verify questions are appended to the FRS open-questions section.

        Requirements:
            - This test is needed to prove question-style user input is preserved in the living FRS.
            - Verify the formatted open question appears in the FRS content.

        :param tmp_path: Temporary pytest directory fixture.
        :param event_publisher: Event publisher fixture.
    """

    llm = AsyncMock(side_effect=["question", "Should we support SSO?"])
    manager = FRSManager(tmp_path / ".faith", event_publisher, llm, project_name="Demo")

    result = await manager.handle_user_input("Do we need SSO?", [], {})

    assert result.entry_id is None
    assert "Should we support SSO?" in manager.read_frs()
