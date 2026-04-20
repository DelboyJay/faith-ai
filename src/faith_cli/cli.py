"""Description:
    Provide the FAITH CLI commands used to bootstrap and control the local stack.

Requirements:
    - Enforce shared compatibility checks before starting or updating the stack.
    - Manage the optional host worker from the host side rather than the PA.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import click

from faith_cli import __version__
from faith_cli.browser import WEB_UI_URL, wait_and_open_browser
from faith_cli.checks import check_docker, check_git, check_python_version
from faith_cli.docker import (
    DEFAULT_OLLAMA_MODEL,
    compose_down,
    compose_pull,
    compose_status,
    compose_up,
    install_default_ollama_model,
    is_running,
)
from faith_cli.host_worker import get_host_worker_status, start_host_worker, stop_host_worker
from faith_cli.http_client import (
    PA_BASE_URL,
    WEB_BASE_URL,
    get_known_route_manifests,
    get_status,
    pa_is_reachable,
    request_shutdown,
)
from faith_cli.paths import (
    archetypes_dir,
    bundled_compose_file,
    bundled_config_dir,
    bundled_data_dir,
    config_dir,
    data_dir,
    editable_compose_contents,
    env_file,
    faith_home,
    installed_compose_file,
    is_editable_install,
    is_first_run,
    is_initialised,
    logs_dir,
    recent_projects_file,
    secrets_file,
)
from faith_pa import __version__ as faith_pa_version
from faith_pa.config.migration import MigrationEngine
from faith_shared import __version__ as faith_shared_version
from faith_shared.compatibility import FaithCompatibilityError, validate_component_versions
from faith_web.version import __version__ as faith_web_version

BANNER = "FAITH - Framework AI Team Hive"


def _copy_tree(src, dst) -> None:
    """Description:
        Copy one directory tree into another destination.

    Requirements:
        - Create the destination directory when missing.
        - Ignore missing source directories.

    :param src: Source directory path.
    :param dst: Destination directory path.
    """

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
    """Description:
        Write one text file only when the target does not yet exist.

    Requirements:
        - Preserve existing user-managed files.

    :param path: Target file path.
    :param content: File content to write when missing.
    """

    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _ensure_framework_home() -> None:
    """Description:
        Create and populate the extracted FAITH home directory.

    Requirements:
        - Create the required config, data, logs, and archetype directories.
        - Extract packaged resources into `~/.faith`.
        - Generate a repo-backed compose file when running from an editable install.
    """

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

    gitignore = bundled_compose_file().parent / ".gitignore"
    if gitignore.exists() and not (faith_home() / ".gitignore").exists():
        shutil.copy2(gitignore, faith_home() / ".gitignore")

    env_template = bundled_config_dir() / ".env.template"
    if env_template.exists() and not env_file().exists():
        shutil.copy2(env_template, env_file())

    secrets_template = bundled_config_dir() / "secrets.yaml.template"
    if secrets_template.exists() and not secrets_file().exists():
        shutil.copy2(secrets_template, secrets_file())

    _write_if_missing(recent_projects_file(), 'schema_version: "1.0"\nprojects: []\n')


def _print_banner() -> None:
    """Description:
        Render the FAITH CLI banner.

    Requirements:
        - Keep the terminal banner consistent across commands.
    """

    click.secho(BANNER, fg="cyan")


def _open_ui_message() -> None:
    """Description:
        Print the Web UI URL when the browser is not opened automatically.

    Requirements:
        - Always show the URL plainly for manual access.
    """

    click.echo(f"Open {WEB_UI_URL} in your browser.")


def _validate_runtime_compatibility() -> None:
    """Description:
        Validate package and schema compatibility before stack operations.

    Requirements:
        - Require the core FAITH package versions to match.
        - Refuse startup when schema migration is still required.

    :raises click.ClickException: If runtime compatibility validation fails.
    """

    try:
        validate_component_versions(
            {
                "faith_cli": __version__,
                "faith_pa": faith_pa_version,
                "faith_shared": faith_shared_version,
                "faith_web": faith_web_version,
            }
        )
    except FaithCompatibilityError as exc:
        raise click.ClickException(str(exc)) from exc

    project_faith_dir = Path.cwd() / ".faith" if (Path.cwd() / ".faith").exists() else None
    engine = MigrationEngine(config_dir(), project_faith_dir)
    pending = engine.check_all()
    if pending:
        guidance = "\n".join(engine.migration_guide(item) for item in pending)
        raise click.ClickException(guidance)


def _show_runtime_status() -> None:
    """Description:
        Print compose, PA, and host-worker status information.

    Requirements:
        - Show compose service status first.
        - Include host-worker status even when disabled.
    """

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
    else:
        redis = status_data.get("redis", {})
        config = status_data.get("config", {})
        click.echo("")
        click.echo(f"PA service: {status_data.get('service', 'unknown')}")
        click.echo(f"PA version: {status_data.get('version', 'unknown')}")
        click.echo(f"PA health: {status_data.get('status', 'unknown')}")
        click.echo(f"Redis connected: {redis.get('connected', False)}")
        click.echo(f"Config dir: {config.get('config_dir', 'unknown')}")

    host_worker = get_host_worker_status()
    label = "running" if host_worker.running else "stopped"
    click.echo("")
    click.echo(
        f"Host worker: {'enabled' if host_worker.enabled else 'disabled'} "
        f"({host_worker.mode}, {label})"
    )


def _websocket_base_url(http_base_url: str) -> str:
    """Description:
        Convert one local HTTP base URL into its matching WebSocket base URL.

    Requirements:
        - Support the local `http://` route-manifest endpoints exposed by FAITH services.

    :param http_base_url: HTTP base URL for the service.
    :returns: Equivalent WebSocket base URL.
    """

    if http_base_url.startswith("https://"):
        return "wss://" + http_base_url.removeprefix("https://")
    return "ws://" + http_base_url.removeprefix("http://")


def _format_route_url(base_url: str, route: dict[str, Any]) -> str:
    """Description:
        Build one absolute endpoint URL for CLI display.

    Requirements:
        - Use the HTTP base for HTTP routes and WebSocket base for WebSocket routes.
        - Avoid double slashes when joining service bases with paths.

    :param base_url: HTTP base URL for the service.
    :param route: Route manifest entry payload.
    :returns: Absolute URL string for display.
    """

    protocol = route.get("protocol", "http")
    root = _websocket_base_url(base_url) if protocol == "websocket" else base_url
    path = str(route.get("path", ""))
    return f"{root.rstrip('/')}{path}"


def _render_route_manifest(base_url: str, manifest: dict[str, Any]) -> None:
    """Description:
        Print one service route manifest in a stable table-like layout.

    Requirements:
        - Show the owning service header before its routes.
        - Include method, expected HTTP codes, URL, and summary for each route.

    :param base_url: HTTP base URL for the service.
    :param manifest: Parsed route manifest payload.
    """

    service = manifest.get("service", "unknown-service")
    version = manifest.get("version", "unknown")
    click.echo(f"{service} ({version})")

    routes = sorted(
        manifest.get("routes", []),
        key=lambda item: (
            str(item.get("protocol", "")),
            str(item.get("method") or ""),
            str(item.get("path", "")),
        ),
    )
    for route in routes:
        method = str(route.get("method") or route.get("protocol", "")).upper()
        codes = route.get("expected_status_codes", [])
        code_text = ",".join(str(code) for code in codes) if codes else "-"
        url = _format_route_url(base_url, route)
        summary = str(route.get("summary", ""))
        click.echo(f"  [{method:<9}] [{code_text:<11}] {url}")
        click.echo(f"      {summary}")


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, invoke_without_command=True)
@click.version_option(version=__version__)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Description:
        Serve as the root Click command group for the FAITH CLI.

    Requirements:
        - Show group help when no subcommand is provided.

    :param ctx: Active Click command context.
    """

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
def init() -> None:
    """Description:
        Bootstrap `~/.faith` and start the current repo-backed FAITH stack.

    Requirements:
        - Check prerequisites before extracting runtime assets.
        - Validate shared compatibility before starting containers.
        - Start the optional host worker when enabled.
    """

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
    _validate_runtime_compatibility()
    click.echo(f"Framework home ready at {faith_home()}")

    result = compose_up()
    if result.returncode != 0:
        raise click.ClickException("docker compose up failed")

    click.echo(f"Installing default Ollama model {DEFAULT_OLLAMA_MODEL} for the Project Agent.")
    model_result = install_default_ollama_model()
    if model_result.returncode != 0:
        raise click.ClickException(
            f"Failed to install default Ollama model {DEFAULT_OLLAMA_MODEL}."
        )

    host_worker = start_host_worker()
    if host_worker.enabled and host_worker.running:
        click.echo(f"Host worker started with pid {host_worker.pid}.")

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
    """Description:
        Start the current FAITH stack.

    Requirements:
        - Refuse to run before `faith init` has bootstrapped the home directory.
        - Validate shared compatibility before starting containers.
        - Start the optional host worker when enabled.
    """

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()
    _validate_runtime_compatibility()

    if is_running():
        click.echo("FAITH is already running.")
        _open_ui_message()
        return

    result = compose_up()
    if result.returncode != 0:
        raise click.ClickException("docker compose up failed")

    host_worker = start_host_worker()
    if host_worker.enabled and host_worker.running:
        click.echo(f"Host worker started with pid {host_worker.pid}.")

    if wait_and_open_browser():
        click.echo("FAITH stack started.")
    else:
        click.secho("FAITH stack started, but the Web UI is not ready yet.", fg="yellow")
        _open_ui_message()


@main.command()
def stop() -> None:
    """Description:
        Stop the FAITH stack, requesting graceful PA shutdown first when possible.

    Requirements:
        - Attempt coordinated PA shutdown before forcing compose down.
        - Stop the optional host worker as part of shutdown.
    """

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()

    if not is_running():
        click.echo("FAITH is not running.")
        stop_host_worker()
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

    worker_status = stop_host_worker()
    if worker_status.enabled:
        click.echo("Host worker stopped.")
    click.echo("FAITH stack stopped.")


@main.command()
def restart() -> None:
    """Description:
        Restart the FAITH stack.

    Requirements:
        - Validate compatibility before starting the refreshed stack.
        - Stop and restart the optional host worker with the stack.
    """

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()
    _validate_runtime_compatibility()

    if is_running():
        if pa_is_reachable():
            request_shutdown()
        down_result = compose_down()
        if down_result.returncode != 0:
            click.secho(
                "docker compose down reported an error; continuing with startup.", fg="yellow"
            )

    stop_host_worker()

    up_result = compose_up()
    if up_result.returncode != 0:
        raise click.ClickException("docker compose up failed")

    start_host_worker()

    if wait_and_open_browser():
        click.echo("FAITH stack restarted.")
    else:
        click.secho("FAITH stack restarted, but the Web UI is not ready yet.", fg="yellow")
        _open_ui_message()


@main.command()
def status() -> None:
    """Description:
        Show Docker, PA, and host-worker status for the current FAITH stack.

    Requirements:
        - Refuse to run before the framework home has been initialised.
    """

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()
    _show_runtime_status()


@main.command(name="show-urls")
def show_urls() -> None:
    """Description:
        Discover and print the currently exposed FAITH service endpoints.

    Requirements:
        - Query the standard `GET /api/routes` manifest on known services.
        - Avoid hard-coding PA or Web UI route lists inside the CLI.
        - Provide actionable guidance when no services are reachable.
    """

    _print_banner()
    manifests = get_known_route_manifests()
    available = [(base_url, manifest) for base_url, manifest in manifests if manifest is not None]

    if not available:
        raise click.ClickException(
            "No route manifests are available. Start FAITH with `faith init` or `faith start`, then retry."
        )

    unavailable_by_base = {
        PA_BASE_URL: "faith-project-agent",
        WEB_BASE_URL: "faith-web-ui",
    }
    for base_url, manifest in manifests:
        if manifest is None:
            click.secho(
                f"Routes unavailable for {unavailable_by_base.get(base_url, base_url)} at {base_url}.",
                fg="yellow",
            )

    if any(manifest is None for _, manifest in manifests):
        click.echo("")

    for index, (base_url, manifest) in enumerate(available):
        _render_route_manifest(base_url, manifest)
        if index != len(available) - 1:
            click.echo("")


@main.command()
def update() -> None:
    """Description:
        Pull fresh images for the current stack and restart it.

    Requirements:
        - Validate compatibility before update restart.
        - Stop and restart the optional host worker with the stack.
    """

    _print_banner()
    if not is_initialised():
        raise click.ClickException("FAITH is not initialised yet. Run 'faith init' first.")
    check_docker()
    _validate_runtime_compatibility()

    if is_running():
        if pa_is_reachable():
            request_shutdown()
        compose_down()

    stop_host_worker()

    pull_result = compose_pull()
    if pull_result.returncode != 0:
        raise click.ClickException("docker compose pull failed")

    up_result = compose_up()
    if up_result.returncode != 0:
        raise click.ClickException("docker compose up failed")

    start_host_worker()

    click.echo("FAITH stack updated.")
    _open_ui_message()


@main.command()
@click.argument("prompt", required=False)
def run(prompt: str | None = None) -> None:
    """Description:
        Provide the placeholder task-submission command until FAITH-054 is implemented.

    Requirements:
        - Keep the command visible in CLI help.
        - Avoid pretending the real task submission flow already exists.

    :param prompt: Optional task prompt supplied by the user.
    """

    del prompt
    click.echo("Not yet implemented - see FAITH-054.")


@main.command(name="help")
@click.pass_context
def help_command(ctx: click.Context) -> None:
    """Description:
        Show CLI help explicitly through a subcommand.

    Requirements:
        - Print the parent group help when invoked from the root CLI.

    :param ctx: Active Click command context.
    """

    click.echo(ctx.parent.get_help() if ctx.parent else ctx.get_help())
