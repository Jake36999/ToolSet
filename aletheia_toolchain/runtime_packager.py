#!/usr/bin/env python3
"""runtime_packager.py — Phase 9.

Redacted runtime evidence packager.

Assembles a shareable, redacted evidence bundle from a Phase 8 output
directory.  Applies sensitive-content redaction to all text tails before
packaging.  Full logs are never included by default.

Inputs:
  --runtime-dir DIR      Phase 8 output directory (required).
  --config PATH          Optional project config JSON (informational only).
  --out PATH             Output JSON bundle (required).
  --markdown-out PATH    Optional Markdown summary.

Output bundle keys:
  tool_version, packaged_at, runtime_dir, metrics_summary,
  timeline_summary, stdout_tail_redacted, stderr_tail_redacted,
  file_inventory, redaction_applied, missing_artefacts.

Exit codes:
  0  — Packaging complete.
  1  — Invocation error (runtime-dir not found, unreadable metrics).
"""

import argparse
import csv
import datetime
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Optional aletheia_tool_core helpers
# ---------------------------------------------------------------------------

try:
    from aletheia_tool_core.reports import write_json_report, write_markdown_report
    from aletheia_tool_core.security import sanitize_content
    _CORE_AVAILABLE = True
except ImportError:
    _CORE_AVAILABLE = False

    def sanitize_content(text: str) -> str:  # type: ignore[misc]
        return text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKAGER_VERSION: str = "v9.0"
_ENCODING: str = "utf-8"

# All artefacts that runtime_end_watcher.py may produce.
_KNOWN_ARTEFACTS = [
    "runtime_metrics.json",
    "timeline.csv",
    "stdout_tail.txt",
    "stderr_tail.txt",
    "runtime_summary.md",
    "stdout.log",
    "stderr.log",
]

# Keys copied verbatim from runtime_metrics.json into the summary.
_METRICS_SUMMARY_KEYS = [
    "run_name",
    "exit_code",
    "timed_out",
    "start_failed",
    "duration_s",
    "start_iso",
    "end_iso",
    "metrics_mode",
    "watcher_version",
]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _inventory_artefacts(runtime_dir: pathlib.Path) -> tuple:
    """Return (file_inventory list, missing_artefacts list)."""
    inventory: List[Dict[str, Any]] = []
    missing: List[str] = []
    for fname in _KNOWN_ARTEFACTS:
        p = runtime_dir / fname
        present = p.exists()
        entry: Dict[str, Any] = {"name": fname, "present": present, "size_bytes": 0}
        if present:
            entry["size_bytes"] = p.stat().st_size
        else:
            missing.append(fname)
        inventory.append(entry)
    return inventory, missing


def _load_metrics_summary(runtime_dir: pathlib.Path) -> Dict[str, Any]:
    """Return a subset of runtime_metrics.json suitable for the package header."""
    path = runtime_dir / "runtime_metrics.json"
    if not path.exists():
        return {}
    with path.open(encoding=_ENCODING) as fh:
        metrics = json.load(fh)
    return {k: metrics.get(k) for k in _METRICS_SUMMARY_KEYS}


def _load_timeline_summary(runtime_dir: pathlib.Path) -> Dict[str, Any]:
    """Summarise timeline.csv without buffering all rows."""
    summary: Dict[str, Any] = {
        "row_count": 0,
        "columns": [],
        "first_timestamp": None,
        "last_timestamp": None,
    }
    path = runtime_dir / "timeline.csv"
    if not path.exists():
        return summary
    rows: List[Dict[str, str]] = []
    try:
        with path.open(encoding=_ENCODING) as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            if reader.fieldnames:
                summary["columns"] = list(reader.fieldnames)
    except Exception:
        return summary
    summary["row_count"] = len(rows)
    if rows:
        summary["first_timestamp"] = rows[0].get("timestamp", "")
        summary["last_timestamp"] = rows[-1].get("timestamp", "")
    return summary


def _load_redacted_tail(runtime_dir: pathlib.Path, fname: str) -> List[str]:
    """Read a tail file, apply redaction, return as a list of lines."""
    path = runtime_dir / fname
    if not path.exists():
        return []
    text = path.read_text(encoding=_ENCODING, errors="replace")
    text = sanitize_content(text)
    return text.splitlines()


# ---------------------------------------------------------------------------
# Core packer
# ---------------------------------------------------------------------------

def _pack(
    runtime_dir: pathlib.Path,
    config_path: Optional[pathlib.Path],
) -> Dict[str, Any]:
    file_inventory, missing_artefacts = _inventory_artefacts(runtime_dir)
    metrics_summary = _load_metrics_summary(runtime_dir)
    timeline_summary = _load_timeline_summary(runtime_dir)
    stdout_tail = _load_redacted_tail(runtime_dir, "stdout_tail.txt")
    stderr_tail = _load_redacted_tail(runtime_dir, "stderr_tail.txt")

    config_note: Optional[str] = None
    if config_path is not None:
        if config_path.exists():
            config_note = str(config_path)
        else:
            config_note = f"(not found: {config_path})"

    bundle: Dict[str, Any] = {
        "tool_version": PACKAGER_VERSION,
        "packaged_at": datetime.datetime.now().isoformat(),
        "runtime_dir": str(runtime_dir),
        "metrics_summary": metrics_summary,
        "timeline_summary": timeline_summary,
        "stdout_tail_redacted": stdout_tail,
        "stderr_tail_redacted": stderr_tail,
        "file_inventory": file_inventory,
        "redaction_applied": _CORE_AVAILABLE,
        "missing_artefacts": missing_artefacts,
    }
    if config_note is not None:
        bundle["config_source"] = config_note

    return bundle


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------

def _write_reports(
    bundle: Dict[str, Any],
    out_path: pathlib.Path,
    markdown_out: Optional[pathlib.Path],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _CORE_AVAILABLE:
        write_json_report(bundle, out_path)  # type: ignore[name-defined]
    else:
        with out_path.open("w", encoding=_ENCODING) as fh:
            json.dump(bundle, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    if markdown_out is None:
        return

    ms = bundle.get("metrics_summary") or {}
    ts = bundle.get("timeline_summary") or {}
    overview = {
        "Run name":       str(ms.get("run_name", "")),
        "Exit code":      str(ms.get("exit_code")),
        "Timed out":      str(ms.get("timed_out")),
        "Duration (s)":   str(ms.get("duration_s")),
        "Packaged at":    bundle.get("packaged_at", ""),
        "Redacted":       str(bundle.get("redaction_applied", False)),
    }
    timeline_info = {
        "Sample rows":       str(ts.get("row_count", 0)),
        "First sample":      str(ts.get("first_timestamp") or "N/A"),
        "Last sample":       str(ts.get("last_timestamp") or "N/A"),
    }
    missing = bundle.get("missing_artefacts") or []
    inventory_lines = [
        f"{e['name']} — {'present' if e['present'] else 'MISSING'} "
        f"({e.get('size_bytes', 0)} bytes)"
        for e in bundle.get("file_inventory", [])
    ]
    sections: Dict[str, Any] = {
        "Overview": overview,
        "Timeline Summary": timeline_info,
        "File Inventory": inventory_lines,
        "Missing Artefacts": missing or ["(none)"],
    }
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    if _CORE_AVAILABLE:
        write_markdown_report(  # type: ignore[name-defined]
            "Runtime Evidence Package", sections, markdown_out,
        )
    else:
        lines = ["# Runtime Evidence Package", ""]
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="runtime_packager",
        description=(
            "Redacted runtime evidence packager (Phase 9). "
            "Assembles a shareable, redacted evidence bundle from a "
            "Phase 8 output directory. Full logs are never included."
        ),
    )
    parser.add_argument(
        "--runtime-dir",
        required=True,
        dest="runtime_dir",
        metavar="DIR",
        help="Phase 8 output directory to package.",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="JSON",
        help="Optional project config JSON (informational only).",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="JSON",
        help="Output path for the packaged JSON bundle.",
    )
    parser.add_argument(
        "--markdown-out",
        default=None,
        dest="markdown_out",
        metavar="MD",
        help="Optional output path for a Markdown summary.",
    )

    args = parser.parse_args()

    runtime_dir = pathlib.Path(args.runtime_dir)
    if not runtime_dir.exists():
        print(
            f"ERROR: --runtime-dir not found: '{runtime_dir}'",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        bundle = _pack(
            runtime_dir,
            config_path=pathlib.Path(args.config) if args.config else None,
        )
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    _write_reports(
        bundle,
        out_path=pathlib.Path(args.out),
        markdown_out=pathlib.Path(args.markdown_out) if args.markdown_out else None,
    )


if __name__ == "__main__":
    main()
