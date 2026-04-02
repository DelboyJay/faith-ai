"""Resolve paths for the FAITH CLI bootstrap flow."""

from __future__ import annotations

from pathlib import Path


def package_root() -> Path:
    """Return the installed CLI package root."""

    return Path(__file__).resolve().parent


def package_resources_root() -> Path:
    """Return the bundled CLI resource directory."""

    return package_root() / "resources"


def bundled_config_dir() -> Path:
    """Return the bundled framework config template directory."""

    return package_resources_root() / "config"


def bundled_data_dir() -> Path:
    """Return the bundled framework data template directory."""

    return package_resources_root() / "data"


def bundled_compose_file() -> Path:
    """Return the bundled bootstrap compose file shipped with the CLI."""

    return package_resources_root() / "docker-compose.yml"


def source_root() -> Path:
    """Return the repository root for local development workflows."""

    return Path(__file__).resolve().parents[2]


def source_compose_file() -> Path:
    """Return the repository-backed compose file used for development."""

    return source_root() / "docker-compose.yml"


def is_editable_install() -> bool:
    """Return whether the CLI is running from a local repository checkout."""

    root = source_root()
    return (root / "pyproject.toml").exists() and (root / "src").exists()


def faith_home() -> Path:
    """Return the user-owned FAITH home directory."""

    return Path.home() / ".faith"


def config_dir() -> Path:
    """Return the extracted framework config directory."""

    return faith_home() / "config"


def data_dir() -> Path:
    """Return the extracted framework data directory."""

    return faith_home() / "data"


def logs_dir() -> Path:
    """Return the extracted framework log directory."""

    return faith_home() / "logs"


def archetypes_dir() -> Path:
    """Return the extracted archetype directory."""

    return config_dir() / "archetypes"


def env_file() -> Path:
    """Return the extracted environment template path."""

    return config_dir() / ".env"


def secrets_file() -> Path:
    """Return the extracted secrets template path."""

    return config_dir() / "secrets.yaml"


def recent_projects_file() -> Path:
    """Return the framework recent-projects file path."""

    return config_dir() / "recent-projects.yaml"


def installed_compose_file() -> Path:
    """Return the extracted bootstrap compose file path."""

    return faith_home() / "docker-compose.yml"


def compose_file() -> Path:
    """Return the compose file used by the CLI runtime."""

    return installed_compose_file()


def editable_compose_contents() -> str:
    """Return a repo-backed compose file for editable installs.

    The generated compose file builds PA and Web UI images from the local
    repository checkout while keeping config, data, and logs under the user's
    FAITH home directory.
    """

    root = source_root().as_posix()
    config = config_dir().as_posix()
    data = data_dir().as_posix()
    logs = logs_dir().as_posix()
    env = env_file().as_posix()
    redis_conf = (source_root() / "containers" / "redis" / "redis.conf").as_posix()

    return f"""services:
  pa:
    build:
      context: {root}
      dockerfile: containers/pa/Dockerfile
    container_name: faith-pa
    ports:
      - \"8000:8000\"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - {config}:/config:ro
      - {data}:/data
      - {logs}:/logs
    environment:
      - FAITH_CONFIG_DIR=/config
      - FAITH_LOG_DIR=/logs
      - FAITH_DATA_DIR=/data
      - FAITH_REDIS_URL=redis://redis:6379/0
      - MCP_REGISTRY_URL=http://mcp-registry:8080
    env_file:
      - {env}
    networks:
      - maf-network
    restart: unless-stopped
    depends_on:
      redis:
        condition: service_healthy
      ollama:
        condition: service_started
      mcp-registry:
        condition: service_started
    healthcheck:
      test: [\"CMD\", \"python\", \"-c\", \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)\"]
      interval: 15s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: faith-redis
    command: redis-server /usr/local/etc/redis/redis.conf
    volumes:
      - redis-data:/data
      - {redis_conf}:/usr/local/etc/redis/redis.conf:ro
    networks:
      - maf-network
    restart: unless-stopped
    healthcheck:
      test: [\"CMD\", \"redis-cli\", \"ping\"]
      interval: 10s
      timeout: 3s
      retries: 3

  web-ui:
    build:
      context: {root}
      dockerfile: containers/web-ui/Dockerfile
    container_name: faith-web-ui
    ports:
      - \"8080:8080\"
    volumes:
      - {logs}:/logs:ro
    environment:
      - FAITH_PA_URL=http://pa:8000
    networks:
      - maf-network
    restart: unless-stopped
    depends_on:
      pa:
        condition: service_started
    healthcheck:
      test: [\"CMD\", \"python\", \"-c\", \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)\"]
      interval: 15s
      timeout: 5s
      retries: 5

  ollama:
    image: ollama/ollama:latest
    container_name: faith-ollama
    ports:
      - \"11434:11434\"
    volumes:
      - ollama-data:/root/.ollama
    networks:
      - maf-network
    restart: unless-stopped

  mcp-registry:
    image: ghcr.io/modelcontextprotocol/registry:latest
    container_name: faith-mcp-registry
    ports:
      - \"8081:8080\"
    networks:
      - maf-network
    restart: unless-stopped

networks:
  maf-network:
    name: maf-network
    driver: bridge

volumes:
  redis-data:
  ollama-data:
"""


def is_initialised() -> bool:
    """Return True when the local FAITH home has been bootstrapped."""

    required_paths = [
        faith_home(),
        config_dir(),
        data_dir(),
        logs_dir(),
        archetypes_dir(),
        env_file(),
        secrets_file(),
        recent_projects_file(),
        installed_compose_file(),
    ]
    return all(path.exists() for path in required_paths)


def is_first_run() -> bool:
    """Return True while the user has not provided secrets yet."""

    secrets = secrets_file()
    if not secrets.exists():
        return True
    content = secrets.read_text(encoding="utf-8").strip()
    if not content:
        return True
    return "your_" in content.lower() or "changeme" in content.lower()
