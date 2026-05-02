"""Fake create_file_map_v3 for LTA-2 runner tests.

Accepts the same flags as the real tool, writes a minimal CSV and
optional health-report JSON, then exits 0 (or the code in FAKE_SCANNER_EXIT).
"""
import argparse
import csv
import json
import os
import pathlib
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--roots", nargs="+")
parser.add_argument("-o", "--out", default="file_map.csv")
parser.add_argument("--health-report")
parser.add_argument("--hash", action="store_true")
parser.add_argument("--profile", default="default")
parser.add_argument("--include-exts")
parser.add_argument("--exclude-dirs")
parser.add_argument("--max-file-size", type=int, default=0)
parser.add_argument("--fail-on-pollution", action="store_true")
args = parser.parse_args()

out = pathlib.Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(
        fh,
        fieldnames=["abs_path", "rel_path", "ext", "size_bytes", "sha1", "health_status"],
    )
    writer.writeheader()
    writer.writerow({
        "abs_path": "/fake/main.py",
        "rel_path": "main.py",
        "ext": ".py",
        "size_bytes": "100",
        "sha1": "abc123def456",
        "health_status": "PASS",
    })

if args.health_report:
    hp = pathlib.Path(args.health_report)
    hp.parent.mkdir(parents=True, exist_ok=True)
    with open(hp, "w", encoding="utf-8") as fh:
        json.dump({"status": "PASS", "row_count": 1, "issues": []}, fh)

sys.exit(int(os.environ.get("FAKE_SCANNER_EXIT", "0")))
