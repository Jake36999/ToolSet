"""Centralized policy checks for Local Tool Assist MCP."""

from __future__ import annotations

import os
import pathlib
import re
from dataclasses import asdict, dataclass
from typing import Optional

from local_tool_assist_mcp.session import TOOLCHAIN_ROOT

_SECRET_PATTERNS = [
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"(?i)token\s*[:=]\s*\S+"),
    re.compile(r"(?i)secret\s*[:=]\s*\S+"),
]


@dataclass(frozen=True)
class PolicyError(Exception):
    code: str
    message: str
    blocked: bool = True

    def to_result(self) -> dict:
        return {"policy": asdict(self)}


def _is_subpath(child: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def ensure_no_arbitrary_shell(argv: list[str], allowed_scripts: set[str]) -> None:
    script = pathlib.Path(argv[1]).name if len(argv) > 1 else ""
    if script not in allowed_scripts:
        raise PolicyError("NO_ARBITRARY_SHELL", f"Script not allowed: {script}")


def ensure_session_owned_read(path: pathlib.Path, output_root: pathlib.Path, session_dir: Optional[pathlib.Path] = None) -> None:
    if not _is_subpath(path, output_root):
        raise PolicyError("SESSION_OWNED_READ_ONLY", f"Path outside output root: {path}")
    if _is_subpath(path, TOOLCHAIN_ROOT):
        raise PolicyError("NO_TOOLCHAIN_READ", f"Path inside toolchain: {path}")
    if session_dir is not None and not _is_subpath(path, session_dir):
        raise PolicyError("SESSION_OWNED_READ_ONLY", f"Path outside session scope: {path}")


def ensure_outputs_outside_toolchain(path: pathlib.Path) -> None:
    if _is_subpath(path, TOOLCHAIN_ROOT):
        raise PolicyError("NO_OUTPUTS_IN_TOOLCHAIN", f"Output path inside toolchain: {path}")


def ensure_slice_prereqs(session_dict: dict, dev_mode: bool, params: Optional[dict] = None) -> None:
    params = params or {}
    artifacts = session_dict.get("artifacts", {})
    if not (artifacts.get("manifest_csv") or params.get("manifest_csv")):
        raise PolicyError("MANIFEST_REQUIRED", "manifest_csv artifact is required before slicing")

    doctor_path = artifacts.get("manifest_doctor_json", "")
    if not doctor_path:
        raise PolicyError("DOCTOR_REQUIRED", "manifest doctor report required before slicing")
    status = session_dict.get("latest", {}).get("manifest_doctor_status")
    if status not in {"PASS", "WARN"}:
        raise PolicyError("DOCTOR_PASS_WARN_REQUIRED", "doctor status must be PASS/WARN before slicing")

    approved = session_dict.get("review_state", {}).get("slice_approved", False)
    if not approved and not dev_mode:
        raise PolicyError("REVIEW_APPROVAL_REQUIRED", "review_state.slice_approved must be true before slicing")


def record_doctor_status(session_dict: dict, status: str) -> None:
    session_dict.setdefault("latest", {})["manifest_doctor_status"] = status


def ensure_remote_mcp_auth(remote_url: str) -> None:
    if not remote_url:
        return
    dev_mode = os.environ.get("LTA_DEV_MODE", "").strip() == "1"
    if dev_mode:
        return
    token = os.environ.get("LTA_MCP_AUTH_TOKEN", "").strip()
    if remote_url.startswith(("http://", "https://")) and not token:
        raise PolicyError("REMOTE_MCP_AUTH_REQUIRED", "Remote MCP requires auth token unless dev mode")


def ensure_no_secrets_in_text(text: str) -> None:
    for pat in _SECRET_PATTERNS:
        if pat.search(text or ""):
            raise PolicyError("SECRET_IN_ARTIFACT", "Potential key/token found in artifact text")
