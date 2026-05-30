#!/usr/bin/env bash
# fix-pth.sh — macOS UF_HIDDEN workaround for CPython 3.13 editable installs
#
# macOS may set the UF_HIDDEN flag on files created inside .venv/.
# CPython 3.13's site.py skips hidden .pth files, breaking editable installs.
# Run this after `uv sync` to restore .pth processing.

set -euo pipefail

VENV_DIR="${1:-.venv}"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "error: $VENV_DIR not found" >&2
    exit 1
fi

count=$(find "$VENV_DIR" -name "*.pth" | wc -l | tr -d ' ')
if [[ "$count" -eq 0 ]]; then
    echo "no .pth files found in $VENV_DIR"
    exit 0
fi

find "$VENV_DIR" -name "*.pth" -exec chflags nohidden {} +
echo "fixed $count .pth file(s) in $VENV_DIR"
