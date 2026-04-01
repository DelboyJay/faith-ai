#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

echo "Regenerating FAITH dependency graph from epic.yaml..."

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  echo "Failed to generate graph. Check Python is installed and on PATH."
  exit 1
fi

"$PYTHON_BIN" "$SCRIPT_DIR/generate_dependency_graph.py"

if [ -f "$SCRIPT_DIR/dependency-graph.svg" ]; then
  echo "Generated dependency-graph.svg"
fi
