"""Description:
    Expose the FAITH Web UI backend package metadata.

Requirements:
    - Publish one package version for compatibility checks.
    - Keep the app factory import available for runtime startup.
"""

from faith_web.version import __version__
from faith_web.app import create_app

__all__ = ["__version__", "create_app"]
