"""
Architecture Validator — Phase 7 of the Aletheia Toolchain Upgrade.

Reads a semantic slicer JSON bundle, resolves a config/profile, evaluates
generic architecture/risk rules, and writes JSON plus optional Markdown reports.

Supported bundle formats:
  - semantic_slicer_v7.0.py JSON output (agnostic_bundle_v7.0_phase6)
  - semantic_slicer_v6.0.py JSON output (agnostic_bundle_v5.6_snapshot_r3)
  - The aletheia_toolchain synthetic fixture format (tests/fixtures/)

All rules are driven by config/profile data. No project-specific logic is
hard-coded inside this module.

Exit codes:
  0 — PASS or WARN
  1 — invocation error (bad args, unreadable files)
  2 — FAIL (one or more FAIL-severity findings)
"""

import argparse
import importlib
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional

from aletheia_tool_core.config import (
    ConfigError,
    load_json_config,
    validate_config,
    resolve_profile,
)
from aletheia_tool_core.reports import write_json_report, write_markdown_report


# ============================================================================
# CONSTANTS
# ============================================================================
VALIDATOR_VERSION = "1.0"
SEVERITY_ORDER = {"PASS": 0, "INFO": 1, "WARN": 2, "FAIL": 3}


# ============================================================================
# FINDING
# ============================================================================
class Finding:
    """Represents a single validation result."""

    def __init__(
        self,
        id: str,
        severity: str,
        message: str,
        file_path: Optional[str] = None,
        slice_id: Optional[str] = None,
        evidence: Optional[str] = None,
        recommendation: Optional[str] = None,
        confidence: str = "HIGH",
    ) -> None:
        if severity not in SEVERITY_ORDER:
            raise ValueError(f"severity must be one of {list(SEVERITY_ORDER)}, got {severity!r}")
        self.id = id
        self.severity = severity
        self.message = message
        self.file_path = file_path
        self.slice_id = slice_id
        self.evidence = evidence
        self.recommendation = recommendation
        self.confidence = confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "message": self.message,
            "file_path": self.file_path,
            "slice_id": self.slice_id,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
        }


# ============================================================================
# BUNDLE READER
# ============================================================================
class BundleReader:
    """Format-agnostic accessor over a loaded bundle dict.

    Handles both the v7.0 JSON structure (agnostic_bundle_v7.0_phase6) and the
    simplified synthetic fixture shape used in aletheia_toolchain/tests/fixtures/.
    """

    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data

    # ---- Metadata ----

    def get_schema_version(self) -> str:
        meta = self.data.get("meta", {})
        if isinstance(meta, dict):
            return str(meta.get("bundle_schema_version", "unknown"))
        return str(self.data.get("metadata", {}).get("version", "unknown"))

    def is_deterministic(self) -> Optional[bool]:
        meta = self.data.get("meta", {})
        if isinstance(meta, dict) and "deterministic" in meta:
            return bool(meta["deterministic"])
        meta2 = self.data.get("metadata", {})
        if isinstance(meta2, dict) and "deterministic_mode" in meta2:
            return bool(meta2["deterministic_mode"])
        return None

    def get_stats(self) -> Dict[str, Any]:
        meta = self.data.get("meta", {})
        if isinstance(meta, dict):
            stats = meta.get("stats")
            if isinstance(stats, dict):
                return stats
        return self.data.get("statistics", {})

    # ---- Files and paths ----

    def get_all_file_paths(self) -> List[str]:
        """Return every file path mentioned anywhere in the bundle."""
        paths: List[str] = []

        # v7: layer_2_intelligence list of {path: ...}
        intel = self.data.get("layer_2_intelligence", [])
        if isinstance(intel, list):
            for item in intel:
                if isinstance(item, dict) and item.get("path"):
                    p = item["path"]
                    if p not in paths:
                        paths.append(p)

        # v7: layer_3_full_files list
        for item in (self.data.get("layer_3_full_files") or []):
            if isinstance(item, dict) and item.get("path"):
                p = item["path"]
                if p not in paths:
                    paths.append(p)

        # sample fixture: layer_2_code_intelligence.slices[].file
        slices = (self.data.get("layer_2_code_intelligence") or {}).get("slices", [])
        for s in slices:
            if isinstance(s, dict) and s.get("file"):
                p = s["file"]
                if p not in paths:
                    paths.append(p)

        # sample fixture: layer_1_5_architecture_context.entry_points
        l15 = self.data.get("layer_1_5_architecture_context") or {}
        for ep in (l15.get("entry_points") or []):
            if ep not in paths:
                paths.append(ep)

        # v7: layer_1_8_entry_points
        for ep in (self.data.get("layer_1_8_entry_points") or []):
            if ep not in paths:
                paths.append(ep)

        return paths

    def get_entry_points(self) -> List[str]:
        """Return files identified as entry points."""
        # v7 format
        eps = self.data.get("layer_1_8_entry_points")
        if isinstance(eps, list):
            return eps
        # sample fixture format
        l15 = self.data.get("layer_1_5_architecture_context") or {}
        eps2 = l15.get("entry_points")
        if isinstance(eps2, list):
            return eps2
        return []

    def get_file_extensions(self) -> List[str]:
        return list({pathlib.Path(p).suffix.lower() for p in self.get_all_file_paths() if p})

    # ---- Imports ----

    def get_all_imports(self) -> List[str]:
        """Return deduplicated set of all imported module names."""
        imports: List[str] = []

        # v7: layer_1_7_import_graph is list of {path, imports}
        ig = self.data.get("layer_1_7_import_graph")
        if isinstance(ig, list):
            for item in ig:
                if isinstance(item, dict):
                    imports.extend(item.get("imports") or [])
        elif isinstance(ig, dict):
            # sample fixture: dict of {file: {imports: []}}
            for file_data in ig.values():
                if isinstance(file_data, dict):
                    imports.extend(file_data.get("imports") or [])

        # v7: layer_2_intelligence[].import_graph
        for item in (self.data.get("layer_2_intelligence") or []):
            if isinstance(item, dict):
                imports.extend(item.get("import_graph") or [])

        # sample: slices[].dependencies
        for s in (self.data.get("layer_2_code_intelligence") or {}).get("slices") or []:
            if isinstance(s, dict):
                imports.extend(s.get("dependencies") or [])

        return list(set(imports))

    def get_external_deps(self) -> Dict[str, List[str]]:
        arch = self.data.get("system_architecture_context") or {}
        deps = arch.get("external_dependencies") or {}
        if isinstance(deps, dict):
            return {
                "stdlib": list(deps.get("stdlib") or []),
                "third_party": list(deps.get("third_party") or []),
            }
        return {"stdlib": [], "third_party": []}

    # ---- Uncertainties ----

    def get_syntax_errors(self) -> List[str]:
        ux = self.data.get("layer_x_uncertainties") or {}
        if isinstance(ux, dict):
            errs = ux.get("syntax_errors")
            if isinstance(errs, list):
                return errs
        ver = (self.data.get("verification") or {}).get("syntax_errors")
        if isinstance(ver, list):
            return ver
        return []

    def get_dynamic_behaviors(self) -> List[Any]:
        """Return dynamic-behavior flags from Layer X."""
        ux = self.data.get("layer_x_uncertainties") or {}
        if isinstance(ux, dict):
            # v7 format: dynamic_behaviors list of {path, flags}
            dyn = ux.get("dynamic_behaviors")
            if isinstance(dyn, list):
                return dyn
            # sample fixture: separate keys
            items = []
            for key in ("dynamic_imports", "eval_exec_calls"):
                val = ux.get(key)
                if isinstance(val, list):
                    items.extend(val)
            return items
        return []

    def get_dynamic_call_names(self) -> List[str]:
        """Flatten dynamic behaviors into a list of call/pattern names."""
        names: List[str] = []
        for item in self.get_dynamic_behaviors():
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for flag in (item.get("flags") or []):
                    if isinstance(flag, dict):
                        names.append(flag.get("detail", ""))
                    else:
                        names.append(str(flag))
        return names

    # ---- Architecture context ----

    def get_system_contracts(self) -> List[str]:
        arch = self.data.get("system_architecture_context") or {}
        contracts = arch.get("system_contracts") or []
        return list(contracts) if isinstance(contracts, list) else []

    # ---- High-risk slices ----

    def get_high_risk_slice_ids(self) -> List[str]:
        """Return slice IDs with risk_level == 'CRITICAL' or 'HIGH'."""
        ids: List[str] = []
        # v7: layer_2_7_patch_collision_risk dict of {slice_id: {risk_level, ...}}
        risk = self.data.get("layer_2_7_patch_collision_risk") or {}
        if isinstance(risk, dict):
            for sid, meta in risk.items():
                if isinstance(meta, dict) and meta.get("risk_level") in ("CRITICAL", "HIGH"):
                    ids.append(sid)
        return ids


# ============================================================================
# RULE ENGINE
# ============================================================================
class RuleEngine:
    """Evaluates a set of generic architecture/risk rules against a bundle + config."""

    def __init__(
        self,
        bundle: BundleReader,
        config: Dict[str, Any],
        profile_settings: Dict[str, Any],
    ) -> None:
        self.bundle = bundle
        self.config = config
        self.profile = profile_settings
        self.arch_exp: Dict[str, Any] = config.get("architecture_expectations") or {}
        self.risk_rules: Dict[str, Any] = config.get("risk_rules") or {}
        self.findings: List[Finding] = []

    def _add(self, finding: Finding) -> None:
        self.findings.append(finding)

    # ---------------------------------------------------------------- R-AV001
    def check_required_paths(self) -> None:
        """R-AV001: Files listed in architecture_expectations.required_paths must appear in the bundle."""
        required = self.arch_exp.get("required_paths") or []
        if not required:
            return
        file_paths = self.bundle.get_all_file_paths()
        for req in required:
            found = any(req in p for p in file_paths)
            if not found:
                self._add(Finding(
                    id="R-AV001",
                    severity="WARN",
                    message=f"Required path '{req}' not found in bundle.",
                    evidence=f"Bundle file list: {file_paths[:10]}",
                    recommendation=f"Ensure '{req}' is included in the scan scope.",
                    confidence="HIGH",
                ))

    # ---------------------------------------------------------------- R-AV002
    def check_required_exts(self) -> None:
        """R-AV002: architecture_expectations.required_exts must be represented in the bundle."""
        required = self.arch_exp.get("required_exts") or []
        if not required:
            return
        present_exts = self.bundle.get_file_extensions()
        for req_ext in required:
            norm = req_ext.lower() if req_ext.startswith(".") else f".{req_ext.lower()}"
            if norm not in present_exts:
                self._add(Finding(
                    id="R-AV002",
                    severity="WARN",
                    message=f"Required extension '{req_ext}' not found in bundle.",
                    evidence=f"Extensions present: {sorted(present_exts)}",
                    recommendation=f"Add source files with extension '{req_ext}' to the scan scope.",
                    confidence="HIGH",
                ))

    # ---------------------------------------------------------------- R-AV003
    def check_required_entry_points(self) -> None:
        """R-AV003: architecture_expectations.required_entry_points must appear in the bundle."""
        required = self.arch_exp.get("required_entry_points") or []
        if not required:
            return
        all_paths = self.bundle.get_all_file_paths()
        entry_points = self.bundle.get_entry_points()
        for req in required:
            found = any(req in p for p in all_paths) or any(req in ep for ep in entry_points)
            if not found:
                self._add(Finding(
                    id="R-AV003",
                    severity="WARN",
                    message=f"Required entry point '{req}' not detected in bundle.",
                    evidence=f"Detected entry points: {entry_points}",
                    recommendation=f"Ensure '{req}' is scanned and identifiable as an entry point.",
                    confidence="MEDIUM",
                ))

    # ---------------------------------------------------------------- R-AV004
    def check_required_imports(self) -> None:
        """R-AV004: architecture_expectations.required_imports must appear in the bundle."""
        required = self.arch_exp.get("required_imports") or []
        if not required:
            return
        all_imports = self.bundle.get_all_imports()
        for req in required:
            found = any(req in imp for imp in all_imports)
            if not found:
                self._add(Finding(
                    id="R-AV004",
                    severity="WARN",
                    message=f"Required import '{req}' not found in bundle import graph.",
                    evidence=f"Imports found (sample): {sorted(all_imports)[:15]}",
                    recommendation=f"Add or expose the '{req}' import in the codebase.",
                    confidence="MEDIUM",
                ))

    # ---------------------------------------------------------------- R-AV005
    def check_forbidden_imports(self) -> None:
        """R-AV005: architecture_expectations.forbidden_imports must NOT appear in the bundle."""
        forbidden = self.arch_exp.get("forbidden_imports") or []
        if not forbidden:
            return
        all_imports = self.bundle.get_all_imports()
        for fb in forbidden:
            hits = [imp for imp in all_imports if fb in imp]
            if hits:
                self._add(Finding(
                    id="R-AV005",
                    severity="FAIL",
                    message=f"Forbidden import '{fb}' detected in bundle.",
                    evidence=f"Matched imports: {hits}",
                    recommendation=f"Remove or replace all uses of '{fb}'.",
                    confidence="HIGH",
                ))

    # ---------------------------------------------------------------- R-AV006
    def check_forbidden_runtime_behaviours(self) -> None:
        """R-AV006: risk_rules.forbidden_behaviors must not appear in Layer X dynamic calls."""
        forbidden = self.risk_rules.get("forbidden_behaviors") or []
        if not forbidden:
            return
        dynamic_calls = self.bundle.get_dynamic_call_names()
        for fb in forbidden:
            hits = [c for c in dynamic_calls if fb.lower() in c.lower()]
            if hits:
                self._add(Finding(
                    id="R-AV006",
                    severity="FAIL",
                    message=f"Forbidden runtime behaviour '{fb}' detected in Layer X.",
                    evidence=f"Layer X flags: {hits}",
                    recommendation=f"Eliminate all uses of '{fb}' from the codebase.",
                    confidence="HIGH",
                ))

    # ---------------------------------------------------------------- R-AV007
    def check_required_output_style(self) -> None:
        """R-AV007: architecture_expectations.required_output_style.deterministic, if set,
        must match the bundle's deterministic flag."""
        style = self.arch_exp.get("required_output_style") or {}
        if not style:
            return
        if "deterministic" in style:
            required_det = bool(style["deterministic"])
            actual_det = self.bundle.is_deterministic()
            if actual_det is None:
                self._add(Finding(
                    id="R-AV007",
                    severity="INFO",
                    message="Could not determine whether the bundle was produced deterministically.",
                    evidence="Bundle metadata lacks a clear 'deterministic' field.",
                    recommendation="Reproduce the bundle with --deterministic for auditable output.",
                    confidence="LOW",
                ))
            elif required_det and not actual_det:
                self._add(Finding(
                    id="R-AV007",
                    severity="WARN",
                    message="Bundle was not produced with --deterministic but config requires deterministic output.",
                    evidence=f"Bundle deterministic={actual_det}; required deterministic={required_det}",
                    recommendation="Re-run slicer with --deterministic.",
                    confidence="HIGH",
                ))

    # ---------------------------------------------------------------- R-AV008
    def check_layer_x_uncertainty_policy(self) -> None:
        """R-AV008: risk_rules.max_uncertainties — warn when Layer X dynamic behavior count is high."""
        max_ux = self.risk_rules.get("max_uncertainties")
        dynamic = self.bundle.get_dynamic_behaviors()
        dyn_count = len(dynamic)
        syntax_errors = self.bundle.get_syntax_errors()
        err_count = len(syntax_errors)

        # Always surface syntax errors as WARN
        if err_count > 0:
            self._add(Finding(
                id="R-AV008a",
                severity="WARN",
                message=f"{err_count} syntax error(s) in bundle — AST slices may be incomplete.",
                evidence=f"Syntax errors: {syntax_errors[:5]}",
                recommendation="Fix syntax errors so the slicer can produce complete analysis.",
                confidence="HIGH",
            ))

        # Check dynamic-behavior threshold
        if max_ux is not None and dyn_count > int(max_ux):
            self._add(Finding(
                id="R-AV008b",
                severity="WARN",
                message=f"Layer X dynamic behavior count ({dyn_count}) exceeds threshold ({max_ux}).",
                evidence=f"Dynamic items: {dynamic[:5]}",
                recommendation="Reduce use of eval/exec/dynamic imports to improve static analysis coverage.",
                confidence="HIGH",
            ))

    # ---------------------------------------------------------------- R-AV009
    def check_memory_performance_patterns(self) -> None:
        """R-AV009: flag an elevated number of CRITICAL/HIGH risk slices."""
        max_high = self.risk_rules.get("max_high_risk_slices")
        if max_high is None:
            return
        high_risk = self.bundle.get_high_risk_slice_ids()
        count = len(high_risk)
        if count > int(max_high):
            self._add(Finding(
                id="R-AV009",
                severity="WARN",
                message=f"{count} CRITICAL/HIGH-risk slices exceed the threshold of {max_high}.",
                evidence=f"High-risk slice IDs (sample): {high_risk[:5]}",
                recommendation="Review high-risk slices for safe refactoring opportunities.",
                confidence="MEDIUM",
            ))

    # ---------------------------------------------------------------- R-AV010
    def check_required_contracts(self) -> None:
        """R-AV010: architecture_expectations.required_contracts (keyword list) must
        appear somewhere in system_contracts."""
        required = self.arch_exp.get("required_contracts") or []
        if not required:
            return
        contracts = self.bundle.get_system_contracts()
        contract_text = " ".join(contracts).lower()
        for keyword in required:
            if keyword.lower() not in contract_text:
                self._add(Finding(
                    id="R-AV010",
                    severity="INFO",
                    message=f"Required contract keyword '{keyword}' not found in system contracts.",
                    evidence=f"System contracts (sample): {contracts[:5]}",
                    recommendation=f"Add assertions or guards that capture the '{keyword}' contract.",
                    confidence="LOW",
                ))

    def run_all(self) -> List[Finding]:
        self.check_required_paths()
        self.check_required_exts()
        self.check_required_entry_points()
        self.check_required_imports()
        self.check_forbidden_imports()
        self.check_forbidden_runtime_behaviours()
        self.check_required_output_style()
        self.check_layer_x_uncertainty_policy()
        self.check_memory_performance_patterns()
        self.check_required_contracts()
        return self.findings


# ============================================================================
# PLUGIN SYSTEM
# ============================================================================
def run_plugins(config: Dict[str, Any], bundle_reader: BundleReader) -> List[Finding]:
    """Load and execute optional plugin modules listed in config['plugins'].

    Plugin failures are isolated: any ImportError or exception becomes a WARN finding.
    A plugin module must expose ``run_checks(bundle_reader, config) -> List[dict]``, where
    each dict has at minimum ``id``, ``severity``, and ``message`` keys.
    """
    plugins: List[str] = config.get("plugins") or []
    findings: List[Finding] = []
    if not plugins:
        return findings

    for plugin_path in plugins:
        try:
            spec = importlib.util.spec_from_file_location("_av_plugin", plugin_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load spec from: {plugin_path}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            raw_findings = mod.run_checks(bundle_reader, config)
            for rf in (raw_findings or []):
                if isinstance(rf, dict):
                    findings.append(Finding(
                        id=str(rf.get("id", "PLUGIN")),
                        severity=str(rf.get("severity", "INFO")),
                        message=str(rf.get("message", "(no message)")),
                        file_path=rf.get("file_path"),
                        slice_id=rf.get("slice_id"),
                        evidence=rf.get("evidence"),
                        recommendation=rf.get("recommendation"),
                        confidence=str(rf.get("confidence", "LOW")),
                    ))
        except Exception as exc:
            findings.append(Finding(
                id="PLUGIN-ERROR",
                severity="WARN",
                message=f"Plugin '{plugin_path}' failed and was skipped: {exc}",
                recommendation="Check plugin module path and interface.",
                confidence="HIGH",
            ))
    return findings


# ============================================================================
# REPORT BUILDER
# ============================================================================
def _top_level_status(findings: List[Finding]) -> str:
    best = "PASS"
    for f in findings:
        if SEVERITY_ORDER.get(f.severity, 0) > SEVERITY_ORDER.get(best, 0):
            best = f.severity
    if best in ("PASS", "INFO"):
        return "PASS"
    return best  # "WARN" or "FAIL"


def build_report(
    findings: List[Finding],
    bundle_path: str,
    config_path: Optional[str],
    profile_name: Optional[str],
    bundle_schema_version: str,
) -> Dict[str, Any]:
    status = _top_level_status(findings)
    severity_counts: Dict[str, int] = {"PASS": 0, "INFO": 0, "WARN": 0, "FAIL": 0}
    for f in findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

    return {
        "validator_version": VALIDATOR_VERSION,
        "status": status,
        "bundle": bundle_path,
        "bundle_schema_version": bundle_schema_version,
        "config": config_path,
        "profile": profile_name,
        "findings_count": len(findings),
        "severity_counts": severity_counts,
        "findings": [f.to_dict() for f in findings],
    }


def _markdown_sections(report: Dict[str, Any]) -> Dict[str, Any]:
    sections: Dict[str, Any] = {
        "Architecture Validator Report": (
            f"**Status**: {report['status']}  \n"
            f"**Bundle**: {report['bundle']}  \n"
            f"**Config**: {report.get('config', '(none)')}  \n"
            f"**Profile**: {report.get('profile', '(none)')}  \n"
            f"**Bundle schema version**: {report.get('bundle_schema_version', 'unknown')}  \n"
            f"**Findings**: {report['findings_count']}"
        ),
        "Severity Summary": report["severity_counts"],
    }
    if report["findings"]:
        lines = []
        for f in report["findings"]:
            lines.append(
                f"**[{f['severity']}]** `{f['id']}` — {f['message']}"
                + (f" *(confidence: {f['confidence']})*" if f.get("confidence") else "")
            )
            if f.get("evidence"):
                lines.append(f"  - *Evidence*: {f['evidence']}")
            if f.get("recommendation"):
                lines.append(f"  - *Recommendation*: {f['recommendation']}")
        sections["Findings"] = lines
    else:
        sections["Findings"] = ["No findings — architecture checks passed cleanly."]
    return sections


# ============================================================================
# MAIN
# ============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Architecture Validator {VALIDATOR_VERSION} — evaluates slicer bundles against config rules."
    )
    parser.add_argument("--bundle", required=True, help="Path to a slicer JSON bundle file.")
    parser.add_argument("--config", help="Path to a semantic_project_config JSON file.")
    parser.add_argument("--profile", help="Named profile to resolve from --config.")
    parser.add_argument("--out", required=True, help="Path for the JSON validation report.")
    parser.add_argument("--markdown-out", help="Optional path for a Markdown report.")
    args = parser.parse_args()

    # ---- Load bundle ----
    bundle_path = pathlib.Path(args.bundle)
    if not bundle_path.exists():
        sys.exit(f"ERROR: Bundle file not found: {bundle_path}")
    try:
        bundle_data = json.loads(bundle_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: Bundle is not valid JSON: {exc}")
    if not isinstance(bundle_data, dict):
        sys.exit("ERROR: Bundle root must be a JSON object.")

    bundle_reader = BundleReader(bundle_data)

    # ---- Load config ----
    cfg_data: Dict[str, Any] = {}
    if args.config:
        try:
            cfg_data = load_json_config(pathlib.Path(args.config))
            validate_config(cfg_data)
        except FileNotFoundError as exc:
            sys.exit(f"ERROR: Config file not found: {exc}")
        except ConfigError as exc:
            sys.exit(f"ERROR: Config validation failed: {exc}")

    # ---- Resolve profile ----
    profile_settings: Dict[str, Any] = {}
    if args.profile and cfg_data:
        try:
            profile_settings = resolve_profile(cfg_data, args.profile)
        except ConfigError as exc:
            sys.exit(f"ERROR: {exc}")
    elif args.profile and not cfg_data:
        sys.exit("ERROR: --profile requires --config.")

    # ---- Run rules ----
    engine = RuleEngine(bundle_reader, cfg_data, profile_settings)
    findings = engine.run_all()

    # ---- Run plugins ----
    findings.extend(run_plugins(cfg_data, bundle_reader))

    # ---- Build and write report ----
    report = build_report(
        findings=findings,
        bundle_path=str(bundle_path),
        config_path=args.config,
        profile_name=args.profile,
        bundle_schema_version=bundle_reader.get_schema_version(),
    )

    out_path = pathlib.Path(args.out)
    write_json_report(report, out_path)
    print(f"Architecture Validator status: {report['status']}")
    print(f"Report written: {out_path}")

    if args.markdown_out:
        md_path = pathlib.Path(args.markdown_out)
        write_markdown_report(
            title="Architecture Validator Report",
            sections=_markdown_sections(report),
            out_path=md_path,
        )
        print(f"Markdown report written: {md_path}")

    if report["status"] == "FAIL":
        sys.exit(2)


if __name__ == "__main__":
    main()
