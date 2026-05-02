"""Fake tool_command_linter for LTA-2 runner tests.

Accepts the same flags as the real tool. Exit code and status are
controlled via environment variables:
  FAKE_LINTER_EXIT   — integer exit code (default 0)
"""
import argparse
import json
import os
import pathlib
import sys

parser = argparse.ArgumentParser()
source = parser.add_mutually_exclusive_group(required=True)
source.add_argument("--command")
source.add_argument("--command-file")
parser.add_argument("--config")
parser.add_argument("--out", default="command_lint_report.json")
parser.add_argument("--rewrite-out")
args = parser.parse_args()

exit_code = int(os.environ.get("FAKE_LINTER_EXIT", "0"))
status = "BLOCK" if exit_code == 2 else "PASS"

report: dict = {"status": status, "errors": [], "warnings": [], "summary": f"fake linter {status}"}
if exit_code == 2:
    report["errors"].append({"rule_id": "R001", "message": "fake block from linter"})

out = pathlib.Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    json.dump(report, fh)

sys.exit(exit_code)
