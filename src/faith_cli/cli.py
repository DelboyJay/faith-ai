"""FAITH CLI entry points and commands."""

from __future__ import annotations

import shutil

import click

from faith_cli import __version__
from faith_cli.browser import WEB_UI_URL, wait_and_open_browser
from faith_cli.checks import check_docker, check_git, check_python_version
from faith_cli.docker import compose_down, compose_pull, compose_status, compose_up, is_running
from faith_cli.host_worker import get_host_worker_status
from faith_cli.http_client import get_status, pa_is_reachable, request_shutdown
from faith_cli.paths import (
    archetypes_dir,
    bundled_compose_file,
    editable_compose_contents,
    is_editable_install,
    bundled_config_dir,
    bundled_data_dir,
    config_dir,
    data_dir,
    env_file,
    faith_home,
    installed_compose_file,
    is_first_run,
    is_initialised,
    logs_dir,
    recent_projects_file,
    secrets_file,
)

BANNER = "FAITH - Framework AI Team Hive"


def _copy_tree(src, dst) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def _write_if_missing(path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _ensure_framework_home() -> None:
    faith_home().mkdir(parents=True, exist_ok=True)
    config_dir().mkdir(parents=True, exist_ok=True)
    data_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
    archetypes_dir().mkdir(parents=True, exist_ok=True)

    _copy_tree(bundled_config_dir() / "archetypes", archetypes_dir())
    _copy_tree(bundled_data_dir(), data_dir())

    if is_editable_install():
        installed_compose_file().write_text(editable_compose_contents(), encoding="utf-8")
    elif not installed_compose_file().exists():
        shutil.copy2(bundled_compose_file(), installed_compose_file())
    if (bundled_compose_file().parent / ".gitignore").exists() and not (
        faith_home() / ".gitignore"
    ).exists():
        shutil.copy2(bundled_compose_file().parent / ".gitignore", faith_home() / ".gitignore")

    if (bundled_config_dir() / ".env.template").exists() and not env_file().exists():
        shutil.copy2(bundled_config_dir() / ".env.template", env_file())
    if (bundled_config_dir() / "secrets.yaml.template").exists() and not secrets_file().exists():
        shutil.copy2(bundled_config_dir() / "secrets.yaml.template", secrets_file())

    _write_if_missing(recent_projects_file(), 'schema_version: "1.0"\nprojects: []\n')


def _print_banner() -> None:
    click.secho(BANNER, fg="cyan")


def _open_ui_message() -> None:
    click.echo(f"Open {WEB_UI_URL} in your browser.")


def _show_runtime_status() -> None:
    result = compose_status()
    if result.returncode != 0:
        raise click.ClickException("docker compose ps failed")

    output = (result.stdout or "").strip()
    if output:
        click.echo(output)
    else:
        click.echo("No compose services are currently listed.")

    status_data = get_status()
    if not status_data:
        click.secho("Project Agent status is not reachable yet.", fg="yellow")
        return

    redis = status_data.get("redis", {})
    config = status_data.get("config", {})
    click.echo("")
    click.echo(f"PA service: {status_data.get('service', 'unknown')}")
    click.echo(f"PA version: {status_data.get('version', 'unknown')}")
    click.echo(f"PA health: {status_data.get('status', 'unknown')}")
    click.echo(f"Redis connected: {redis.get('connected', False)}")
    click.echo(f"Config dir: {config.get('config_dir', 'unknown')}")

    host_worker = get_host_worker_status()
    click.echo(
        f"Host worker: {'enabled' if host_worker.enabled else 'disabled'} ({host_worker.mode})"
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, invoke_without_command=True)
@click.version_option(version=__version__)
@click.pass_context
def main(ctx: click.Context) -> None:
    """FAITH CLI for bootstrapping and controlling the local POC stack."""

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
def init() -> None:
    """Bootstrap ~/.faith and start the current repo-backed FAITH POC."""

    _print_banner()
    if is_initialised() and not click.confirm(
        f"{faith_home()} is already initialised. Continue and preserve existing config?",
        default=True,
    ):
        click.echo("Initialisation cancelled.")
        return

    check_python_version()
    check_docker()
    check_git()
    _ensure_framework_home()
    click.echo(f"Framework home ready at {faith_home()}")

    result = compose_up()
    if result.returncode != 0:
        raise click.ClickException("docker compose up failed")

    if wait_and_open_browser():
        click.echo("FAITH stack started and the Web UI is ready.")
    else:
        click.secho("FAITH stack started, but the Web UI did not respond in time.", fg="yellow")
        _open_ui_message()

    if is_first_run():
        click.echo(
            "First-run config is still using templates; complete setup in the Web UI when available."
        )


@main.command()
def start() -> None:
    """Start the current FAITH POC stack."""

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()

    if is_running():
        click.echo("FAITH is already running.")
        _open_ui_message()
        return

    result = compose_up()
    if result.returncode != 0:
        raise click.ClickException("docker compose up failed")

    if wait_and_open_browser():
        click.echo("FAITH stack started.")
    else:
        click.secho("FAITH stack started, but the Web UI is not ready yet.", fg="yellow")
        _open_ui_message()


@main.command()
def stop() -> None:
    """Stop the FAITH stack, requesting graceful PA shutdown first when possible."""

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()

    if not is_running():
        click.echo("FAITH is not running.")
        return

    if pa_is_reachable():
        if request_shutdown():
            click.echo("Project Agent accepted the shutdown request.")
        else:
            click.secho(
                "Project Agent did not complete coordinated shutdown; stopping containers directly.",
                fg="yellow",
            )
    else:
        click.secho("Project Agent is not reachable; stopping containers directly.", fg="yellow")

    result = compose_down()
    if result.returncode != 0:
        raise click.ClickException("docker compose down failed")
    click.echo("FAITH stack stopped.")


@main.command()
def restart() -> None:
    """Restart the FAITH stack."""

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()

    if is_running():
        if pa_is_reachable():
            request_shutdown()
        down_result = compose_down()
        if down_result.returncode != 0:
            click.secho(
                "docker compose down reported an error; continuing with startup.", fg="yellow"
            )

    up_result = compose_up()
    if up_result.returncode != 0:
        raise click.ClickException("docker compose up failed")

    if wait_and_open_browser():
        click.echo("FAITH stack restarted.")
    else:
        click.secho("FAITH stack restarted, but the Web UI is not ready yet.", fg="yellow")
        _open_ui_message()


@main.command()
def status() -> None:
    """Show Docker and Project Agent status for the current FAITH stack."""

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()
    _show_runtime_status()


@main.command()
def update() -> None:
    """Pull fresh images for the current stack and restart it."""

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()

    if is_running():
        if pa_is_reachable():
            request_shutdown()
        compose_down()

    pull_result = compose_pull()
    if pull_result.returncode != 0:
        raise click.ClickException("docker compose pull failed")

    up_result = compose_up()
    if up_result.returncode != 0:
        raise click.ClickException("docker compose up failed")

    click.echo("FAITH stack updated.")
    _open_ui_message()


@main.command(name="help")
@click.pass_context
def help_command(ctx: click.Context) -> None:
    """Show CLI help."""

    click.echo(ctx.parent.get_help() if ctx.parent else ctx.get_help())


