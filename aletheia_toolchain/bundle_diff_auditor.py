#!/usr/bin/env python3
"""bundle_diff_auditor.py — Phase 10.

Slicer bundle staleness auditor.

Compares an existing slicer bundle against the current file manifest
to detect files not covered by the bundle (INCOMPLETE) or, when a
second bundle is provided, fingerprint drift between bundle versions
(STALE).

Inputs:
  --old-bundle PATH          Existing slicer JSON bundle (required).
  --current-manifest PATH    Current file manifest CSV (required).
  --new-bundle PATH          Optional updated slicer bundle for fingerprint
                             comparison.
  --out PATH                 Output JSON report (required).
  --markdown-out PATH        Optional Markdown summary.

Output status:
  CURRENT     — Bundle covers all manifest files; no fingerprint drift.
  STALE       — All manifest files covered, but fingerprints differ in the
                new bundle (file contents changed since old bundle was made).
  INCOMPLETE  — Manifest contains files not present in the bundle; re-slicing
                required.

Status priority: INCOMPLETE > STALE > CURRENT.

Fingerprint fields compared (from layer_3_full_files / layer_2_intelligence):
  sha1, mtime_iso, size_bytes.

Exit codes:
  0  — Analysis complete (any status).
  1  — Invocation error (missing required files, unreadable inputs).
"""

import argparse
import csv
import datetime
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Set

try:
    from aletheia_tool_core.reports import write_json_report, write_markdown_report
    _CORE_AVAILABLE = True
except ImportError:
    _CORE_AVAILABLE = False

AUDITOR_VERSION: str = "v10.0"
_ENCODING: str = "utf-8"

# CSV columns tried in order when looking for the file path.
_PATH_COLUMNS = ("rel_path", "path", "abs_path")


def _extract_bundle_files(
    bundle_data: Dict[str, Any],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Return {path: fingerprint_or_None} from a slicer bundle.

    Prefers layer_3_full_files; supplements with layer_2_intelligence for
    any paths not already found.
    """
    files: Dict[str, Optional[Dict[str, Any]]] = {}

    for entry in bundle_data.get("layer_3_full_files") or []:
        if isinstance(entry, dict):
            p = entry.get("path", "")
            if p:
                files[p] = entry.get("fingerprint") or None

    for entry in bundle_data.get("layer_2_intelligence") or []:
        if isinstance(entry, dict):
            p = entry.get("path", "")
            if p and p not in files:
                files[p] = entry.get("fingerprint") or None

    return files


def _extract_manifest_paths(manifest_path: pathlib.Path) -> Set[str]:
    """Return the set of file paths listed in the manifest CSV."""
    paths: Set[str] = set()
    try:
        with manifest_path.open(encoding=_ENCODING, newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                return paths
            # Pick whichever path column exists first.
            col: Optional[str] = None
            for candidate in _PATH_COLUMNS:
                if candidate in reader.fieldnames:
                    col = candidate
                    break
            if col is None:
                # Fall back to first column.
                col = reader.fieldnames[0]
            for row in reader:
                p = (row.get(col) or "").strip()
                if p:
                    paths.add(p)
    except Exception:
        pass
    return paths


def _fingerprint_changed(
    old_fp: Optional[Dict[str, Any]],
    new_fp: Optional[Dict[str, Any]],
) -> bool:
    """Return True if the two fingerprints differ in any available field."""
    if old_fp is None or new_fp is None:
        return False
    for field in ("sha1", "mtime_iso", "size_bytes"):
        old_val = old_fp.get(field)
        new_val = new_fp.get(field)
        if old_val is not None and new_val is not None and old_val != new_val:
            return True
    return False


def _audit(
    old_bundle_path: pathlib.Path,
    manifest_path: pathlib.Path,
    new_bundle_path: Optional[pathlib.Path],
) -> Dict[str, Any]:
    with old_bundle_path.open(encoding=_ENCODING) as fh:
        old_bundle_data = json.load(fh)

    bundle_schema_version: Optional[str] = (
        (old_bundle_data.get("meta") or {}).get("bundle_schema_version")
    )
    old_files = _extract_bundle_files(old_bundle_data)
    manifest_paths = _extract_manifest_paths(manifest_path)

    bundle_path_set = set(old_files.keys())

    new_files: List[str] = sorted(manifest_paths - bundle_path_set)
    missing_files: List[str] = sorted(bundle_path_set - manifest_paths)

    changed_files: List[str] = []
    fingerprint_mismatches: List[Dict[str, Any]] = []
    new_bundle_loaded = False

    if new_bundle_path is not None and new_bundle_path.exists():
        with new_bundle_path.open(encoding=_ENCODING) as fh:
            new_bundle_data = json.load(fh)
        new_files_dict = _extract_bundle_files(new_bundle_data)
        new_bundle_loaded = True

        for path, old_fp in old_files.items():
            new_fp = new_files_dict.get(path)
            if new_fp is None:
                continue
            if _fingerprint_changed(old_fp, new_fp):
                changed_files.append(path)
                fingerprint_mismatches.append({
                    "file": path,
                    "old_fingerprint": old_fp,
                    "new_fingerprint": new_fp,
                })

        changed_files.sort()
        fingerprint_mismatches.sort(key=lambda x: x["file"])

    if new_files:
        status = "INCOMPLETE"
    elif fingerprint_mismatches:
        status = "STALE"
    else:
        status = "CURRENT"

    if status == "INCOMPLETE":
        action = (
            f"INCOMPLETE: {len(new_files)} file(s) in manifest not covered by bundle. "
            "Re-run semantic_slicer_v7.0.py to update the bundle."
        )
    elif status == "STALE":
        action = (
            f"STALE: {len(fingerprint_mismatches)} file(s) have changed fingerprints. "
            "Re-run semantic_slicer_v7.0.py to refresh the bundle."
        )
    else:
        if new_bundle_loaded:
            action = "CURRENT: Bundle covers all manifest files and fingerprints match."
        else:
            action = (
                "CURRENT: Bundle covers all manifest files. "
                "Provide --new-bundle to enable fingerprint comparison."
            )

    return {
        "tool_version": AUDITOR_VERSION,
        "audited_at": datetime.datetime.now().isoformat(),
        "old_bundle": str(old_bundle_path),
        "current_manifest": str(manifest_path),
        "new_bundle": str(new_bundle_path) if new_bundle_path is not None else None,
        "bundle_schema_version": bundle_schema_version,
        "status": status,
        "files_checked": len(old_files),
        "changed_files": changed_files,
        "new_files": new_files,
        "missing_files": missing_files,
        "fingerprint_mismatches": fingerprint_mismatches,
        "recommended_next_action": action,
    }


def _write_reports(
    result: Dict[str, Any],
    out_path: pathlib.Path,
    markdown_out: Optional[pathlib.Path],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _CORE_AVAILABLE:
        write_json_report(result, out_path)  # type: ignore[name-defined]
    else:
        with out_path.open("w", encoding=_ENCODING) as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    if markdown_out is None:
        return

    overview = {
        "Status":              result["status"],
        "Audited at":          result.get("audited_at", ""),
        "Files checked":       str(result.get("files_checked", 0)),
        "New files":           str(len(result.get("new_files", []))),
        "Missing files":       str(len(result.get("missing_files", []))),
        "Changed files":       str(len(result.get("changed_files", []))),
        "Bundle schema":       str(result.get("bundle_schema_version") or "N/A"),
    }
    fp_lines = [
        f"{m['file']}: {m['old_fingerprint']} → {m['new_fingerprint']}"
        for m in result.get("fingerprint_mismatches", [])
    ] or ["(none)"]

    sections: Dict[str, Any] = {
        "Overview": overview,
        "New Files (in manifest, not in bundle)": result.get("new_files") or ["(none)"],
        "Missing Files (in bundle, not in manifest)": result.get("missing_files") or ["(none)"],
        "Fingerprint Mismatches": fp_lines,
        "Recommended Next Action": result.get("recommended_next_action", ""),
    }
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    if _CORE_AVAILABLE:
        write_markdown_report(  # type: ignore[name-defined]
            "Bundle Diff Auditor Report", sections, markdown_out,
        )
    else:
        lines = ["# Bundle Diff Auditor Report", ""]
        for sec, body in sections.items():
            lines.append(f"## {sec}")
            lines.append("")
            if isinstance(body, dict):
                for k, v in body.items():
                    lines.append(f"- **{k}**: {v}")
            elif isinstance(body, list):
                for item in body:
                    lines.append(f"- {item}")
            else:
                lines.append(str(body))
            lines.append("")
        with markdown_out.open("w", encoding=_ENCODING) as fh:
            fh.write("\n".join(lines).rstrip() + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bundle_diff_auditor",
        description=(
            "Slicer bundle staleness auditor (Phase 10). "
            "Detects INCOMPLETE coverage and STALE fingerprints by comparing "
            "a slicer bundle against the current file manifest."
        ),
    )
    parser.add_argument(
        "--old-bundle", required=True, dest="old_bundle", metavar="JSON",
        help="Existing slicer JSON bundle to audit.",
    )
    parser.add_argument(
        "--current-manifest", required=True, dest="current_manifest", metavar="CSV",
        help="Current file manifest CSV (from create_file_map_v3.py).",
    )
    parser.add_argument(
        "--new-bundle", default=None, dest="new_bundle", metavar="JSON",
        help="Optional updated slicer bundle for fingerprint comparison.",
    )
    parser.add_argument(
        "--out", required=True, metavar="JSON",
        help="Output path for the JSON audit report.",
    )
    parser.add_argument(
        "--markdown-out", default=None, dest="markdown_out", metavar="MD",
        help="Optional output path for a Markdown summary.",
    )

    args = parser.parse_args()

    old_bundle_path = pathlib.Path(args.old_bundle)
    manifest_path = pathlib.Path(args.current_manifest)

    if not old_bundle_path.exists():
        print(f"ERROR: --old-bundle not found: '{old_bundle_path}'", file=sys.stderr)
        sys.exit(1)
    if not manifest_path.exists():
        print(f"ERROR: --current-manifest not found: '{manifest_path}'", file=sys.stderr)
        sys.exit(1)

    new_bundle_path = pathlib.Path(args.new_bundle) if args.new_bundle else None
    if new_bundle_path is not None and not new_bundle_path.exists():
        print(f"ERROR: --new-bundle not found: '{new_bundle_path}'", file=sys.stderr)
        sys.exit(1)

    try:
        result = _audit(old_bundle_path, manifest_path, new_bundle_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    _write_reports(
        result,
        out_path=pathlib.Path(args.out),
        markdown_out=pathlib.Path(args.markdown_out) if args.markdown_out else None,
    )


if __name__ == "__main__":
    main()
