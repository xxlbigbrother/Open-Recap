#!/usr/bin/env python3
"""Cross-platform test runner: one isolated pytest process per skill group.

Several stages use skill-local lib.py modules. Collecting all tests in one process
would mix those modules, so run each group in its own subprocess.

Works on macOS, Linux, and Windows (the bash equivalent is scripts/test.sh).

Usage: python scripts/test.py            # run every skill group
       python scripts/test.py script     # run one or more named groups
"""
import subprocess
import os
import sys
from pathlib import Path

GROUPS = ["understanding", "cut", "voiceover", "assemble", "script", "orchestrator", "runtime"]


def _require_pytest():
    """Fail with the actual problem instead of reporting every group as a test failure.

    Without this, a missing pytest prints "No module named pytest" for every group and then
    "FAILED groups: understanding, cut, ..." — indistinguishable from real failures.
    """
    import importlib.util

    if importlib.util.find_spec("pytest") is not None:
        return True
    print(
        f"pytest is not installed for this interpreter ({sys.executable}).\n"
        f"  Install it:  {sys.executable} -m pip install pytest\n"
        "  Or run the suite with an interpreter that has it (PYTHON=... scripts/test.sh).",
        file=sys.stderr,
    )
    return False


def main(argv):
    if not _require_pytest():
        return 2
    root = Path(__file__).resolve().parent.parent
    groups = argv or GROUPS
    failed = []
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "scripts") + os.pathsep + env.get("PYTHONPATH", "")
    for group in groups:
        print(f"== {group} ==", flush=True)
        result = subprocess.run(
            # -rs prints skip reasons so a silently-skipped real-render test (e.g. ffmpeg
            # missing) is visible in CI output instead of a bare "s".
            [sys.executable, "-m", "pytest", str(root / "tests" / group), "-q", "-rs"],
            cwd=str(root), env=env,
        )
        if result.returncode != 0:
            failed.append(group)
    if failed:
        print(f"FAILED groups: {', '.join(failed)}")
        return 1
    print("All skill groups passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
