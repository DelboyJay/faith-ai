"""Secret resolution helpers for PA-managed runtimes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


class SecretResolver:
    """Resolve secret references and environment substitution."""

    def __init__(
        self,
        config_dir: Path | None = None,
        *,
        secrets_path: Path | None = None,
        env_path: Path | None = None,
        environment: dict[str, str] | None = None,
    ):
        if secrets_path is None and config_dir is not None:
            secrets_path = Path(config_dir) / "secrets.yaml"
        if env_path is None and config_dir is not None:
            env_path = Path(config_dir) / ".env"
        self.secrets_path = Path(secrets_path) if secrets_path is not None else None
        self.env_path = Path(env_path) if env_path is not None else None
        self.environment = dict(os.environ)
        self.environment.update(environment or {})
        self.environment.update(self._load_env_file())
        self.secrets = self._load_secrets()

    def _load_env_file(self) -> dict[str, str]:
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
        if self.secrets_path is None or not self.secrets_path.exists():
            return {}
        raw = self.secrets_path.read_text(encoding="utf-8")
        raw = _ENV_PATTERN.sub(lambda match: self.environment.get(match.group(1), ""), raw)
        loaded = yaml.safe_load(raw) or {}
        return loaded if isinstance(loaded, dict) else {}

    def _substitute(self, value: str) -> str:
        return _ENV_PATTERN.sub(lambda match: self.environment.get(match.group(1), ""), value)

    def resolve_secret_ref(self, secret_ref: str) -> Any:
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
        resolved = {key: self._substitute(value) for key, value in (env or {}).items()}
        for key, ref in (env_secret_refs or {}).items():
            secret = self.resolve_secret_ref(ref)
            resolved[key] = str(
                secret if not isinstance(secret, dict) else secret.get("value", secret)
            )
        return resolved

    def resolve_container_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
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
        return self.resolve_environment(env=env, env_secret_refs=env_secret_refs)
