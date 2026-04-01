"""Tests for the FAITH CLI package."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

from click.testing import CliRunner

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
    assert paths.is_initialised()


def test_is_first_run_detects_template_secret(monkeypatch, tmp_path: Path) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    (home / "config").mkdir(parents=True)
    (home / "config" / "secrets.yaml").write_text(
        "openrouter_api_key: your_key_here\n", encoding="utf-8"
    )
    assert paths.is_first_run()


def test_compose_command_uses_repo_project_directory() -> None:
    command = compose_command("ps")
    assert command[:3] == ["docker", "compose", "--project-name"]
    assert "--project-directory" in command
    assert str(paths.source_root()) in command
    assert str(paths.source_compose_file()) in command


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
