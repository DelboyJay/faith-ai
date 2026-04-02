"""Description:
    Maintain the living project FRS document from user input and runtime context.

Requirements:
    - Create the FRS document when it does not yet exist.
    - Classify incoming user input into requirement, refinement, correction, or question flows.
    - Persist generated FRS updates and publish change events.
    - Identify affected agents when a new or changed FRS entry impacts active work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from faith_shared.protocol.events import EventPublisher, EventType, FaithEvent


class InputClassification(str, Enum):
    """Description:
        Enumerate the high-level user-input classifications used by the FRS manager.

    Requirements:
        - Distinguish new requirements, refinements, corrections, and open questions.
    """

    NEW_REQUIREMENT = "new_requirement"
    REFINEMENT = "refinement"
    CORRECTION = "correction"
    QUESTION = "question"


@dataclass(slots=True)
class AffectedAgent:
    """Description:
        Describe one active agent affected by an FRS change.

    Requirements:
        - Preserve the target agent, referenced FRS context, and follow-up instruction.

    :param agent_id: Affected agent identifier.
    :param context_ref: Reference to the affected FRS entry.
    :param instruction: Follow-up instruction for the affected agent.
    """

    agent_id: str
    context_ref: str
    instruction: str


@dataclass(slots=True)
class FRSUpdateResult:
    """Description:
        Represent the result of processing one user input into the living FRS.

    Requirements:
        - Preserve the input classification, affected entry, affected agents, and FRS path.

    :param classification: Classification chosen for the input.
    :param entry_id: Affected FRS entry identifier, when one exists.
    :param affected_agents: Active agents that should be informed of the change.
    :param frs_path: Path to the updated FRS document.
    """

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
    """Description:
        Update the living FRS document in response to user input.

    Requirements:
        - Maintain the project FRS under ``.faith/docs/frs.md``.
        - Use the supplied LLM callable for classification and entry generation.
        - Publish FRS change events after each successful update.

    :param faith_dir: Project ``.faith`` directory.
    :param event_publisher: Event publisher used for FRS change notifications.
    :param llm_call: Async callable used for classification and text generation.
    :param project_name: Human-readable project name for the generated template.
    """

    def __init__(
        self,
        faith_dir: Path,
        event_publisher: EventPublisher | Any,
        llm_call: Any,
        project_name: str = "Untitled Project",
    ) -> None:
        """Description:
            Initialise the FRS manager.

        Requirements:
            - Resolve the FRS path under ``docs/frs.md`` inside the supplied ``.faith`` directory.

        :param faith_dir: Project ``.faith`` directory.
        :param event_publisher: Event publisher used for FRS change notifications.
        :param llm_call: Async callable used for classification and text generation.
        :param project_name: Human-readable project name for the generated template.
        """

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
        """Description:
            Classify one user input and apply the resulting FRS update.

        Requirements:
            - Create the FRS if it does not yet exist.
            - Handle questions by appending to the open-questions section.
            - Publish file-changed events after a successful update.
            - Determine the active agents affected by non-question changes.

        :param user_input: Raw user input to process.
        :param active_agents: Currently active agent identifiers.
        :param active_tasks: Mapping of active task identifiers to descriptions.
        :param model: Model name used for LLM calls.
        :returns: Structured FRS update result.
        """

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
        """Description:
            Classify one user input into the FRS update categories.

        Requirements:
            - Use the supplied LLM callable to return one classification token.
            - Fall back to ``new_requirement`` when parsing fails.

        :param user_input: Raw user input to classify.
        :param model: Model name used for the classification call.
        :returns: Input classification.
        """

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
        """Description:
            Read the current FRS document from disk.

        Requirements:
            - Return an empty string when the FRS file does not exist.

        :returns: Current FRS document text.
        """

        if not self.frs_path.exists():
            return ""
        return self.frs_path.read_text(encoding="utf-8")

    def write_frs(self, content: str) -> None:
        """Description:
            Persist the supplied FRS document text to disk.

        Requirements:
            - Create the parent documentation directory when needed.

        :param content: FRS document text to write.
        """

        self.frs_path.parent.mkdir(parents=True, exist_ok=True)
        self.frs_path.write_text(content, encoding="utf-8")

    def ensure_frs_exists(self) -> str:
        """Description:
            Ensure the project FRS document exists and return its current content.

        Requirements:
            - Generate the default template when the FRS does not yet exist.

        :returns: Existing or newly created FRS document text.
        """

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
        """Description:
            Return the next sequential requirement identifier.

        Requirements:
            - Scan the current FRS content for existing requirement identifiers.

        :param content: Current FRS document text.
        :returns: Next requirement identifier.
        """

        matches = REQ_PATTERN.findall(content)
        return f"REQ-{(max(map(int, matches)) + 1 if matches else 1):03d}"

    def get_next_dec_id(self, content: str) -> str:
        """Description:
            Return the next sequential decision identifier.

        Requirements:
            - Scan the current FRS content for existing decision identifiers.

        :param content: Current FRS document text.
        :returns: Next decision identifier.
        """

        matches = DEC_PATTERN.findall(content)
        return f"DEC-{(max(map(int, matches)) + 1 if matches else 1):03d}"

    def parse_sections(self, content: str) -> dict[str, str]:
        """Description:
            Parse the canonical FRS sections from the document text.

        Requirements:
            - Return an empty body for any missing section header.

        :param content: Current FRS document text.
        :returns: Mapping of section header to section body text.
        """

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
        """Description:
            Append one entry to a named FRS section and refresh the timestamp.

        Requirements:
            - Create the section when it does not yet exist.
            - Preserve existing section body content.

        :param content: Current FRS document text.
        :param section_header: Target section header.
        :param new_entry: New entry text to append.
        :returns: Updated FRS document text.
        """

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
        """Description:
            Replace one existing FRS entry body and refresh the timestamp.

        Requirements:
            - Leave the content unchanged when the entry identifier is not present.

        :param content: Current FRS document text.
        :param entry_id: Requirement or decision identifier to replace.
        :param new_text: Replacement entry text.
        :returns: Updated FRS document text.
        """

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
        """Description:
            Parse the structured LLM response used for refinements and corrections.

        Requirements:
            - Fall back to ``NEW`` and the raw response text when explicit fields are missing.

        :param response: Raw LLM response text.
        :returns: Parsed entry identifier and replacement text.
        """

        entry_match = re.search(r"ENTRY_ID:\s*(.+)", response, re.IGNORECASE)
        text_match = re.search(r"UPDATED_TEXT:\s*(.+)", response, re.IGNORECASE)
        entry_id = entry_match.group(1).strip() if entry_match else "NEW"
        updated_text = text_match.group(1).strip() if text_match else response.strip()
        return entry_id, updated_text

    @staticmethod
    def _parse_decision_from_response(response: str) -> str | None:
        """Description:
            Parse an optional decision note from a correction response.

        Requirements:
            - Return ``None`` when no decision marker is present or the value is ``NONE``.

        :param response: Raw LLM response text.
        :returns: Parsed decision text, if any.
        """

        match = re.search(r"DECISION:\s*(.+)", response, re.IGNORECASE)
        if not match:
            return None
        value = match.group(1).strip()
        return None if value.upper() == "NONE" else value

    def _update_timestamp(self, content: str) -> str:
        """Description:
            Refresh the ``Last Updated`` line in the FRS document.

        Requirements:
            - Write the timestamp in UTC using the PA ownership suffix.

        :param content: Current FRS document text.
        :returns: Updated FRS document text.
        """

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
        """Description:
            Generate the updated FRS content for one classified user input.

        Requirements:
            - Create a new requirement entry for new requirements.
            - Replace an existing entry for refinements and corrections.
            - Append a decision note for corrections when the LLM provides one.

        :param user_input: Raw user input to process.
        :param classification: Classification chosen for the input.
        :param frs_content: Current FRS document text.
        :param model: Model name used for the update call.
        :returns: Updated FRS content and affected entry identifier.
        """

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
        """Description:
            Publish the standard FRS file-changed event after an update.

        Requirements:
            - Support publisher objects exposing ``publish``, ``file_changed``, or ``events`` storage.

        :param entry_id: Updated requirement or decision identifier, when one exists.
        :param classification: Classification of the change that was applied.
        """

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
        """Description:
            Determine which active agents should be informed about an FRS change.

        Requirements:
            - Return an empty list when no agents are active.
            - Keep only agents that are currently active.
            - Attach the changed FRS entry reference to each affected agent.

        :param user_input: Raw user input that triggered the update.
        :param entry_id: Updated entry identifier.
        :param classification: Classification of the change.
        :param active_agents: Currently active agent identifiers.
        :param active_tasks: Mapping of active task identifiers to descriptions.
        :param model: Model name used for the affected-agent call.
        :returns: Affected agent payloads.
        """

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
