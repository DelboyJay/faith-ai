"""Living FRS document management for the PA."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from faith.protocol.events import EventPublisher, EventType, FaithEvent


class InputClassification(str, Enum):
    NEW_REQUIREMENT = "new_requirement"
    REFINEMENT = "refinement"
    CORRECTION = "correction"
    QUESTION = "question"


@dataclass(slots=True)
class AffectedAgent:
    agent_id: str
    context_ref: str
    instruction: str


@dataclass(slots=True)
class FRSUpdateResult:
    classification: InputClassification
    entry_id: str | None
    affected_agents: list[AffectedAgent]
    frs_path: str


FRS_TEMPLATE = """# Project FRS — {project_name}
## Last Updated: {timestamp} by PA

### Requirements

### Decisions

### Out of Scope

### Open Questions
"""

SECTION_REQUIREMENTS = "### Requirements"
SECTION_DECISIONS = "### Decisions"
SECTION_OUT_OF_SCOPE = "### Out of Scope"
SECTION_OPEN_QUESTIONS = "### Open Questions"
ALL_SECTIONS = [
    SECTION_REQUIREMENTS,
    SECTION_DECISIONS,
    SECTION_OUT_OF_SCOPE,
    SECTION_OPEN_QUESTIONS,
]
REQ_PATTERN = re.compile(r"^- REQ-(\d+):", re.MULTILINE)
DEC_PATTERN = re.compile(r"^- DEC-(\d+):", re.MULTILINE)


class FRSManager:
    def __init__(
        self,
        faith_dir: Path,
        event_publisher: EventPublisher | Any,
        llm_call: Any,
        project_name: str = "Untitled Project",
    ) -> None:
        self.faith_dir = Path(faith_dir)
        self.frs_path = self.faith_dir / "docs" / "frs.md"
        self.event_publisher = event_publisher
        self.llm_call = llm_call
        self.project_name = project_name

    async def handle_user_input(
        self,
        user_input: str,
        active_agents: list[str],
        active_tasks: dict[str, str],
        model: str = "gpt-5.4",
    ) -> FRSUpdateResult:
        classification = await self.classify_input(user_input, model)
        if classification is InputClassification.QUESTION:
            content = self.ensure_frs_exists()
            formatted = await self.llm_call(
                [
                    {"role": "system", "content": "Format this as one clear open question."},
                    {"role": "user", "content": user_input},
                ],
                model,
            )
            updated = self.update_section(content, SECTION_OPEN_QUESTIONS, f"- {formatted.strip()}")
            self.write_frs(updated)
            await self._publish_frs_changed(entry_id=None, classification=classification)
            return FRSUpdateResult(classification, None, [], str(self.frs_path))

        current = self.ensure_frs_exists()
        updated, entry_id = await self._generate_frs_update(
            user_input, classification, current, model
        )
        self.write_frs(updated)
        await self._publish_frs_changed(entry_id=entry_id, classification=classification)
        affected_agents = await self._determine_affected_agents(
            user_input, entry_id, classification, active_agents, active_tasks, model
        )
        return FRSUpdateResult(classification, entry_id, affected_agents, str(self.frs_path))

    async def classify_input(self, user_input: str, model: str = "gpt-5.4") -> InputClassification:
        response = await self.llm_call(
            [
                {"role": "system", "content": "Return exactly one classification token."},
                {"role": "user", "content": user_input},
            ],
            model,
        )
        normalized = response.strip().lower()
        for classification in InputClassification:
            if classification.value in normalized:
                return classification
        return InputClassification.NEW_REQUIREMENT

    def read_frs(self) -> str:
        if not self.frs_path.exists():
            return ""
        return self.frs_path.read_text(encoding="utf-8")

    def write_frs(self, content: str) -> None:
        self.frs_path.parent.mkdir(parents=True, exist_ok=True)
        self.frs_path.write_text(content, encoding="utf-8")

    def ensure_frs_exists(self) -> str:
        content = self.read_frs()
        if content:
            return content
        content = FRS_TEMPLATE.format(
            project_name=self.project_name,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )
        self.write_frs(content)
        return content

    def get_next_req_id(self, content: str) -> str:
        matches = REQ_PATTERN.findall(content)
        return f"REQ-{(max(map(int, matches)) + 1 if matches else 1):03d}"

    def get_next_dec_id(self, content: str) -> str:
        matches = DEC_PATTERN.findall(content)
        return f"DEC-{(max(map(int, matches)) + 1 if matches else 1):03d}"

    def parse_sections(self, content: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        for idx, header in enumerate(ALL_SECTIONS):
            start = content.find(header)
            if start == -1:
                sections[header] = ""
                continue
            body_start = content.find("\n", start) + 1
            end = len(content)
            for next_header in ALL_SECTIONS[idx + 1 :]:
                next_start = content.find(next_header, body_start)
                if next_start != -1:
                    end = next_start
                    break
            sections[header] = content[body_start:end].rstrip()
        return sections

    def update_section(self, content: str, section_header: str, new_entry: str) -> str:
        start = content.find(section_header)
        if start == -1:
            return content + f"\n{section_header}\n{new_entry}\n"
        body_start = content.find("\n", start) + 1
        end = len(content)
        for next_header in ALL_SECTIONS[ALL_SECTIONS.index(section_header) + 1 :]:
            next_start = content.find(next_header, body_start)
            if next_start != -1:
                end = next_start
                break
        existing = content[body_start:end].rstrip()
        new_body = f"{existing}\n{new_entry}\n".strip("\n") + "\n"
        updated = (
            content[:body_start]
            + new_body
            + ("\n" if not new_body.endswith("\n\n") else "")
            + content[end:]
        )
        return self._update_timestamp(updated)

    def replace_entry(self, content: str, entry_id: str, new_text: str) -> str:
        updated, count = re.subn(
            rf"^(- {re.escape(entry_id)}:) .+$",
            rf"\1 {new_text}",
            content,
            flags=re.MULTILINE,
        )
        if count == 0:
            return content
        return self._update_timestamp(updated)

    @staticmethod
    def _parse_entry_response(response: str) -> tuple[str, str]:
        entry_match = re.search(r"ENTRY_ID:\s*(.+)", response, re.IGNORECASE)
        text_match = re.search(r"UPDATED_TEXT:\s*(.+)", response, re.IGNORECASE)
        entry_id = entry_match.group(1).strip() if entry_match else "NEW"
        updated_text = text_match.group(1).strip() if text_match else response.strip()
        return entry_id, updated_text

    @staticmethod
    def _parse_decision_from_response(response: str) -> str | None:
        match = re.search(r"DECISION:\s*(.+)", response, re.IGNORECASE)
        if not match:
            return None
        value = match.group(1).strip()
        return None if value.upper() == "NONE" else value

    def _update_timestamp(self, content: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return re.sub(
            r"^## Last Updated:.*$",
            f"## Last Updated: {timestamp} by PA",
            content,
            count=1,
            flags=re.MULTILINE,
        )

    async def _generate_frs_update(
        self,
        user_input: str,
        classification: InputClassification,
        frs_content: str,
        model: str,
    ) -> tuple[str, str]:
        if classification is InputClassification.NEW_REQUIREMENT:
            req_id = self.get_next_req_id(frs_content)
            entry = (await self.llm_call([{"role": "user", "content": user_input}], model)).strip()
            return self.update_section(
                frs_content, SECTION_REQUIREMENTS, f"- {req_id}: {entry}"
            ), req_id

        response = await self.llm_call([{"role": "user", "content": user_input}], model)
        entry_id, updated_text = self._parse_entry_response(response)
        updated = self.replace_entry(frs_content, entry_id, updated_text)
        if classification is InputClassification.CORRECTION:
            decision = self._parse_decision_from_response(response)
            if decision:
                dec_id = self.get_next_dec_id(updated)
                updated = self.update_section(
                    updated,
                    SECTION_DECISIONS,
                    f"- {dec_id}: Corrected {entry_id} — {decision}",
                )
        return updated, entry_id

    async def _publish_frs_changed(
        self,
        *,
        entry_id: str | None,
        classification: InputClassification,
    ) -> None:
        event = FaithEvent(
            event=EventType.FILE_CHANGED,
            source="pa",
            data={
                "file": "frs.md",
                "path": str(self.frs_path),
                "entry_id": entry_id,
                "change_type": classification.value,
            },
        )
        if hasattr(self.event_publisher, "publish"):
            await self.event_publisher.publish(event)
        elif hasattr(self.event_publisher, "file_changed"):
            await self.event_publisher.file_changed("frs.md", "", "")
        elif hasattr(self.event_publisher, "events"):
            self.event_publisher.events.append(event)

    async def _determine_affected_agents(
        self,
        user_input: str,
        entry_id: str,
        classification: InputClassification,
        active_agents: list[str],
        active_tasks: dict[str, str],
        model: str,
    ) -> list[AffectedAgent]:
        del classification, active_tasks
        if not active_agents:
            return []
        response = await self.llm_call(
            [
                {
                    "role": "system",
                    "content": (
                        "Return one line per affected agent in the format "
                        "'AGENT: <id> | INSTRUCTION: <instruction>' or NONE."
                    ),
                },
                {"role": "user", "content": user_input},
            ],
            model,
        )
        if response.strip().upper() == "NONE":
            return []
        affected: list[AffectedAgent] = []
        for line in response.splitlines():
            match = re.match(r"AGENT:\s*(.+?)\s*\|\s*INSTRUCTION:\s*(.+)", line.strip())
            if not match:
                continue
            agent_id, instruction = match.groups()
            if agent_id in active_agents:
                affected.append(
                    AffectedAgent(
                        agent_id=agent_id,
                        context_ref=f"frs/{entry_id}",
                        instruction=instruction,
                    )
                )
        return affected
