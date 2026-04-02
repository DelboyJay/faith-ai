"""Description:
    Expose package metadata for the FAITH command-line interface package.

Requirements:
    - Keep the public package surface minimal at import time.
    - Expose the installed package version for CLI reporting.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
