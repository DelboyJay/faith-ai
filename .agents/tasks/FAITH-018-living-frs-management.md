# FAITH-018 — Living FRS Document Management

**Phase:** 4 — PA Core
**Complexity:** M
**Model:** Opus / GPT-5.4 high reasoning
**Status:** TODO
**Dependencies:** FAITH-015
**FRS Reference:** Section 3.8

---

## Objective

Implement the PA's FRS management workflow: receive user natural language input about project requirements, classify it (new requirement, refinement, correction, or question), update `.faith/docs/frs.md` with numbered REQ/DEC entries, publish a `file:changed` event, determine which active agents and tasks are affected, and issue updated instructions to those agents. The FRS is the single source of truth for all project requirements — agents query it via RAG and reference entries by ID (`context_ref: frs/REQ-012`) rather than quoting full text.

---

## Architecture

```
faith/pa/
├── __init__.py
└── frs_manager.py       ← FRSManager class (this task)

tests/
└── test_frs_manager.py  ← Tests (this task)
```

The FRS document lives at `.faith/docs/frs.md` within the project workspace. The `FRSManager` is a PA-internal component — it is called by the PA's main loop when user input is classified as requirements-related.

---

## Files to Create

### 1. `faith/pa/frs_manager.py`

```python
"""Living FRS document management for the FAITH Project Agent.

Handles the full FRS workflow: classify user input, update the FRS
document at .faith/docs/frs.md, publish change events, determine
affected agents/tasks, and issue updated instructions.

FRS Reference: Section 3.8
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from faith.protocol.events import EventPublisher, EventType, FaithEvent

logger = logging.getLogger("faith.pa.frs_manager")


class InputClassification(str, Enum):
    """Classification of user input relative to the FRS."""

    NEW_REQUIREMENT = "new_requirement"
    REFINEMENT = "refinement"
    CORRECTION = "correction"
    QUESTION = "question"


# ──────────────────────────────────────────────────
# FRS document structure constants
# ──────────────────────────────────────────────────

FRS_TEMPLATE = """\
# Project FRS — {project_name}
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

# Ordered list of all sections for parsing
ALL_SECTIONS = [
    SECTION_REQUIREMENTS,
    SECTION_DECISIONS,
    SECTION_OUT_OF_SCOPE,
    SECTION_OPEN_QUESTIONS,
]

# Regex patterns for numbered entries
REQ_PATTERN = re.compile(r"^- REQ-(\d+):", re.MULTILINE)
DEC_PATTERN = re.compile(r"^- DEC-(\d+):", re.MULTILINE)


class FRSManager:
    """Manages the living FRS document for a FAITH project.

    The FRSManager is a PA-internal component responsible for:
    1. Classifying user input as requirement/refinement/correction/question
    2. Updating .faith/docs/frs.md with numbered entries
    3. Publishing file:changed events when the FRS changes
    4. Determining which agents/tasks are affected by changes
    5. Generating updated instructions for affected agents

    Attributes:
        faith_dir: Path to the project's .faith directory.
        frs_path: Path to .faith/docs/frs.md.
        event_publisher: EventPublisher for system-events.
        llm_call: Async callable for LLM inference (from PA's LLMClient).
        project_name: Human-readable project name.
    """

    def __init__(
        self,
        faith_dir: Path,
        event_publisher: EventPublisher,
        llm_call: Any,
        project_name: str = "Untitled Project",
    ):
        """Initialise the FRS manager.

        Args:
            faith_dir: Path to the project's .faith directory.
            event_publisher: EventPublisher for broadcasting file changes.
            llm_call: Async callable with signature
                (messages: list[dict], model: str) -> str.
                This is the PA's LLM client — used to classify input
                and generate FRS entries.
            project_name: Human-readable project name for the FRS header.
        """
        self.faith_dir = faith_dir
        self.frs_path = faith_dir / "docs" / "frs.md"
        self.event_publisher = event_publisher
        self.llm_call = llm_call
        self.project_name = project_name

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    async def handle_user_input(
        self,
        user_input: str,
        active_agents: list[str],
        active_tasks: dict[str, str],
        model: str = "gpt-4o",
    ) -> FRSUpdateResult:
        """Process user input through the full FRS workflow.

        This is the main entry point called by the PA when user input
        is requirements-related.

        Args:
            user_input: The user's natural language input.
            active_agents: List of currently active agent IDs.
            active_tasks: Dict of task_id -> task_description for
                active tasks.
            model: LLM model to use for classification and generation.

        Returns:
            FRSUpdateResult with classification, changes made, and
            instructions for affected agents.
        """
        # Step 1: Classify the input
        classification = await self.classify_input(user_input, model)
        logger.info(f"User input classified as: {classification.value}")

        # Step 2: Handle based on classification
        if classification == InputClassification.QUESTION:
            return await self._handle_question(
                user_input, active_agents, active_tasks, model
            )

        # Step 3: Generate the FRS entry/update via LLM
        frs_content = self.read_frs()
        updated_content, entry_id = await self._generate_frs_update(
            user_input, classification, frs_content, model
        )

        # Step 4: Write the updated FRS
        self.write_frs(updated_content)
        logger.info(f"FRS updated: {entry_id} ({classification.value})")

        # Step 5: Publish file:changed event
        await self._publish_frs_changed(entry_id, classification)

        # Step 6: Determine affected agents and generate instructions
        affected = await self._determine_affected_agents(
            user_input, entry_id, classification,
            active_agents, active_tasks, model
        )

        return FRSUpdateResult(
            classification=classification,
            entry_id=entry_id,
            affected_agents=affected,
            frs_path=str(self.frs_path),
        )

    async def classify_input(
        self, user_input: str, model: str = "gpt-4o"
    ) -> InputClassification:
        """Classify user input as requirement, refinement, correction, or question.

        Uses the PA's LLM to determine the intent of the user's input
        relative to the current FRS state.

        Args:
            user_input: The user's natural language input.
            model: LLM model to use for classification.

        Returns:
            The InputClassification enum value.
        """
        frs_summary = self._get_frs_summary()

        messages = [
            {
                "role": "system",
                "content": (
                    "You are classifying user input for a project requirements document.\n"
                    "Respond with EXACTLY one of these words:\n"
                    "- new_requirement — the user is adding a brand new requirement\n"
                    "- refinement — the user is refining or elaborating on an existing requirement or decision\n"
                    "- correction — the user is correcting or reversing a previous requirement or decision\n"
                    "- question — the user is asking a question that needs discussion before it becomes a requirement\n\n"
                    "Current FRS summary:\n" + frs_summary
                ),
            },
            {"role": "user", "content": user_input},
        ]

        response = await self.llm_call(messages, model)
        response = response.strip().lower().replace('"', "").replace("'", "")

        # Parse the classification — be lenient with LLM output
        for cls in InputClassification:
            if cls.value in response:
                return cls

        # Default to new_requirement if classification is ambiguous
        logger.warning(
            f"Ambiguous classification '{response}' — defaulting to new_requirement"
        )
        return InputClassification.NEW_REQUIREMENT

    def read_frs(self) -> str:
        """Read the current FRS document from disk.

        Returns:
            The FRS content, or an empty template if the file
            doesn't exist yet.
        """
        try:
            return self.frs_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.info(
                f"FRS not found at {self.frs_path} — will create from template"
            )
            return ""

    def write_frs(self, content: str) -> None:
        """Write updated FRS content to disk.

        Creates the parent directory if it doesn't exist.

        Args:
            content: The full FRS markdown content to write.
        """
        self.frs_path.parent.mkdir(parents=True, exist_ok=True)
        self.frs_path.write_text(content, encoding="utf-8")
        logger.debug(f"FRS written to {self.frs_path}")

    def ensure_frs_exists(self) -> str:
        """Ensure the FRS file exists, creating from template if needed.

        Returns:
            The FRS content (existing or newly created).
        """
        content = self.read_frs()
        if not content:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            content = FRS_TEMPLATE.format(
                project_name=self.project_name,
                timestamp=timestamp,
            )
            self.write_frs(content)
            logger.info(f"Created new FRS from template at {self.frs_path}")
        return content

    def get_next_req_id(self, content: str) -> str:
        """Get the next available REQ-NNN identifier.

        Args:
            content: Current FRS content.

        Returns:
            Next REQ ID string (e.g. "REQ-001").
        """
        matches = REQ_PATTERN.findall(content)
        if not matches:
            return "REQ-001"
        max_num = max(int(m) for m in matches)
        return f"REQ-{max_num + 1:03d}"

    def get_next_dec_id(self, content: str) -> str:
        """Get the next available DEC-NNN identifier.

        Args:
            content: Current FRS content.

        Returns:
            Next DEC ID string (e.g. "DEC-001").
        """
        matches = DEC_PATTERN.findall(content)
        if not matches:
            return "DEC-001"
        max_num = max(int(m) for m in matches)
        return f"DEC-{max_num + 1:03d}"

    def parse_sections(self, content: str) -> dict[str, str]:
        """Parse the FRS document into named sections.

        Args:
            content: Full FRS markdown content.

        Returns:
            Dict mapping section header to section body text.
        """
        sections: dict[str, str] = {}

        for i, section_header in enumerate(ALL_SECTIONS):
            start = content.find(section_header)
            if start == -1:
                sections[section_header] = ""
                continue

            # Content starts after the header line
            body_start = content.find("\n", start)
            if body_start == -1:
                sections[section_header] = ""
                continue
            body_start += 1  # skip the newline

            # Find the end — either the next section or end of file
            end = len(content)
            for next_header in ALL_SECTIONS[i + 1 :]:
                next_start = content.find(next_header, body_start)
                if next_start != -1:
                    end = next_start
                    break

            sections[section_header] = content[body_start:end].rstrip("\n")

        return sections

    def update_section(
        self, content: str, section_header: str, new_entry: str
    ) -> str:
        """Append an entry to a specific FRS section.

        Args:
            content: Full FRS markdown content.
            section_header: The section to update (e.g. "### Requirements").
            new_entry: The entry text to append (e.g. "- REQ-005: ...").

        Returns:
            Updated FRS content with the new entry appended to
            the specified section.
        """
        start = content.find(section_header)
        if start == -1:
            logger.warning(
                f"Section '{section_header}' not found in FRS — appending"
            )
            return content + f"\n\n{section_header}\n{new_entry}\n"

        # Find the end of this section's content
        body_start = content.find("\n", start) + 1

        # Find the next section header
        end = len(content)
        section_idx = ALL_SECTIONS.index(section_header) if section_header in ALL_SECTIONS else -1
        if section_idx >= 0:
            for next_header in ALL_SECTIONS[section_idx + 1 :]:
                next_start = content.find(next_header, body_start)
                if next_start != -1:
                    end = next_start
                    break

        # Get existing section body and append new entry
        existing_body = content[body_start:end].rstrip()

        if existing_body:
            new_body = existing_body + "\n" + new_entry + "\n"
        else:
            new_body = new_entry + "\n"

        # Reconstruct the document
        before = content[:body_start]
        after = content[end:]
        updated = before + new_body + "\n" + after

        # Update the timestamp
        updated = self._update_timestamp(updated)

        return updated

    def replace_entry(
        self, content: str, entry_id: str, new_text: str
    ) -> str:
        """Replace an existing numbered entry in the FRS.

        Used for corrections and refinements that modify an existing
        REQ or DEC entry.

        Args:
            content: Full FRS markdown content.
            entry_id: The entry ID to replace (e.g. "REQ-003").
            new_text: The replacement text (excluding the "- REQ-003: " prefix).

        Returns:
            Updated FRS content with the entry replaced.
        """
        pattern = re.compile(
            rf"^(- {re.escape(entry_id)}:) .+$", re.MULTILINE
        )
        replacement = f"\\1 {new_text}"
        updated, count = pattern.subn(replacement, content)

        if count == 0:
            logger.warning(f"Entry {entry_id} not found in FRS for replacement")
            return content

        updated = self._update_timestamp(updated)
        return updated

    # ──────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────

    def _update_timestamp(self, content: str) -> str:
        """Update the 'Last Updated' line in the FRS header.

        Args:
            content: FRS content.

        Returns:
            Content with updated timestamp.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        updated = re.sub(
            r"^## Last Updated:.*$",
            f"## Last Updated: {timestamp} by PA",
            content,
            count=1,
            flags=re.MULTILINE,
        )
        return updated

    def _get_frs_summary(self) -> str:
        """Build a concise summary of current FRS state for LLM context.

        Returns:
            Summary string listing existing entry IDs, or a note
            that no FRS exists yet.
        """
        content = self.read_frs()
        if not content:
            return "No FRS exists yet — this will be the first entry."

        req_ids = [f"REQ-{m}" for m in REQ_PATTERN.findall(content)]
        dec_ids = [f"DEC-{m}" for m in DEC_PATTERN.findall(content)]

        sections = self.parse_sections(content)
        out_of_scope = sections.get(SECTION_OUT_OF_SCOPE, "").strip()
        open_questions = sections.get(SECTION_OPEN_QUESTIONS, "").strip()

        lines = []
        if req_ids:
            lines.append(f"Existing requirements: {', '.join(req_ids)}")
        if dec_ids:
            lines.append(f"Existing decisions: {', '.join(dec_ids)}")
        if out_of_scope:
            lines.append(f"Out of scope items present: yes")
        if open_questions:
            lines.append(f"Open questions present: yes")

        # Include the full FRS so the LLM can reason about refinements
        lines.append("\nFull FRS content:\n" + content)

        return "\n".join(lines) if lines else "FRS exists but is empty."

    async def _generate_frs_update(
        self,
        user_input: str,
        classification: InputClassification,
        frs_content: str,
        model: str,
    ) -> tuple[str, str]:
        """Use the LLM to generate the FRS update.

        Args:
            user_input: The user's natural language input.
            classification: How the input was classified.
            frs_content: Current FRS content.
            model: LLM model to use.

        Returns:
            Tuple of (updated_frs_content, entry_id).
        """
        # Ensure FRS exists
        if not frs_content:
            frs_content = self.ensure_frs_exists()

        if classification == InputClassification.NEW_REQUIREMENT:
            return await self._add_new_requirement(
                user_input, frs_content, model
            )
        elif classification == InputClassification.REFINEMENT:
            return await self._refine_entry(
                user_input, frs_content, model
            )
        elif classification == InputClassification.CORRECTION:
            return await self._correct_entry(
                user_input, frs_content, model
            )

        # Should not reach here — questions are handled separately
        raise ValueError(f"Unexpected classification: {classification}")

    async def _add_new_requirement(
        self, user_input: str, frs_content: str, model: str
    ) -> tuple[str, str]:
        """Add a new requirement entry to the FRS.

        Returns:
            Tuple of (updated_content, new_req_id).
        """
        req_id = self.get_next_req_id(frs_content)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are writing a requirement entry for a project FRS document.\n"
                    "Convert the user's input into a clear, concise requirement statement.\n"
                    "Respond with ONLY the requirement text — no prefix, no ID, no bullet.\n"
                    "Be specific and actionable. One sentence if possible, two if needed.\n"
                    "Do NOT include the REQ-NNN prefix — that will be added automatically."
                ),
            },
            {"role": "user", "content": user_input},
        ]

        entry_text = await self.llm_call(messages, model)
        entry_text = entry_text.strip()

        new_entry = f"- {req_id}: {entry_text}"
        updated = self.update_section(
            frs_content, SECTION_REQUIREMENTS, new_entry
        )
        return updated, req_id

    async def _refine_entry(
        self, user_input: str, frs_content: str, model: str
    ) -> tuple[str, str]:
        """Refine an existing FRS entry.

        The LLM identifies which entry to refine and generates the
        updated text. If no specific entry is referenced, it creates
        a new requirement instead.

        Returns:
            Tuple of (updated_content, entry_id).
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You are refining an entry in a project FRS document.\n"
                    "Given the user's refinement and the current FRS, determine:\n"
                    "1. Which entry (REQ-NNN or DEC-NNN) is being refined\n"
                    "2. The updated text for that entry\n\n"
                    "Respond in EXACTLY this format (two lines only):\n"
                    "ENTRY_ID: REQ-001\n"
                    "UPDATED_TEXT: The refined requirement text here\n\n"
                    "If the refinement doesn't clearly map to an existing entry, "
                    "respond with:\n"
                    "ENTRY_ID: NEW\n"
                    "UPDATED_TEXT: The new requirement text here\n\n"
                    "Current FRS:\n" + frs_content
                ),
            },
            {"role": "user", "content": user_input},
        ]

        response = await self.llm_call(messages, model)
        entry_id, updated_text = self._parse_entry_response(response)

        if entry_id == "NEW":
            # Create a new requirement instead
            req_id = self.get_next_req_id(frs_content)
            new_entry = f"- {req_id}: {updated_text}"
            updated = self.update_section(
                frs_content, SECTION_REQUIREMENTS, new_entry
            )
            return updated, req_id

        updated = self.replace_entry(frs_content, entry_id, updated_text)
        return updated, entry_id

    async def _correct_entry(
        self, user_input: str, frs_content: str, model: str
    ) -> tuple[str, str]:
        """Correct an existing FRS entry.

        Similar to refinement but the intent is to fix an error.
        May also add a DEC entry documenting the correction rationale.

        Returns:
            Tuple of (updated_content, entry_id).
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You are correcting an entry in a project FRS document.\n"
                    "Given the user's correction and the current FRS, determine:\n"
                    "1. Which entry (REQ-NNN or DEC-NNN) is being corrected\n"
                    "2. The corrected text for that entry\n"
                    "3. Optionally, a decision entry documenting why the correction was made\n\n"
                    "Respond in EXACTLY this format:\n"
                    "ENTRY_ID: REQ-001\n"
                    "UPDATED_TEXT: The corrected requirement text here\n"
                    "DECISION: Optional rationale (or NONE if not needed)\n\n"
                    "Current FRS:\n" + frs_content
                ),
            },
            {"role": "user", "content": user_input},
        ]

        response = await self.llm_call(messages, model)
        entry_id, updated_text = self._parse_entry_response(response)
        decision_text = self._parse_decision_from_response(response)

        # Apply the correction
        updated = self.replace_entry(frs_content, entry_id, updated_text)

        # Add a decision entry if the LLM provided rationale
        if decision_text:
            dec_id = self.get_next_dec_id(updated)
            dec_entry = f"- {dec_id}: Corrected {entry_id} — {decision_text}"
            updated = self.update_section(
                updated, SECTION_DECISIONS, dec_entry
            )

        return updated, entry_id

    async def _handle_question(
        self,
        user_input: str,
        active_agents: list[str],
        active_tasks: dict[str, str],
        model: str,
    ) -> "FRSUpdateResult":
        """Handle a question by adding it to Open Questions.

        Args:
            user_input: The user's question.
            active_agents: Currently active agent IDs.
            active_tasks: Active task_id -> description mapping.
            model: LLM model to use.

        Returns:
            FRSUpdateResult with the question recorded.
        """
        frs_content = self.read_frs()
        if not frs_content:
            frs_content = self.ensure_frs_exists()

        messages = [
            {
                "role": "system",
                "content": (
                    "You are recording a question in a project FRS document.\n"
                    "Convert the user's question into a clear, concise open question.\n"
                    "Respond with ONLY the question text — no prefix, no bullet.\n"
                    "Keep the original intent but make it precise."
                ),
            },
            {"role": "user", "content": user_input},
        ]

        question_text = await self.llm_call(messages, model)
        question_text = question_text.strip()

        new_entry = f"- {question_text}"
        updated = self.update_section(
            frs_content, SECTION_OPEN_QUESTIONS, new_entry
        )
        self.write_frs(updated)

        return FRSUpdateResult(
            classification=InputClassification.QUESTION,
            entry_id=None,
            affected_agents=[],
            frs_path=str(self.frs_path),
        )

    async def _publish_frs_changed(
        self, entry_id: str, classification: InputClassification
    ) -> None:
        """Publish a file:changed event for the FRS update.

        Args:
            entry_id: The entry that was added or modified.
            classification: How the change was classified.
        """
        event = FaithEvent(
            event=EventType.FILE_CHANGED,
            source="pa",
            data={
                "path": str(self.frs_path),
                "file": "frs.md",
                "change_type": classification.value,
                "entry_id": entry_id,
            },
        )
        await self.event_publisher.publish(event)
        logger.debug(f"Published file:changed event for FRS ({entry_id})")

    async def _determine_affected_agents(
        self,
        user_input: str,
        entry_id: str,
        classification: InputClassification,
        active_agents: list[str],
        active_tasks: dict[str, str],
        model: str,
    ) -> list[AffectedAgent]:
        """Determine which active agents are affected by the FRS change.

        Uses the LLM to reason about which agents/tasks need to be
        notified based on the change and their current assignments.

        Args:
            user_input: Original user input.
            entry_id: The FRS entry that changed.
            classification: How the change was classified.
            active_agents: Currently active agent IDs.
            active_tasks: Active task_id -> description mapping.
            model: LLM model to use.

        Returns:
            List of AffectedAgent with instructions for each.
        """
        if not active_agents:
            return []

        task_summary = "\n".join(
            f"- {tid}: {desc}" for tid, desc in active_tasks.items()
        ) or "No active tasks."

        agent_list = ", ".join(active_agents)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are determining which agents are affected by an FRS change.\n"
                    "Given the change and the list of active agents and tasks, "
                    "identify which agents need updated instructions.\n\n"
                    "Respond with one line per affected agent in this format:\n"
                    "AGENT: agent-id | INSTRUCTION: what the agent needs to do differently\n\n"
                    "If no agents are affected, respond with: NONE\n\n"
                    f"Active agents: {agent_list}\n"
                    f"Active tasks:\n{task_summary}\n"
                    f"FRS change: {classification.value} — {entry_id}\n"
                ),
            },
            {"role": "user", "content": user_input},
        ]

        response = await self.llm_call(messages, model)

        if "NONE" in response.upper() and "|" not in response:
            return []

        affected = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line or "AGENT:" not in line.upper():
                continue

            parts = line.split("|", 1)
            if len(parts) != 2:
                continue

            agent_part = parts[0].strip()
            instruction_part = parts[1].strip()

            # Extract agent ID
            agent_id = agent_part.split(":", 1)[-1].strip()
            if agent_id not in active_agents:
                continue

            # Extract instruction
            instruction = instruction_part.split(":", 1)[-1].strip()

            affected.append(
                AffectedAgent(
                    agent_id=agent_id,
                    entry_id=entry_id,
                    instruction=instruction,
                )
            )

        return affected

    @staticmethod
    def _parse_entry_response(response: str) -> tuple[str, str]:
        """Parse the LLM's ENTRY_ID / UPDATED_TEXT response.

        Args:
            response: Raw LLM response text.

        Returns:
            Tuple of (entry_id, updated_text).
        """
        entry_id = "NEW"
        updated_text = ""

        for line in response.strip().split("\n"):
            line = line.strip()
            upper = line.upper()
            if upper.startswith("ENTRY_ID:"):
                entry_id = line.split(":", 1)[1].strip().upper()
            elif upper.startswith("UPDATED_TEXT:"):
                updated_text = line.split(":", 1)[1].strip()

        return entry_id, updated_text

    @staticmethod
    def _parse_decision_from_response(response: str) -> Optional[str]:
        """Parse an optional DECISION line from the LLM response.

        Args:
            response: Raw LLM response text.

        Returns:
            Decision text, or None if not present or NONE.
        """
        for line in response.strip().split("\n"):
            line = line.strip()
            if line.upper().startswith("DECISION:"):
                text = line.split(":", 1)[1].strip()
                if text.upper() == "NONE":
                    return None
                return text
        return None


# ──────────────────────────────────────────────────
# Data classes for results
# ──────────────────────────────────────────────────


class AffectedAgent:
    """An agent affected by an FRS change, with instructions.

    Attributes:
        agent_id: The affected agent's identifier.
        entry_id: The FRS entry that triggered the change.
        instruction: What the agent needs to do differently.
    """

    def __init__(self, agent_id: str, entry_id: str, instruction: str):
        self.agent_id = agent_id
        self.entry_id = entry_id
        self.instruction = instruction

    def __repr__(self) -> str:
        return (
            f"AffectedAgent(agent_id={self.agent_id!r}, "
            f"entry_id={self.entry_id!r}, "
            f"instruction={self.instruction!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AffectedAgent):
            return NotImplemented
        return (
            self.agent_id == other.agent_id
            and self.entry_id == other.entry_id
            and self.instruction == other.instruction
        )


class FRSUpdateResult:
    """Result of processing user input through the FRS workflow.

    Attributes:
        classification: How the input was classified.
        entry_id: The FRS entry that was created or modified (None for questions).
        affected_agents: List of agents needing updated instructions.
        frs_path: Path to the FRS file.
    """

    def __init__(
        self,
        classification: InputClassification,
        entry_id: Optional[str],
        affected_agents: list[AffectedAgent],
        frs_path: str,
    ):
        self.classification = classification
        self.entry_id = entry_id
        self.affected_agents = affected_agents
        self.frs_path = frs_path

    def __repr__(self) -> str:
        return (
            f"FRSUpdateResult(classification={self.classification.value!r}, "
            f"entry_id={self.entry_id!r}, "
            f"affected_agents={len(self.affected_agents)})"
        )
```

### 2. `faith/pa/__init__.py`

```python
"""FAITH Project Agent — core PA components."""

from faith.pa.frs_manager import FRSManager, InputClassification, FRSUpdateResult, AffectedAgent

__all__ = [
    "FRSManager",
    "InputClassification",
    "FRSUpdateResult",
    "AffectedAgent",
]
```

### 3. `tests/test_frs_manager.py`

```python
"""Tests for the FAITH FRS Manager.

Covers input classification, FRS document parsing and manipulation,
event publishing, affected agent determination, and the full
handle_user_input workflow.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from faith.pa.frs_manager import (
    AffectedAgent,
    FRSManager,
    FRSUpdateResult,
    InputClassification,
    FRS_TEMPLATE,
    SECTION_REQUIREMENTS,
    SECTION_DECISIONS,
    SECTION_OUT_OF_SCOPE,
    SECTION_OPEN_QUESTIONS,
    REQ_PATTERN,
    DEC_PATTERN,
)
from faith.protocol.events import EventPublisher, EventType, FaithEvent


# ──────────────────────────────────────────────────
# Fake Redis and EventPublisher for testing
# ──────────────────────────────────────────────────


class FakeRedis:
    """Minimal fake async Redis client for testing."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))

    def pubsub(self):
        return MagicMock()


# ──────────────────────────────────────────────────
# Sample FRS content for tests
# ──────────────────────────────────────────────────

SAMPLE_FRS = """\
# Project FRS — Test Project
## Last Updated: 2026-03-24 10:00 UTC by PA

### Requirements
- REQ-001: The system shall authenticate users via JWT tokens
- REQ-002: The API shall support pagination for list endpoints

### Decisions
- DEC-001: Use PostgreSQL as the primary database

### Out of Scope
- Mobile native applications

### Open Questions
- Should we support WebSocket for real-time updates?
"""


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────


@pytest.fixture
def tmp_faith_dir(tmp_path):
    """Create a temporary .faith directory."""
    faith_dir = tmp_path / ".faith"
    (faith_dir / "docs").mkdir(parents=True)
    return faith_dir


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def event_publisher(fake_redis):
    return EventPublisher(fake_redis, source="pa")


@pytest.fixture
def mock_llm():
    """Async callable mock for LLM inference."""
    return AsyncMock()


@pytest.fixture
def frs_manager(tmp_faith_dir, event_publisher, mock_llm):
    """Create an FRSManager with test fixtures."""
    return FRSManager(
        faith_dir=tmp_faith_dir,
        event_publisher=event_publisher,
        llm_call=mock_llm,
        project_name="Test Project",
    )


@pytest.fixture
def frs_manager_with_content(frs_manager):
    """FRSManager with SAMPLE_FRS already written."""
    frs_manager.write_frs(SAMPLE_FRS)
    return frs_manager


# ──────────────────────────────────────────────────
# FRS template and file creation tests
# ──────────────────────────────────────────────────


def test_read_frs_returns_empty_when_missing(frs_manager):
    """Reading a nonexistent FRS returns empty string."""
    assert frs_manager.read_frs() == ""


def test_ensure_frs_creates_template(frs_manager):
    """ensure_frs_exists creates the file from template."""
    content = frs_manager.ensure_frs_exists()
    assert "# Project FRS — Test Project" in content
    assert "### Requirements" in content
    assert "### Decisions" in content
    assert "### Out of Scope" in content
    assert "### Open Questions" in content
    # File was actually written
    assert frs_manager.frs_path.exists()


def test_ensure_frs_preserves_existing(frs_manager_with_content):
    """ensure_frs_exists does not overwrite an existing FRS."""
    content = frs_manager_with_content.ensure_frs_exists()
    assert "REQ-001" in content
    assert "JWT tokens" in content


def test_write_frs_creates_parent_dirs(tmp_path):
    """write_frs creates parent directories if they don't exist."""
    faith_dir = tmp_path / ".faith"
    # Note: docs/ directory does NOT exist yet
    manager = FRSManager(
        faith_dir=faith_dir,
        event_publisher=MagicMock(),
        llm_call=AsyncMock(),
    )
    manager.write_frs("# Test FRS")
    assert manager.frs_path.exists()
    assert manager.frs_path.read_text(encoding="utf-8") == "# Test FRS"


# ──────────────────────────────────────────────────
# ID generation tests
# ──────────────────────────────────────────────────


def test_get_next_req_id_empty():
    """First requirement gets REQ-001."""
    manager = FRSManager(
        faith_dir=Path("/tmp"),
        event_publisher=MagicMock(),
        llm_call=AsyncMock(),
    )
    assert manager.get_next_req_id("### Requirements\n") == "REQ-001"


def test_get_next_req_id_increments(frs_manager_with_content):
    """Next req ID increments from highest existing."""
    content = frs_manager_with_content.read_frs()
    assert frs_manager_with_content.get_next_req_id(content) == "REQ-003"


def test_get_next_dec_id_increments(frs_manager_with_content):
    """Next dec ID increments from highest existing."""
    content = frs_manager_with_content.read_frs()
    assert frs_manager_with_content.get_next_dec_id(content) == "DEC-002"


def test_get_next_dec_id_empty():
    """First decision gets DEC-001."""
    manager = FRSManager(
        faith_dir=Path("/tmp"),
        event_publisher=MagicMock(),
        llm_call=AsyncMock(),
    )
    assert manager.get_next_dec_id("### Decisions\n") == "DEC-001"


# ──────────────────────────────────────────────────
# Section parsing tests
# ──────────────────────────────────────────────────


def test_parse_sections(frs_manager):
    """parse_sections extracts all four sections."""
    sections = frs_manager.parse_sections(SAMPLE_FRS)
    assert "REQ-001" in sections[SECTION_REQUIREMENTS]
    assert "REQ-002" in sections[SECTION_REQUIREMENTS]
    assert "DEC-001" in sections[SECTION_DECISIONS]
    assert "Mobile" in sections[SECTION_OUT_OF_SCOPE]
    assert "WebSocket" in sections[SECTION_OPEN_QUESTIONS]


def test_parse_sections_empty_document(frs_manager):
    """Parsing an empty string returns empty sections."""
    sections = frs_manager.parse_sections("")
    for section in sections.values():
        assert section == ""


def test_parse_sections_missing_section(frs_manager):
    """Missing sections return empty strings."""
    partial_frs = "### Requirements\n- REQ-001: Something\n"
    sections = frs_manager.parse_sections(partial_frs)
    assert "REQ-001" in sections[SECTION_REQUIREMENTS]
    assert sections[SECTION_DECISIONS] == ""


# ──────────────────────────────────────────────────
# Section update tests
# ──────────────────────────────────────────────────


def test_update_section_appends_requirement(frs_manager):
    """update_section appends a new entry to the specified section."""
    updated = frs_manager.update_section(
        SAMPLE_FRS,
        SECTION_REQUIREMENTS,
        "- REQ-003: The system shall support OAuth2 login",
    )
    assert "REQ-003: The system shall support OAuth2 login" in updated
    # Original entries preserved
    assert "REQ-001" in updated
    assert "REQ-002" in updated


def test_update_section_updates_timestamp(frs_manager):
    """update_section refreshes the Last Updated timestamp."""
    updated = frs_manager.update_section(
        SAMPLE_FRS,
        SECTION_REQUIREMENTS,
        "- REQ-003: New requirement",
    )
    assert "2026-03-24 10:00 UTC" not in updated
    assert "by PA" in updated


def test_update_section_to_decisions(frs_manager):
    """Entries can be added to any section."""
    updated = frs_manager.update_section(
        SAMPLE_FRS,
        SECTION_DECISIONS,
        "- DEC-002: Use Redis for caching",
    )
    assert "DEC-002: Use Redis for caching" in updated
    assert "DEC-001" in updated  # Original preserved


def test_update_section_to_out_of_scope(frs_manager):
    """Out of scope items can be appended."""
    updated = frs_manager.update_section(
        SAMPLE_FRS,
        SECTION_OUT_OF_SCOPE,
        "- Desktop native applications",
    )
    assert "Desktop native applications" in updated
    assert "Mobile native applications" in updated


# ──────────────────────────────────────────────────
# Entry replacement tests
# ──────────────────────────────────────────────────


def test_replace_entry_updates_text(frs_manager):
    """replace_entry modifies an existing entry's text."""
    updated = frs_manager.replace_entry(
        SAMPLE_FRS,
        "REQ-001",
        "The system shall authenticate users via OAuth2 and JWT tokens",
    )
    assert "OAuth2 and JWT" in updated
    # Original REQ-001 text gone
    assert "- REQ-001: The system shall authenticate users via JWT tokens" not in updated


def test_replace_entry_preserves_other_entries(frs_manager):
    """Replacing one entry does not affect others."""
    updated = frs_manager.replace_entry(
        SAMPLE_FRS, "REQ-001", "Updated text"
    )
    assert "REQ-002: The API shall support pagination" in updated
    assert "DEC-001" in updated


def test_replace_entry_nonexistent_returns_unchanged(frs_manager):
    """Replacing a nonexistent entry returns the original content."""
    updated = frs_manager.replace_entry(
        SAMPLE_FRS, "REQ-999", "This should not appear"
    )
    assert updated == SAMPLE_FRS


def test_replace_entry_updates_timestamp(frs_manager):
    """replace_entry refreshes the Last Updated timestamp."""
    updated = frs_manager.replace_entry(
        SAMPLE_FRS, "REQ-001", "Updated text"
    )
    assert "2026-03-24 10:00 UTC" not in updated


# ──────────────────────────────────────────────────
# Input classification tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_new_requirement(frs_manager, mock_llm):
    """LLM returning 'new_requirement' is parsed correctly."""
    mock_llm.return_value = "new_requirement"
    result = await frs_manager.classify_input("We need rate limiting")
    assert result == InputClassification.NEW_REQUIREMENT


@pytest.mark.asyncio
async def test_classify_refinement(frs_manager, mock_llm):
    """LLM returning 'refinement' is parsed correctly."""
    mock_llm.return_value = "refinement"
    result = await frs_manager.classify_input(
        "The JWT tokens should expire after 1 hour"
    )
    assert result == InputClassification.REFINEMENT


@pytest.mark.asyncio
async def test_classify_correction(frs_manager, mock_llm):
    """LLM returning 'correction' is parsed correctly."""
    mock_llm.return_value = "correction"
    result = await frs_manager.classify_input(
        "Actually, use session cookies instead of JWT"
    )
    assert result == InputClassification.CORRECTION


@pytest.mark.asyncio
async def test_classify_question(frs_manager, mock_llm):
    """LLM returning 'question' is parsed correctly."""
    mock_llm.return_value = "question"
    result = await frs_manager.classify_input(
        "Should we support SSO?"
    )
    assert result == InputClassification.QUESTION


@pytest.mark.asyncio
async def test_classify_ambiguous_defaults_to_new(frs_manager, mock_llm):
    """Ambiguous LLM output defaults to new_requirement."""
    mock_llm.return_value = "I'm not sure, maybe something new?"
    result = await frs_manager.classify_input("Something unclear")
    assert result == InputClassification.NEW_REQUIREMENT


@pytest.mark.asyncio
async def test_classify_tolerates_extra_text(frs_manager, mock_llm):
    """Classification works even if LLM includes extra text."""
    mock_llm.return_value = "This is a refinement of an existing requirement."
    result = await frs_manager.classify_input("Add pagination limits")
    assert result == InputClassification.REFINEMENT


# ──────────────────────────────────────────────────
# Full workflow tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_new_requirement(frs_manager_with_content, mock_llm):
    """Full workflow for adding a new requirement."""
    mock_llm.side_effect = [
        "new_requirement",                          # classify
        "The system shall enforce rate limiting at 100 req/min",  # generate entry
        "NONE",                                     # affected agents
    ]

    result = await frs_manager_with_content.handle_user_input(
        user_input="We need rate limiting at 100 requests per minute",
        active_agents=["software-developer"],
        active_tasks={"task-001": "Implement auth module"},
    )

    assert result.classification == InputClassification.NEW_REQUIREMENT
    assert result.entry_id == "REQ-003"
    assert result.affected_agents == []

    # Verify FRS was updated
    content = frs_manager_with_content.read_frs()
    assert "REQ-003" in content
    assert "rate limiting" in content


@pytest.mark.asyncio
async def test_handle_refinement(frs_manager_with_content, mock_llm):
    """Full workflow for refining an existing requirement."""
    mock_llm.side_effect = [
        "refinement",                                # classify
        "ENTRY_ID: REQ-001\nUPDATED_TEXT: The system shall authenticate users via JWT with 1-hour expiry",  # refine
        "NONE",                                      # affected agents
    ]

    result = await frs_manager_with_content.handle_user_input(
        user_input="JWT tokens should expire after 1 hour",
        active_agents=[],
        active_tasks={},
    )

    assert result.classification == InputClassification.REFINEMENT
    assert result.entry_id == "REQ-001"

    content = frs_manager_with_content.read_frs()
    assert "1-hour expiry" in content


@pytest.mark.asyncio
async def test_handle_correction_with_decision(
    frs_manager_with_content, mock_llm
):
    """Full workflow for correcting a requirement with a rationale decision."""
    mock_llm.side_effect = [
        "correction",                                # classify
        (
            "ENTRY_ID: REQ-001\n"
            "UPDATED_TEXT: The system shall authenticate users via session cookies\n"
            "DECISION: JWT was too complex for the current scope; session cookies are simpler and sufficient"
        ),                                           # correct
        "NONE",                                      # affected agents
    ]

    result = await frs_manager_with_content.handle_user_input(
        user_input="Actually, let's use session cookies instead of JWT",
        active_agents=[],
        active_tasks={},
    )

    assert result.classification == InputClassification.CORRECTION
    assert result.entry_id == "REQ-001"

    content = frs_manager_with_content.read_frs()
    assert "session cookies" in content
    assert "DEC-002" in content
    assert "Corrected REQ-001" in content


@pytest.mark.asyncio
async def test_handle_question(frs_manager_with_content, mock_llm):
    """Full workflow for recording a question."""
    mock_llm.side_effect = [
        "question",                                  # classify
        "Should we support single sign-on (SSO) via SAML or OIDC?",  # format question
    ]

    result = await frs_manager_with_content.handle_user_input(
        user_input="What about SSO? SAML or OIDC?",
        active_agents=[],
        active_tasks={},
    )

    assert result.classification == InputClassification.QUESTION
    assert result.entry_id is None

    content = frs_manager_with_content.read_frs()
    assert "SSO" in content


@pytest.mark.asyncio
async def test_handle_input_with_affected_agents(
    frs_manager_with_content, mock_llm
):
    """Affected agents are identified and given instructions."""
    mock_llm.side_effect = [
        "new_requirement",                          # classify
        "The API shall require authentication on all endpoints",  # entry
        "AGENT: software-developer | INSTRUCTION: Update all API endpoints to require JWT auth headers",  # affected
    ]

    result = await frs_manager_with_content.handle_user_input(
        user_input="All API endpoints must be authenticated",
        active_agents=["software-developer", "tester"],
        active_tasks={"task-001": "Implement API endpoints"},
    )

    assert len(result.affected_agents) == 1
    assert result.affected_agents[0].agent_id == "software-developer"
    assert "JWT auth" in result.affected_agents[0].instruction


@pytest.mark.asyncio
async def test_handle_input_creates_frs_if_missing(frs_manager, mock_llm):
    """FRS is created from template if it doesn't exist."""
    mock_llm.side_effect = [
        "new_requirement",
        "The system shall support user registration",
        "NONE",
    ]

    result = await frs_manager.handle_user_input(
        user_input="Users need to be able to register",
        active_agents=[],
        active_tasks={},
    )

    assert result.entry_id == "REQ-001"
    content = frs_manager.read_frs()
    assert "# Project FRS — Test Project" in content
    assert "REQ-001" in content


# ──────────────────────────────────────────────────
# Event publishing tests
# ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_frs_changed_event_published(
    frs_manager_with_content, mock_llm, fake_redis
):
    """A file:changed event is published when the FRS is updated."""
    mock_llm.side_effect = [
        "new_requirement",
        "Rate limiting requirement",
        "NONE",
    ]

    await frs_manager_with_content.handle_user_input(
        user_input="Add rate limiting",
        active_agents=[],
        active_tasks={},
    )

    # Check that a file:changed event was published to system-events
    events = [
        (ch, json.loads(msg))
        for ch, msg in fake_redis.published
        if ch == "system-events"
    ]
    assert len(events) >= 1

    file_events = [
        e for _, e in events if e.get("event") == "file:changed"
    ]
    assert len(file_events) == 1
    assert file_events[0]["data"]["file"] == "frs.md"
    assert file_events[0]["data"]["entry_id"] == "REQ-003"


# ──────────────────────────────────────────────────
# LLM response parsing tests
# ──────────────────────────────────────────────────


def test_parse_entry_response_standard():
    """Standard ENTRY_ID / UPDATED_TEXT response is parsed."""
    response = "ENTRY_ID: REQ-001\nUPDATED_TEXT: Updated requirement text"
    entry_id, text = FRSManager._parse_entry_response(response)
    assert entry_id == "REQ-001"
    assert text == "Updated requirement text"


def test_parse_entry_response_new():
    """NEW entry response is parsed correctly."""
    response = "ENTRY_ID: NEW\nUPDATED_TEXT: A brand new requirement"
    entry_id, text = FRSManager._parse_entry_response(response)
    assert entry_id == "NEW"
    assert text == "A brand new requirement"


def test_parse_entry_response_case_insensitive():
    """Parsing tolerates case variations in labels."""
    response = "entry_id: REQ-005\nupdated_text: Some text"
    entry_id, text = FRSManager._parse_entry_response(response)
    assert entry_id == "REQ-005"
    assert text == "Some text"


def test_parse_decision_present():
    """Decision text is extracted when present."""
    response = (
        "ENTRY_ID: REQ-001\n"
        "UPDATED_TEXT: Updated\n"
        "DECISION: Changed because of new requirements"
    )
    decision = FRSManager._parse_decision_from_response(response)
    assert decision == "Changed because of new requirements"


def test_parse_decision_none():
    """DECISION: NONE returns None."""
    response = (
        "ENTRY_ID: REQ-001\n"
        "UPDATED_TEXT: Updated\n"
        "DECISION: NONE"
    )
    decision = FRSManager._parse_decision_from_response(response)
    assert decision is None


def test_parse_decision_absent():
    """Missing DECISION line returns None."""
    response = "ENTRY_ID: REQ-001\nUPDATED_TEXT: Updated"
    decision = FRSManager._parse_decision_from_response(response)
    assert decision is None


# ──────────────────────────────────────────────────
# AffectedAgent and FRSUpdateResult tests
# ──────────────────────────────────────────────────


def test_affected_agent_equality():
    """AffectedAgent supports equality comparison."""
    a = AffectedAgent("dev", "REQ-001", "Update auth")
    b = AffectedAgent("dev", "REQ-001", "Update auth")
    assert a == b


def test_affected_agent_repr():
    """AffectedAgent has a readable repr."""
    a = AffectedAgent("dev", "REQ-001", "Update auth")
    assert "dev" in repr(a)
    assert "REQ-001" in repr(a)


def test_frs_update_result_repr():
    """FRSUpdateResult has a readable repr."""
    result = FRSUpdateResult(
        classification=InputClassification.NEW_REQUIREMENT,
        entry_id="REQ-001",
        affected_agents=[],
        frs_path="/tmp/frs.md",
    )
    assert "new_requirement" in repr(result)
    assert "REQ-001" in repr(result)
```

---

## Integration Points

The FRSManager integrates with three key FAITH components:

```python
# FAITH-015: SessionManager provides context for handle_user_input
# The PA's main loop calls FRSManager when user input is requirements-related:
from faith.pa.frs_manager import FRSManager

frs_manager = FRSManager(
    faith_dir=session_manager.faith_dir,
    event_publisher=event_publisher,
    llm_call=llm_client.chat_completion,
    project_name=session_manager.project_name,
)

# When the PA detects requirements-related input:
result = await frs_manager.handle_user_input(
    user_input="We need rate limiting at 100 req/min",
    active_agents=session_manager.get_active_agent_ids(),
    active_tasks=session_manager.get_active_tasks(),
)

# The PA then issues instructions to affected agents:
for agent in result.affected_agents:
    msg = CompactMessage(
        from_agent="pa",
        to_agent=agent.agent_id,
        channel=f"pa-{agent.agent_id}",
        msg_id=next_msg_id(),
        type=MessageType.INSTRUCTION,
        tags=["frs-update"],
        summary=agent.instruction,
        context_ref=f"frs/{result.entry_id}",
    )
    await redis.publish(f"pa-{agent.agent_id}", msg.to_json())
```

```python
# FAITH-008: EventPublisher broadcasts file:changed events
# When the FRS is updated, agents watching frs.md are notified
# automatically via the event system. Agents query the updated
# frs.md via the RAG tool (FAITH-028) using context_ref:
#   context_ref: frs/REQ-012
```

```python
# FAITH-013: LLMClient provides the llm_call callable
# The FRSManager accepts any async callable with the signature:
#   async def llm_call(messages: list[dict], model: str) -> str
# In production this is the PA's LLMClient.chat_completion method.
# In tests it is replaced with AsyncMock.
```

---

## Acceptance Criteria

1. `FRSManager.__init__` correctly sets up paths (`.faith/docs/frs.md`), stores references to the event publisher and LLM callable, and does not create files on construction.
2. `ensure_frs_exists()` creates the FRS from template with all four sections (Requirements, Decisions, Out of Scope, Open Questions) when no file exists, and preserves existing content when the file already exists.
3. `classify_input()` uses the PA's LLM to classify user input into one of four categories (new_requirement, refinement, correction, question) and defaults to `new_requirement` when the LLM response is ambiguous.
4. `handle_user_input()` executes the full workflow: classify, update FRS, publish `file:changed` event, determine affected agents, and return an `FRSUpdateResult`.
5. New requirements are appended to the Requirements section with auto-incrementing `REQ-NNN` IDs.
6. Refinements and corrections use `replace_entry()` to modify existing entries in-place, preserving all other entries.
7. Corrections optionally add a `DEC-NNN` entry documenting the rationale for the change.
8. Questions are appended to the Open Questions section without a numbered prefix.
9. The `Last Updated` timestamp is refreshed on every FRS modification.
10. A `file:changed` event is published to `system-events` after every FRS update, including the entry ID and change type.
11. `_determine_affected_agents()` uses the LLM to identify which active agents need updated instructions and returns `AffectedAgent` objects with actionable instructions.
12. `parse_sections()` correctly splits the FRS into its four sections and handles missing sections gracefully.
13. `get_next_req_id()` and `get_next_dec_id()` correctly increment from the highest existing ID.
14. All 35 tests in `tests/test_frs_manager.py` pass, covering template creation, ID generation, section parsing, section updates, entry replacement, input classification, full workflow (all four classification paths), event publishing, LLM response parsing, and data class behaviour.

---

## Notes for Implementer

- **LLM call interface**: The `llm_call` parameter is a plain async callable `(messages, model) -> str`, not the full `LLMClient` object. This keeps the FRSManager decoupled from the client's retry logic and provider routing (FAITH-013). In production the PA passes `llm_client.chat_completion`; in tests it is an `AsyncMock`.
- **No direct agent communication**: The FRSManager returns an `FRSUpdateResult` with affected agents and instructions. It does not send messages to agents directly — that is the PA's responsibility. This keeps the FRSManager focused on FRS logic and testable in isolation.
- **FRS path**: The FRS lives at `.faith/docs/frs.md` inside the project workspace. This is the new architecture — there are no references to `workspace/docs/frs.md`.
- **File history**: The filesystem tool (FAITH-022) automatically versions `.faith/docs/frs.md` on every write via its file watcher. The FRSManager does not need to handle versioning — it simply writes the file and publishes the event.
- **RAG integration**: Agents query the FRS via the RAG tool (FAITH-028), which auto-indexes files in `.faith/docs/` on `file:changed` events. The `context_ref: frs/REQ-012` notation in compact protocol messages lets agents reference specific entries without quoting the full text.
- **Concurrency**: Only one FRSManager instance exists (inside the PA), and the PA processes user input sequentially. There is no concurrent write risk. If this changes in the future, file locking should be added.
- **LLM prompt engineering**: The classification and generation prompts are deliberately simple and structured. If the LLM produces unexpected output, the parsing methods are lenient — `classify_input` defaults to `new_requirement`, and `_parse_entry_response` defaults to `NEW`. This ensures the workflow never crashes on malformed LLM output.
- **Token efficiency**: The FRS summary sent to the LLM for classification includes the full FRS content. For very large FRS documents (hundreds of entries), consider summarising only the entry IDs and section headings. This is acceptable for the initial implementation since most project FRS documents will be modest in size.
- **Testing pattern**: Tests use `AsyncMock` with `side_effect` lists to simulate the multi-step LLM call sequence in `handle_user_input`. Each test sets up the exact sequence of LLM responses needed for the workflow path under test.

