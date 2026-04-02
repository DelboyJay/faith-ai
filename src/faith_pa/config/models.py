"""Description:
    Re-export the canonical shared FAITH config models for compatibility.

Requirements:
    - Keep `faith_pa.config.models` import-compatible for existing callers.
    - Delegate all model ownership to `faith_shared.config.models`.
"""

from faith_shared.config.models import *  # noqa: F403
