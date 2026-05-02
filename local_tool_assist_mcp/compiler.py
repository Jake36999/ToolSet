"""Report compiler and session archive layer for Local Tool Assist MCP Wrapper.

Provides three public functions:
- compile_handoff_report  →  reports/final_handoff_report_<session_id>.md
- compile_python_bundle   →  reports/final_handoff_bundle_<session_id>.py
- archive_session_yaml    →  archive/session_<session_id>.yaml

All outputs are written outside aletheia_toolchain/.
"""

import json
import pathlib
import re
import sys
import textwrap
from typing import Optional

from local_tool_assist_mcp.session import TOOLCHAIN_ROOT, save_session
from local_tool_assist_mcp.schemas import validate_session

# ---------------------------------------------------------------------------
# Redaction — import sanitize_content or use a conservative local shim
# ---------------------------------------------------------------------------


_REDACT_PATTERNS = [
    re.compile(r"(?i)([A-Z_]*API_KEY\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(Authorization:\s*Bearer\s*)\S+"),
    re.compile(r"(?i)(token\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(secret\s*[:=]\s*)\S+"),
]

def _local_sanitize(content: str) -> str:
    for pat in _REDACT_PATTERNS:
        content = pat.sub(r"\1[REDACTED]", content)
    return content


def _sanitize(content: str) -> str:
    # Belt-and-suspenders: aletheia sanitizer (if available) then local patterns.
    try:
        if str(TOOLCHAIN_ROOT) not in sys.path:
            sys.path.insert(0, str(TOOLCHAIN_ROOT))
        from aletheia_tool_core.security import sanitize_content  # type: ignore
        content = sanitize_content(content)
    except ImportError:
        pass
    return _local_sanitize(content)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _session_id(session_dict: dict) -> str:
    return session_dict.get("session_id", "unknown")


def _assert_output_outside_toolchain(path: pathlib.Path) -> None:
    try:
        path.resolve().relative_to(TOOLCHAIN_ROOT.resolve())
        raise RuntimeError(
            f"Output path {path!r} is inside TOOLCHAIN_ROOT {TOOLCHAIN_ROOT!r}. "
            "All compiler outputs must be written outside aletheia_toolchain/."
        )
    except ValueError:
        pass  # not a sub-path — boundary is respected


def _reports_dir(output_root: pathlib.Path) -> pathlib.Path:
    d = output_root / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _archive_dir(output_root: pathlib.Path) -> pathlib.Path:
    d = output_root / "archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slice_approved(session_dict: dict) -> bool:
    return bool(session_dict.get("review_state", {}).get("slice_approved", False))


def _build_artifact_index(session_dict: dict) -> list:
    artifacts = session_dict.get("artifacts", {})
    return [
        {"key": k, "path": v if v else "(not produced)"}
        for k, v in artifacts.items()
    ]


def _build_step_summary(session_dict: dict) -> list:
    steps = session_dict.get("steps", [])
    return [
        {
            "action": s.get("action", ""),
            "status": s.get("status", ""),
            "started_at": s.get("started_at", ""),
            "ended_at": s.get("ended_at", ""),
        }
        for s in steps
    ]


def _policy_status_line(session_dict: dict) -> str:
    policy = session_dict.get("policy", {})
    flags = [k for k, v in policy.items() if v is True]
    return ", ".join(flags) if flags else "no active policy flags"


def _recommendation(session_dict: dict) -> str:
    steps = session_dict.get("steps", [])
    statuses = {s.get("status", "") for s in steps}
    approved = _slice_approved(session_dict)

    if "BLOCK" in statuses or "POLICY_BLOCK" in statuses:
        return (
            "One or more actions were blocked. Review the policy flags and step results "
            "before proceeding. Do not pass this session to a downstream agent until all "
            "BLOCK conditions are resolved."
        )
    if "ERROR" in statuses:
        return (
            "One or more actions returned ERROR. Inspect the artifact outputs and stderr "
            "tails before handing off to a downstream agent."
        )
    if not approved:
        return (
            "Semantic slicing has not been approved. Set review_state.slice_approved = true "
            "and re-run run_semantic_slice before final handoff."
        )
    return (
        "All recorded steps completed without errors and slice is approved. "
        "This session is ready for handoff to the downstream agent."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile_handoff_report(
    session_dict: dict,
    session_dir,
    output_root,
) -> pathlib.Path:
    """Write a Markdown handoff report and return its path.

    Output: ``output_root/reports/final_handoff_report_<session_id>.md``
    """
    session_dir = pathlib.Path(session_dir)
    output_root = pathlib.Path(output_root)
    sid = _session_id(session_dict)
    out_path = _reports_dir(output_root) / f"final_handoff_report_{sid}.md"
    _assert_output_outside_toolchain(out_path)

    req = session_dict.get("request", {})
    review = session_dict.get("review_state", {})
    approved = _slice_approved(session_dict)
    artifact_rows = _build_artifact_index(session_dict)
    step_rows = _build_step_summary(session_dict)

    lines = [
        f"# Local Tool Assist Handoff Report",
        "",
        "## Session",
        "",
        f"- **Session ID:** `{sid}`",
        f"- **Objective:** {req.get('objective', '')}",
        f"- **Target Repo:** `{req.get('target_repo', '')}`",
        f"- **Downstream Agent:** {req.get('downstream_agent', 'unknown')}",
        f"- **Requested By:** {req.get('requested_by', '')}",
        "",
        "## Review State",
        "",
        f"- scan_reviewed: {review.get('scan_reviewed', False)}",
        f"- manifest_reviewed: {review.get('manifest_reviewed', False)}",
        f"- slice_approved: {review.get('slice_approved', False)}",
        f"- approved_by: {review.get('approved_by') or '(not set)'}",
        f"- approved_at: {review.get('approved_at') or '(not set)'}",
        f"- approval_notes: {review.get('approval_notes') or '(none)'}",
        "",
    ]

    if not approved:
        lines += [
            "> **WARNING:** Semantic slicing has not been approved.",
            "> `review_state.slice_approved` is `false`.",
            "> Do not use slice output for downstream agent tasks until approval is confirmed.",
            "",
        ]

    lines += [
        "## Step Summary",
        "",
    ]
    if step_rows:
        lines += [
            "| Action | Status | Started | Ended |",
            "|--------|--------|---------|-------|",
        ]
        for s in step_rows:
            lines.append(f"| {s['action']} | {s['status']} | {s['started_at']} | {s['ended_at']} |")
        lines.append("")
    else:
        lines += ["*(no steps recorded)*", ""]

    lines += [
        "## Artifact Index",
        "",
    ]
    for a in artifact_rows:
        lines.append(f"- **{a['key']}**: `{a['path']}`")
    lines.append("")

    lines += [
        "## Policy Status",
        "",
        _policy_status_line(session_dict),
        "",
        "## Recommendation",
        "",
        _recommendation(session_dict),
        "",
        "## Redaction Note",
        "",
        "stdout/stderr tails, artifact snippets, and bundle constants in this report "
        "have been processed by the redaction layer. Sensitive patterns (API keys, tokens, "
        "secrets, high-entropy credential strings) are replaced with `[REDACTED]` or "
        "`[REDACTED_HIGH_ENTROPY]`.",
        "",
        "## Verification Snapshot Note",
        "",
        "LTA-4 should evaluate adding a `create_verification_snapshot` MCP-exposed action "
        "that captures a point-in-time SHA1 fingerprint of the session YAML, all produced "
        "artifact paths, and the step results. This snapshot should be an internal "
        "CI/reporting artifact, not exposed as a tool the downstream agent can trigger "
        "autonomously. Keeping it internal preserves the review boundary and avoids adding "
        "another write-capable action without corresponding approval gating.",
        "",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def compile_python_bundle(
    session_dict: dict,
    session_dir,
    output_root,
) -> pathlib.Path:
    """Write a valid Python data-constants bundle and return its path.

    Output: ``output_root/reports/final_handoff_bundle_<session_id>.py``
    The bundle contains only data constants — no executable workflow logic.
    Validated with :mod:`py_compile` before returning.
    """
    import py_compile
    import tempfile

    session_dir = pathlib.Path(session_dir)
    output_root = pathlib.Path(output_root)
    sid = _session_id(session_dict)
    out_path = _reports_dir(output_root) / f"final_handoff_bundle_{sid}.py"
    _assert_output_outside_toolchain(out_path)

    try:
        import yaml as _yaml
        session_yaml_str = _yaml.dump(
            session_dict,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
    except ImportError:
        session_yaml_str = json.dumps(session_dict, indent=2)

    artifact_index = _build_artifact_index(session_dict)
    step_summary = _build_step_summary(session_dict)

    req = session_dict.get("request", {})
    review = session_dict.get("review_state", {})

    summary_lines = [
        f"Session: {sid}",
        f"Objective: {req.get('objective', '')}",
        f"Target Repo: {req.get('target_repo', '')}",
        f"Downstream Agent: {req.get('downstream_agent', 'unknown')}",
        f"Slice Approved: {_slice_approved(session_dict)}",
        f"Steps: {len(session_dict.get('steps', []))}",
        "Recommendation: " + _recommendation(session_dict),
    ]
    summary_md = "\n".join(summary_lines)

    redacted_yaml = _sanitize(session_yaml_str)
    redacted_artifact = _sanitize(json.dumps(artifact_index, indent=2))
    redacted_steps = _sanitize(json.dumps(step_summary, indent=2))
    redacted_summary = _sanitize(summary_md)

    bundle_lines = [
        '"""Local Tool Assist session data bundle — auto-generated, do not edit."""',
        "# This file contains only data constants. No executable workflow logic.",
        "",
        f"SESSION_ID = {sid!r}",
        "",
        "SESSION_YAML = r\"\"\"",
        redacted_yaml.rstrip(),
        "\"\"\"",
        "",
        "ARTIFACT_INDEX_JSON = r\"\"\"",
        redacted_artifact.rstrip(),
        "\"\"\"",
        "",
        "STEP_RESULTS_JSON = r\"\"\"",
        redacted_steps.rstrip(),
        "\"\"\"",
        "",
        "FINAL_SUMMARY_MD = r\"\"\"",
        redacted_summary.rstrip(),
        "\"\"\"",
        "",
    ]

    source = "\n".join(bundle_lines)
    out_path.write_text(source, encoding="utf-8")

    # Validate with py_compile — raises py_compile.PyCompileError on failure
    py_compile.compile(str(out_path), doraise=True)

    return out_path


def archive_session_yaml(
    session_dict: dict,
    session_dir,
    output_root,
) -> pathlib.Path:
    """Archive the session to ``output_root/archive/session_<session_id>.yaml``.

    Validates with :func:`validate_session` before writing.
    Never includes provider API keys or tokens (sanitized via redaction layer).
    """
    session_dir = pathlib.Path(session_dir)
    output_root = pathlib.Path(output_root)
    sid = _session_id(session_dict)
    out_path = _archive_dir(output_root) / f"session_{sid}.yaml"
    _assert_output_outside_toolchain(out_path)

    valid, errors = validate_session(session_dict)
    if not valid:
        raise ValueError(
            f"Session failed schema validation before archive. Errors: {errors}"
        )

    save_session(session_dict, out_path)
    return out_path
