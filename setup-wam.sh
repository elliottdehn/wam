#!/bin/sh
# Create the local WAM environment on macOS and Linux.
#
# This is the POSIX counterpart to Setup-WAM.ps1 and does the same three
# things: build a virtual environment beside this checkout, install WAM into
# it as an editable package, and prove the result actually runs.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV="$ROOT/.venv"
VENV_PYTHON="$VENV/bin/python"

# Prefer python3: on macOS and most distributions a bare `python` is either
# absent or still Python 2, so resolving it first would pick the wrong one.
if [ ! -x "$VENV_PYTHON" ]; then
    BASE_PYTHON=""
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
                "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
            BASE_PYTHON=$candidate
            break
        fi
    done
    if [ -z "$BASE_PYTHON" ]; then
        # The floor matches what macOS ships in the Command Line Tools, so
        # reaching this on a Mac means the tools are missing rather than out
        # of date.  Say which interpreter was actually found.
        echo "WAM requires Python 3.9 or newer (pyproject.toml requires-python)." >&2
        echo "Found: $(python3 -V 2>&1 || echo 'no python3 on PATH')" >&2
        echo "On macOS: xcode-select --install, or brew install python@3.13." >&2
        exit 1
    fi
    "$BASE_PYTHON" -m venv "$VENV"
fi

if [ ! -x "$VENV_PYTHON" ]; then
    echo "The virtual environment did not create $VENV_PYTHON" >&2
    exit 1
fi

# Python 3.9 bundles pip 21.2, which predates PEP 660 and refuses an editable
# install from a pyproject-only project ("editable mode currently requires a
# setuptools-based build").  Upgrading first is what makes the 3.9 floor real
# rather than nominal.
"$VENV_PYTHON" -m pip install --quiet --upgrade "pip>=21.3" setuptools wheel

# Editable installation keeps the CLI bound to this checkout, so an agent
# always runs the exact source files it is reviewing and modifying.
"$VENV_PYTHON" -m pip install --editable "$ROOT"
"$VENV_PYTHON" -m wam.codex_cli --help >/dev/null

echo "WAM is ready: $VENV_PYTHON"
