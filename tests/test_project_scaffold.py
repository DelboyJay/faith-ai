"""
Description:
    Validate the FAITH repository scaffold against the Phase 1 foundation
    requirements so the layout can be checked objectively.

Requirements:
    - Prove the canonical `src/` package layout exists.
    - Prove the bootstrap templates and data files needed by `faith init`
      are present in the repository.
    - Prove packaging metadata is aligned to the `src/` layout.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_src_package_layout_exists() -> None:
    """
    Description:
        Verify the canonical FAITH package layout exists under `src/`.

    Requirements:
        - This test is needed to prove FAITH-001 provides the package ownership
          structure defined in the FRS and epic.
        - Verify every primary package directory and FAITH-owned MCP subpackage
          has an `__init__.py` file.
    """
    expected_files = [
        ROOT / "src" / "faith_cli" / "__init__.py",
        ROOT / "src" / "faith_pa" / "__init__.py",
        ROOT / "src" / "faith_web" / "__init__.py",
        ROOT / "src" / "faith_shared" / "__init__.py",
        ROOT / "src" / "faith_mcp" / "__init__.py",
        ROOT / "src" / "faith_mcp" / "filesystem" / "__init__.py",
        ROOT / "src" / "faith_mcp" / "python_exec" / "__init__.py",
        ROOT / "src" / "faith_mcp" / "code_index" / "__init__.py",
        ROOT / "src" / "faith_mcp" / "fulltext_search" / "__init__.py",
        ROOT / "src" / "faith_mcp" / "kv_store" / "__init__.py",
    ]

    missing = [str(path.relative_to(ROOT)) for path in expected_files if not path.exists()]
    assert not missing, f"Missing scaffold files: {missing}"


def test_bootstrap_templates_and_framework_assets_exist() -> None:
    """
    Description:
        Verify the repository contains the framework-home templates bundled by
        `faith init`.

    Requirements:
        - This test is needed to prove the CLI has the committed assets it must
          extract into `~/.faith/`.
        - Verify the expected config, web, container, log, and test scaffold
          files exist.
    """
    expected_paths = [
        ROOT / "config" / ".env.template",
        ROOT / "config" / "secrets.yaml.template",
        ROOT / "config" / "archetypes" / "software-developer.yaml",
        ROOT / "config" / "archetypes" / "qa-tester.yaml",
        ROOT / "config" / "archetypes" / "technical-writer.yaml",
        ROOT / "config" / "archetypes" / "code-reviewer.yaml",
        ROOT / "config" / "archetypes" / "devops-engineer.yaml",
        ROOT / "data" / "model-prices.default.json",
        ROOT / "data" / "provider-privacy.json",
        ROOT / "logs" / ".gitkeep",
        ROOT / "web" / ".gitkeep",
        ROOT / "containers" / "pa" / "Dockerfile",
        ROOT / "containers" / "web-ui" / "Dockerfile",
        ROOT / "containers" / "mcp-runtime" / "Dockerfile",
        ROOT / "tests" / "__init__.py",
        ROOT / "setup.ps1",
        ROOT / "setup.sh",
    ]

    missing = [str(path.relative_to(ROOT)) for path in expected_paths if not path.exists()]
    assert not missing, f"Missing bootstrap assets: {missing}"


def test_model_prices_default_json_is_valid() -> None:
    """
    Description:
        Verify the bundled model pricing data file is valid JSON.

    Requirements:
        - This test is needed to prove the committed pricing reference is
          loadable before any live pricing refresh happens.
        - Verify the JSON has a `generated_date` field and a `models` object.
    """
    payload = json.loads((ROOT / "data" / "model-prices.default.json").read_text())

    assert payload["generated_date"]
    assert isinstance(payload["models"], dict)
    assert payload["models"]


def test_pyproject_uses_src_layout_for_packages() -> None:
    """
    Description:
        Verify packaging metadata is aligned with the canonical `src/` layout.

    Requirements:
        - This test is needed to prove `faith-cli` is packaged from `src/`
          rather than relying on repository-root package discovery.
        - Verify the CLI entry point remains `faith`.
    """
    pyproject_text = (ROOT / "pyproject.toml").read_text()

    assert "package-dir" in pyproject_text or 'where = ["src"]' in pyproject_text
    assert 'faith = "faith_cli.cli:main"' in pyproject_text
