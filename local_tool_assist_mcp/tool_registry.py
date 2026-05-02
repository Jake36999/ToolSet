"""Tool registry mapping wrapper action names to Aletheia tool entries."""

import pathlib
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class ToolEntry:
    """Immutable descriptor for one registered wrapper action."""

    action: str
    script_name: str
    timeout_seconds: int
    requires_review_approval: bool
    build_flags: Callable  # (params: dict, session_dir: Path) -> List[str]
    collect_artifacts: Callable  # (params: dict, session_dir: Path) -> dict
    primary_json_report_key: Optional[str]  # session["artifacts"] key for status read-back


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


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: Dict[str, ToolEntry] = {
    "scan_directory": ToolEntry(
        action="scan_directory",
        script_name="create_file_map_v3.py",
        timeout_seconds=120,
        requires_review_approval=False,
        build_flags=_scan_flags,
        collect_artifacts=_scan_artifacts,
        primary_json_report_key="manifest_health_json",
    ),
    "validate_manifest": ToolEntry(
        action="validate_manifest",
        script_name="manifest_doctor.py",
        timeout_seconds=60,
        requires_review_approval=False,
        build_flags=_doctor_flags,
        collect_artifacts=_doctor_artifacts,
        primary_json_report_key="manifest_doctor_json",
    ),
    "lint_tool_command": ToolEntry(
        action="lint_tool_command",
        script_name="tool_command_linter.py",
        timeout_seconds=30,
        requires_review_approval=False,
        build_flags=_linter_flags,
        collect_artifacts=_linter_artifacts,
        primary_json_report_key="command_lint_json",
    ),
    "run_semantic_slice": ToolEntry(
        action="run_semantic_slice",
        script_name="semantic_slicer_v7.0.py",
        timeout_seconds=300,
        requires_review_approval=True,
        build_flags=_slicer_flags,
        collect_artifacts=_slicer_artifacts,
        primary_json_report_key="slicer_json",
    ),
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
