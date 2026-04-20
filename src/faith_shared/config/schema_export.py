"""Description:
    Export JSON schema files from the canonical shared config models.

Requirements:
    - Generate the schema files from the Pydantic models rather than editing
      JSON files by hand.
    - Write into `src/faith_shared/schemas` by default so tooling and tests use
      the same generated output.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from faith_shared.config.models import (
    AgentConfig,
    ExternalMCPToolConfig,
    SecretsConfig,
    SecurityConfig,
    SystemConfig,
)

SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "agent-config.schema.json": AgentConfig,
    "secrets.schema.json": SecretsConfig,
    "security.schema.json": SecurityConfig,
    "system.schema.json": SystemConfig,
    "tool-config.schema.json": ExternalMCPToolConfig,
}


def schema_output_dir() -> Path:
    """Description:
        Return the canonical schema output directory for shared contracts.

    Requirements:
        - Resolve the path relative to the shared package source tree.

    :returns: Path to the shared schema directory.
    """

    return Path(__file__).resolve().parents[1] / "schemas"


def export_schemas(output_dir: Path | None = None) -> list[Path]:
    """Description:
        Generate JSON schema files for the shared config models.

    Requirements:
        - Create the output directory when it does not exist.
        - Return the list of schema files that were written.

    :param output_dir: Optional target directory for generated schema files.
    :returns: Ordered list of generated schema file paths.
    """

    target_dir = output_dir or schema_output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    written_files: list[Path] = []
    for filename, model_class in SCHEMA_MAP.items():
        path = target_dir / filename
        path.write_text(
            json.dumps(model_class.model_json_schema(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written_files.append(path)
    return written_files


if __name__ == "__main__":
    export_schemas()
