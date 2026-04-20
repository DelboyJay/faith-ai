"""Description:
    Verify secret resolution for environment variables, secret references, and container specs.

Requirements:
    - Prove environment substitution and secret-reference injection both work.
    - Prove container specifications promote secret references into resolved fields.
    - Prove unknown secret references fail with ``KeyError``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from faith_pa.pa.secret_resolver import SecretResolver


def test_resolve_environment_and_secret_refs(tmp_path: Path) -> None:
    """Description:
    Verify environment placeholders and secret references are resolved together.

    Requirements:
        - This test is needed to prove runtime environment maps can combine plain values and secret references safely.
        - Verify environment substitution and secret injection produce the expected mapping.

    :param tmp_path: Temporary pytest directory fixture.
    """

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
    """Description:
    Verify container specs replace password secret references with resolved secret values.

    Requirements:
        - This test is needed to prove container runtime specs can be materialised without exposing unresolved secret references.
        - Verify the resolved spec contains the password and preserves plain environment values.

    :param tmp_path: Temporary pytest directory fixture.
    """

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
    """Description:
    Verify unknown secret references fail with ``KeyError``.

    Requirements:
        - This test is needed to prove missing secret references do not fail open.
        - Verify resolving an unknown secret name raises ``KeyError``.

    :param tmp_path: Temporary pytest directory fixture.
    """

    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("secrets: {}\n", encoding="utf-8")

    resolver = SecretResolver(secrets_path=secrets_path)

    with pytest.raises(KeyError):
        resolver.resolve_secret_ref("missing")
