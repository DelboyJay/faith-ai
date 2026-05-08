"""Description:
    Cover automatic discovery and loading of project-root ``cag/`` documents.

Requirements:
    - Verify discovered ``cag/`` files are returned in a deterministic order.
    - Verify project ``cag/`` files auto-load into the agent CAG flow without manual registration.
    - Verify over-budget corpora surface actionable guidance instead of silent omission.
    - Verify budget checks do not rewrite or compress user-maintained ``cag/`` files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from faith_pa.agent.base import BaseAgent
from faith_pa.config.models import AgentConfig, SystemConfig
from faith_pa.pa.session import SessionManager


def build_agent_config(**overrides: object) -> AgentConfig:
    """Description:
        Build a valid agent configuration payload for project CAG tests.

    Requirements:
        - Provide the minimum valid agent configuration used by the project CAG suite.
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
        Build a valid system configuration payload for project CAG tests.

    Requirements:
        - Provide the minimum valid system configuration used by the project CAG suite.
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


def write_cag_file(project_root: Path, relative_path: str, content: str) -> Path:
    """Description:
        Create one project-root ``cag/`` file for the test fixtures.

    Requirements:
        - Preserve the requested relative path under the project ``cag/`` directory.
        - Create parent directories as needed for nested discovery cases.

    :param project_root: Temporary project root.
    :param relative_path: Relative path below ``cag/``.
    :param content: File content to write.
    :returns: Written file path.
    """

    path = project_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestProjectCAGDiscovery:
    """Description:
        Verify the project ``cag/`` discovery helpers behave deterministically.

    Requirements:
        - Prove only supported markdown/text files are discovered.
        - Prove discovery order is stable across nested paths.
    """

    def test_discover_project_cag_documents_returns_sorted_supported_files(
        self, tmp_path: Path
    ) -> None:
        """Description:
            Verify project ``cag/`` discovery returns supported files in sorted order.

        Requirements:
            - This test is needed to prove project-load discovery is deterministic.
            - Verify unsupported file types are ignored and nested supported files remain ordered.

        :param tmp_path: Temporary pytest directory fixture.
        """

        from faith_pa.pa.cag_auto_load import discover_project_cag_documents

        alpha = write_cag_file(tmp_path, "cag/alpha.txt", "alpha")
        nested_md = write_cag_file(tmp_path, "cag/notes/02-background.md", "background")
        nested_txt = write_cag_file(tmp_path, "cag/notes/03-appendix.txt", "appendix")
        write_cag_file(tmp_path, "cag/notes/ignore.png", "not supported")

        discovered = discover_project_cag_documents(tmp_path)

        assert discovered == [alpha.resolve(), nested_md.resolve(), nested_txt.resolve()]


class TestProjectCAGAutoLoading:
    """Description:
        Verify project ``cag/`` documents auto-load into the PA and agent flow.

    Requirements:
        - Prove manual per-file CAG registration is not required for project ``cag/`` files.
        - Prove the existing file-change reload path still works for auto-discovered documents.
    """

    def test_base_agent_auto_loads_project_cag_documents_without_manual_registration(
        self, tmp_path: Path
    ) -> None:
        """Description:
            Verify the base agent loads project ``cag/`` files even when the config lists none.

        Requirements:
            - This test is needed to prove project-root CAG is treated as a default source.
            - Verify the loaded system prompt includes both discovered files.

        :param tmp_path: Temporary project root.
        """

        write_cag_file(tmp_path, "cag/alpha.md", "# Alpha\n\n- One.\n")
        write_cag_file(tmp_path, "cag/beta.txt", "Beta reference text.")

        agent = BaseAgent(
            agent_id="agent-cag",
            config=build_agent_config(),
            system_config=build_system_config(),
            prompt_text="Prompt",
            project_root=tmp_path,
        )

        result = agent.load_cag_documents()
        prompt = agent.build_system_prompt()

        assert result.success is True
        assert result.loaded_count == 2
        assert "--- CAG Reference: cag/alpha.md ---" in prompt
        assert "--- CAG Reference: cag/beta.txt ---" in prompt

    @pytest.mark.asyncio
    async def test_session_start_reports_over_budget_project_cag_guidance(
        self, tmp_path: Path
    ) -> None:
        """Description:
            Verify session start surfaces actionable guidance when project ``cag/`` is too large.

        Requirements:
            - This test is needed to prove budget pressure is reported instead of silently dropping docs.
            - Verify the report names the largest contributors and suggests lower-cost alternatives.

        :param tmp_path: Temporary project root.
        """

        agent_dir = tmp_path / ".faith" / "agents" / "developer"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "config.yaml").write_text(
            """
name: Developer
role: implementation
tools:
  - filesystem
cag_documents: []
cag_max_tokens: 1
""".strip(),
            encoding="utf-8",
        )
        write_cag_file(tmp_path, "cag/core-rules.md", "# Core Rules\n\n" + ("rule\n" * 200))
        write_cag_file(
            tmp_path,
            "cag/background.txt",
            "Background\n\n" + ("context " * 150),
        )
        manager = SessionManager(project_root=tmp_path, system_config=build_system_config())

        session = await manager.start_session(trigger="web-ui")
        session_meta = json.loads((session.path / "session.meta.json").read_text(encoding="utf-8"))
        report = session_meta["cag_validation"]["report"].lower()

        assert "core-rules.md" in report
        assert "background.txt" in report
        assert "summary" in report
        assert "split" in report
        assert "rag" in report
        assert "rewrite" in report or "compress" in report

    def test_project_cag_budget_checks_do_not_rewrite_documents(self, tmp_path: Path) -> None:
        """Description:
            Verify over-budget CAG handling does not rewrite or summarise user files.

        Requirements:
            - This test is needed to prove FAITH does not perform lossy compression automatically.
            - Verify the on-disk ``cag/`` files remain byte-for-byte unchanged after loading.

        :param tmp_path: Temporary project root.
        """

        original_alpha = "# Alpha\n\n- One.\n"
        original_beta = "Beta reference text.\n" + ("context " * 100)
        alpha_path = write_cag_file(tmp_path, "cag/alpha.md", original_alpha)
        beta_path = write_cag_file(tmp_path, "cag/beta.txt", original_beta)

        agent = BaseAgent(
            agent_id="agent-no-loss",
            config=build_agent_config(cag_max_tokens=1),
            system_config=build_system_config(),
            prompt_text="Prompt",
            project_root=tmp_path,
        )

        result = agent.load_cag_documents()

        assert result.success is False
        assert alpha_path.read_text(encoding="utf-8") == original_alpha
        assert beta_path.read_text(encoding="utf-8") == original_beta
        assert sorted((tmp_path / "cag").rglob("*")) == [alpha_path, beta_path]

    def test_auto_loaded_project_cag_documents_reload_through_file_change_path(
        self, tmp_path: Path
    ) -> None:
        """Description:
            Verify auto-loaded project ``cag/`` documents still reload through the file-change path.

        Requirements:
            - This test is needed to prove the existing reload path covers auto-discovered documents.
            - Verify the in-memory CAG content updates after a matching file change.

        :param tmp_path: Temporary project root.
        """

        target = write_cag_file(tmp_path, "cag/rules.md", "# Rules\n\n- Keep going.\n")

        agent = BaseAgent(
            agent_id="agent-reload",
            config=build_agent_config(),
            system_config=build_system_config(),
            prompt_text="Prompt",
            project_root=tmp_path,
        )
        agent.load_cag_documents()
        target.write_text("# Rules\n\n- Updated.\n", encoding="utf-8")

        changed = agent.handle_cag_file_changed(target)

        assert changed is True
        assert "Updated" in agent.cag_manager.documents[0].content
