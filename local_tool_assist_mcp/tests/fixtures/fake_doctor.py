"""Fake manifest_doctor for LTA-2 runner tests.

Accepts the same flags as the real tool. Exit code and status are
controlled via environment variables:
  FAKE_DOCTOR_EXIT   — integer exit code (default 0)
  FAKE_DOCTOR_ERROR  — if set, adds an "error" key to the report (schema error path)
"""
import argparse
import json
import os
import pathlib
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--manifest", required=True)
parser.add_argument("--config")
parser.add_argument("--out", default="manifest_doctor_report.json")
parser.add_argument("--markdown-out")
parser.add_argument("--required-path", action="append", default=[], dest="required_paths")
parser.add_argument("--required-ext", action="append", default=[], dest="required_exts")
parser.add_argument("--max-rows-soft", type=int, default=0)
parser.add_argument("--max-rows-hard", type=int, default=0)
parser.add_argument("--max-file-size", type=int, default=0)
args = parser.parse_args()

exit_code = int(os.environ.get("FAKE_DOCTOR_EXIT", "0"))
status = "BLOCK" if exit_code == 2 else "PASS"

report: dict = {"status": status, "findings": {}, "summary": f"fake doctor {status}"}
if os.environ.get("FAKE_DOCTOR_ERROR"):
    report["error"] = "schema parse error"

out = pathlib.Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    json.dump(report, fh)

if args.markdown_out:
    md = pathlib.Path(args.markdown_out)
    md.parent.mkdir(parents=True, exist_ok=True)
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(f"# Manifest Doctor\n\nStatus: **{status}**\n")

sys.exit(exit_code)
