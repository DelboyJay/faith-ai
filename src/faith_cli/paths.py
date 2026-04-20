"""Description:
    Resolve paths for the FAITH CLI bootstrap flow.

Requirements:
    - Keep all host-owned CLI paths under `~/.faith`.
    - Expose dedicated helpers for compose, config, logs, and host-worker state.
"""

from __future__ import annotations

from pathlib import Path


def package_root() -> Path:
    """Description:
        Return the installed CLI package root.

    Requirements:
        - Resolve the directory containing the installed CLI package modules.

    :returns: Installed CLI package directory.
    """

    return Path(__file__).resolve().parent


def package_resources_root() -> Path:
    """Description:
        Return the bundled CLI resource directory.

    Requirements:
        - Keep resource discovery relative to the installed package path.

    :returns: CLI resource directory.
    """

    return package_root() / "resources"


def bundled_config_dir() -> Path:
    """Description:
        Return the bundled framework config template directory.

    Requirements:
        - Keep config templates inside the CLI package resources.

    :returns: Bundled config template directory.
    """

    return package_resources_root() / "config"


def bundled_data_dir() -> Path:
    """Description:
        Return the bundled framework data template directory.

    Requirements:
        - Keep shipped seed data inside the CLI package resources.

    :returns: Bundled data directory.
    """

    return package_resources_root() / "data"


def bundled_compose_file() -> Path:
    """Description:
        Return the bundled bootstrap compose file shipped with the CLI.

    Requirements:
        - Use the packaged compose file for non-editable installs.

    :returns: Packaged compose file path.
    """

    return package_resources_root() / "docker-compose.yml"


def source_root() -> Path:
    """Description:
        Return the repository root for local development workflows.

    Requirements:
        - Resolve the root relative to the editable-install module path.

    :returns: Repository root path.
    """

    return Path(__file__).resolve().parents[2]


def source_compose_file() -> Path:
    """Description:
        Return the repository-backed compose file used for development.

    Requirements:
        - Resolve the compose path relative to the repository root.

    :returns: Repository compose file path.
    """

    return source_root() / "docker-compose.yml"


def is_editable_install() -> bool:
    """Description:
        Return whether the CLI is running from a local repository checkout.

    Requirements:
        - Detect the editable install by looking for repository markers.

    :returns: ``True`` when running from a repository checkout.
    """

    root = source_root()
    return (root / "pyproject.toml").exists() and (root / "src").exists()


def faith_home() -> Path:
    """Description:
        Return the user-owned FAITH home directory.

    Requirements:
        - Keep all extracted bootstrap assets under the user home directory.

    :returns: FAITH home directory path.
    """

    return Path.home() / ".faith"


def config_dir() -> Path:
    """Description:
        Return the extracted framework config directory.

    Requirements:
        - Keep the config tree under the FAITH home directory.

    :returns: Framework config directory path.
    """

    return faith_home() / "config"


def data_dir() -> Path:
    """Description:
        Return the extracted framework data directory.

    Requirements:
        - Keep framework data under the FAITH home directory.

    :returns: Framework data directory path.
    """

    return faith_home() / "data"


def logs_dir() -> Path:
    """Description:
        Return the extracted framework log directory.

    Requirements:
        - Keep framework logs under the FAITH home directory.

    :returns: Framework log directory path.
    """

    return faith_home() / "logs"


def archetypes_dir() -> Path:
    """Description:
        Return the extracted archetype directory.

    Requirements:
        - Keep archetypes under the framework config directory.

    :returns: Extracted archetype directory path.
    """

    return config_dir() / "archetypes"


def env_file() -> Path:
    """Description:
        Return the extracted environment template path.

    Requirements:
        - Keep the environment file under the framework config directory.

    :returns: Extracted environment file path.
    """

    return config_dir() / ".env"


def secrets_file() -> Path:
    """Description:
        Return the extracted secrets template path.

    Requirements:
        - Keep the secrets file under the framework config directory.

    :returns: Extracted secrets file path.
    """

    return config_dir() / "secrets.yaml"


def recent_projects_file() -> Path:
    """Description:
        Return the framework recent-projects file path.

    Requirements:
        - Keep recent project state under the framework config directory.

    :returns: Recent-projects file path.
    """

    return config_dir() / "recent-projects.yaml"


def host_worker_pid_file() -> Path:
    """Description:
        Return the pid file path for the optional host worker.

    Requirements:
        - Store worker runtime state under the FAITH home directory.

    :returns: Host-worker pid file path.
    """

    return faith_home() / "host-worker.pid"


def host_worker_log_file() -> Path:
    """Description:
        Return the host-worker log file path.

    Requirements:
        - Keep host-worker logs under the extracted logs directory.

    :returns: Host-worker log file path.
    """

    return logs_dir() / "host-worker.log"


def installed_compose_file() -> Path:
    """Description:
        Return the extracted bootstrap compose file path.

    Requirements:
        - Always resolve the runtime compose path under `~/.faith`.

    :returns: Extracted compose file path.
    """

    return faith_home() / "docker-compose.yml"


def compose_file() -> Path:
    """Description:
        Return the compose file used by the CLI runtime.

    Requirements:
        - Always use the extracted compose file for runtime operations.

    :returns: Runtime compose file path.
    """

    return installed_compose_file()


def editable_compose_contents() -> str:
    """Description:
        Return a repo-backed compose file for editable installs.

    Requirements:
        - Build the PA and Web UI from the local repository checkout.
        - Keep config, data, and logs under the user's FAITH home directory.

    :returns: Generated YAML text for the editable-install compose file.
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
      - FAITH_PROJECT_AGENT_MODEL=ollama/llama3:8b
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
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  mcp-registry:
    image: ghcr.io/modelcontextprotocol/registry:latest
    container_name: faith-mcp-registry
    ports:
      - \"8081:8080\"
    environment:
      - MCP_REGISTRY_DATABASE_URL=postgres://mcp_registry:mcp_registry@mcp-registry-db:5432/mcp_registry?sslmode=disable
      - MCP_REGISTRY_ENABLE_ANONYMOUS_AUTH=true
      - MCP_REGISTRY_JWT_PRIVATE_KEY=000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
    networks:
      - maf-network
    restart: unless-stopped
    depends_on:
      mcp-registry-db:
        condition: service_healthy

  mcp-registry-db:
    image: postgres:16-alpine
    container_name: faith-mcp-registry-db
    environment:
      - POSTGRES_USER=mcp_registry
      - POSTGRES_PASSWORD=mcp_registry
      - POSTGRES_DB=mcp_registry
    volumes:
      - mcp-registry-db-data:/var/lib/postgresql/data
    networks:
      - maf-network
    restart: unless-stopped
    healthcheck:
      test: [\"CMD-SHELL\", \"pg_isready -U mcp_registry -d mcp_registry\"]
      interval: 10s
      timeout: 3s
      retries: 5

networks:
  maf-network:
    name: maf-network
    driver: bridge

volumes:
  redis-data:
  ollama-data:
  mcp-registry-db-data:
"""


def is_initialised() -> bool:
    """Description:
        Return True when the local FAITH home has been bootstrapped.

    Requirements:
        - Require the extracted compose file, templates, and framework folders.

    :returns: ``True`` when the runtime home is ready.
    """

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
    """Description:
        Return True while the user has not provided secrets yet.

    Requirements:
        - Treat placeholder or empty secrets files as unfinished first-run setup.

    :returns: ``True`` when first-run setup is still incomplete.
    """

    secrets = secrets_file()
    if not secrets.exists():
        return True
    content = secrets.read_text(encoding="utf-8").strip()
    if not content:
        return True
    return "your_" in content.lower() or "changeme" in content.lower()
