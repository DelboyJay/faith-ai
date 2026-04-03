"""
Description:
    Load project-level configuration for the FAITH Python execution MCP server.

Requirements:
    - Read ``.faith/tools/python.yaml`` when it exists.
    - Fall back to validated defaults when the file is absent or invalid.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from faith_shared.config.models import PythonToolConfig

logger = logging.getLogger("faith.mcp.python_exec.config")


def load_python_config(faith_dir: Path) -> PythonToolConfig:
    """
    Description:
        Load the Python tool configuration from the project ``.faith`` directory.

    Requirements:
        - Return validated defaults when the config file is missing.
        - Log parse failures and still return a usable default config.

    :param faith_dir: Project ``.faith`` directory.
    :returns: Validated Python tool configuration.
    """

    config_path = Path(faith_dir) / "tools" / "python.yaml"
    if not config_path.exists():
        return PythonToolConfig()

    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return PythonToolConfig.model_validate(payload)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to parse %s: %s", config_path, exc)
        return PythonToolConfig()
