#!/bin/sh
set -eu

exec python -m faith_mcp.filesystem.server
