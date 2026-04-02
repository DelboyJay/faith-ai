"""Tests for the FAITH CLI package."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

from click.testing import CliRunner

import faith_cli.cli as cli_module
from faith_cli import paths
from faith_cli.cli import main
from faith_cli.docker import compose_command


def _fake_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / ".faith"
    monkeypatch.setattr(paths, "faith_home", lambda: home)
    monkeypatch.setattr(paths, "config_dir", lambda: home / "config")
    monkeypatch.setattr(paths, "data_dir", lambda: home / "data")
    monkeypatch.setattr(paths, "logs_dir", lambda: home / "logs")
    monkeypatch.setattr(paths, "archetypes_dir", lambda: home / "config" / "archetypes")
    monkeypatch.setattr(paths, "env_file", lambda: home / "config" / ".env")
    monkeypatch.setattr(paths, "secrets_file", lambda: home / "config" / "secrets.yaml")
    monkeypatch.setattr(
        paths, "recent_projects_file", lambda: home / "config" / "recent-projects.yaml"
    )
    monkeypatch.setattr(paths, "installed_compose_file", lambda: home / "docker-compose.yml")
    monkeypatch.setattr(cli_module, "faith_home", lambda: home)
    monkeypatch.setattr(cli_module, "config_dir", lambda: home / "config")
    monkeypatch.setattr(cli_module, "data_dir", lambda: home / "data")
    monkeypatch.setattr(cli_module, "logs_dir", lambda: home / "logs")
    monkeypatch.setattr(cli_module, "archetypes_dir", lambda: home / "config" / "archetypes")
    monkeypatch.setattr(cli_module, "env_file", lambda: home / "config" / ".env")
    monkeypatch.setattr(cli_module, "secrets_file", lambda: home / "config" / "secrets.yaml")
    monkeypatch.setattr(
        cli_module, "recent_projects_file", lambda: home / "config" / "recent-projects.yaml"
    )
    monkeypatch.setattr(cli_module, "installed_compose_file", lambda: home / "docker-compose.yml")
    return home


def test_source_root_points_to_repo_root() -> None:
    assert (paths.source_root() / "docker-compose.yml").exists()


def test_is_initialised_false_when_home_missing(monkeypatch, tmp_path: Path) -> None:
    _fake_home(monkeypatch, tmp_path)
    assert not paths.is_initialised()


def test_is_initialised_true_when_required_files_exist(monkeypatch, tmp_path: Path) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    (home / "config" / "archetypes").mkdir(parents=True)
    (home / "data").mkdir()
    (home / "logs").mkdir()
    (home / "config" / ".env").write_text("FAITH_ENV=test\n", encoding="utf-8")
    (home / "config" / "secrets.yaml").write_text("api_key: changeme\n", encoding="utf-8")
    (home / "config" / "recent-projects.yaml").write_text("projects: []\n", encoding="utf-8")
    (home / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert paths.is_initialised()


def test_is_first_run_detects_template_secret(monkeypatch, tmp_path: Path) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    (home / "config").mkdir(parents=True)
    (home / "config" / "secrets.yaml").write_text(
        "openrouter_api_key: your_key_here\n", encoding="utf-8"
    )
    assert paths.is_first_run()


def test_compose_command_uses_installed_project_directory() -> None:
    """
    Description:
        Verify the compose command resolves against the extracted FAITH home.

    Requirements:
        - This test is needed to prove CLI Docker operations run against the
          installed bootstrap bundle rather than the repository checkout.
        - Verify the compose command points at `~/.faith` and the installed
          compose file path.
    """
    command = compose_command("ps")
    assert command[:3] == ["docker", "compose", "--project-name"]
    assert "--project-directory" in command
    assert str(paths.faith_home()) in command
    assert str(paths.compose_file()) in command


def test_help_shows_available_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "start" in result.output
    assert "status" in result.output


def test_init_bootstraps_home(monkeypatch, tmp_path: Path) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr("faith_cli.cli.check_python_version", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_git", lambda: None)
    monkeypatch.setattr(
        "faith_cli.cli.compose_up", lambda: subprocess.CompletedProcess(["docker"], 0)
    )
    monkeypatch.setattr("faith_cli.cli.wait_and_open_browser", lambda: True)

    runner = CliRunner()
    result = runner.invoke(main, ["init"])

    assert result.exit_code == 0
    assert home.exists()
    assert (home / "config" / ".env").exists()
    assert (home / "config" / "secrets.yaml").exists()
    assert (home / "config" / "recent-projects.yaml").exists()
    assert (home / "docker-compose.yml").exists()
    assert (home / ".gitignore").exists()


def test_init_prompts_before_reinitialising(monkeypatch) -> None:
    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: True)
    monkeypatch.setattr("faith_cli.cli.click.confirm", lambda *args, **kwargs: False)
    runner = CliRunner()
    result = runner.invoke(main, ["init"])
    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()


def test_start_requires_initialisation(monkeypatch) -> None:
    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: False)
    runner = CliRunner()
    result = runner.invoke(main, ["start"])
    assert result.exit_code != 0
    assert "faith init" in result.output.lower()


def test_start_when_already_running(monkeypatch) -> None:
    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: True)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.is_running", lambda: True)
    runner = CliRunner()
    result = runner.invoke(main, ["start"])
    assert result.exit_code == 0
    assert "already running" in result.output.lower()


def test_stop_falls_back_to_compose_down(monkeypatch) -> None:
    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: True)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.is_running", lambda: True)
    monkeypatch.setattr("faith_cli.cli.pa_is_reachable", lambda: False)
    down = Mock(return_value=subprocess.CompletedProcess(["docker"], 0))
    monkeypatch.setattr("faith_cli.cli.compose_down", down)
    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 0
    down.assert_called_once()


def test_status_renders_runtime_details(monkeypatch) -> None:
    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: True)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr(
        "faith_cli.cli.compose_status",
        lambda: subprocess.CompletedProcess(
            ["docker"], 0, stdout="NAME STATUS\nfaith-pa running\n", stderr=""
        ),
    )
    monkeypatch.setattr(
        "faith_cli.cli.get_status",
        lambda: {
            "service": "faith-project-agent",
            "version": "0.1.0",
            "status": "ok",
            "redis": {"connected": True},
            "config": {"config_dir": "/config"},
        },
    )
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "faith-project-agent" in result.output
    assert "Redis connected: True" in result.output


def test_restart_starts_after_shutdown(monkeypatch) -> None:
    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: True)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.is_running", lambda: True)
    monkeypatch.setattr("faith_cli.cli.pa_is_reachable", lambda: True)
    monkeypatch.setattr("faith_cli.cli.request_shutdown", lambda: True)
    monkeypatch.setattr(
        "faith_cli.cli.compose_down", lambda: subprocess.CompletedProcess(["docker"], 0)
    )
    monkeypatch.setattr(
        "faith_cli.cli.compose_up", lambda: subprocess.CompletedProcess(["docker"], 0)
    )
    monkeypatch.setattr("faith_cli.cli.wait_and_open_browser", lambda: True)
    runner = CliRunner()
    result = runner.invoke(main, ["restart"])
    assert result.exit_code == 0
    assert "restarted" in result.output.lower()


def test_help_subcommand_prints_group_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_bundled_cli_resources_exist() -> None:
    """
    Description:
        Verify the CLI package carries the bootstrap assets it must extract for
        end users.

    Requirements:
        - This test is needed to prove `faith init` can work from an installed
          wheel rather than depending on repository-root files.
        - Verify the bundled compose file, config templates, and data assets
          exist under the CLI package resources directory.
    """
    resources_root = paths.package_resources_root()

    expected_paths = [
        resources_root / "docker-compose.yml",
        resources_root / "config" / ".env.template",
        resources_root / "config" / "secrets.yaml.template",
        resources_root / "config" / "archetypes" / "software-developer.yaml",
        resources_root / "data" / "model-prices.default.json",
        resources_root / "data" / "provider-privacy.json",
        resources_root / ".gitignore",
    ]

    missing = [
        str(path.relative_to(resources_root)) for path in expected_paths if not path.exists()
    ]
    assert not missing, f"Missing bundled CLI resources: {missing}"


def test_compose_file_uses_installed_bundle(monkeypatch, tmp_path: Path) -> None:
    """
    Description:
        Verify the CLI resolves Docker Compose from the user-owned FAITH home.

    Requirements:
        - This test is needed to prove `faith init` and later CLI commands use
          the extracted bootstrap bundle rather than the repository checkout.
        - Verify `compose_file()` returns the installed compose path.
    """
    home = _fake_home(monkeypatch, tmp_path)
    home.mkdir(parents=True, exist_ok=True)
    installed = home / "docker-compose.yml"
    installed.write_text("services: {}\n", encoding="utf-8")

    assert paths.compose_file() == installed

def test_init_writes_repo_backed_compose_for_editable_install(monkeypatch, tmp_path: Path) -> None:
    """
    Description:
        Verify editable installs bootstrap a compose file that builds from the
        local repository rather than pulling stale published images.

    Requirements:
        - This test is needed to prevent `faith init` from starting outdated
          image-based services while the local checkout contains newer code.
        - Verify the generated compose file references the repository root as
          the PA and Web UI build context.
    """

    home = _fake_home(monkeypatch, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr("faith_cli.cli.check_python_version", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_git", lambda: None)
    monkeypatch.setattr(
        "faith_cli.cli.compose_up", lambda: subprocess.CompletedProcess(["docker"], 0)
    )
    monkeypatch.setattr("faith_cli.cli.wait_and_open_browser", lambda: True)
    monkeypatch.setattr("faith_cli.paths.source_root", lambda: repo_root)
    monkeypatch.setattr("faith_cli.paths.is_editable_install", lambda: True)

    runner = CliRunner()
    result = runner.invoke(main, ["init"])

    assert result.exit_code == 0
    compose_text = (home / "docker-compose.yml").read_text(encoding="utf-8")
    expected_root = repo_root.as_posix()
    assert f"context: {expected_root}" in compose_text
    assert "ghcr.io/faith/faith-project-agent:latest" not in compose_text
    assert "ghcr.io/faith/faith-web-ui:latest" not in compose_text
