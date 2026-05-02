#!/usr/bin/env python3
"""pipeline_gatekeeper.py — Phase 10.

Pipeline gate evaluation tool.

Consumes analysis reports from prior phases and evaluates stop/go gates
based on a configurable policy.  Outputs a structured PASS / WARN / BLOCK
verdict with per-gate details and override guidance.

Inputs:
  --manifest-report PATH   JSON from manifest_doctor.py (optional).
  --validator-report PATH  JSON from architecture_validator.py (optional).
  --runtime-report PATH    JSON from oom_forensics_reporter.py (optional).
  --policy PATH            Gate policy JSON (optional; built-in defaults apply).
  --out PATH               Output JSON report (required).
  --markdown-out PATH      Optional Markdown summary.

Output status:
  PASS   — All provided gates passed.
  WARN   — One or more gates issued warnings; pipeline may proceed with care.
  BLOCK  — One or more gates require action before proceeding.

Policy JSON format:
  {
    "policy_version": "1.0",
    "required_reports": ["manifest", "validator"],
    "gates": {
      "manifest": {
        "label": "Manifest health",
        "status_field": "status",
        "status_map": {"PASS": "PASS", "WARN": "WARN", "BLOCK": "BLOCK"},
        "overrideable": false
      }
    }
  }
  Gate keys: "manifest", "validator", "runtime".
  status_map values must be "PASS", "WARN", or "BLOCK".

Exit codes:
  0  — PASS or WARN.
  2  — BLOCK.
  1  — Invocation error (unreadable inputs, missing --out).
"""

import argparse
import datetime
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional

try:
    from aletheia_tool_core.reports import write_json_report, write_markdown_report
    _CORE_AVAILABLE = True
except ImportError:
    _CORE_AVAILABLE = False

GATEKEEPER_VERSION: str = "v10.0"
_ENCODING: str = "utf-8"

_DEFAULT_POLICY: Dict[str, Any] = {
    "policy_version": "1.0",
    "required_reports": [],
    "gates": {
        "manifest": {
            "label": "Manifest health",
            "status_field": "status",
            "status_map": {
                "PASS": "PASS",
                "WARN": "WARN",
                "BLOCK": "BLOCK",
            },
            "overrideable": False,
        },
        "validator": {
            "label": "Architecture validation",
            "status_field": "status",
            "status_map": {
                "PASS": "PASS",
                "WARN": "WARN",
                "FAIL": "BLOCK",
            },
            "overrideable": True,
        },
        "runtime": {
            "label": "Runtime memory risk",
            "status_field": "overall_memory_risk",
            "status_map": {
                "NONE": "PASS",
                "LOW": "WARN",
                "MEDIUM": "WARN",
                "HIGH": "BLOCK",
            },
            "overrideable": True,
        },
    },
}

_REPORT_ARG_TO_GATE = {
    "manifest_report": "manifest",
    "validator_report": "validator",
    "runtime_report": "runtime",
}


def _load_policy(policy_path: Optional[pathlib.Path]) -> Dict[str, Any]:
    if policy_path is None:
        return _DEFAULT_POLICY
    with policy_path.open(encoding=_ENCODING) as fh:
        return json.load(fh)


def _summary_for_gate(gate_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "tool_version": data.get("tool_version") or data.get("validator_version"),
    }
    if gate_id == "manifest":
        entry["status"] = data.get("status")
        entry["summary"] = data.get("summary", {})
        entry["recommended_action"] = data.get("recommended_action")
    elif gate_id == "validator":
        entry["status"] = data.get("status")
        entry["findings_count"] = data.get("findings_count", 0)
        entry["severity_counts"] = data.get("severity_counts", {})
    elif gate_id == "runtime":
        entry["overall_memory_risk"] = data.get("overall_memory_risk")
        entry["findings_count"] = len(data.get("findings", []))
        entry["timed_out"] = data.get("timed_out")
    return entry


def _evaluate(
    report_paths: Dict[str, Optional[pathlib.Path]],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    gates = policy.get("gates", _DEFAULT_POLICY["gates"])
    required_reports: List[str] = list(policy.get("required_reports", []))

    failed_gates: List[Dict[str, Any]] = []
    warning_gates: List[Dict[str, Any]] = []
    missing_reports: List[str] = []
    input_report_summary: Dict[str, Any] = {}

    for gate_id, gate_cfg in gates.items():
        report_path = report_paths.get(gate_id)
        label = gate_cfg.get("label", gate_id)
        overrideable = bool(gate_cfg.get("overrideable", False))
        status_field: str = gate_cfg.get("status_field", "status")
        status_map: Dict[str, str] = gate_cfg.get("status_map", {})

        if report_path is None:
            if gate_id in required_reports:
                missing_reports.append(gate_id)
                failed_gates.append({
                    "gate_id": gate_id,
                    "label": label,
                    "outcome": "BLOCK",
                    "report_status": None,
                    "report_path": None,
                    "overrideable": overrideable,
                    "reason": "Required report not provided.",
                })
            continue

        if not report_path.exists():
            missing_reports.append(gate_id)
            failed_gates.append({
                "gate_id": gate_id,
                "label": label,
                "outcome": "BLOCK",
                "report_status": None,
                "report_path": str(report_path),
                "overrideable": overrideable,
                "reason": f"Report file not found: {report_path}",
            })
            continue

        try:
            with report_path.open(encoding=_ENCODING) as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            failed_gates.append({
                "gate_id": gate_id,
                "label": label,
                "outcome": "BLOCK",
                "report_status": None,
                "report_path": str(report_path),
                "overrideable": overrideable,
                "reason": f"Unreadable report: {exc}",
            })
            continue

        raw_status = data.get(status_field)
        raw_key = str(raw_status).upper() if raw_status is not None else ""
        outcome = status_map.get(raw_key, "WARN")

        gate_entry: Dict[str, Any] = {
            "gate_id": gate_id,
            "label": label,
            "outcome": outcome,
            "report_status": raw_status,
            "report_path": str(report_path),
            "overrideable": overrideable,
        }

        input_report_summary[gate_id] = _summary_for_gate(gate_id, data)
        input_report_summary[gate_id]["report_path"] = str(report_path)

        if outcome == "BLOCK":
            failed_gates.append(gate_entry)
        elif outcome == "WARN":
            warning_gates.append(gate_entry)

    if failed_gates:
        aggregate = "BLOCK"
    elif warning_gates:
        aggregate = "WARN"
    else:
        aggregate = "PASS"

    overrideable_gates: List[str] = [
        g["gate_id"] for g in failed_gates + warning_gates
        if g.get("overrideable")
    ]

    if aggregate == "BLOCK":
        if overrideable_gates:
            action = (
                f"BLOCK: Address failing gates or apply authorized overrides for: "
                f"{', '.join(overrideable_gates)}."
            )
        else:
            action = "BLOCK: Address all blocking gates before proceeding."
    elif aggregate == "WARN":
        action = "WARN: Review warning gates. Proceed with caution or address issues."
    else:
        action = "PASS: All provided gates passed. Pipeline may proceed."

    return {
        "tool_version": GATEKEEPER_VERSION,
        "evaluated_at": datetime.datetime.now().isoformat(),
        "status": aggregate,
        "failed_gates": failed_gates,
        "warning_gates": warning_gates,
        "overrideable_gates": overrideable_gates,
        "missing_reports": missing_reports,
        "recommended_next_action": action,
        "input_report_summary": input_report_summary,
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
        "Status":     result["status"],
        "Evaluated":  result.get("evaluated_at", ""),
        "Failed gates":   str(len(result.get("failed_gates", []))),
        "Warning gates":  str(len(result.get("warning_gates", []))),
        "Overrideable":   ", ".join(result.get("overrideable_gates", [])) or "(none)",
        "Missing reports": ", ".join(result.get("missing_reports", [])) or "(none)",
    }
    failed_lines = [
        f"{g['gate_id']} [{g['label']}] — {g.get('reason', g.get('report_status', ''))}"
        for g in result.get("failed_gates", [])
    ] or ["(none)"]
    warning_lines = [
        f"{g['gate_id']} [{g['label']}] — status: {g.get('report_status', '')}"
        for g in result.get("warning_gates", [])
    ] or ["(none)"]

    sections: Dict[str, Any] = {
        "Overview": overview,
        "Failed Gates": failed_lines,
        "Warning Gates": warning_lines,
        "Recommended Next Action": result.get("recommended_next_action", ""),
    }
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    if _CORE_AVAILABLE:
        write_markdown_report(  # type: ignore[name-defined]
            "Pipeline Gatekeeper Report", sections, markdown_out,
        )
    else:
        lines = ["# Pipeline Gatekeeper Report", ""]
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
        prog="pipeline_gatekeeper",
        description=(
            "Pipeline gate evaluation tool (Phase 10). "
            "Evaluates PASS / WARN / BLOCK verdicts from prior-phase reports "
            "using a configurable gate policy."
        ),
    )
    parser.add_argument(
        "--manifest-report", default=None, dest="manifest_report", metavar="JSON",
        help="JSON report from manifest_doctor.py.",
    )
    parser.add_argument(
        "--validator-report", default=None, dest="validator_report", metavar="JSON",
        help="JSON report from architecture_validator.py.",
    )
    parser.add_argument(
        "--runtime-report", default=None, dest="runtime_report", metavar="JSON",
        help="JSON report from oom_forensics_reporter.py.",
    )
    parser.add_argument(
        "--policy", default=None, metavar="JSON",
        help="Gate policy JSON (optional; built-in defaults apply).",
    )
    parser.add_argument(
        "--out", required=True, metavar="JSON",
        help="Output path for the gatekeeper JSON report.",
    )
    parser.add_argument(
        "--markdown-out", default=None, dest="markdown_out", metavar="MD",
        help="Optional output path for a Markdown summary.",
    )

    args = parser.parse_args()

    policy_path = pathlib.Path(args.policy) if args.policy else None
    if policy_path is not None and not policy_path.exists():
        print(f"ERROR: --policy not found: '{policy_path}'", file=sys.stderr)
        sys.exit(1)

    try:
        policy = _load_policy(policy_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: Cannot read policy: {exc}", file=sys.stderr)
        sys.exit(1)

    report_paths: Dict[str, Optional[pathlib.Path]] = {
        "manifest":  pathlib.Path(args.manifest_report) if args.manifest_report else None,
        "validator": pathlib.Path(args.validator_report) if args.validator_report else None,
        "runtime":   pathlib.Path(args.runtime_report) if args.runtime_report else None,
    }

    try:
        result = _evaluate(report_paths, policy)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    _write_reports(
        result,
        out_path=pathlib.Path(args.out),
        markdown_out=pathlib.Path(args.markdown_out) if args.markdown_out else None,
    )

    sys.exit(2 if result["status"] == "BLOCK" else 0)


if __name__ == "__main__":
    main()
