"""Description:
    Cover the FAITH CLI package behaviour.

Requirements:
    - Verify the CLI lifecycle commands, bootstrap helpers, and route-discovery output behave correctly.
    - Keep the CLI test surface aligned with the current command contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import click
import pytest
import yaml
from click.testing import CliRunner

import faith_cli.checks as checks_module
import faith_cli.cli as cli_module
import faith_cli.docker as docker_module
from faith_cli import paths
from faith_cli.checks import check_docker
from faith_cli.cli import main
from faith_cli.docker import compose_command, install_default_ollama_model
from faith_web import version


def _fake_home(monkeypatch, tmp_path: Path) -> Path:
    """Description:
        Redirect CLI path helpers into one temporary FAITH home directory.

    Requirements:
        - Keep CLI tests isolated from the real user home directory.
        - Patch both the shared path helpers and the imported CLI aliases.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    :returns: Temporary FAITH home directory path.
    """

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
    """Description:
        Verify the CLI source-root helper resolves to the repository root.

    Requirements:
        - This test is needed to prove editable-install resource generation can find the root compose file.
        - Verify the repository docker-compose file exists under the resolved source root.
    """

    assert (paths.source_root() / "docker-compose.yml").exists()


def test_is_initialised_false_when_home_missing(monkeypatch, tmp_path: Path) -> None:
    """Description:
        Verify the installed-home check returns false when the FAITH home is absent.

    Requirements:
        - This test is needed to prove CLI startup commands do not treat a missing home directory as initialized.
        - Verify the helper returns ``False`` for a fresh temporary location.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    _fake_home(monkeypatch, tmp_path)
    assert not paths.is_initialised()


def test_is_initialised_true_when_required_files_exist(monkeypatch, tmp_path: Path) -> None:
    """Description:
        Verify the installed-home check returns true once the required files exist.

    Requirements:
        - This test is needed to prove CLI lifecycle commands recognise a valid extracted FAITH home.
        - Verify the helper returns ``True`` when the expected bootstrap files are present.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

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
    """Description:
        Verify first-run detection recognises the template secrets file.

    Requirements:
        - This test is needed to prove the CLI can detect when the user has not yet completed setup.
        - Verify the helper returns ``True`` when the placeholder API key is still present.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

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
        - This test is needed to prove CLI Docker operations run against the installed bootstrap bundle rather than the repository checkout.
        - Verify the compose command points at `~/.faith` and the installed compose file path.
    """

    command = compose_command("ps")
    assert command[:3] == ["docker", "compose", "--project-name"]
    assert "--project-directory" in command
    assert str(paths.faith_home()) in command
    assert str(paths.compose_file()) in command


def test_default_ollama_install_pulls_llama3_8b(monkeypatch) -> None:
    """Description:
        Verify the default Ollama installer pulls the baseline PA model.

    Requirements:
        - This test is needed to prove default first-run model installation always targets the 6GB GPU baseline.
        - Verify the Docker Compose command pulls ``llama3:8b`` when no override is supplied.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    captured_args = []

    def _capture_run_compose(
        *args: str,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Description:
            Capture the compose invocation used by the Ollama installer.

        Requirements:
            - Avoid starting Docker during this unit test.
            - Preserve the exact command arguments for assertion.

        :param args: Docker Compose arguments supplied by the production helper.
        :param capture_output: Whether output capture was requested.
        :returns: Successful completed process.
        """

        captured_args.append((args, capture_output))
        return subprocess.CompletedProcess(["docker"], 0)

    monkeypatch.setattr(docker_module, "run_compose", _capture_run_compose)

    result = install_default_ollama_model()

    assert result.returncode == 0
    assert captured_args == [(("exec", "-T", "ollama", "ollama", "pull", "llama3:8b"), False)]


def test_help_shows_available_commands() -> None:
    """Description:
        Verify the CLI help output lists the expected command surface.

    Requirements:
        - This test is needed to prove users can discover the implemented lifecycle and discovery commands.
        - Verify the help output includes ``init``, ``start``, ``status``, and ``show-urls``.
    """

    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "start" in result.output
    assert "status" in result.output
    assert "show-urls" in result.output


def test_check_docker_reports_timeout_without_traceback(monkeypatch) -> None:
    """Description:
        Verify Docker daemon probe timeouts become friendly CLI errors.

    Requirements:
        - This test is needed to prevent `faith init` from crashing with a Python traceback when Docker hangs.
        - Verify the error explains that Docker did not respond and tells the user to start Docker Desktop.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    def _timeout_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        """Description:
            Simulate a Docker CLI command that does not return before the timeout.

        Requirements:
            - Preserve the subprocess.run call signature used by the Docker check.
            - Raise the same timeout exception produced by the standard library.

        :param args: Command arguments supplied by the Docker check.
        :param capture_output: Whether subprocess output capture was requested.
        :param text: Whether text-mode output was requested.
        :param timeout: Timeout passed by the Docker check.
        :returns: This helper always raises before returning.
        :raises subprocess.TimeoutExpired: Always raised to simulate a hung Docker daemon.
        """

        del capture_output, text
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(checks_module.shutil, "which", lambda command: command)
    monkeypatch.setattr(checks_module.subprocess, "run", _timeout_run)

    with pytest.raises(click.ClickException) as exc_info:
        check_docker()

    message = str(exc_info.value)
    assert "Docker did not respond" in message
    assert "Docker Desktop" in message
    assert "faith init" in message


def test_init_bootstraps_home(monkeypatch, tmp_path: Path) -> None:
    """Description:
        Verify ``faith init`` bootstraps the installed FAITH home structure.

    Requirements:
        - This test is needed to prove initialization creates the expected config, data, and compose files.
        - Verify the command succeeds when prerequisite and compose calls are stubbed out.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    home = _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr("faith_cli.cli.check_python_version", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_git", lambda: None)
    monkeypatch.setattr(
        "faith_cli.cli.compose_up", lambda: subprocess.CompletedProcess(["docker"], 0)
    )
    monkeypatch.setattr(
        "faith_cli.cli.install_default_ollama_model",
        lambda: subprocess.CompletedProcess(["docker"], 0),
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


def test_init_installs_default_ollama_model(monkeypatch, tmp_path: Path) -> None:
    """Description:
        Verify ``faith init`` pulls the default local PA model into Ollama.

    Requirements:
        - This test is needed to prove first-run setup prepares the PA model for offline/local chat.
        - Verify init invokes the Ollama model installer after compose startup succeeds.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    _fake_home(monkeypatch, tmp_path)
    installed_models = []
    monkeypatch.setattr("faith_cli.cli.check_python_version", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_git", lambda: None)
    monkeypatch.setattr(
        "faith_cli.cli.compose_up", lambda: subprocess.CompletedProcess(["docker"], 0)
    )
    monkeypatch.setattr(
        "faith_cli.cli.install_default_ollama_model",
        lambda: installed_models.append("called") or subprocess.CompletedProcess(["docker"], 0),
    )
    monkeypatch.setattr("faith_cli.cli.wait_and_open_browser", lambda: True)

    runner = CliRunner()
    result = runner.invoke(main, ["init"])

    assert result.exit_code == 0
    assert installed_models == ["called"]


def test_init_fails_when_default_ollama_model_install_fails(monkeypatch, tmp_path: Path) -> None:
    """Description:
        Verify ``faith init`` reports a failed default Ollama model installation.

    Requirements:
        - This test is needed to prevent FAITH from claiming first-run readiness without the local PA model.
        - Verify a non-zero model pull result stops init with a clear message.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr("faith_cli.cli.check_python_version", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.check_git", lambda: None)
    monkeypatch.setattr(
        "faith_cli.cli.compose_up", lambda: subprocess.CompletedProcess(["docker"], 0)
    )
    monkeypatch.setattr(
        "faith_cli.cli.install_default_ollama_model",
        lambda: subprocess.CompletedProcess(["docker"], 1, stderr="pull failed"),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["init"])

    assert result.exit_code != 0
    assert "ollama" in result.output.lower()
    assert "llama3:8b" in result.output


def test_init_prompts_before_reinitialising(monkeypatch) -> None:
    """Description:
        Verify ``faith init`` stops when the user declines reinitialization.

    Requirements:
        - This test is needed to prove the CLI preserves existing config when the user cancels.
        - Verify the command exits successfully with a cancellation message.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: True)
    monkeypatch.setattr("faith_cli.cli.click.confirm", lambda *args, **kwargs: False)
    runner = CliRunner()
    result = runner.invoke(main, ["init"])
    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()


def test_start_requires_initialisation(monkeypatch) -> None:
    """Description:
        Verify ``faith start`` refuses to run before initialization.

    Requirements:
        - This test is needed to prove the CLI does not start against a missing FAITH home.
        - Verify the command guides the user toward ``faith init``.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: False)
    runner = CliRunner()
    result = runner.invoke(main, ["start"])
    assert result.exit_code != 0
    assert "faith init" in result.output.lower()


def test_start_when_already_running(monkeypatch) -> None:
    """Description:
        Verify ``faith start`` reports success when the stack is already running.

    Requirements:
        - This test is needed to prove the CLI avoids unnecessary compose operations when the stack is already up.
        - Verify the command reports the running state cleanly.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: True)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.is_running", lambda: True)
    runner = CliRunner()
    result = runner.invoke(main, ["start"])
    assert result.exit_code == 0
    assert "already running" in result.output.lower()


def test_stop_falls_back_to_compose_down(monkeypatch) -> None:
    """Description:
        Verify ``faith stop`` falls back to Docker Compose teardown when the PA is unreachable.

    Requirements:
        - This test is needed to prove stack shutdown still works when coordinated PA shutdown is unavailable.
        - Verify the compose teardown helper is invoked exactly once.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    monkeypatch.setattr("faith_cli.cli.is_initialised", lambda: True)
    monkeypatch.setattr("faith_cli.cli.check_docker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.is_running", lambda: True)
    monkeypatch.setattr("faith_cli.cli.pa_is_reachable", lambda: False)
    monkeypatch.setattr("faith_cli.cli.stop_host_worker", lambda: SimpleNamespace(enabled=False))
    down = Mock(return_value=subprocess.CompletedProcess(["docker"], 0))
    monkeypatch.setattr("faith_cli.cli.compose_down", down)
    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 0
    down.assert_called_once()


def test_status_renders_runtime_details(monkeypatch) -> None:
    """Description:
        Verify ``faith status`` renders compose and PA status information.

    Requirements:
        - This test is needed to prove the status command surfaces runtime details from compose and the PA.
        - Verify the rendered output includes the PA service name and Redis health.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

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
            "version": version.__version__,
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
    """Description:
        Verify ``faith restart`` tears down and starts the stack again.

    Requirements:
        - This test is needed to prove restart performs the stop-then-start lifecycle under normal conditions.
        - Verify the command reports a successful restart.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

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
    monkeypatch.setattr("faith_cli.cli.stop_host_worker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.start_host_worker", lambda: None)
    monkeypatch.setattr("faith_cli.cli.wait_and_open_browser", lambda: True)
    runner = CliRunner()
    result = runner.invoke(main, ["restart"])
    assert result.exit_code == 0
    assert "restarted" in result.output.lower()


def test_help_subcommand_prints_group_help() -> None:
    """Description:
        Verify the explicit help subcommand prints the group help output.

    Requirements:
        - This test is needed to prove ``faith help`` behaves consistently with ``faith --help``.
        - Verify the output contains the Click usage header.
    """

    runner = CliRunner()
    result = runner.invoke(main, ["help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_bundled_cli_resources_exist() -> None:
    """
    Description:
        Verify the CLI package carries the bootstrap assets it must extract for end users.

    Requirements:
        - This test is needed to prove `faith init` can work from an installed wheel rather than depending on repository-root files.
        - Verify the bundled compose file, config templates, and data assets exist under the CLI package resources directory.
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
        - This test is needed to prove `faith init` and later CLI commands use the extracted bootstrap bundle rather than the repository checkout.
        - Verify `compose_file()` returns the installed compose path.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    home = _fake_home(monkeypatch, tmp_path)
    home.mkdir(parents=True, exist_ok=True)
    installed = home / "docker-compose.yml"
    installed.write_text("services: {}\n", encoding="utf-8")

    assert paths.compose_file() == installed


def test_init_writes_repo_backed_compose_for_editable_install(monkeypatch, tmp_path: Path) -> None:
    """
    Description:
        Verify editable installs bootstrap a compose file that builds from the local repository rather than pulling stale published images.

    Requirements:
        - This test is needed to prevent `faith init` from starting outdated image-based services while the local checkout contains newer code.
        - Verify the generated compose file references the repository root as the PA and Web UI build context.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
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
    monkeypatch.setattr(
        "faith_cli.cli.install_default_ollama_model",
        lambda: subprocess.CompletedProcess(["docker"], 0),
    )
    monkeypatch.setattr("faith_cli.cli.wait_and_open_browser", lambda: True)
    monkeypatch.setattr("faith_cli.paths.source_root", lambda: repo_root)
    monkeypatch.setattr("faith_cli.paths.is_editable_install", lambda: True)
    monkeypatch.setattr("faith_cli.cli.is_editable_install", lambda: True)

    runner = CliRunner()
    result = runner.invoke(main, ["init"])

    assert result.exit_code == 0
    compose_text = (home / "docker-compose.yml").read_text(encoding="utf-8")
    expected_root = repo_root.as_posix()
    assert f"context: {expected_root}" in compose_text
    assert "ghcr.io/faith/faith-project-agent:latest" not in compose_text
    assert "ghcr.io/faith/faith-web-ui:latest" not in compose_text
    assert "mcp-registry-db:" in compose_text
    assert "MCP_REGISTRY_DATABASE_URL" in compose_text
    assert "MCP_REGISTRY_JWT_PRIVATE_KEY" in compose_text
    assert "FAITH_PROJECT_AGENT_MODEL=ollama/llama3:8b" in compose_text


def test_editable_compose_enables_ollama_nvidia_gpu(monkeypatch, tmp_path: Path) -> None:
    """Description:
        Verify editable-install compose generation grants Ollama NVIDIA GPU access.

    Requirements:
        - This test is needed to prevent FAITH from creating CPU-only Ollama containers on Windows hosts with Docker GPU support.
        - Verify the generated Ollama service reserves one NVIDIA GPU with GPU capabilities.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    _fake_home(monkeypatch, tmp_path)

    compose_data = yaml.safe_load(paths.editable_compose_contents())
    devices = compose_data["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"]

    assert devices == [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]


def test_packaged_compose_sets_default_project_agent_model() -> None:
    """Description:
        Verify the packaged compose file declares the default local PA model.

    Requirements:
        - This test is needed to keep wheel installs aligned with editable installs.
        - Verify the packaged bootstrap compose environment selects the 6GB-class Ollama model.
    """

    compose_text = paths.bundled_compose_file().read_text(encoding="utf-8")

    assert "FAITH_PROJECT_AGENT_MODEL=ollama/llama3:8b" in compose_text


def test_packaged_compose_enables_ollama_nvidia_gpu() -> None:
    """Description:
        Verify the packaged compose file grants Ollama NVIDIA GPU access.

    Requirements:
        - This test is needed to keep packaged installs aligned with editable installs.
        - Verify the packaged Ollama service reserves one NVIDIA GPU with GPU capabilities.
    """

    compose_data = yaml.safe_load(paths.bundled_compose_file().read_text(encoding="utf-8"))
    devices = compose_data["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"]

    assert devices == [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]


def test_validate_runtime_compatibility_rejects_pending_schema_migration(
    monkeypatch, tmp_path: Path
) -> None:
    """
    Description:
        Verify the CLI blocks startup when config migration is still required.

    Requirements:
        - This test is needed to prove `faith init/start/update` enforce shared schema compatibility.
        - Verify the compatibility check raises a Click exception when a config file uses an older schema version.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    home = _fake_home(monkeypatch, tmp_path)
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / ".faith").mkdir(parents=True, exist_ok=True)
    (home / "config" / "secrets.yaml").write_text(
        'schema_version: "0.9"\nsecrets: {}\n',
        encoding="utf-8",
    )

    with pytest.raises(click.ClickException):
        cli_module._validate_runtime_compatibility()


def test_host_worker_status_treats_os_error_pid_probe_as_stale(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Description:
        Verify host-worker status tolerates Windows pid-probe OS errors.

    Requirements:
        - This test is needed to prevent `faith init` from crashing when an old host-worker pid cannot be probed.
        - Verify the stale pid file is removed and the worker is reported as stopped.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    from faith_cli import host_worker

    pid_file = tmp_path / "host-worker.pid"
    pid_file.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(host_worker, "host_worker_pid_file", lambda: pid_file)

    def _raise_windows_probe_error(pid: int, signal_number: int) -> None:
        """Description:
            Simulate Windows rejecting the non-destructive pid probe.

        Requirements:
            - Match the signature of ``os.kill``.
            - Raise a generic ``OSError`` like the Windows error seen during init.

        :param pid: Process identifier being probed.
        :param signal_number: Signal number supplied by the caller.
        :raises OSError: Always raised to simulate an unprobeable stale pid.
        """

        del pid, signal_number
        raise OSError(11, "An attempt was made to load a program with an incorrect format")

    monkeypatch.setattr(host_worker.os, "kill", _raise_windows_probe_error)

    status = host_worker.get_host_worker_status()

    assert status.running is False
    assert status.pid is None
    assert not pid_file.exists()


def test_host_worker_status_treats_system_error_pid_probe_as_stale(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Description:
        Verify host-worker status tolerates Windows SystemError pid-probe failures.

    Requirements:
        - This test is needed to prevent `faith init` from crashing when Windows reports a stale pid through ``SystemError``.
        - Verify the stale pid file is removed and the worker is reported as stopped.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    from faith_cli import host_worker

    pid_file = tmp_path / "host-worker.pid"
    pid_file.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(host_worker, "host_worker_pid_file", lambda: pid_file)

    def _raise_windows_system_error(pid: int, signal_number: int) -> None:
        """Description:
            Simulate Python surfacing a Windows pid-probe failure as ``SystemError``.

        Requirements:
            - Match the signature of ``os.kill``.
            - Raise the same broad exception class seen during `faith init`.

        :param pid: Process identifier being probed.
        :param signal_number: Signal number supplied by the caller.
        :raises SystemError: Always raised to simulate the Windows probe failure.
        """

        del pid, signal_number
        raise SystemError("<class 'OSError'> returned a result with an exception set")

    monkeypatch.setattr(host_worker.os, "kill", _raise_windows_system_error)

    status = host_worker.get_host_worker_status()

    assert status.running is False
    assert status.pid is None
    assert not pid_file.exists()


def test_host_worker_start_and_stop_lifecycle(monkeypatch, tmp_path: Path) -> None:
    """
    Description:
        Verify the optional host worker can be started and stopped by the CLI helper.

    Requirements:
        - This test is needed to prove the host worker stays user-scoped and manageable by `faith-cli`.
        - Verify the worker creates a pid file when started and removes it again when stopped.

    :param monkeypatch: Pytest monkeypatch fixture.
    :param tmp_path: Temporary workspace root.
    """

    from faith_cli import host_worker

    pid_file = tmp_path / "host-worker.pid"
    log_file = tmp_path / "host-worker.log"
    monkeypatch.setenv("FAITH_ENABLE_HOST_WORKER", "1")
    monkeypatch.setattr(host_worker, "host_worker_pid_file", lambda: pid_file)
    monkeypatch.setattr(host_worker, "host_worker_log_file", lambda: log_file)
    monkeypatch.setattr(host_worker, "logs_dir", lambda: tmp_path)

    started = host_worker.start_host_worker()
    assert started.enabled is True
    assert started.running is True
    assert started.pid is not None
    assert pid_file.exists()

    stopped = host_worker.stop_host_worker()
    assert stopped.enabled is True
    assert stopped.running is False
    assert not pid_file.exists()


def test_run_placeholder_command_reports_faith_054() -> None:
    """Description:
        Verify the CLI exposes the placeholder ``faith run`` command until task submission is implemented.

    Requirements:
        - This test is needed to prove the command surface matches the Phase 1 CLI contract.
        - Verify ``faith run`` returns a clear placeholder message instead of failing with missing-command output.
    """

    runner = CliRunner()
    result = runner.invoke(main, ["run", "hello"])
    assert result.exit_code == 0
    assert "FAITH-054" in result.output


def test_show_urls_renders_service_manifests(monkeypatch) -> None:
    """Description:
        Verify the CLI renders discovered service routes from the shared route manifests.

    Requirements:
        - This test is needed to prove ``faith show-urls`` does not hard-code PA or Web UI endpoints.
        - Verify the command prints absolute HTTP and WebSocket URLs from the returned manifests.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    manifests = [
        (
            "http://localhost:8000",
            {
                "service": "faith-project-agent",
                "version": version.__version__,
                "routes": [
                    {
                        "protocol": "http",
                        "method": "GET",
                        "path": "/api/routes",
                        "summary": "Return the PA route manifest.",
                        "expected_status_codes": [200],
                    },
                    {
                        "protocol": "websocket",
                        "method": None,
                        "path": "/ws/status",
                        "summary": "Stream PA status.",
                        "expected_status_codes": [],
                    },
                ],
            },
        ),
        (
            "http://localhost:8080",
            {
                "service": "faith-web-ui",
                "version": version.__version__,
                "routes": [
                    {
                        "protocol": "http",
                        "method": "GET",
                        "path": "/",
                        "summary": "Serve the main UI.",
                        "expected_status_codes": [200],
                    }
                ],
            },
        ),
    ]
    monkeypatch.setattr("faith_cli.cli.get_known_route_manifests", lambda: manifests)

    runner = CliRunner()
    result = runner.invoke(main, ["show-urls"])

    assert result.exit_code == 0
    assert "http://localhost:8000/api/routes" in result.output
    assert "ws://localhost:8000/ws/status" in result.output
    assert "http://localhost:8080/" in result.output


def test_show_urls_reports_when_no_services_are_reachable(monkeypatch) -> None:
    """Description:
        Verify the CLI fails cleanly when no route manifests can be fetched.

    Requirements:
        - This test is needed to prove ``faith show-urls`` gives a useful fix hint instead of failing with a traceback.
        - Verify the command exits non-zero and tells the user to start FAITH first.

    :param monkeypatch: Pytest monkeypatch fixture.
    """

    monkeypatch.setattr(
        "faith_cli.cli.get_known_route_manifests",
        lambda: [("http://localhost:8000", None), ("http://localhost:8080", None)],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["show-urls"])

    assert result.exit_code != 0
    assert "Start FAITH with `faith init` or `faith start`" in result.output
