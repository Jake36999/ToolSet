"""Manifest Doctor: validate and score create_file_map_v2 manifest CSVs."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from aletheia_tool_core.manifest import (
    DEFAULT_SUSPICIOUS_DIRECTORIES,
    analyze_manifest_rows,
    load_manifest_csv,
)
from aletheia_tool_core.reports import write_json_report, write_markdown_report


def build_markdown_sections(report: Dict[str, Any]) -> Dict[str, Any]:
    summary = report["summary"]
    details = report["details"]

    sections: Dict[str, Any] = {
        "Manifest Summary": {
            "row_count": summary["row_count"],
            "root_count": summary["root_count"],
            "missing_files": summary["missing_files"],
            "duplicate_abs_paths": summary["duplicate_abs_paths"],
            "duplicate_rel_paths": summary["duplicate_rel_paths"],
            "suspicious_paths": summary["suspicious_paths"],
        }
    }

    if details["missing_files"]:
        sections["Missing files"] = details["missing_files"]
    if details["duplicate_abs_paths"]:
        sections["Duplicate absolute paths"] = details["duplicate_abs_paths"]
    if details["duplicate_rel_paths"]:
        sections["Duplicate relative paths"] = details["duplicate_rel_paths"]
    if details["suspicious_paths"]:
        sections["Suspicious paths"] = details["suspicious_paths"]

    if report["status"] == "PASS":
        sections["Recommendation"] = "Manifest looks healthy. Proceed with downstream tooling."
    elif report["status"] == "WARN":
        sections["Recommendation"] = "Review suspicious paths before slicing."
    else:
        sections["Recommendation"] = "Fix missing or duplicate paths before using this manifest."

    return sections


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manifest Doctor for create_file_map_v2 CSV manifests")
    parser.add_argument("--manifest", required=True, help="Path to the manifest CSV file")
    parser.add_argument("--out", "-o", default="manifest_doctor_report.json", help="JSON report output path")
    parser.add_argument("--markdown", help="Optional Markdown report output path")
    parser.add_argument(
        "--suspicious-dirs",
        default=",".join(DEFAULT_SUSPICIOUS_DIRECTORIES),
        help="Comma-separated list of suspicious directories to detect",
    )
    return parser.parse_args()


def load_and_validate_manifest(path: Path) -> List[Dict[str, str]]:
    return load_manifest_csv(path)


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    try:
        rows = load_and_validate_manifest(manifest_path)
    except Exception as exc:
        print(f"Error reading manifest: {exc}", file=sys.stderr)
        return 1

    suspicious_dirs = [part.strip() for part in args.suspicious_dirs.split(",") if part.strip()]
    report = analyze_manifest_rows(rows, suspicious_dirs)
    report["manifest_path"] = str(manifest_path.resolve())
    report["suspicious_directories"] = suspicious_dirs

    write_json_report(report, Path(args.out))
    if args.markdown:
        sections = build_markdown_sections(report)
        write_markdown_report("Manifest Doctor Report", sections, Path(args.markdown))

    print(f"Manifest Doctor status: {report['status']}")
    return 0 if report["status"] != "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
