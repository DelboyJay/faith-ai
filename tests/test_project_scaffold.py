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

import tomllib
from packaging.requirements import Requirement
from packaging.version import Version

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


def test_project_root_agents_md_exists() -> None:
    """
    Description:
        Verify the repository root keeps the always-on AGENTS.md control file.

    Requirements:
        - This test is needed to prevent the PA project-instruction editor from loading an empty prompt because the repository root control file was removed.
        - Verify `AGENTS.md` exists at the project root.
    """

    assert (ROOT / "AGENTS.md").exists()


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


def _load_pyproject_dependencies() -> list[str]:
    """
    Description:
        Load the runtime dependency declarations from `pyproject.toml`.

    Requirements:
        - This helper is needed so packaging-floor tests can inspect the
          declared runtime dependency ranges without relying on string
          substring checks.
        - Return the `project.dependencies` list exactly as declared.

    :returns: Runtime dependency declarations from `pyproject.toml`.
    """

    payload = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return list(payload["project"]["dependencies"])


def _load_requirements_lines(path: Path) -> list[str]:
    """
    Description:
        Load concrete requirement lines from a container requirements file.

    Requirements:
        - This helper is needed so dependency-floor checks can compare package
          specifiers across the Python packaging metadata and container build
          manifests consistently.
        - Ignore blank lines and comments.

    :param path: Requirements file to read.
    :returns: Clean requirement lines for the given file.
    """

    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _find_requirement(requirements: list[str], package_name: str) -> Requirement:
    """
    Description:
        Find and parse a specific package requirement from a manifest list.

    Requirements:
        - This helper is needed so the dependency-floor regression test can
          verify exact lower bounds for security-sensitive packages.
        - Raise an assertion if the requested package is not present.

    :param requirements: Raw requirement strings from a manifest.
    :param package_name: Normalized package name to locate.
    :returns: Parsed requirement object for the requested package.
    """

    for requirement_text in requirements:
        requirement = Requirement(requirement_text)
        if requirement.name == package_name:
            return requirement
    raise AssertionError(f"Expected requirement for {package_name!r} was not found.")


def _assert_minimum_floor(requirements: list[str], package_name: str, expected_floor: str) -> None:
    """
    Description:
        Assert that a dependency manifest does not allow versions below a
        required security floor.

    Requirements:
        - This helper is needed to prevent GitHub or other scanners from
          flagging manifests that still allow known-vulnerable package ranges.
        - Verify there is at least one `>=` or `==` bound that meets or exceeds
          the required floor.

    :param requirements: Raw requirement strings from a manifest.
    :param package_name: Package whose lower bound must be checked.
    :param expected_floor: Minimum acceptable version string.
    """

    requirement = _find_requirement(requirements, package_name)
    floor = Version(expected_floor)
    candidate_floors = []
    for specifier in requirement.specifier:
        if specifier.operator in {">=", "=="}:
            candidate_floors.append(Version(specifier.version))
    assert candidate_floors, f"{package_name!r} does not declare a lower bound."
    assert any(candidate >= floor for candidate in candidate_floors), (
        f"{package_name!r} must be constrained at or above {expected_floor}, "
        f"but found {requirement.specifier!s}."
    )


def test_dependency_manifests_pin_security_patched_floors() -> None:
    """
    Description:
        Verify the main Python dependency manifests no longer allow known
        vulnerable package versions.

    Requirements:
        - This test is needed to prevent GitHub and other manifest-based
          scanners from continuing to report dependency alerts after the safe
          installed environment has been verified.
        - Verify the Python runtime manifests keep `python-dotenv`,
          `python-multipart`, `requests`, and the FastAPI/Starlette stack at or
          above the agreed safe floors.
    """

    pyproject_requirements = _load_pyproject_dependencies()
    pa_requirements = _load_requirements_lines(ROOT / "containers" / "pa" / "requirements.txt")
    web_ui_requirements = _load_requirements_lines(
        ROOT / "containers" / "web-ui" / "requirements.txt"
    )
    tool_python_requirements = _load_requirements_lines(
        ROOT / "containers" / "tool-python" / "requirements.txt"
    )

    _assert_minimum_floor(pyproject_requirements, "python-dotenv", "1.2.2")
    _assert_minimum_floor(pyproject_requirements, "python-multipart", "0.0.29")
    _assert_minimum_floor(pyproject_requirements, "requests", "2.34.2")
    _assert_minimum_floor(pyproject_requirements, "starlette", "1.2.0")

    _assert_minimum_floor(pa_requirements, "python-dotenv", "1.2.2")
    _assert_minimum_floor(pa_requirements, "python-multipart", "0.0.29")
    _assert_minimum_floor(pa_requirements, "starlette", "1.2.0")

    _assert_minimum_floor(web_ui_requirements, "python-multipart", "0.0.29")
    _assert_minimum_floor(web_ui_requirements, "starlette", "1.2.0")

    _assert_minimum_floor(tool_python_requirements, "requests", "2.34.2")


def test_web_ui_container_requirements_include_runtime_imports() -> None:
    """
    Description:
        Verify the Web UI container installs third-party packages imported at startup.

    Requirements:
        - This test is needed to prevent the Web UI image from entering a restart loop
          when `faith_web.app` imports its PA HTTP client dependency.
        - Verify `httpx` is present in `containers/web-ui/requirements.txt`.
    """
    requirements_text = (ROOT / "containers" / "web-ui" / "requirements.txt").read_text()

    assert "httpx" in requirements_text


def test_pa_container_requirements_include_multipart_for_upload_routes() -> None:
    """
    Description:
        Verify the PA container installs the multipart dependency required by FastAPI upload routes.

    Requirements:
        - This test is needed to prevent the PA image from crash-looping when storage
          or upload endpoints declare `UploadFile`/form-data parameters.
        - Verify `python-multipart` is present in `containers/pa/requirements.txt`.
    """
    requirements_text = (ROOT / "containers" / "pa" / "requirements.txt").read_text()

    assert "python-multipart" in requirements_text


def test_web_ui_dockerfile_copies_panel_runtime_before_build() -> None:
    """
    Description:
        Verify the bundled Web UI Docker build includes the legacy panel runtime files.

    Requirements:
        - This test is needed to prevent the frontend image build from failing when
          `web/src/main.jsx` imports browser panel modules from `web/js/`.
        - Verify the frontend-build stage copies `web/js/` before `npm run build`.
    """
    dockerfile_text = (ROOT / "containers" / "web-ui" / "Dockerfile").read_text()

    assert "COPY web/js/ /app/web/js/" in dockerfile_text
    assert dockerfile_text.index("COPY web/js/ /app/web/js/") < dockerfile_text.index(
        "RUN npm run build"
    )


def test_tool_python_requirements_pin_patched_lxml() -> None:
    """
    Description:
        Verify the Python tool container depends on a patched lxml release.

    Requirements:
        - This test is needed to prevent the Python execution image from shipping
          with the known high-severity lxml vulnerability reported by dependency
          scanners.
        - Verify the tool-python requirements pin lxml at or above the patched
          6.1.0 release line.
    """
    requirements_text = (ROOT / "containers" / "tool-python" / "requirements.txt").read_text()

    assert "lxml>=6.1.0" in requirements_text


def test_compose_provides_mcp_registry_database() -> None:
    """
    Description:
        Verify the repository compose stack provides PostgreSQL for the MCP registry.

    Requirements:
        - This test is needed to prevent the registry container from restarting because it falls back to localhost PostgreSQL.
        - Verify the compose file defines both the registry database service and the registry database URL.
    """
    compose_text = (ROOT / "docker-compose.yml").read_text()

    assert "mcp-registry-db:" in compose_text
    assert "MCP_REGISTRY_DATABASE_URL" in compose_text
    assert "MCP_REGISTRY_JWT_PRIVATE_KEY" in compose_text
    assert "postgres:16-alpine" in compose_text


def test_compose_mounts_project_root_for_pa_prompt_and_context_files() -> None:
    """
    Description:
        Verify the repository compose stack mounts the project root into the PA container.

    Requirements:
        - This test is needed to prevent the PA prompt editor and effective-context compiler from seeing an empty `/app` workspace.
        - Verify the compose file mounts the repository root at `/workspace`.
        - Verify the PA environment points `FAITH_PROJECT_ROOT` at the mounted workspace.
    """
    compose_text = (ROOT / "docker-compose.yml").read_text()

    assert "- .:/workspace" in compose_text
    assert "FAITH_PROJECT_ROOT=/workspace" in compose_text
