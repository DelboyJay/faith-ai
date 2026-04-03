"""Description:
    Resolve secret references and environment substitutions for PA-managed runtimes.

Requirements:
    - Load optional ``.env`` and ``secrets.yaml`` files from the configured config directory.
    - Support ``${VAR}`` environment substitution inside secret payloads.
    - Resolve named secret references for runtime environment and container specifications.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


class SecretResolver:
    """Description:
        Resolve secrets and environment variables for PA-managed runtime specifications.

    Requirements:
        - Allow callers to point at explicit secret and environment files.
        - Merge OS environment variables, caller overrides, and ``.env`` values.
        - Support named secret references across common secret groupings.

    :param config_dir: Optional configuration directory containing ``secrets.yaml`` and ``.env``.
    :param secrets_path: Optional explicit secrets file path.
    :param env_path: Optional explicit environment file path.
    :param environment: Optional environment overrides to merge on top of the host environment.
    """

    def __init__(
        self,
        config_dir: Path | None = None,
        *,
        secrets_path: Path | None = None,
        env_path: Path | None = None,
        environment: dict[str, str] | None = None,
    ):
        """Description:
            Initialise the secret resolver with the configured file paths and environment state.

        Requirements:
            - Derive ``secrets.yaml`` and ``.env`` from ``config_dir`` when explicit paths are absent.
            - Merge environment values in the correct precedence order.
            - Load environment-file values and secrets during initialisation.

        :param config_dir: Optional configuration directory containing ``secrets.yaml`` and ``.env``.
        :param secrets_path: Optional explicit secrets file path.
        :param env_path: Optional explicit environment file path.
        :param environment: Optional environment overrides to merge on top of the host environment.
        """

        if secrets_path is None and config_dir is not None:
            secrets_path = Path(config_dir) / "secrets.yaml"
        if env_path is None and config_dir is not None:
            env_path = Path(config_dir) / ".env"
        self.secrets_path = Path(secrets_path) if secrets_path is not None else None
        self.env_path = Path(env_path) if env_path is not None else None
        self.environment = dict(self._load_env_file())
        self.environment.update(dict(os.environ))
        self.environment.update(environment or {})
        self.secrets = self._load_secrets()

    def _load_env_file(self) -> dict[str, str]:
        """Description:
            Load environment variables from the configured ``.env`` file.

        Requirements:
            - Ignore blank lines, comments, and malformed entries.
            - Return an empty mapping when no environment file exists.

        :returns: Environment variables loaded from the ``.env`` file.
        """

        if self.env_path is None or not self.env_path.exists():
            return {}
        loaded: dict[str, str] = {}
        for line in self.env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            loaded[key.strip()] = value.strip().strip("'\"")
        return loaded

    def _load_secrets(self) -> dict[str, Any]:
        """Description:
            Load and environment-expand the configured secrets file.

        Requirements:
            - Return an empty mapping when no secrets file exists.
            - Apply ``${VAR}`` substitution before YAML parsing.
            - Return an empty mapping when the parsed payload is not a mapping.

        :returns: Parsed secret payload mapping.
        """

        if self.secrets_path is None or not self.secrets_path.exists():
            return {}
        raw = self.secrets_path.read_text(encoding="utf-8")
        raw = _ENV_PATTERN.sub(lambda match: self.environment.get(match.group(1), ""), raw)
        loaded = yaml.safe_load(raw) or {}
        return loaded if isinstance(loaded, dict) else {}

    def _substitute(self, value: str) -> str:
        """Description:
            Substitute ``${VAR}`` placeholders inside one string value.

        Requirements:
            - Replace unknown variables with empty strings.

        :param value: String value containing optional environment placeholders.
        :returns: Substituted string value.
        """

        return _ENV_PATTERN.sub(lambda match: self.environment.get(match.group(1), ""), value)

    def resolve_secret_ref(self, secret_ref: str) -> Any:
        """Description:
            Resolve one logical secret reference from the loaded secret payload.

        Requirements:
            - Check the top-level mapping first.
            - Then search the common grouped secret sections used by FAITH.
            - Raise ``KeyError`` when the secret cannot be found.

        :param secret_ref: Secret reference name to resolve.
        :returns: Resolved secret value.
        :raises KeyError: If the secret reference does not exist.
        """

        if secret_ref in self.secrets:
            return self.secrets[secret_ref]
        secrets = self.secrets.get("secrets")
        if isinstance(secrets, dict) and secret_ref in secrets:
            return secrets[secret_ref]
        for group_name in ("databases", "services", "credentials", "tokens"):
            group = self.secrets.get(group_name)
            if isinstance(group, dict) and secret_ref in group:
                return group[secret_ref]
        raise KeyError(secret_ref)

    def resolve_environment(
        self,
        *,
        env: dict[str, str] | None = None,
        env_secret_refs: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Description:
            Resolve a runtime environment mapping and any associated secret references.

        Requirements:
            - Apply environment substitution to plain environment values first.
            - Inject resolved secret values for the supplied secret-reference mapping.

        :param env: Plain environment values to substitute.
        :param env_secret_refs: Mapping of environment keys to secret references.
        :returns: Fully resolved environment mapping.
        """

        resolved = {key: self._substitute(value) for key, value in (env or {}).items()}
        for key, ref in (env_secret_refs or {}).items():
            secret = self.resolve_secret_ref(ref)
            resolved[key] = str(
                secret if not isinstance(secret, dict) else secret.get("value", secret)
            )
        return resolved

    def resolve_container_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Description:
            Resolve secret references inside one container specification mapping.

        Requirements:
            - Replace ``environment`` and ``env_secret_refs`` with a resolved environment mapping.
            - Expand ``password_secret_ref`` and ``secret_ref`` when present.

        :param spec: Container specification payload to resolve.
        :returns: Resolved container specification mapping.
        """

        resolved = dict(spec)
        env = self.resolve_environment(
            env=resolved.get("environment"),
            env_secret_refs=resolved.get("env_secret_refs"),
        )
        if env:
            resolved["environment"] = env
        if "password_secret_ref" in resolved:
            resolved["password"] = self.resolve_secret_ref(str(resolved.pop("password_secret_ref")))
        if "secret_ref" in resolved:
            resolved["secret"] = self.resolve_secret_ref(str(resolved["secret_ref"]))
        return resolved

    def resolve_env(
        self,
        env: dict[str, str] | None = None,
        env_secret_refs: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Description:
            Provide a short alias for environment resolution.

        Requirements:
            - Delegate to ``resolve_environment`` without changing behaviour.

        :param env: Plain environment values to substitute.
        :param env_secret_refs: Mapping of environment keys to secret references.
        :returns: Fully resolved environment mapping.
        """

        return self.resolve_environment(env=env, env_secret_refs=env_secret_refs)

    def resolve_tool_config(self, tool_config: dict[str, Any]) -> dict[str, Any]:
        """Description:
            Resolve secret-backed values embedded in one tool configuration mapping.

        Requirements:
            - Preserve the original config structure while replacing secret references with runtime values.
            - Remove the ``env_secret_refs`` helper mapping after resolution.
            - Recursively expand nested ``secret_ref`` keys without overwriting explicit values.

        :param tool_config: Tool configuration mapping to resolve.
        :returns: Resolved tool configuration mapping.
        """

        return self._resolve_tool_value(tool_config)

    def _resolve_tool_value(self, value: Any) -> Any:
        """Description:
            Recursively resolve secret-backed values inside one tool-config payload node.

        Requirements:
            - Resolve nested mappings and lists recursively.
            - Merge `secret_ref` payloads into the surrounding mapping without overwriting explicit values.
            - Resolve `env` and `env_secret_refs` helper mappings before returning the node.

        :param value: Tool-config payload node to resolve.
        :returns: Resolved payload node.
        """

        if isinstance(value, list):
            return [self._resolve_tool_value(item) for item in value]

        if not isinstance(value, dict):
            return value

        resolved = {
            key: self._resolve_tool_value(item)
            for key, item in value.items()
            if key != "env_secret_refs"
        }

        env = self.resolve_environment(
            env=resolved.get("env"),
            env_secret_refs=value.get("env_secret_refs"),
        )
        if env:
            resolved["env"] = env
        elif "env" in resolved and not resolved["env"]:
            resolved.pop("env", None)

        if "secret_ref" in value:
            secret_value = self.resolve_secret_ref(str(value["secret_ref"]))
            if isinstance(secret_value, dict):
                for key, item in secret_value.items():
                    resolved.setdefault(key, item)
            else:
                resolved.setdefault("value", secret_value)
            resolved.pop("secret_ref", None)

        return resolved

    def build_env_dict(self, secret_ref: str) -> dict[str, str]:
        """Description:
            Build an environment mapping from one named secret reference.

        Requirements:
            - Upper-case scalar keys for runtime environment injection.
            - Return ``VALUE`` for scalar secret payloads.

        :param secret_ref: Secret reference name to expand.
        :returns: Environment mapping derived from the resolved secret.
        """

        secret = self.resolve_secret_ref(secret_ref)
        if isinstance(secret, dict):
            return {key.upper(): str(value) for key, value in secret.items()}
        return {"VALUE": str(secret)}
