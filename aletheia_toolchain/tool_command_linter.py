"""Tool Command Linter: validate Aletheia tool commands before execution.

Parses a command string or command file, detects unsafe or invalid invocation
patterns, and writes a machine-readable JSON lint report.  Never executes the
command being linted.

Status semantics:
  PASS  — no issues found; safe to run.
  WARN  — non-fatal issues; review before running, but allowed to proceed.
  BLOCK — at least one error; must not run without fixing.

Exit codes:
  0  — PASS or WARN
  2  — BLOCK
  1  — usage / I/O error
"""

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aletheia_tool_core.config import ConfigError, load_json_config
from aletheia_tool_core.reports import write_json_report

# ---------------------------------------------------------------------------
# Rule identifiers
# ---------------------------------------------------------------------------
R001 = "R001"  # create_file_map_v2 -o  → BLOCK
R002 = "R002"  # create_file_map_v3 -o  → WARN (deprecated alias)
R003 = "R003"  # slicer broad positional '.'  without --manifest  → WARN
R004 = "R004"  # slicer --manifest + positional '.'  → BLOCK
R005 = "R005"  # slicer automated extraction missing --deterministic  → WARN
R006 = "R006"  # output path matches re-ingestion pattern  → WARN
R007 = "R007"  # command file has broad slicer without manifest_doctor step  → WARN

# ---------------------------------------------------------------------------
# Tool identity
# ---------------------------------------------------------------------------
TOOL_V2 = "create_file_map_v2"
TOOL_V3 = "create_file_map_v3"
TOOL_SLICER = "slicer"
TOOL_DOCTOR = "manifest_doctor"
TOOL_LINTER = "tool_command_linter"
TOOL_UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Re-ingestion output patterns
# ---------------------------------------------------------------------------
_REINGESTION_PATTERNS: List[str] = [
    r"_bundle_",
    r"bundle_",
    r"_[Ee]xtraction",
    r"_bundle\.(py|json|txt|yaml)",
    r"\d{8}_\d{6}",      # timestamp suffix: _20260429_161325
    r"_review_bundle",
    r"_post_implementation_review",
]
_REINGESTION_RE = re.compile("|".join(_REINGESTION_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# Slicer schema knowledge
# ---------------------------------------------------------------------------
# Flags that consume the NEXT token as a value
_SLICER_VALUE_FLAGS: frozenset = frozenset({
    "--format", "-o", "--output", "--base-dir", "--manifest",
    "--focus", "--depth", "--system-purpose", "--research-target",
    "--agent-role", "--agent-task", "--workers", "--explain",
})
# Boolean flags (store_true)
_SLICER_BOOL_FLAGS: frozenset = frozenset({
    "--git-diff", "--verbose", "--append-rules", "--deterministic",
    "--no-redaction", "--heatmap",
})
# nargs="*" flags — consume contiguous non-flag tokens
_SLICER_NARGS_FLAGS: frozenset = frozenset({
    "--rules", "--ignore-dirs", "--ignore-exts",
})
# Flags that indicate automated / agent-driven invocation
_SLICER_AUTOMATION_FLAGS: frozenset = frozenset({
    "--agent-task", "--agent-role",
})
# Output flags
_SLICER_OUTPUT_FLAGS: frozenset = frozenset({"-o", "--output"})

# v2 / v3 output flags
_FILEMAP_OUTPUT_FLAGS: frozenset = frozenset({"-o", "--out"})

# v3 boolean flags
_V3_BOOL_FLAGS: frozenset = frozenset({"--hash", "--fail-on-pollution"})
# v3 value flags
_V3_VALUE_FLAGS: frozenset = frozenset({
    "--out", "-o", "--include-exts", "--exclude-dirs",
    "--profile", "--health-report", "--max-file-size",
})
# v3 nargs+ flags
_V3_NARGS_FLAGS: frozenset = frozenset({"--roots"})

# v2 boolean flags
_V2_BOOL_FLAGS: frozenset = frozenset({"--hash"})
# v2 value flags
_V2_VALUE_FLAGS: frozenset = frozenset({
    "--out", "--include-exts", "--exclude-dirs",
})
# v2 nargs+ flags
_V2_NARGS_FLAGS: frozenset = frozenset({"--roots"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class LintFinding:
    __slots__ = ("level", "rule_id", "message", "fragment", "autofix")

    def __init__(
        self,
        level: str,
        rule_id: str,
        message: str,
        fragment: str = "",
        autofix: Optional[str] = None,
    ) -> None:
        self.level = level          # "error" | "warning"
        self.rule_id = rule_id
        self.message = message
        self.fragment = fragment    # the offending portion of the command
        self.autofix = autofix      # rewrite suggestion, or None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "rule_id": self.rule_id,
            "message": self.message,
        }
        if self.fragment:
            d["fragment"] = self.fragment
        if self.autofix:
            d["autofix"] = self.autofix
        return d


class InvocationRecord:
    """One recognised tool invocation extracted from a command string."""

    __slots__ = (
        "tool", "script_token", "raw_line",
        "flags_present", "flag_values", "positionals",
    )

    def __init__(
        self,
        tool: str,
        script_token: str,
        raw_line: str,
        flags_present: frozenset,
        flag_values: Dict[str, Any],
        positionals: List[str],
    ) -> None:
        self.tool = tool
        self.script_token = script_token
        self.raw_line = raw_line
        self.flags_present = flags_present
        self.flag_values = flag_values
        self.positionals = positionals


# ---------------------------------------------------------------------------
# Command text normalisation
# ---------------------------------------------------------------------------

def _normalise_command_text(text: str) -> List[str]:
    """Collapse PS1/bash line continuations and return logical command lines."""
    logical: List[str] = []
    pending: List[str] = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        # Discard pure comment lines
        if stripped.startswith("#"):
            continue
        # Discard empty lines (unless we're accumulating)
        if not stripped and not pending:
            continue
        # PS1 backtick continuation
        if stripped.endswith("`"):
            pending.append(stripped[:-1].rstrip())
            continue
        # Bash backslash continuation
        if stripped.endswith("\\") and not stripped.endswith("\\\\"):
            pending.append(stripped[:-1].rstrip())
            continue
        # End of a logical line
        pending.append(stripped)
        joined = " ".join(pending).strip()
        if joined:
            logical.append(joined)
        pending = []

    # Anything left in pending
    if pending:
        joined = " ".join(pending).strip()
        if joined:
            logical.append(joined)

    return logical


def _tokenise(line: str) -> List[str]:
    """Tokenise a single logical command line using shlex (posix=False)."""
    try:
        raw = shlex.split(line, posix=False)
    except ValueError:
        # Unmatched quote — fall back to simple split
        raw = line.split()
    # Strip outer quotes from each token
    result: List[str] = []
    for tok in raw:
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
            tok = tok[1:-1]
        result.append(tok)
    return result


def _identify_tool(script_token: str) -> str:
    stem = Path(script_token).stem.lower()
    if "create_file_map_v2" in stem:
        return TOOL_V2
    if "create_file_map_v3" in stem:
        return TOOL_V3
    if "semantic_slicer" in stem:
        return TOOL_SLICER
    if "manifest_doctor" in stem:
        return TOOL_DOCTOR
    if "tool_command_linter" in stem:
        return TOOL_LINTER
    return TOOL_UNKNOWN


def _is_interpreter(tok: str) -> bool:
    stem = Path(tok).stem.lower()
    return stem in ("python", "python3", "py", "python3.11", "python3.10", "python3.12")


# ---------------------------------------------------------------------------
# Argument parser per tool
# ---------------------------------------------------------------------------

def _parse_args_for_tool(
    tokens: List[str],
    tool: str,
) -> Tuple[frozenset, Dict[str, Any], List[str]]:
    """
    Walk the token list and classify tokens as:
      - flag (starts with -)
      - flag value (follows a value-taking or nargs flag)
      - positional

    Returns (flags_present, flag_values, positionals).
    """
    if tool == TOOL_SLICER:
        value_flags = _SLICER_VALUE_FLAGS
        bool_flags = _SLICER_BOOL_FLAGS
        nargs_flags = _SLICER_NARGS_FLAGS
    elif tool == TOOL_V2:
        value_flags = _V2_VALUE_FLAGS
        bool_flags = _V2_BOOL_FLAGS
        nargs_flags = _V2_NARGS_FLAGS
    elif tool == TOOL_V3:
        value_flags = _V3_VALUE_FLAGS
        bool_flags = _V3_BOOL_FLAGS
        nargs_flags = _V3_NARGS_FLAGS
    else:
        value_flags = frozenset()
        bool_flags = frozenset()
        nargs_flags = frozenset()

    flags_present: set = set()
    flag_values: Dict[str, Any] = {}
    positionals: List[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # --flag=value form
        if tok.startswith("-") and "=" in tok:
            flag, _, val = tok.partition("=")
            flags_present.add(flag)
            flag_values[flag] = val
            i += 1
            continue

        # Boolean (store_true) flag
        if tok in bool_flags:
            flags_present.add(tok)
            i += 1
            continue

        # nargs="*" / nargs="+" flags: consume non-flag tokens
        if tok in nargs_flags:
            flags_present.add(tok)
            vals: List[str] = []
            i += 1
            while i < len(tokens) and not tokens[i].startswith("-"):
                vals.append(tokens[i])
                i += 1
            flag_values[tok] = vals
            continue

        # Value-taking flag: consume next token as value
        if tok in value_flags:
            flags_present.add(tok)
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                flag_values[tok] = tokens[i + 1]
                i += 2
            else:
                i += 1
            continue

        # Unknown flag — consume next token if it looks like a value
        if tok.startswith("-"):
            flags_present.add(tok)
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                flag_values[tok] = tokens[i + 1]
                i += 2
            else:
                i += 1
            continue

        # Positional token
        positionals.append(tok)
        i += 1

    return frozenset(flags_present), flag_values, positionals


# ---------------------------------------------------------------------------
# Invocation extraction
# ---------------------------------------------------------------------------

def extract_invocations(logical_lines: List[str]) -> List[InvocationRecord]:
    """
    Find all recognised tool invocations across the logical command lines.
    An invocation begins with a Python interpreter token followed by a script token.
    """
    invocations: List[InvocationRecord] = []

    for line in logical_lines:
        tokens = _tokenise(line)
        if not tokens:
            continue

        # Find python interpreter token (may be first or after 'py' wrapper)
        idx = 0
        if _is_interpreter(tokens[0]):
            idx = 1
        else:
            continue  # line does not start with a Python interpreter

        if idx >= len(tokens):
            continue

        script_token = tokens[idx]
        tool = _identify_tool(script_token)
        if tool == TOOL_UNKNOWN:
            continue

        arg_tokens = tokens[idx + 1:]
        flags_present, flag_values, positionals = _parse_args_for_tool(arg_tokens, tool)

        invocations.append(InvocationRecord(
            tool=tool,
            script_token=script_token,
            raw_line=line,
            flags_present=flags_present,
            flag_values=flag_values,
            positionals=positionals,
        ))

    return invocations


# ---------------------------------------------------------------------------
# Lint rules
# ---------------------------------------------------------------------------

def _get_output_value(inv: InvocationRecord) -> Optional[str]:
    """Return the output path value from any known output flag, or None."""
    for flag in (*_FILEMAP_OUTPUT_FLAGS, *_SLICER_OUTPUT_FLAGS):
        if flag in inv.flag_values:
            return str(inv.flag_values[flag])
    return None


def _output_is_reingestion(path: str) -> bool:
    return bool(_REINGESTION_RE.search(path))


def rule_r001(inv: InvocationRecord) -> Optional[LintFinding]:
    """R001: create_file_map_v2.py does not support -o; using it is always an error."""
    if inv.tool == TOOL_V2 and "-o" in inv.flags_present:
        return LintFinding(
            level="error",
            rule_id=R001,
            message=(
                "create_file_map_v2.py does not accept -o. "
                "The flag will be treated as an unrecognised argument and the command will fail. "
                "Use --out instead."
            ),
            fragment=inv.raw_line,
            autofix="Replace -o <value> with --out <value>",
        )
    return None


def rule_r002(inv: InvocationRecord) -> Optional[LintFinding]:
    """R002: create_file_map_v3.py -o is deprecated; prefer --out."""
    if inv.tool == TOOL_V3 and "-o" in inv.flags_present and "--out" not in inv.flags_present:
        return LintFinding(
            level="warning",
            rule_id=R002,
            message=(
                "create_file_map_v3.py accepts -o as a deprecated alias for --out. "
                "The command will run but will emit a deprecation warning. "
                "Prefer --out for clarity and forward compatibility."
            ),
            fragment=inv.raw_line,
            autofix="Replace -o <value> with --out <value>",
        )
    return None


def rule_r003(inv: InvocationRecord) -> Optional[LintFinding]:
    """R003: slicer positional '.' without --manifest — broad scan risk (WARN).

    WARN rather than BLOCK because a broad '.' scan is technically valid when
    the user explicitly intends to scan the whole tree. The risk is that it
    produces enormous file sets (as seen in the transcript: 110k-file scans).
    The linter warns rather than blocks to avoid rejecting legitimate wide scans.
    """
    if (
        inv.tool == TOOL_SLICER
        and "." in inv.positionals
        and "--manifest" not in inv.flags_present
        and "--git-diff" not in inv.flags_present
    ):
        return LintFinding(
            level="warning",
            rule_id=R003,
            message=(
                "Semantic slicer is invoked with a broad positional '.' and no --manifest. "
                "This may scan the entire working tree and produce very large bundles. "
                "Consider using --manifest to restrict the file set."
            ),
            fragment=inv.raw_line,
            autofix="Add --manifest <path_to_csv> and remove the positional '.'",
        )
    return None


def rule_r004(inv: InvocationRecord) -> Optional[LintFinding]:
    """R004: slicer --manifest + positional '.' — contradictory and produces a massive scan."""
    if (
        inv.tool == TOOL_SLICER
        and "." in inv.positionals
        and "--manifest" in inv.flags_present
    ):
        return LintFinding(
            level="error",
            rule_id=R004,
            message=(
                "Semantic slicer has both --manifest and a positional '.' argument. "
                "The slicer will merge the manifest file list with the broad '.' scan, "
                "producing a superset that defeats the purpose of the manifest. "
                "Remove the positional '.' when --manifest is specified."
            ),
            fragment=inv.raw_line,
            autofix="Remove the positional '.' from the argument list",
        )
    return None


def rule_r005(inv: InvocationRecord) -> Optional[LintFinding]:
    """R005: automated slicer invocation missing --deterministic (WARN).

    WARN rather than BLOCK because --deterministic is a best-practice flag for
    reproducibility, not a hard correctness requirement. The command will run
    correctly without it; the output will simply carry a variable timestamp.
    """
    if inv.tool != TOOL_SLICER:
        return None
    is_automated = bool(inv.flags_present & _SLICER_AUTOMATION_FLAGS)
    if not is_automated:
        # Also flag if the output name looks like an automated bundle artefact
        out_val = _get_output_value(inv)
        if out_val and _output_is_reingestion(out_val):
            is_automated = True
    if is_automated and "--deterministic" not in inv.flags_present:
        triggers = sorted(inv.flags_present & (_SLICER_AUTOMATION_FLAGS | {"-o", "--output"}))
        return LintFinding(
            level="warning",
            rule_id=R005,
            message=(
                "Automated or scripted slicer invocation detected "
                f"({', '.join(triggers) if triggers else 'output name pattern'}). "
                "--deterministic is not set; the bundle will include a variable "
                "timestamp, making repeated runs non-reproducible."
            ),
            fragment=inv.raw_line,
            autofix="Add --deterministic to the slicer command",
        )
    return None


def rule_r006(inv: InvocationRecord) -> Optional[LintFinding]:
    """R006: output path matches a re-ingestion pattern (WARN).

    WARN rather than BLOCK because names like '_bundle_' could be intentional.
    The rule catches accidental re-ingestion of generated artefacts, not all
    uses of bundle-like naming conventions.
    """
    out_val = _get_output_value(inv)
    if out_val and _output_is_reingestion(out_val):
        return LintFinding(
            level="warning",
            rule_id=R006,
            message=(
                f"Output path '{out_val}' matches a pattern associated with "
                "generated bundle artefacts (e.g. _bundle_, _Extraction, timestamps). "
                "Writing tool output to a bundle-named path risks re-ingestion "
                "in a future scan."
            ),
            fragment=out_val,
            autofix=None,
        )
    return None


def rule_r007_for_file(invocations: List[InvocationRecord]) -> Optional[LintFinding]:
    """R007: command file has a broad slicer invocation without a manifest_doctor step.

    Only fires when analysing a command file (multiple invocations); a single
    inline command would not include a manifest_doctor step by design.
    WARN rather than BLOCK because the manifest_doctor may have been run
    separately in an earlier session.
    """
    has_doctor = any(inv.tool == TOOL_DOCTOR for inv in invocations)
    has_broad_slicer = any(
        inv.tool == TOOL_SLICER and "." in inv.positionals
        for inv in invocations
    )
    if has_broad_slicer and not has_doctor:
        return LintFinding(
            level="warning",
            rule_id=R007,
            message=(
                "Command file contains a broad slicer invocation but no "
                "manifest_doctor.py step. Running manifest_doctor before the slicer "
                "detects pollution, verifies required coverage, and prevents "
                "large unfiltered scans."
            ),
            fragment="(command file level)",
            autofix="Add a manifest_doctor.py invocation before the slicer command",
        )
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _apply_rules(
    invocations: List[InvocationRecord],
    is_file: bool,
    disabled_rules: frozenset,
) -> List[LintFinding]:
    findings: List[LintFinding] = []

    per_invocation_rules = [rule_r001, rule_r002, rule_r003, rule_r004, rule_r005, rule_r006]

    for inv in invocations:
        for rule_fn in per_invocation_rules:
            finding = rule_fn(inv)
            if finding and finding.rule_id not in disabled_rules:
                findings.append(finding)

    # File-level rule
    if is_file and R007 not in disabled_rules:
        finding = rule_r007_for_file(invocations)
        if finding:
            findings.append(finding)

    return findings


def _determine_status(findings: List[LintFinding]) -> str:
    if any(f.level == "error" for f in findings):
        return "BLOCK"
    if any(f.level == "warning" for f in findings):
        return "WARN"
    return "PASS"


def build_report(
    findings: List[LintFinding],
    command_source: str,
    status: str,
    invocation_count: int,
) -> Dict[str, Any]:
    errors = [f.to_dict() for f in findings if f.level == "error"]
    warnings = [f.to_dict() for f in findings if f.level == "warning"]
    autofixes = [
        f.autofix for f in findings if f.autofix
    ]
    return {
        "status": status,
        "safe_to_run": status != "BLOCK",
        "command_source": command_source,
        "invocations_analysed": invocation_count,
        "errors": errors,
        "warnings": warnings,
        "autofix_suggestions": autofixes,
    }


def build_rewrite_text(
    findings: List[LintFinding],
    command_source: str,
) -> str:
    lines: List[str] = [
        "# Tool Command Linter — Rewrite Suggestions",
        f"# Source: {command_source}",
        "",
    ]
    actionable = [f for f in findings if f.autofix]
    if not actionable:
        lines.append("# No autofix suggestions available.")
        return "\n".join(lines)

    for finding in actionable:
        lines.append(f"# [{finding.rule_id}] {finding.message}")
        if finding.fragment and finding.fragment != "(command file level)":
            lines.append(f"# Fragment: {finding.fragment}")
        lines.append(f"# Suggestion: {finding.autofix}")
        lines.append("")

    lines.append("# NOTE: This file lists suggestions only. No original command was modified.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Lint Aletheia tool commands for unsafe or invalid patterns. "
            "Never executes the command being linted."
        )
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--command", metavar="CMD",
        help="Inline command string to lint.",
    )
    source_group.add_argument(
        "--command-file", metavar="PATH",
        help="Path to a command file (.ps1, .sh, or plain text) to lint.",
    )
    parser.add_argument(
        "--config", metavar="PATH",
        help="Optional JSON config file (may disable rules or add custom patterns).",
    )
    parser.add_argument(
        "--out", default="command_lint_report.json",
        help="JSON report output path (default: command_lint_report.json).",
    )
    parser.add_argument(
        "--rewrite-out", metavar="PATH", dest="rewrite_out",
        help="Optional path to write human-readable rewrite suggestions. "
             "Original command/file is never modified.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # --- Load optional config ---
    disabled_rules: frozenset = frozenset()
    extra_reingestion: List[str] = []

    if args.config:
        try:
            cfg = load_json_config(Path(args.config))
        except (FileNotFoundError, ConfigError) as exc:
            print(f"Error loading config: {exc}", file=sys.stderr)
            return 1
        disabled_raw = cfg.get("disabled_rules", [])
        disabled_rules = frozenset(str(r) for r in disabled_raw)
        extra_reingestion = cfg.get("extra_reingestion_patterns", [])
        if extra_reingestion:
            global _REINGESTION_RE
            combined = _REINGESTION_PATTERNS + extra_reingestion
            _REINGESTION_RE = re.compile("|".join(combined), re.IGNORECASE)

    # --- Load command text ---
    if args.command:
        command_text = args.command
        command_source = "inline"
        is_file = False
    else:
        cmd_path = Path(args.command_file)
        if not cmd_path.exists():
            print(f"Error: command file not found: {cmd_path}", file=sys.stderr)
            return 1
        command_text = cmd_path.read_text(encoding="utf-8", errors="replace")
        command_source = cmd_path.name
        is_file = True

    # --- Parse ---
    logical_lines = _normalise_command_text(command_text)
    invocations = extract_invocations(logical_lines)

    # --- Apply rules ---
    findings = _apply_rules(invocations, is_file=is_file, disabled_rules=disabled_rules)
    status = _determine_status(findings)
    report = build_report(findings, command_source, status, len(invocations))

    # --- Write JSON report ---
    write_json_report(report, Path(args.out))

    # --- Write rewrite suggestions (optional, never modifies original) ---
    if args.rewrite_out:
        rewrite_text = build_rewrite_text(findings, command_source)
        Path(args.rewrite_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.rewrite_out).write_text(rewrite_text, encoding="utf-8")
        print(f"Rewrite suggestions written to: {args.rewrite_out}")

    print(f"Lint status: {status} "
          f"({len([f for f in findings if f.level == 'error'])} errors, "
          f"{len([f for f in findings if f.level == 'warning'])} warnings)")

    return 2 if status == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
