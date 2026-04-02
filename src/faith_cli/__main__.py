"""Description:
    Provide the module entrypoint used by ``python -m faith_cli``.

Requirements:
    - Delegate execution to the canonical CLI command group.
"""

from faith_cli.cli import main

if __name__ == "__main__":
    main()
