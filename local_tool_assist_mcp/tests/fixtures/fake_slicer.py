"""Fake semantic_slicer_v7.0 for LTA-2 runner tests.

Accepts the same flags as the real tool. Always exits 0 (the gate test
never reaches the subprocess — it returns POLICY_BLOCK before running).
"""
import argparse
import json
import pathlib
import sys

parser = argparse.ArgumentParser()
parser.add_argument("paths", nargs="*")
parser.add_argument("-o", "--output")
parser.add_argument("--manifest")
parser.add_argument("--base-dir", default=".")
parser.add_argument("--format", default="text")
parser.add_argument("--deterministic", action="store_true")
parser.add_argument("--config")
parser.add_argument("--task-profile")
parser.add_argument("--allow-path-merge-with-manifest", action="store_true")
parser.add_argument("--no-redaction", action="store_true")
args = parser.parse_args()

if args.output:
    out_path = pathlib.Path(args.output)
else:
    base = pathlib.Path(args.base_dir)
    out_path = base / f"{base.name}_bundle.json"

out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump({
        "layers": {"layer_1_summary": [], "layer_2_intelligence": [], "layer_3_full_files": []},
        "manifest_path": args.manifest or "",
        "format": args.format,
    }, fh)

sys.exit(0)
