from __future__ import annotations

from pathlib import Path

import pytest

from faith_pa.pa.secret_resolver import SecretResolver


def test_resolve_environment_and_secret_refs(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("secrets:\n  api_key: super-secret\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("BASE_URL=https://example.test\n", encoding="utf-8")

    resolver = SecretResolver(
        secrets_path=secrets_path,
        env_path=env_path,
        environment={"EXTRA_TOKEN": "abc123"},
    )

    resolved = resolver.resolve_environment(
        env={"URL": "${BASE_URL}", "TOKEN": "${EXTRA_TOKEN}"},
        env_secret_refs={"API_KEY": "api_key"},
    )

    assert resolved == {
        "URL": "https://example.test",
        "TOKEN": "abc123",
        "API_KEY": "super-secret",
    }


def test_resolve_container_spec_promotes_secret_ref(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("secrets:\n  db_password: swordfish\n", encoding="utf-8")

    resolver = SecretResolver(secrets_path=secrets_path)
    spec = resolver.resolve_container_spec(
        {
            "image": "postgres:16",
            "password_secret_ref": "db_password",
            "environment": {"MODE": "dev"},
        }
    )

    assert spec["password"] == "swordfish"
    assert spec["environment"]["MODE"] == "dev"


def test_unknown_secret_ref_raises_key_error(tmp_path: Path) -> None:
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("secrets: {}\n", encoding="utf-8")

    resolver = SecretResolver(secrets_path=secrets_path)

    with pytest.raises(KeyError):
        resolver.resolve_secret_ref("missing")

