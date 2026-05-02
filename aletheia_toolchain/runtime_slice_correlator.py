#!/usr/bin/env python3
"""runtime_slice_correlator.py — Phase 9.

Runtime-to-slice correlation tool.

Maps runtime symptoms (from a Phase 8 evidence directory) to ranked code
slices from a semantic slicer bundle.  Produces a ranked list of correlations
with confidence levels, uncertainty notes, and suggested --explain commands
for follow-up investigation.

Inputs:
  --runtime-report DIR   Phase 8 output directory (required).
  --bundle-json PATH     Slicer JSON bundle from semantic_slicer_v7.0.py (required).
  --out PATH             Output JSON report (required).
  --markdown-out PATH    Optional Markdown report.

Degrades gracefully when the bundle contains no slice metadata, reporting
degraded_mode=true with a clear explanation.

Exit codes:
  0  — Analysis complete (including degraded-mode runs).
  1  — Invocation error (missing required files, unreadable inputs).
"""

import argparse
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Optional aletheia_tool_core helpers
# ---------------------------------------------------------------------------

try:
    from aletheia_tool_core.reports import write_json_report, write_markdown_report
    _CORE_AVAILABLE = True
except ImportError:
    _CORE_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CORRELATOR_VERSION: str = "v9.0"
TOP_K: int = 5
_ENCODING: str = "utf-8"

# Regex patterns for Python traceback file references.
_TB_FILE_RE = re.compile(r'File "([^"]+)"')
# ImportError module name.
_IMPORT_RE = re.compile(r"No module named '([^']+)'")
# Exception class names.
_EXC_RE = re.compile(r"\b(\w+(?:Error|Exception|Warning))\b")


# ---------------------------------------------------------------------------
# Evidence extraction
# ---------------------------------------------------------------------------

def _read_tail(runtime_dir: pathlib.Path, fname: str) -> str:
    """Read a tail file from a Phase 8 directory.  Returns '' if absent."""
    p = runtime_dir / fname
    return p.read_text(encoding=_ENCODING, errors="replace") if p.exists() else ""


def _extract_symptom_keywords(text: str) -> Set[str]:
    """Pull file names, module names, and exception types from log text."""
    keywords: Set[str] = set()

    for match in _TB_FILE_RE.finditer(text):
        full_path = match.group(1)
        keywords.add(full_path)
        keywords.add(pathlib.Path(full_path).name)
        keywords.add(pathlib.Path(full_path).stem)

    for match in _IMPORT_RE.finditer(text):
        keywords.add(match.group(1))

    for match in _EXC_RE.finditer(text):
        keywords.add(match.group(1))

    return {kw for kw in keywords if kw}


# ---------------------------------------------------------------------------
# Bundle slice extraction
# ---------------------------------------------------------------------------

def _get_bundle_slices(bundle_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract a normalised slice list from a bundle, regardless of format.

    Returns a list of dicts with at least: id, name, file, dependencies,
    complexity.  Returns [] when no slice data can be found.
    """
    # --- v7 format: layer_2_intelligence ---
    intel = bundle_data.get("layer_2_intelligence", [])
    if isinstance(intel, list) and intel:
        slices = []
        for item in intel:
            if isinstance(item, dict):
                slices.append({
                    "id":           item.get("path", ""),
                    "name":         item.get("path", ""),
                    "file":         item.get("path", ""),
                    "dependencies": item.get("import_graph") or [],
                    "complexity":   0,
                })
        if slices:
            return slices

    # --- fixture / v6 format: layer_2_code_intelligence.slices ---
    slices2 = (bundle_data.get("layer_2_code_intelligence") or {}).get("slices", [])
    if isinstance(slices2, list) and slices2:
        return [s for s in slices2 if isinstance(s, dict)]

    return []


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_slice(
    slice_data: Dict[str, Any],
    keywords: Set[str],
    full_text: str,
) -> Tuple[float, str, str]:
    """Score a slice's relevance to the observed runtime symptoms.

    Returns (score, reason, confidence).

    Score bands:
      3.0  File path found literally in runtime logs (HIGH).
      2.0  Slice name found literally in runtime logs (HIGH).
      1.0  A slice dependency matches a keyword (MEDIUM).
      0.5  High structural complexity (complexity > 10) (LOW).
    """
    score = 0.0
    reasons: List[str] = []
    confidence = "LOW"

    file_path: str = slice_data.get("file", "")
    slice_name: str = slice_data.get("name", "")
    deps: List[str] = list(slice_data.get("dependencies") or [])
    complexity = slice_data.get("complexity") or 0

    # --- File path match ---
    if file_path:
        stem = pathlib.Path(file_path).stem
        fname = pathlib.Path(file_path).name
        if (
            file_path in full_text
            or fname in keywords
            or stem in keywords
            or file_path in keywords
        ):
            score += 3.0
            reasons.append(f"File '{file_path}' referenced in runtime logs")
            confidence = "HIGH"

    # --- Slice name match ---
    if slice_name and slice_name != file_path and slice_name in full_text:
        score += 2.0
        reasons.append(f"Slice name '{slice_name}' found in runtime logs")
        if confidence == "LOW":
            confidence = "MEDIUM"

    # --- Dependency match ---
    for dep in deps:
        if dep in keywords:
            score += 1.0
            reasons.append(f"Dependency '{dep}' referenced in runtime logs")
            if confidence == "LOW":
                confidence = "MEDIUM"
            break  # count each slice once for dependency hits

    # --- Complexity hint ---
    try:
        if int(complexity) > 10:
            score += 0.5
            reasons.append(f"High complexity ({complexity})")
    except (TypeError, ValueError):
        pass

    reason = "; ".join(reasons) if reasons else "No direct correlation found"
    return score, reason, confidence


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def _correlate(
    runtime_dir: pathlib.Path,
    bundle_path: pathlib.Path,
) -> Dict[str, Any]:
    # Load runtime metrics.
    metrics_path = runtime_dir / "runtime_metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError(
            f"runtime_metrics.json not found in '{runtime_dir}'."
        )
    with metrics_path.open(encoding=_ENCODING) as fh:
        metrics = json.load(fh)

    run_name: str = metrics.get("run_name", "unknown")

    # Load runtime logs.
    stderr_text = _read_tail(runtime_dir, "stderr_tail.txt")
    stdout_text = _read_tail(runtime_dir, "stdout_tail.txt")
    combined_text = stderr_text + "\n" + stdout_text

    # Load bundle.
    with bundle_path.open(encoding=_ENCODING) as fh:
        bundle_data = json.load(fh)

    slices = _get_bundle_slices(bundle_data)

    if not slices:
        return {
            "tool_version": CORRELATOR_VERSION,
            "run_name": run_name,
            "degraded_mode": True,
            "degradation_reason": (
                "No slice data found in the bundle. "
                "Bundle may lack 'layer_2_intelligence' or "
                "'layer_2_code_intelligence.slices'. "
                "Re-run the slicer or provide a v7 bundle."
            ),
            "correlations": [],
            "uncertainty_notes": (
                "Cannot correlate runtime evidence to code slices without "
                "slice metadata."
            ),
            "evidence_summary": {
                "stderr_lines": len(stderr_text.splitlines()),
                "stdout_lines": len(stdout_text.splitlines()),
                "keywords_extracted": 0,
                "slices_scored": 0,
            },
        }

    keywords = _extract_symptom_keywords(combined_text)

    scored: List[Tuple[float, Dict[str, Any], str, str]] = []
    for s in slices:
        sc, reason, conf = _score_slice(s, keywords, combined_text)
        scored.append((sc, s, reason, conf))

    scored.sort(key=lambda x: x[0], reverse=True)

    correlations: List[Dict[str, Any]] = []
    for rank, (sc, s, reason, conf) in enumerate(scored[:TOP_K], start=1):
        file_path = s.get("file", "")
        slice_id = s.get("id", "")
        suggest = (
            f"python semantic_slicer_v7.0.py {file_path} --explain"
            if file_path
            else "python semantic_slicer_v7.0.py --explain"
        )
        correlations.append({
            "rank": rank,
            "slice_id": slice_id,
            "file_path": file_path,
            "correlation_score": round(sc, 2),
            "correlation_reason": reason,
            "confidence": conf,
            "suggested_explain_cmd": suggest,
            "evidence": (
                f"Keywords from logs (up to 5): {sorted(keywords)[:5]}"
            ),
        })

    return {
        "tool_version": CORRELATOR_VERSION,
        "run_name": run_name,
        "degraded_mode": False,
        "correlations": correlations,
        "uncertainty_notes": (
            "Correlation is based on file-path matching in tracebacks and import-graph "
            "overlap. Scores are heuristic — a high score does not guarantee the slice "
            "caused the observed behaviour. Slices with score 0.0 had no detectable link "
            "to the runtime symptoms."
        ),
        "evidence_summary": {
            "stderr_lines": len(stderr_text.splitlines()),
            "stdout_lines": len(stdout_text.splitlines()),
            "keywords_extracted": len(keywords),
            "slices_scored": len(slices),
        },
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

    summary = {
        "Run":           result.get("run_name", ""),
        "Degraded mode": str(result.get("degraded_mode", False)),
        "Correlations":  str(len(result.get("correlations", []))),
        "Slices scored": str(result.get("evidence_summary", {}).get("slices_scored", 0)),
    }
    corr_lines = []
    for c in result.get("correlations", []):
        corr_lines.append(
            f"#{c['rank']} [{c['confidence']}] {c['slice_id']} — {c['correlation_reason']}"
        )
    sections: Dict[str, Any] = {
        "Summary": summary,
        "Correlations": corr_lines or ["No correlations (degraded mode or no slices)."],
        "Uncertainty Notes": result.get("uncertainty_notes", ""),
    }
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    if _CORE_AVAILABLE:
        write_markdown_report(  # type: ignore[name-defined]
            "Runtime Slice Correlator Report", sections, markdown_out,
        )
    else:
        lines = ["# Runtime Slice Correlator Report", ""]
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
        prog="runtime_slice_correlator",
        description=(
            "Runtime-to-slice correlation tool (Phase 9). "
            "Maps Phase 8 runtime symptoms to ranked code slices "
            "in a slicer bundle with confidence levels and suggested "
            "--explain commands."
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
        "--bundle-json",
        required=True,
        dest="bundle_json",
        metavar="JSON",
        help="Slicer JSON bundle produced by semantic_slicer_v7.0.py.",
    )
    parser.add_argument(
        "--out",
        required=True,
        metavar="JSON",
        help="Output path for the JSON correlation report.",
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
    bundle_path = pathlib.Path(args.bundle_json)

    if not runtime_dir.exists():
        print(
            f"ERROR: --runtime-report directory not found: '{runtime_dir}'",
            file=sys.stderr,
        )
        sys.exit(1)

    if not bundle_path.exists():
        print(
            f"ERROR: --bundle-json not found: '{bundle_path}'",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = _correlate(runtime_dir, bundle_path)
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
