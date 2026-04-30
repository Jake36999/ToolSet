"""Manifest Doctor: validate create_file_map_v3 manifest CSVs."""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

from aletheia_tool_core.config import ConfigError, load_json_config
from aletheia_tool_core.manifest import DEFAULT_SUSPICIOUS_DIRECTORIES, load_manifest_csv
from aletheia_tool_core.reports import write_json_report, write_markdown_report

# Substrings in rel_path that identify generated bundle artifacts.
_BUNDLE_ARTIFACT_SUBSTRINGS = ["_bundle_"]


def _is_suspicious(path: str, suspicious_dirs: List[str]) -> bool:
    norm = path.replace("\\", "/").lower()
    return any(
        f"/{d.lower()}/" in norm or norm.endswith(f"/{d.lower()}")
        for d in suspicious_dirs
    )


def _is_bundle_artifact(rel_path: str) -> bool:
    lower = rel_path.lower()
    return any(pat in lower for pat in _BUNDLE_ARTIFACT_SUBSTRINGS)


def _parse_csv_list(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def run_checks(
    rows: List[Dict[str, str]],
    suspicious_dirs: List[str],
    required_paths: List[str],
    required_exts: List[str],
    max_rows_soft: int,
    max_rows_hard: int,
    max_file_size: int,
) -> Dict[str, Any]:
    missing_files: List[str] = []
    suspicious_paths: List[str] = []
    bundle_artifacts: List[str] = []
    oversize_files: List[str] = []

    present_exts: set = set()
    present_rel_paths_lower: List[str] = []

    for row in rows:
        abs_path = row.get("abs_path", "").strip()
        rel_path = row.get("rel_path", "").strip()
        ext = row.get("ext", "").strip().lower()

        if rel_path:
            present_rel_paths_lower.append(rel_path.lower())
        if ext:
            present_exts.add(ext)

        if abs_path:
            if not Path(abs_path).exists():
                missing_files.append(abs_path)
            if _is_suspicious(abs_path, suspicious_dirs):
                suspicious_paths.append(abs_path)

        if rel_path and _is_bundle_artifact(rel_path):
            bundle_artifacts.append(rel_path)

        if max_file_size > 0:
            try:
                if int(row.get("size", 0)) > max_file_size:
                    oversize_files.append(rel_path or abs_path)
            except (ValueError, TypeError):
                pass

    missing_required_paths: List[str] = []
    for rp in required_paths:
        rp_lower = rp.lower()
        if not any(rp_lower in p for p in present_rel_paths_lower):
            missing_required_paths.append(rp)

    missing_required_exts: List[str] = []
    for ext in required_exts:
        normalized = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        if normalized not in present_exts:
            missing_required_exts.append(normalized)

    row_count = len(rows)
    return {
        "row_count": row_count,
        "missing_files": missing_files,
        "suspicious_paths": suspicious_paths,
        "bundle_artifacts": bundle_artifacts,
        "oversize_files": oversize_files,
        "missing_required_paths": missing_required_paths,
        "missing_required_exts": missing_required_exts,
        "rows_exceeded_soft": max_rows_soft > 0 and row_count > max_rows_soft,
        "rows_exceeded_hard": max_rows_hard > 0 and row_count > max_rows_hard,
        "max_rows_soft": max_rows_soft,
        "max_rows_hard": max_rows_hard,
        "max_file_size": max_file_size,
    }


def determine_status(findings: Dict[str, Any]) -> str:
    if (
        findings["missing_files"]
        or findings["missing_required_paths"]
        or findings["missing_required_exts"]
        or findings["rows_exceeded_hard"]
    ):
        return "BLOCK"
    if (
        findings["suspicious_paths"]
        or findings["bundle_artifacts"]
        or findings["oversize_files"]
        or findings["rows_exceeded_soft"]
    ):
        return "WARN"
    return "PASS"


def build_report(
    findings: Dict[str, Any],
    manifest_path: Path,
    status: str,
    suspicious_dirs: List[str],
) -> Dict[str, Any]:
    # Identify which suspicious dir names were actually encountered.
    flagged_dir_names: set = set()
    for p in findings["suspicious_paths"]:
        norm = p.replace("\\", "/").lower()
        for d in suspicious_dirs:
            if f"/{d.lower()}/" in norm or norm.endswith(f"/{d.lower()}"):
                flagged_dir_names.add(d)

    if status == "BLOCK":
        recommended_action = "Fix blocking issues before using this manifest with downstream tools."
    elif status == "WARN":
        recommended_action = "Review warnings. Consider adding flagged directories to the exclude list."
    else:
        recommended_action = "Manifest is healthy. Proceed with downstream tooling."

    return {
        "status": status,
        "manifest_path": str(manifest_path.resolve()),
        "summary": {
            "row_count": findings["row_count"],
            "missing_files": len(findings["missing_files"]),
            "suspicious_paths": len(findings["suspicious_paths"]),
            "bundle_artifacts": len(findings["bundle_artifacts"]),
            "oversize_files": len(findings["oversize_files"]),
            "missing_required_paths": len(findings["missing_required_paths"]),
            "missing_required_exts": len(findings["missing_required_exts"]),
            "rows_exceeded_soft": findings["rows_exceeded_soft"],
            "rows_exceeded_hard": findings["rows_exceeded_hard"],
        },
        "findings": {
            "missing_files": findings["missing_files"],
            "suspicious_paths": findings["suspicious_paths"],
            "bundle_artifacts": findings["bundle_artifacts"],
            "oversize_files": findings["oversize_files"],
            "missing_required_paths": findings["missing_required_paths"],
            "missing_required_exts": findings["missing_required_exts"],
        },
        "thresholds": {
            "max_rows_soft": findings["max_rows_soft"],
            "max_rows_hard": findings["max_rows_hard"],
            "max_file_size": findings["max_file_size"],
        },
        "recommended_action": recommended_action,
        "recommended_exclude_additions": sorted(flagged_dir_names),
    }


def _block_report_for_schema_error(
    manifest_path: Path,
    error_message: str,
    max_rows_soft: int,
    max_rows_hard: int,
    max_file_size: int,
) -> Dict[str, Any]:
    empty_findings: Dict[str, Any] = {
        "missing_files": [],
        "suspicious_paths": [],
        "bundle_artifacts": [],
        "oversize_files": [],
        "missing_required_paths": [],
        "missing_required_exts": [],
    }
    return {
        "status": "BLOCK",
        "manifest_path": str(manifest_path.resolve()),
        "error": error_message,
        "summary": {
            "row_count": 0,
            "missing_files": 0,
            "suspicious_paths": 0,
            "bundle_artifacts": 0,
            "oversize_files": 0,
            "missing_required_paths": 0,
            "missing_required_exts": 0,
            "rows_exceeded_soft": False,
            "rows_exceeded_hard": False,
        },
        "findings": empty_findings,
        "thresholds": {
            "max_rows_soft": max_rows_soft,
            "max_rows_hard": max_rows_hard,
            "max_file_size": max_file_size,
        },
        "recommended_action": "Fix manifest column schema before using this manifest.",
        "recommended_exclude_additions": [],
    }


def build_markdown_sections(report: Dict[str, Any]) -> Dict[str, Any]:
    summary = report["summary"]
    findings = report["findings"]

    sections: Dict[str, Any] = {
        "Summary": {
            "status": report["status"],
            "row_count": summary["row_count"],
            "missing_files": summary["missing_files"],
            "suspicious_paths": summary["suspicious_paths"],
            "bundle_artifacts": summary["bundle_artifacts"],
            "oversize_files": summary["oversize_files"],
            "missing_required_paths": summary["missing_required_paths"],
            "missing_required_exts": summary["missing_required_exts"],
        }
    }

    if findings["missing_files"]:
        sections["Missing Files"] = findings["missing_files"]
    if findings["suspicious_paths"]:
        sections["Suspicious Paths"] = findings["suspicious_paths"]
    if findings["bundle_artifacts"]:
        sections["Bundle Artifacts"] = findings["bundle_artifacts"]
    if findings["oversize_files"]:
        sections["Oversize Files"] = findings["oversize_files"]
    if findings["missing_required_paths"]:
        sections["Missing Required Paths"] = findings["missing_required_paths"]
    if findings["missing_required_exts"]:
        sections["Missing Required Extensions"] = findings["missing_required_exts"]
    if report.get("recommended_exclude_additions"):
        sections["Recommended Exclude Additions"] = report["recommended_exclude_additions"]

    sections["Recommendation"] = report["recommended_action"]
    return sections


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a create_file_map_v3 manifest CSV.")
    parser.add_argument("--manifest", required=True, help="Path to manifest CSV.")
    parser.add_argument("--config", help="Optional JSON config file supplying default thresholds.")
    parser.add_argument(
        "--required-path", action="append", default=[], dest="required_paths",
        metavar="PATH",
        help="Require a row whose rel_path contains PATH. Repeatable.",
    )
    parser.add_argument(
        "--required-ext", action="append", default=[], dest="required_exts",
        metavar="EXT",
        help="Require at least one row with this extension (e.g. .py). Repeatable.",
    )
    parser.add_argument(
        "--max-rows-soft", type=int, default=0,
        help="Row count above which status becomes WARN. 0 = no limit.",
    )
    parser.add_argument(
        "--max-rows-hard", type=int, default=0,
        help="Row count above which status becomes BLOCK. 0 = no limit.",
    )
    parser.add_argument(
        "--max-file-size", type=int, default=0,
        help="Files larger than this (bytes) are flagged as oversize. 0 = no limit.",
    )
    parser.add_argument("--out", default="manifest_doctor_report.json", help="JSON report output path.")
    parser.add_argument("--markdown-out", help="Optional Markdown report output path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)

    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    # Load optional config; CLI args take precedence over config values.
    cfg: Dict = {}
    if args.config:
        try:
            cfg = load_json_config(Path(args.config))
        except (FileNotFoundError, ConfigError) as exc:
            print(f"Error loading config: {exc}", file=sys.stderr)
            return 1

    suspicious_dirs = list(DEFAULT_SUSPICIOUS_DIRECTORIES) + _parse_csv_list(
        cfg.get("suspicious_dirs", "")
    )
    required_paths: list = args.required_paths or cfg.get("required_paths", [])
    required_exts: list = args.required_exts or cfg.get("required_exts", [])
    max_rows_soft: int = args.max_rows_soft or cfg.get("max_rows_soft", 0)
    max_rows_hard: int = args.max_rows_hard or cfg.get("max_rows_hard", 0)
    max_file_size: int = args.max_file_size or cfg.get("max_file_size", 0)

    try:
        rows = load_manifest_csv(manifest_path)
    except ValueError as exc:
        report = _block_report_for_schema_error(
            manifest_path, str(exc), max_rows_soft, max_rows_hard, max_file_size
        )
        write_json_report(report, Path(args.out))
        print("Manifest Doctor status: BLOCK")
        return 2
    except Exception as exc:
        print(f"Error reading manifest: {exc}", file=sys.stderr)
        return 1

    findings = run_checks(
        rows, suspicious_dirs, required_paths, required_exts,
        max_rows_soft, max_rows_hard, max_file_size,
    )
    status = determine_status(findings)
    report = build_report(findings, manifest_path, status, suspicious_dirs)

    write_json_report(report, Path(args.out))

    if args.markdown_out:
        sections = build_markdown_sections(report)
        write_markdown_report("Manifest Doctor Report", sections, Path(args.markdown_out))

    print(f"Manifest Doctor status: {status}")
    return 2 if status == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
