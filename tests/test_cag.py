"""Description:
    Cover the Phase 7 CAG manager, provider caching hints, and base-agent integration.

Requirements:
    - Verify CAG document loading, token-budget validation, and reload handling.
    - Verify provider-specific cache hints are applied only where required.
    - Verify the base agent surfaces CAG validation state and reloads changed CAG files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from faith_pa.agent.base import BaseAgent
from faith_pa.config.models import AgentConfig, SystemConfig


def build_agent_config(**overrides: object) -> AgentConfig:
    """Description:
        Build a valid agent configuration payload for CAG tests.

    Requirements:
        - Provide the minimum valid agent configuration used by the CAG suite.
        - Allow callers to override individual fields per scenario.

    :param overrides: Field overrides merged into the baseline payload.
    :returns: Validated agent configuration model.
    """

    payload = {
        "name": "Software Developer",
        "role": "software-developer",
        "tools": ["filesystem"],
        "cag_documents": [],
    }
    payload.update(overrides)
    return AgentConfig.model_validate(payload)


def build_system_config(**overrides: object) -> SystemConfig:
    """Description:
        Build a valid system configuration payload for CAG tests.

    Requirements:
        - Provide the minimum valid system configuration used by the CAG suite.
        - Allow callers to override individual fields per scenario.

    :param overrides: Field overrides merged into the baseline payload.
    :returns: Validated system configuration model.
    """

    payload = {
        "pa": {"model": "gpt-5.4"},
        "default_agent_model": "gpt-5.4-mini",
    }
    payload.update(overrides)
    return SystemConfig.model_validate(payload)


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Description:
        Build a temporary project tree containing sample CAG documents.

    Requirements:
        - Provide stable document fixtures for loading and reload tests.
        - Create both small and oversized sample documents.

    :param tmp_path: Temporary pytest directory fixture.
    :returns: Temporary project root path.
    """

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "coding-standards.md").write_text(
        "# Coding Standards\n\n- Use type hints.\n- Keep functions short.\n",
        encoding="utf-8",
    )
    (docs_dir / "api-spec.md").write_text(
        "# API Specification\n\nGET /api/routes\nPOST /api/task\n",
        encoding="utf-8",
    )
    (docs_dir / "large.md").write_text("x" * 600, encoding="utf-8")
    return tmp_path


class TestCAGManager:
    """Description:
        Verify the dedicated CAG manager loads, validates, and reloads documents.

    Requirements:
        - Prove CAG loading reports success, missing files, and budget overruns correctly.
        - Prove changed documents can be reloaded in place.
    """

    def test_load_all_success(self, tmp_project: Path) -> None:
        """Description:
            Verify all configured CAG documents load cleanly within budget.

        Requirements:
            - This test is needed to prove the manager reports successful load state.
            - Verify the result includes both loaded documents and no errors.

        :param tmp_project: Temporary project root.
        """

        from faith_pa.agent.cag import CAGManager

        manager = CAGManager(
            project_root=tmp_project,
            model_name="gpt-5.4-mini",
            document_paths=["docs/coding-standards.md", "docs/api-spec.md"],
            max_tokens=8000,
        )

        result = manager.load_all()

        assert result.success is True
        assert result.loaded_count == 2
        assert result.document_count == 2
        assert result.errors == []
        assert result.warnings == []
        assert manager.total_tokens > 0

    def test_load_all_reports_missing_file(self, tmp_project: Path) -> None:
        """Description:
            Verify missing CAG documents are reported as validation errors.

        Requirements:
            - This test is needed to prove session-start validation catches missing references.
            - Verify the result is unsuccessful and includes a missing-file error.

        :param tmp_project: Temporary project root.
        """

        from faith_pa.agent.cag import CAGManager

        manager = CAGManager(
            project_root=tmp_project,
            model_name="gpt-5.4-mini",
            document_paths=["docs/missing.md"],
            max_tokens=8000,
        )

        result = manager.load_all()

        assert result.success is False
        assert result.loaded_count == 0
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].lower()

    def test_load_all_warns_when_budget_exceeded(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Description:
            Verify oversized CAG budgets produce a warning and unsuccessful validation result.

        Requirements:
            - This test is needed to prove FAITH warns before loading too much static context.
            - Verify the warning suggests moving the largest document to RAG.

        :param tmp_project: Temporary project root.
        :param monkeypatch: Pytest monkeypatch fixture.
        """

        from faith_pa.agent.cag import CAGManager

        monkeypatch.setattr(
            "faith_pa.agent.cag.count_text_tokens", lambda text, model=None: len(text)
        )
        manager = CAGManager(
            project_root=tmp_project,
            model_name="gpt-5.4-mini",
            document_paths=["docs/large.md"],
            max_tokens=100,
        )

        result = manager.load_all()

        assert result.success is False
        assert result.total_tokens > 100
        assert len(result.warnings) == 1
        assert "rag" in result.warnings[0].lower()

    def test_reload_document_refreshes_content(self, tmp_project: Path) -> None:
        """Description:
            Verify reloading one CAG document updates the stored content and hash.

        Requirements:
            - This test is needed to prove file-change events refresh agent CAG state.
            - Verify the reloaded document contains the updated content.

        :param tmp_project: Temporary project root.
        """

        from faith_pa.agent.cag import CAGManager

        target = tmp_project / "docs" / "coding-standards.md"
        manager = CAGManager(
            project_root=tmp_project,
            model_name="gpt-5.4-mini",
            document_paths=["docs/coding-standards.md"],
            max_tokens=8000,
        )
        manager.load_all()
        original_hash = manager.documents[0].sha256
        target.write_text("# Updated Standards\n\n- New rule.\n", encoding="utf-8")

        reloaded = manager.reload_document(target)

        assert reloaded is not None
        assert reloaded.loaded is True
        assert reloaded.sha256 != original_hash
        assert "Updated Standards" in reloaded.content


class TestCachingHints:
    """Description:
        Verify provider-specific prompt caching hints for CAG content.

    Requirements:
        - Prove Anthropic requests gain explicit cache-control hints.
        - Prove OpenAI and Ollama payloads remain unchanged.
    """

    def test_detect_provider_variants(self) -> None:
        """Description:
            Verify provider detection recognises Anthropic, OpenAI, and Ollama routes.

        Requirements:
            - This test is needed to prove prompt-caching decisions key off the correct provider.
            - Verify common model and endpoint patterns map to the expected provider enum.
        """

        from faith_pa.agent.caching import LLMProvider, detect_provider

        assert detect_provider("claude-3.5-sonnet", "") == LLMProvider.ANTHROPIC
        assert detect_provider("gpt-4o", "") == LLMProvider.OPENAI
        assert detect_provider("ollama/llama3", "http://ollama:11434") == LLMProvider.OLLAMA

    def test_apply_cache_hints_wraps_anthropic_system_message(self) -> None:
        """Description:
            Verify Anthropic requests gain a cache-control block when CAG is present.

        Requirements:
            - This test is needed to prove FAITH emits explicit Claude prompt-caching hints.
            - Verify the system message is converted to block format with an ephemeral cache-control marker.
        """

        from faith_pa.agent.caching import LLMProvider, apply_cache_hints

        messages = [
            {"role": "system", "content": "Prompt with CAG"},
            {"role": "user", "content": "Do work"},
        ]

        updated = apply_cache_hints(messages, provider=LLMProvider.ANTHROPIC, cag_present=True)

        assert isinstance(updated[0]["content"], list)
        assert updated[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_apply_cache_hints_keeps_openai_payload_unchanged(self) -> None:
        """Description:
            Verify OpenAI payloads remain unchanged because prefix caching is automatic.

        Requirements:
            - This test is needed to prove FAITH does not inject Anthropic-specific structure into OpenAI calls.
            - Verify the system message stays a plain string.
        """

        from faith_pa.agent.caching import LLMProvider, apply_cache_hints

        messages = [
            {"role": "system", "content": "Prompt with CAG"},
            {"role": "user", "content": "Do work"},
        ]

        updated = apply_cache_hints(messages, provider=LLMProvider.OPENAI, cag_present=True)

        assert updated[0]["content"] == "Prompt with CAG"


class TestBaseAgentCAGIntegration:
    """Description:
        Verify BaseAgent integrates the dedicated CAG manager into prompt assembly and reload handling.

    Requirements:
        - Prove the base agent exposes validation state from the CAG manager.
        - Prove file-change handling only reloads tracked CAG documents.
    """

    def test_base_agent_surfaces_validation_result(self, tmp_project: Path) -> None:
        """Description:
            Verify the base agent stores the CAG validation result after loading documents.

        Requirements:
            - This test is needed to prove session-start validation state is available to the PA.
            - Verify the result reports a successful two-document load.

        :param tmp_project: Temporary project root.
        """

        agent = BaseAgent(
            agent_id="agent-cag",
            config=build_agent_config(
                cag_documents=["docs/coding-standards.md", "docs/api-spec.md"],
            ),
            system_config=build_system_config(),
            prompt_text="Prompt",
            project_root=tmp_project,
        )

        result = agent.load_cag_documents()

        assert result.success is True
        assert agent.cag_validation is result
        assert len(agent.cag_manager.documents) == 2

    def test_base_agent_builds_prompt_with_formatted_cag_documents(self, tmp_project: Path) -> None:
        """Description:
            Verify the base agent formats CAG documents with stable source-path headers.

        Requirements:
            - This test is needed to prove the Phase 7 prompt layout uses the dedicated CAG manager output.
            - Verify the system prompt includes the CAG reference header and document content.

        :param tmp_project: Temporary project root.
        """

        agent = BaseAgent(
            agent_id="agent-prompt",
            config=build_agent_config(cag_documents=["docs/coding-standards.md"]),
            system_config=build_system_config(),
            prompt_text="Prompt",
            project_root=tmp_project,
            context_summary="Summary",
        )
        agent.load_cag_documents()

        prompt = agent.build_system_prompt()

        assert "--- CAG Reference: docs/coding-standards.md ---" in prompt
        assert "Coding Standards" in prompt

    def test_handle_cag_file_changed_reloads_matching_document(self, tmp_project: Path) -> None:
        """Description:
            Verify CAG file-change handling reloads only tracked documents.

        Requirements:
            - This test is needed to prove the base agent can refresh static context without a full restart.
            - Verify the manager content updates after a matching document changes.

        :param tmp_project: Temporary project root.
        """

        target = tmp_project / "docs" / "coding-standards.md"
        agent = BaseAgent(
            agent_id="agent-reload",
            config=build_agent_config(cag_documents=["docs/coding-standards.md"]),
            system_config=build_system_config(),
            prompt_text="Prompt",
            project_root=tmp_project,
        )
        agent.load_cag_documents()
        target.write_text("# Rewritten\n\n- Replacement rule.\n", encoding="utf-8")

        changed = agent.handle_cag_file_changed(str(target))

        assert changed is True
        assert "Rewritten" in agent.cag_manager.documents[0].content

    def test_handle_cag_file_changed_ignores_untracked_paths(self, tmp_project: Path) -> None:
        """Description:
            Verify CAG file-change handling ignores unrelated files.

        Requirements:
            - This test is needed to prove normal file changes do not churn unrelated agent CAG state.
            - Verify the method returns false for paths outside the configured CAG set.

        :param tmp_project: Temporary project root.
        """

        other = tmp_project / "docs" / "other.md"
        other.write_text("Other content", encoding="utf-8")
        agent = BaseAgent(
            agent_id="agent-ignore",
            config=build_agent_config(cag_documents=["docs/coding-standards.md"]),
            system_config=build_system_config(),
            prompt_text="Prompt",
            project_root=tmp_project,
        )
        agent.load_cag_documents()

        changed = agent.handle_cag_file_changed(str(other))

        assert changed is False
