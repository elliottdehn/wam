#!/usr/bin/env python3
"""Run WAM's script-style regression tests with the active interpreter.

The historical tests execute assertions at import time and terminate with
``sys.exit``. Running them as isolated child processes preserves that contract
while providing one reliable command for local setup, Codex, and CI.
"""
from __future__ import annotations

import os
import subprocess
import sys


TESTS = (
    "test_near_plane.py",
    "test_mirror.py",
    "test_cinematic.py",
    "test_acceptance_cine.py",
    "test_multiview.py",
    "test_multireference.py",
    "test_edits.py",
    "test_editor_bridge.py",
)


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    for filename in TESTS:
        path = os.path.join(root, filename)
        print("\n=== %s ===" % filename, flush=True)
        completed = subprocess.run([sys.executable, "-B", path], check=False)
        if completed.returncode:
            print("FAILED: %s (exit %d)" % (filename, completed.returncode),
                  file=sys.stderr)
            return completed.returncode
    print("\nAll %d WAM test scripts passed." % len(TESTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
