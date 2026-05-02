"""Safe runner for Local Tool Assist wrapper actions.

Every Aletheia subprocess call goes through ``run_action``.  The runner:

- accepts only registered action names
- never uses ``shell=True``
- sets ``cwd`` to ``TOOLCHAIN_ROOT`` (or a test override)
- strips ``*_API_KEY``, ``*_TOKEN``, and ``*_SECRET`` from the child environment
- enforces the review gate before ``run_semantic_slice``
- returns a structured JSON-compatible result dict
- appends a step record to the session dict
"""

import datetime
import json
import os
import pathlib
import subprocess
import sys
from typing import Dict, Optional

from local_tool_assist_mcp.session import TOOLCHAIN_ROOT, save_session
from local_tool_assist_mcp.tool_registry import REGISTRY, ToolEntry

_TAIL_LINES = 50

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_str() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _tail(text: str, n: int = _TAIL_LINES) -> str:
    """Return the last *n* lines of *text* as a single string."""
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _build_safe_env() -> dict:
    """Return a copy of ``os.environ`` with credential variables stripped.

    Strips any variable whose uppercased name ends with
    ``_API_KEY``, ``_TOKEN``, or ``_SECRET``.
    """
    env = os.environ.copy()
    stripped = [
        k for k in env
        if k.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET"))
    ]
    for k in stripped:
        del env[k]
    return env


def _read_report_status(json_path: Optional[pathlib.Path]) -> Optional[str]:
    """Read the ``status`` field from a JSON report file.

    Returns ``None`` if the file cannot be read or has no recognised status.
    Returns ``"ERROR"`` if the report contains an ``"error"`` key (schema
    parse error path from ``manifest_doctor``).
    """
    if json_path is None or not json_path.exists():
        return None
    try:
        with open(json_path, encoding="utf-8") as fh:
            report = json.load(fh)
        if report.get("error"):
            return "ERROR"
        s = str(report.get("status", ""))
        if s in ("PASS", "WARN", "BLOCK"):
            return s
    except Exception:
        pass
    return None


def _map_status(returncode: int, json_report_path: Optional[pathlib.Path]) -> str:
    """Map subprocess *returncode* + optional report to a wrapper status string."""
    if returncode == 1:
        return "ERROR"
    report_status = _read_report_status(json_report_path)
    if report_status:
        return report_status
    if returncode == 0:
        return "PASS"
    if returncode == 2:
        return "BLOCK"
    return "ERROR"


def _make_result(
    action: str,
    status: str,
    returncode: int,
    started_at: str,
    ended_at: str,
    stdout_tail: str = "",
    stderr_tail: str = "",
    artifacts: Optional[dict] = None,
    policy_blocked: bool = False,
    policy_reason: str = "",
) -> dict:
    return {
        "action": action,
        "status": status,
        "returncode": returncode,
        "started_at": started_at,
        "ended_at": ended_at,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "artifacts": artifacts if artifacts is not None else {},
        "policy": {
            "blocked": policy_blocked,
            "reason": policy_reason,
        },
    }


def _assert_outputs_outside_toolchain(session_dir: pathlib.Path) -> None:
    """Raise :exc:`RuntimeError` if *session_dir* is inside ``TOOLCHAIN_ROOT``."""
    try:
        session_dir.resolve().relative_to(TOOLCHAIN_ROOT.resolve())
        raise RuntimeError(
            f"session_dir {session_dir!r} is inside TOOLCHAIN_ROOT {TOOLCHAIN_ROOT!r}. "
            "All outputs must be written outside aletheia_toolchain/."
        )
    except ValueError:
        pass  # not a sub-path — boundary is respected


def _append_step(session_dict: dict, result: dict) -> None:
    """Append a compact step record to ``session_dict["steps"]``."""
    session_dict.setdefault("steps", []).append({
        "action":     result["action"],
        "status":     result["status"],
        "started_at": result["started_at"],
        "ended_at":   result["ended_at"],
        "returncode": result["returncode"],
    })
    session_dict["updated_at"] = _now_str()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_action(
    action_name: str,
    params: dict,
    session_dict: dict,
    session_dir,
    yaml_path=None,
    toolchain_root=None,
    registry: Optional[Dict[str, ToolEntry]] = None,
) -> dict:
    """Execute a registered wrapper action safely.

    Parameters
    ----------
    action_name:
        One of the four registered action names.
    params:
        Action-specific parameters (see each ``ToolEntry.build_flags``).
    session_dict:
        Live session dict — mutated in-place (step appended, artifacts updated).
    session_dir:
        Path to the session's working directory.  Must not be inside
        ``TOOLCHAIN_ROOT``.
    yaml_path:
        If provided, the session YAML is saved after the action completes.
    toolchain_root:
        Override the subprocess ``cwd`` (used in tests with fake scripts).
    registry:
        Override the tool registry (used in tests with fake script entries).

    Returns
    -------
    dict
        Result conforming to the minimum action result shape.
    """
    session_dir = pathlib.Path(session_dir)
    _assert_outputs_outside_toolchain(session_dir)

    reg = registry if registry is not None else REGISTRY
    cwd = pathlib.Path(toolchain_root) if toolchain_root is not None else TOOLCHAIN_ROOT
    started_at = _now_str()

    # -- Unknown action -------------------------------------------------------
    if action_name not in reg:
        ended_at = _now_str()
        result = _make_result(
            action=action_name,
            status="ERROR",
            returncode=-1,
            started_at=started_at,
            ended_at=ended_at,
            stderr_tail=f"Unknown action: {action_name!r}. Allowed: {sorted(reg)}",
        )
        _append_step(session_dict, result)
        if yaml_path:
            save_session(session_dict, yaml_path)
        return result

    entry: ToolEntry = reg[action_name]

    # -- Review gate ----------------------------------------------------------
    if entry.requires_review_approval:
        approved = session_dict.get("review_state", {}).get("slice_approved", False)
        dev_mode = os.environ.get("LTA_DEV_MODE", "").strip() == "1"
        if not approved and not dev_mode:
            ended_at = _now_str()
            result = _make_result(
                action=action_name,
                status="POLICY_BLOCK",
                returncode=-1,
                started_at=started_at,
                ended_at=ended_at,
                policy_blocked=True,
                policy_reason=(
                    "review_state.slice_approved is false. "
                    "Set review_state.slice_approved = true or LTA_DEV_MODE=1 to bypass."
                ),
            )
            _append_step(session_dict, result)
            if yaml_path:
                save_session(session_dict, yaml_path)
            return result

    # -- Build argv -----------------------------------------------------------
    try:
        flags = entry.build_flags(params, session_dir)
    except (ValueError, KeyError) as exc:
        ended_at = _now_str()
        result = _make_result(
            action=action_name,
            status="ERROR",
            returncode=-1,
            started_at=started_at,
            ended_at=ended_at,
            stderr_tail=str(exc),
        )
        _append_step(session_dict, result)
        if yaml_path:
            save_session(session_dict, yaml_path)
        return result

    argv = [sys.executable, entry.script_name] + flags
    safe_env = _build_safe_env()

    # -- Execute --------------------------------------------------------------
    try:
        proc = subprocess.run(
            argv,
            shell=False,
            cwd=str(cwd),
            timeout=entry.timeout_seconds,
            capture_output=True,
            text=True,
            env=safe_env,
        )
        ended_at = _now_str()

        artifacts = entry.collect_artifacts(params, session_dir)

        primary_key = entry.primary_json_report_key
        primary_path: Optional[pathlib.Path] = None
        if primary_key and artifacts.get(primary_key):
            primary_path = pathlib.Path(artifacts[primary_key])

        status = _map_status(proc.returncode, primary_path)
        result = _make_result(
            action=action_name,
            status=status,
            returncode=proc.returncode,
            started_at=started_at,
            ended_at=ended_at,
            stdout_tail=_tail(proc.stdout),
            stderr_tail=_tail(proc.stderr),
            artifacts=artifacts,
        )

    except subprocess.TimeoutExpired:
        ended_at = _now_str()
        result = _make_result(
            action=action_name,
            status="ERROR",
            returncode=-1,
            started_at=started_at,
            ended_at=ended_at,
            stderr_tail=f"Subprocess timed out after {entry.timeout_seconds}s",
        )

    except OSError as exc:
        ended_at = _now_str()
        result = _make_result(
            action=action_name,
            status="ERROR",
            returncode=-1,
            started_at=started_at,
            ended_at=ended_at,
            stderr_tail=f"Subprocess launch failed: {exc}",
        )

    # -- Update session -------------------------------------------------------
    for k, v in result["artifacts"].items():
        if k in session_dict.get("artifacts", {}):
            session_dict["artifacts"][k] = v

    _append_step(session_dict, result)

    if yaml_path:
        save_session(session_dict, yaml_path)

    return result
