#!/usr/bin/env python3
"""oom_forensics_reporter.py — Phase 9.

Post-run memory/OOM forensics reporter.

Reads runtime artefacts produced by runtime_end_watcher.py and applies a set
of ranked heuristics to surface likely memory/OOM causes with explicit
confidence levels and uncertainty notes.

Inputs:
  --runtime-report DIR   Phase 8 output directory (required).
  --manifest PATH        File-map CSV — optional, used for oversized-file hints.
  --bundle PATH          Slicer JSON bundle — optional, used for import hints.
  --config PATH          Project config JSON — optional.
  --out PATH             Output JSON report (required).
  --markdown-out PATH    Optional Markdown report.

Exit codes:
  0  — Analysis complete (regardless of risk level found).
  1  — Invocation error (missing required inputs, unreadable files).

NOTE: This tool never claims root-cause certainty.  All findings include
      confidence levels and uncertainty_notes explaining the limits of the
      available evidence.
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

REPORTER_VERSION: str = "v9.0"
_ENCODING: str = "utf-8"

# Exit codes that indicate SIGKILL (OS OOM killer or forcible termination).
_SIGKILL_CODES = frozenset({137, -9})
# Exit codes that indicate SIGSEGV (memory corruption / illegal access).
_SIGSEGV_CODES = frozenset({139, -11})

# Patterns whose presence in logs indicates OOM with HIGH confidence.
_HIGH_CONF_LOG_PATTERNS = [
    ("MemoryError",              "OOM-010"),
    ("Cannot allocate memory",   "OOM-011"),
    ("std::bad_alloc",           "OOM-012"),
    ("out of memory",            "OOM-013"),
]

# Patterns consistent with memory issues but with alternative explanations.
_MED_CONF_LOG_PATTERNS = [
    ("Killed",               "OOM-020"),
    ("MemoryWarning",        "OOM-021"),
    ("Segmentation fault",   "OOM-022"),
    ("core dumped",          "OOM-023"),
    ("ResourceWarning",      "OOM-024"),
]


# ---------------------------------------------------------------------------
# Evidence loaders
# ---------------------------------------------------------------------------

def _load_runtime_dir(runtime_dir: pathlib.Path) -> Dict[str, Any]:
    """Load available artefacts from a Phase 8 output directory.

    Raises FileNotFoundError if runtime_metrics.json is absent (required).
    All other artefacts degrade gracefully to empty/default values.
    """
    artefacts: Dict[str, Any] = {}

    metrics_path = runtime_dir / "runtime_metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError(
            f"runtime_metrics.json not found in '{runtime_dir}'. "
            "Point --runtime-report at the directory written by runtime_end_watcher.py."
        )
    with metrics_path.open(encoding=_ENCODING) as fh:
        artefacts["metrics"] = json.load(fh)

    for fname in ("stdout_tail.txt", "stderr_tail.txt"):
        path = runtime_dir / fname
        key = fname.replace(".txt", "").replace("_tail", "_tail")
        artefacts[key] = (
            path.read_text(encoding=_ENCODING, errors="replace") if path.exists() else ""
        )

    artefacts["timeline_rows"] = []
    csv_path = runtime_dir / "timeline.csv"
    if csv_path.exists():
        try:
            with csv_path.open(encoding=_ENCODING) as fh:
                artefacts["timeline_rows"] = list(csv.DictReader(fh))
        except Exception:
            pass

    return artefacts


# ---------------------------------------------------------------------------
# Heuristic checks
# ---------------------------------------------------------------------------

def _check_exit_code(
    exit_code: Optional[int],
    timed_out: bool,
    start_failed: bool,
) -> List[Dict[str, Any]]:
    """Heuristic 1: interpret the process exit code."""
    findings: List[Dict[str, Any]] = []

    if start_failed or exit_code is None:
        return findings

    if exit_code in _SIGKILL_CODES:
        findings.append({
            "id": "OOM-001",
            "severity": "WARN",
            "message": (
                f"Process received SIGKILL (exit code {exit_code}). "
                "This is the signature of the OS OOM killer."
            ),
            "confidence": "HIGH",
            "evidence": f"exit_code={exit_code}",
            "uncertainty_notes": (
                "SIGKILL is also sent by test harnesses, container runtimes, or manual "
                "kill commands. Kernel OOM-killer logs (dmesg | grep -i oom) can confirm "
                "whether this was an OS-initiated kill."
            ),
            "recommendation": (
                "Re-run with --python-tracemalloc --python-faultevidence to capture "
                "allocation traces. If in a container, inspect cgroup memory limits."
            ),
        })
    elif exit_code in _SIGSEGV_CODES:
        findings.append({
            "id": "OOM-002",
            "severity": "WARN",
            "message": (
                f"Process received SIGSEGV (exit code {exit_code}). "
                "May indicate memory corruption, buffer overflow, or stack exhaustion."
            ),
            "confidence": "MEDIUM",
            "evidence": f"exit_code={exit_code}",
            "uncertainty_notes": (
                "SIGSEGV does not always indicate OOM — C extension bugs, stack overflow, "
                "and use-after-free errors produce the same signal. "
                "Python faulthandler output (if available) can disambiguate."
            ),
            "recommendation": (
                "Enable PYTHONFAULTHANDLER=1 via --python-faultevidence. "
                "Check for recursion depth issues with sys.setrecursionlimit."
            ),
        })
    elif exit_code != 0 and not timed_out:
        findings.append({
            "id": "OOM-003",
            "severity": "INFO",
            "message": (
                f"Process exited with nonzero code {exit_code}. "
                "No direct OOM signal detected from exit code alone."
            ),
            "confidence": "LOW",
            "evidence": f"exit_code={exit_code}",
            "uncertainty_notes": (
                "Nonzero exit alone is insufficient evidence for OOM. "
                "The stderr tail may contain more specific exception types."
            ),
            "recommendation": "Examine stderr_tail.txt for exception type and traceback.",
        })

    if timed_out:
        findings.append({
            "id": "OOM-004",
            "severity": "INFO",
            "message": (
                "Process was terminated by the watcher timeout. "
                "Memory pressure (swap thrashing) may be a contributing factor."
            ),
            "confidence": "MEDIUM",
            "evidence": "timed_out=true",
            "uncertainty_notes": (
                "Timeout alone cannot distinguish memory pressure from slow computation, "
                "I/O wait, or deadlock. RSS trend from timeline.csv provides additional signal."
            ),
            "recommendation": (
                "Re-run with --python-tracemalloc and a larger --timeout to capture "
                "memory state near the timeout point."
            ),
        })

    return findings


def _check_log_patterns(text: str, source: str) -> List[Dict[str, Any]]:
    """Heuristic 2: scan log text for OOM-indicative patterns."""
    findings: List[Dict[str, Any]] = []
    if not text:
        return findings
    text_lower = text.lower()

    for pattern, finding_id in _HIGH_CONF_LOG_PATTERNS:
        if pattern.lower() in text_lower:
            findings.append({
                "id": finding_id,
                "severity": "WARN",
                "message": f"OOM-indicative pattern '{pattern}' detected in {source}.",
                "confidence": "HIGH",
                "evidence": f"Pattern '{pattern}' matched in {source}",
                "uncertainty_notes": (
                    "Pattern match in logs is strong but not conclusive — "
                    "the matching line may originate from a sub-process or a caught exception "
                    "that was handled without causing a fatal exit."
                ),
                "recommendation": (
                    "Re-run with --python-tracemalloc to capture the full allocation "
                    "trace leading to this error."
                ),
            })

    for pattern, finding_id in _MED_CONF_LOG_PATTERNS:
        if pattern.lower() in text_lower:
            findings.append({
                "id": finding_id,
                "severity": "INFO",
                "message": f"Pattern '{pattern}' detected in {source}.",
                "confidence": "MEDIUM",
                "evidence": f"Pattern '{pattern}' matched in {source}",
                "uncertainty_notes": (
                    f"'{pattern}' has multiple possible causes beyond OOM "
                    "(e.g. process termination policy, I/O errors, GC warnings)."
                ),
                "recommendation": (
                    f"Investigate the surrounding context in {source} "
                    "to determine whether this is memory-related."
                ),
            })

    return findings


def _check_timeline(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Heuristic 3: look for monotonically increasing RSS in timeline samples."""
    findings: List[Dict[str, Any]] = []
    rss_values: List[int] = []

    for row in rows:
        rss_raw = row.get("rss_bytes", "")
        if rss_raw and str(rss_raw).strip().lstrip("-").isdigit():
            val = int(str(rss_raw).strip())
            if val > 0:
                rss_values.append(val)

    if len(rss_values) < 3:
        return findings

    increasing = all(rss_values[i] < rss_values[i + 1] for i in range(len(rss_values) - 1))
    if increasing:
        growth_kb = (rss_values[-1] - rss_values[0]) // 1024
        findings.append({
            "id": "OOM-030",
            "severity": "INFO",
            "message": (
                f"RSS memory increased monotonically across {len(rss_values)} samples "
                f"(+{growth_kb} KB total: {rss_values[0]//1024} KB → {rss_values[-1]//1024} KB)."
            ),
            "confidence": "LOW",
            "evidence": (
                f"RSS samples: {rss_values[:5]}"
                + ("..." if len(rss_values) > 5 else "")
            ),
            "uncertainty_notes": (
                "Monotonic RSS growth is normal for programs that pre-allocate structures. "
                "It becomes significant only if growth is unbounded relative to available RAM. "
                "Fewer than 10 samples is insufficient to establish a trend."
            ),
            "recommendation": (
                "Run with a smaller --sample-seconds to capture finer-grained RSS trends, "
                "or use tracemalloc for allocation-level detail."
            ),
        })

    return findings


# ---------------------------------------------------------------------------
# Risk aggregator and report builder
# ---------------------------------------------------------------------------

_CONF_ORDER = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}


def _compute_overall_risk(findings: List[Dict[str, Any]]) -> str:
    if not findings:
        return "NONE"
    best = max(findings, key=lambda f: _CONF_ORDER.get(f.get("confidence", "LOW"), 0))
    conf = best.get("confidence", "LOW")
    return {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(conf, "NONE")


def _build_rerun_cmd(metrics: Dict[str, Any]) -> str:
    cmd = metrics.get("cmd") or []
    cmd_str = " ".join(str(c) for c in cmd) if cmd else "<original_command>"
    return (
        f"python runtime_end_watcher.py "
        f"--python-tracemalloc --python-faultevidence "
        f"--out-dir ./forensics_rerun "
        f"--cmd {cmd_str}"
    )


def _analyze(
    runtime_dir: pathlib.Path,
    bundle_path: Optional[pathlib.Path],
    manifest_path: Optional[pathlib.Path],
    config_path: Optional[pathlib.Path],
) -> Dict[str, Any]:
    artefacts = _load_runtime_dir(runtime_dir)
    metrics = artefacts["metrics"]
    stderr_tail = artefacts.get("stderr_tail", "")
    stdout_tail = artefacts.get("stdout_tail", "")
    timeline_rows = artefacts.get("timeline_rows", [])

    findings: List[Dict[str, Any]] = []
    evidence_sources = ["runtime_metrics.json"]

    findings.extend(_check_exit_code(
        metrics.get("exit_code"),
        bool(metrics.get("timed_out")),
        bool(metrics.get("start_failed")),
    ))

    if stderr_tail:
        evidence_sources.append("stderr_tail.txt")
        findings.extend(_check_log_patterns(stderr_tail, "stderr_tail.txt"))
    if stdout_tail:
        evidence_sources.append("stdout_tail.txt")
        findings.extend(_check_log_patterns(stdout_tail, "stdout_tail.txt"))
    if timeline_rows:
        evidence_sources.append("timeline.csv")
        findings.extend(_check_timeline(timeline_rows))

    for label, path in (
        ("bundle", bundle_path),
        ("manifest", manifest_path),
        ("config", config_path),
    ):
        if path is not None:
            if path.exists():
                evidence_sources.append(path.name)
            else:
                findings.append({
                    "id": f"OOM-INFO-{label.upper()}",
                    "severity": "INFO",
                    "message": f"Optional --{label} file not found at '{path}'; skipped.",
                    "confidence": "HIGH",
                    "evidence": f"path={path}",
                    "uncertainty_notes": "N/A — informational only.",
                    "recommendation": f"Verify the --{label} path if deeper analysis is needed.",
                })

    return {
        "tool_version": REPORTER_VERSION,
        "run_name": metrics.get("run_name", "unknown"),
        "exit_code": metrics.get("exit_code"),
        "timed_out": metrics.get("timed_out", False),
        "duration_s": metrics.get("duration_s"),
        "overall_memory_risk": _compute_overall_risk(findings),
        "findings": findings,
        "suggested_commands": [_build_rerun_cmd(metrics)],
        "uncertainty_notes": (
            "All findings are based on coarse post-exit evidence (exit code, log patterns, "
            "RSS samples). Root-cause certainty requires deeper profiling with tracemalloc, "
            "heaptrack, or valgrind. No single indicator constitutes a definitive OOM diagnosis."
        ),
        "evidence_sources_used": evidence_sources,
    }


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------

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
        "Run":              result.get("run_name", ""),
        "Exit code":        str(result.get("exit_code")),
        "Timed out":        str(result.get("timed_out")),
        "Duration (s)":     str(result.get("duration_s")),
        "Overall risk":     result.get("overall_memory_risk", "NONE"),
        "Findings count":   str(len(result.get("findings", []))),
    }
    finding_lines = []
    for f in result.get("findings", []):
        finding_lines.append(
            f"[{f.get('confidence','?')}] {f.get('id','?')}: {f.get('message','')}"
        )
    sections = {
        "Overview": overview,
        "Findings": finding_lines or ["No findings."],
        "Suggested Commands": result.get("suggested_commands", []),
        "Uncertainty Notes": result.get("uncertainty_notes", ""),
    }
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    if _CORE_AVAILABLE:
        write_markdown_report(  # type: ignore[name-defined]
            "OOM Forensics Report", sections, markdown_out,
        )
    else:
        lines = ["# OOM Forensics Report", ""]
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
        prog="oom_forensics_reporter",
        description=(
            "Post-run OOM / memory forensics reporter (Phase 9). "
            "Analyzes Phase 8 runtime artefacts and ranks likely memory "
            "causes with confidence levels and uncertainty notes."
        ),
    )
    parser.add_argument(
        "--runtime-report",
        required=True,
        dest="runtime_report",
        metavar="DIR",
        help="Phase 8 output directory containing runtime_metrics.json etc.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        metavar="CSV",
        help="Optional file-map CSV for oversized-file hints.",
    )
    parser.add_argument(
        "--bundle",
        default=None,
        metavar="JSON",
        help="Optional slicer JSON bundle for import-pattern hints.",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="JSON",
        help="Optional project config JSON.",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="JSON",
        help="Output path for the JSON forensics report.",
    )
    parser.add_argument(
        "--markdown-out",
        default=None,
        dest="markdown_out",
        metavar="MD",
        help="Optional output path for a Markdown summary.",
    )

    args = parser.parse_args()

    runtime_dir = pathlib.Path(args.runtime_report)
    if not runtime_dir.exists():
        print(f"ERROR: --runtime-report directory not found: '{runtime_dir}'", file=sys.stderr)
        sys.exit(1)

    try:
        result = _analyze(
            runtime_dir,
            bundle_path=pathlib.Path(args.bundle) if args.bundle else None,
            manifest_path=pathlib.Path(args.manifest) if args.manifest else None,
            config_path=pathlib.Path(args.config) if args.config else None,
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    _write_reports(
        result,
        out_path=pathlib.Path(args.out),
        markdown_out=pathlib.Path(args.markdown_out) if args.markdown_out else None,
    )


if __name__ == "__main__":
    main()
