"""Tool registry mapping wrapper action names to Aletheia tool entries."""

import pathlib
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class ToolEntry:
    """Immutable descriptor for one registered wrapper action."""

    action: str
    canonical_tool_name: str = ""
    script_name: str = ""
    working_directory: str = "aletheia_toolchain"
    allowed_outputs: Sequence[str] = field(default_factory=tuple)
    exit_code_map: Mapping[int, str] = field(default_factory=dict)
    io_requirements: Mapping[str, object] = field(default_factory=dict)
    write_permissions: Sequence[str] = field(default_factory=tuple)
    timeout_seconds: int = 60
    requires_review_approval: bool = False
    build_flags: Callable = lambda params, session_dir: []  # (params: dict, session_dir: Path) -> List[str]
    collect_artifacts: Callable = lambda params, session_dir: {}  # (params: dict, session_dir: Path) -> dict
    primary_json_report_key: Optional[str] = None  # session["artifacts"] key for status read-back


# ---------------------------------------------------------------------------
# Flag builders — return the flags list (not the full argv)
# ---------------------------------------------------------------------------

def _scan_flags(params: dict, session_dir: pathlib.Path) -> List[str]:
    target = params.get("target_repo")
    if not target:
        raise ValueError("scan_directory requires 'target_repo' in params")
    d = session_dir / "intermediate"
    return [
        "--roots", str(target),
        "--out", str(d / "file_map.csv"),
        "--health-report", str(d / "file_map_health.json"),
        "--hash",
        "--profile", str(params.get("profile", "safe")),
    ]


def _doctor_flags(params: dict, session_dir: pathlib.Path) -> List[str]:
    csv_path = params.get("manifest_csv")
    if not csv_path:
        raise ValueError("validate_manifest requires 'manifest_csv' in params")
    d = session_dir / "intermediate"
    return [
        "--manifest", str(csv_path),
        "--out", str(d / "manifest_doctor.json"),
        "--markdown-out", str(d / "manifest_doctor.md"),
    ]


def _linter_flags(params: dict, session_dir: pathlib.Path) -> List[str]:
    command = params.get("command")
    if not command:
        raise ValueError("lint_tool_command requires 'command' in params")
    d = session_dir / "intermediate"
    return [
        "--command", str(command),
        "--out", str(d / "command_lint.json"),
    ]


def _slicer_flags(params: dict, session_dir: pathlib.Path) -> List[str]:
    csv_path = params.get("manifest_csv")
    target = params.get("target_repo")
    if not csv_path:
        raise ValueError("run_semantic_slice requires 'manifest_csv' in params")
    if not target:
        raise ValueError("run_semantic_slice requires 'target_repo' in params")
    d = session_dir / "intermediate"
    return [
        "--manifest", str(csv_path),
        "-o", str(d / "semantic_slice.json"),
        "--base-dir", str(target),
        "--format", "json",
        "--deterministic",
    ]


# ---------------------------------------------------------------------------
# Artifact collectors — check which output files exist and return their paths
# ---------------------------------------------------------------------------

def _scan_artifacts(params: dict, session_dir: pathlib.Path) -> dict:
    d = session_dir / "intermediate"
    return {
        key: str(path) if path.exists() else ""
        for key, path in [
            ("manifest_csv",         d / "file_map.csv"),
            ("manifest_health_json", d / "file_map_health.json"),
        ]
    }


def _doctor_artifacts(params: dict, session_dir: pathlib.Path) -> dict:
    d = session_dir / "intermediate"
    return {
        key: str(path) if path.exists() else ""
        for key, path in [
            ("manifest_doctor_json", d / "manifest_doctor.json"),
            ("manifest_doctor_md",   d / "manifest_doctor.md"),
        ]
    }


def _linter_artifacts(params: dict, session_dir: pathlib.Path) -> dict:
    d = session_dir / "intermediate"
    p = d / "command_lint.json"
    return {"command_lint_json": str(p) if p.exists() else ""}


def _slicer_artifacts(params: dict, session_dir: pathlib.Path) -> dict:
    d = session_dir / "intermediate"
    p = d / "semantic_slice.json"
    return {"slicer_json": str(p) if p.exists() else ""}




def _noop_flags(params: dict, session_dir: pathlib.Path) -> List[str]:
    _ = (params, session_dir)
    return []


def _noop_artifacts(params: dict, session_dir: pathlib.Path) -> dict:
    _ = (params, session_dir)
    return {}


def _entry(action: str, script_name: str, timeout_seconds: int, requires_review_approval: bool,
           build_flags: Callable, collect_artifacts: Callable, primary_json_report_key: Optional[str]) -> ToolEntry:
    return ToolEntry(
        action=action,
        canonical_tool_name=action,
        script_name=script_name,
        working_directory="aletheia_toolchain",
        allowed_outputs=("json", "md", "csv"),
        exit_code_map={0: "ok", 1: "error", 2: "usage_error"},
        io_requirements={"requires_input": True, "emits_output": True},
        write_permissions=("session_dir/intermediate",),
        timeout_seconds=timeout_seconds,
        requires_review_approval=requires_review_approval,
        build_flags=build_flags,
        collect_artifacts=collect_artifacts,
        primary_json_report_key=primary_json_report_key,
    )

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: Dict[str, ToolEntry] = {
    "scan_directory": _entry("scan_directory", "create_file_map_v3.py", 120, False, _scan_flags, _scan_artifacts, "manifest_health_json"),
    "validate_manifest": _entry("validate_manifest", "manifest_doctor.py", 60, False, _doctor_flags, _doctor_artifacts, "manifest_doctor_json"),
    "lint_tool_command": _entry("lint_tool_command", "tool_command_linter.py", 30, False, _linter_flags, _linter_artifacts, "command_lint_json"),
    "run_semantic_slice": _entry("run_semantic_slice", "semantic_slicer_v7.0.py", 300, True, _slicer_flags, _slicer_artifacts, "slicer_json"),
    "validate_architecture": _entry("validate_architecture", "architecture_validator.py", 120, False, _noop_flags, _noop_artifacts, None),
    "gate_pipeline": _entry("gate_pipeline", "pipeline_gatekeeper.py", 120, True, _noop_flags, _noop_artifacts, None),
    "audit_bundle_diff": _entry("audit_bundle_diff", "bundle_diff_auditor.py", 120, False, _noop_flags, _noop_artifacts, None),
    "package_workspace": _entry("package_workspace", "workspace_packager_v2.4.py", 180, False, _noop_flags, _noop_artifacts, None),
    "package_notebook": _entry("package_notebook", "notebook_packager_v3.1.py", 180, False, _noop_flags, _noop_artifacts, None),
    "watch_runtime_end": _entry("watch_runtime_end", "runtime_end_watcher.py", 180, False, _noop_flags, _noop_artifacts, None),
    "report_oom_forensics": _entry("report_oom_forensics", "oom_forensics_reporter.py", 180, False, _noop_flags, _noop_artifacts, None),
    "correlate_runtime_slice": _entry("correlate_runtime_slice", "runtime_slice_correlator.py", 180, False, _noop_flags, _noop_artifacts, None),
    "package_runtime": _entry("package_runtime", "runtime_packager.py", 180, False, _noop_flags, _noop_artifacts, None),
}


#: Frozenset of script filenames that the runner is allowed to execute.
ALLOWED_SCRIPT_NAMES: frozenset = frozenset(e.script_name for e in REGISTRY.values())


def get_entry(action_name: str) -> ToolEntry:
    """Return the :class:`ToolEntry` for *action_name*.

    Raises :exc:`ValueError` for unregistered action names.
    """
    if action_name not in REGISTRY:
        raise ValueError(
            f"Unknown action: {action_name!r}. Allowed: {sorted(REGISTRY)}"
        )
    return REGISTRY[action_name]
