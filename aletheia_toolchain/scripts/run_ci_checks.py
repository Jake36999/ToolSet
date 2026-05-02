"""scripts/run_ci_checks.py — Phase 12.

Lightweight CI driver for the Aletheia toolchain test suite.

Usage:
    python scripts/run_ci_checks.py [--junit-xml PATH]

Exit codes:
    0  — all checks passed
    1  — one or more checks failed

This script runs from the aletheia_toolchain/ directory and assumes all
tool imports resolve from that working directory.  It is designed to be
called from CI (GitHub Actions) but is equally usable locally.
"""

import argparse
import os
import pathlib
import subprocess
import sys
import time
from typing import List, Optional, Tuple


_TOOLCHAIN = pathlib.Path(__file__).parent.parent
_TEST_DIR = _TOOLCHAIN / "tests"
_ARTIFACTS_DIR = _TOOLCHAIN / "test_artifacts"

_GREEN = "\033[32m"
_RED = "\033[31m"
_RESET = "\033[0m"
_BOLD = "\033[1m"

_NO_COLOR = os.environ.get("NO_COLOR") or not sys.stdout.isatty()


def _color(text: str, code: str) -> str:
    if _NO_COLOR:
        return text
    return f"{code}{text}{_RESET}"


def _run_step(label: str, cmd: List[str], cwd: pathlib.Path) -> Tuple[bool, float]:
    print(f"\n{_color('[RUN]', _BOLD)} {label}")
    start = time.monotonic()
    result = subprocess.run(cmd, cwd=str(cwd))
    elapsed = time.monotonic() - start
    ok = result.returncode == 0
    status = _color("PASS", _GREEN) if ok else _color("FAIL", _RED)
    print(f"      {status}  ({elapsed:.1f}s)")
    return ok, elapsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all Aletheia toolchain CI checks."
    )
    parser.add_argument(
        "--junit-xml",
        metavar="PATH",
        default=None,
        dest="junit_xml",
        help="Optional path for JUnit XML test report (requires unittest-xml-reporting).",
    )
    parser.add_argument(
        "--pattern",
        metavar="GLOB",
        default="test_*.py",
        help="Test file glob pattern (default: test_*.py).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Pass -v to the unittest runner.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _ARTIFACTS_DIR.mkdir(exist_ok=True)

    print(_color(f"\n{'='*60}", _BOLD))
    print(_color("  Aletheia Toolchain CI Checks", _BOLD))
    print(_color(f"{'='*60}\n", _BOLD))

    steps: List[Tuple[str, List[str]]] = []

    # --- Unit + integration test suite ---
    discover_cmd = [
        sys.executable, "-m", "unittest",
        "discover",
        "-s", str(_TEST_DIR),
        "-p", args.pattern,
    ]
    if args.verbose:
        discover_cmd.append("-v")
    if args.junit_xml:
        junit_dir = pathlib.Path(args.junit_xml).parent
        junit_dir.mkdir(parents=True, exist_ok=True)
        try:
            import xmlrunner  # type: ignore
            del xmlrunner
            discover_cmd = [
                sys.executable, "-m", "xmlrunner",
                "--output-file", args.junit_xml,
                "discover",
                "-s", str(_TEST_DIR),
                "-p", args.pattern,
            ]
        except ImportError:
            print("Note: xmlrunner not installed; JUnit XML output skipped.")

    steps.append(("Unit + integration tests", discover_cmd))

    results: List[Tuple[str, bool, float]] = []
    for label, cmd in steps:
        ok, elapsed = _run_step(label, cmd, _TOOLCHAIN)
        results.append((label, ok, elapsed))

    print(_color(f"\n{'='*60}", _BOLD))
    print(_color("  Summary", _BOLD))
    print(_color(f"{'='*60}", _BOLD))

    all_passed = True
    for label, ok, elapsed in results:
        icon = _color("OK", _GREEN) if ok else _color("XX", _RED)
        print(f"  {icon}  {label}  ({elapsed:.1f}s)")
        if not ok:
            all_passed = False

    print()
    if all_passed:
        print(_color("All checks passed.", _GREEN))
        return 0
    else:
        print(_color("One or more checks failed.", _RED))
        print(f"Artifacts directory: {_ARTIFACTS_DIR}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
