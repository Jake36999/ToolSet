"""Session creation, persistence, and review-state management for Local Tool Assist."""

import datetime
from dataclasses import dataclass
import os
import pathlib
import random
import re
import string

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_HERE = pathlib.Path(__file__).parent
_TOOLSET_ROOT = _HERE.parent

#: Default root for all generated session artifacts.
DEFAULT_OUTPUT_ROOT: pathlib.Path = _TOOLSET_ROOT / "local_tool_assist_outputs"

#: Path to the managed Aletheia toolchain — used as cwd for subprocesses.
TOOLCHAIN_ROOT: pathlib.Path = _TOOLSET_ROOT / "aletheia_toolchain"

#: Subdirectories created under the output root on every session.
_OUTPUT_SUBDIRS = ("sessions", "reports", "bundles", "archive")

_SESSION_ID_RE = re.compile(r"^lta_[0-9]{8}T[0-9]{6}Z_[a-z0-9]{6}$")

_REVIEW_STATE_FIELDS = frozenset({
    "scan_reviewed",
    "manifest_reviewed",
    "slice_approved",
    "approved_by",
    "approved_at",
    "approval_notes",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_output_root(output_root=None) -> pathlib.Path:
    if output_root is not None:
        return pathlib.Path(output_root)
    env = os.environ.get("LTA_OUTPUT_ROOT")
    if env:
        return pathlib.Path(env)
    return DEFAULT_OUTPUT_ROOT


def _is_relative_to(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class SessionPaths:
    toolset_root: pathlib.Path
    toolchain_root: pathlib.Path
    output_root: pathlib.Path
    sessions_root: pathlib.Path
    reports_root: pathlib.Path
    bundles_root: pathlib.Path
    archive_root: pathlib.Path

    def __post_init__(self):
        toolset_root = pathlib.Path(self.toolset_root).resolve()
        toolchain_root = pathlib.Path(self.toolchain_root).resolve()
        output_root = pathlib.Path(self.output_root).resolve()
        sessions_root = pathlib.Path(self.sessions_root).resolve()
        reports_root = pathlib.Path(self.reports_root).resolve()
        bundles_root = pathlib.Path(self.bundles_root).resolve()
        archive_root = pathlib.Path(self.archive_root).resolve()

        if toolchain_root.name != "aletheia_toolchain":
            raise ValueError("toolchain_root.name must be 'aletheia_toolchain'")
        if _is_relative_to(output_root, toolchain_root):
            raise ValueError("output_root must be outside toolchain_root")

        for label, path in (
            ("sessions_root", sessions_root),
            ("reports_root", reports_root),
            ("bundles_root", bundles_root),
            ("archive_root", archive_root),
        ):
            if _is_relative_to(path, toolchain_root):
                raise ValueError(f"{label} must be outside toolchain_root")

        object.__setattr__(self, "toolset_root", toolset_root)
        object.__setattr__(self, "toolchain_root", toolchain_root)
        object.__setattr__(self, "output_root", output_root)
        object.__setattr__(self, "sessions_root", sessions_root)
        object.__setattr__(self, "reports_root", reports_root)
        object.__setattr__(self, "bundles_root", bundles_root)
        object.__setattr__(self, "archive_root", archive_root)


def build_session_paths(output_root=None) -> SessionPaths:
    root = _resolve_output_root(output_root)
    return SessionPaths(
        toolset_root=_TOOLSET_ROOT,
        toolchain_root=TOOLCHAIN_ROOT,
        output_root=root,
        sessions_root=root / "sessions",
        reports_root=root / "reports",
        bundles_root=root / "bundles",
        archive_root=root / "archive",
    )


def _now_str() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_session_dict(
    session_id: str,
    objective: str,
    target_repo: str,
    requested_by: str,
    downstream_agent: str,
) -> dict:
    now = _now_str()
    return {
        "schema_version": "LocalToolAssistSession/v1.0",
        "session_id": session_id,
        "created_at": now,
        "updated_at": now,
        "request": {
            "objective": objective,
            "target_repo": str(target_repo),
            "requested_by": requested_by,
            "downstream_agent": downstream_agent,
        },
        "execution_mode": {
            "autonomy": "guided",
            "require_user_review_before_refine": True,
            "allow_command_execution": False,
        },
        "review_state": {
            "scan_reviewed": False,
            "manifest_reviewed": False,
            "slice_approved": False,
            "approved_by": "",
            "approved_at": "",
            "approval_notes": "",
        },
        "policy": {
            "forbid_shell": True,
            "outputs_must_be_outside_toolchain": True,
            "require_manifest_before_slice": True,
            "require_linter_for_generated_commands": True,
            "require_review_before_slice": True,
            "approved_tools": [
                "create_file_map_v3",
                "manifest_doctor",
                "tool_command_linter",
                "semantic_slicer_v7.0",
            ],
        },
        "artifacts": {
            "manifest_csv": "",
            "manifest_health_json": "",
            "manifest_doctor_json": "",
            "manifest_doctor_md": "",
            "command_lint_json": "",
            "slicer_json": "",
            "slicer_md": "",
            "final_markdown": "",
            "final_python_bundle": "",
            "archive_yaml": "",
        },
        "steps": [],
        "redaction": {
            "enabled": True,
            "fingerprint_algorithm": "sha1",
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_session_id() -> str:
    """Return a unique session ID of the form ``lta_YYYYMMDDTHHMMSSZ_xxxxxx``."""
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    short = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"lta_{ts}_{short}"


def create_session(
    objective: str,
    target_repo: str,
    requested_by: str = "user",
    downstream_agent: str = "unknown",
    output_root=None,
) -> tuple:
    """Create a new session directory and session dict.

    Returns ``(session_dict, session_dir)`` where *session_dir* is a
    :class:`pathlib.Path`.  The output root and all five standard
    subdirectories are created with ``exist_ok=True``.
    """
    paths = build_session_paths(output_root)
    for subdir in (paths.sessions_root, paths.reports_root, paths.bundles_root, paths.archive_root):
        subdir.mkdir(parents=True, exist_ok=True)

    session_id = generate_session_id()
    session_dir = paths.sessions_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    return _build_session_dict(session_id, objective, target_repo, requested_by, downstream_agent), session_dir


def save_session(session_dict: dict, yaml_path) -> None:
    """Write *session_dict* to a YAML file at *yaml_path*."""
    if not _HAS_YAML:
        raise ImportError(
            "PyYAML is required for save_session. Install with: pip install PyYAML"
        )
    path = pathlib.Path(yaml_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        _yaml.dump(
            session_dict,
            fh,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


def load_session(yaml_path) -> dict:
    """Read and return a session dict from a YAML file."""
    if not _HAS_YAML:
        raise ImportError(
            "PyYAML is required for load_session. Install with: pip install PyYAML"
        )
    with open(yaml_path, "r", encoding="utf-8") as fh:
        return _yaml.safe_load(fh)


def update_review_state(session_dict: dict, **kwargs) -> dict:
    """Update ``review_state`` fields in-place.

    Returns the same *session_dict* (mutated).  Raises :exc:`ValueError` for
    unrecognised field names.
    """
    unknown = set(kwargs) - _REVIEW_STATE_FIELDS
    if unknown:
        raise ValueError(f"Unknown review_state field(s): {sorted(unknown)}")
    session_dict["review_state"].update(kwargs)
    session_dict["updated_at"] = _now_str()
    return session_dict
