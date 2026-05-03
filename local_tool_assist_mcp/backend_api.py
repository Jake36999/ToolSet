"""Stable backend import API for Local Tool Assist.

agent_backend imports these functions instead of reaching into Tool Assist
runner/script internals directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from local_tool_assist_mcp.mcp_server import (
    _dispatch_archive_session_yaml,
    _dispatch_compile_handoff_report,
    _dispatch_create_session,
    _dispatch_read_report,
    _dispatch_runner_action,
)
from local_tool_assist_mcp.session import load_session

_OK_STATUSES = {"created", "compiled", "archived", "PASS", "WARN", "COMPLETE"}


def _base(
    *,
    ok: bool,
    status: str,
    summary: str,
    artifacts: dict[str, str] | None = None,
    top_candidates: list[Any] | None = None,
    recommended_next_tool: str = "",
    error: dict[str, str] | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "summary": summary[:2000],
        "artifacts": artifacts or {},
        "top_candidates": (top_candidates or [])[:10],
        "recommended_next_tool": recommended_next_tool,
    }
    if error is not None:
        payload["error"] = error
    if content is not None:
        payload["content"] = content
    return payload


def _error(code: str, message: str, status: str = "ERROR") -> dict[str, Any]:
    return _base(
        ok=False,
        status=status,
        summary=message,
        error={"code": code, "message": message[:2000]},
    )


def _string_artifacts(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(k, str) and v not in (None, "")}


def _session_from_path(session_path: str) -> tuple[str, str, Path, dict[str, Any]]:
    """Return (session_id, output_root, yaml_path, session_dict)."""
    path = Path(session_path).expanduser().resolve()
    if path.is_dir():
        yaml_path = path / "session.yaml"
        session_dir = path
    else:
        yaml_path = path
        session_dir = path.parent

    if not yaml_path.exists():
        raise FileNotFoundError(f"session YAML not found: {yaml_path}")

    session_id = session_dir.name

    # Expected layout:
    # <output_root>/sessions/<session_id>/session.yaml
    if session_dir.parent.name == "sessions":
        output_root = str(session_dir.parent.parent)
    else:
        output_root = str(session_dir.parent)

    session_dict = load_session(yaml_path)
    return session_id, output_root, yaml_path, session_dict


def _normalize_runner_response(
    response: dict[str, Any],
    *,
    action: str,
    success_summary: str,
    recommended_next_tool: str = "",
) -> dict[str, Any]:
    if not isinstance(response, dict):
        return _error("toolset_non_object_response", f"{action} returned a non-object response.")

    policy = response.get("policy") if isinstance(response.get("policy"), dict) else {}
    policy_blocked = bool(policy.get("blocked"))

    status = str(response.get("status", "ERROR"))
    returncode = response.get("returncode")
    ok = (status in _OK_STATUSES) and not policy_blocked and (returncode in (None, 0))

    artifacts = _string_artifacts(response.get("artifacts"))
    summary = success_summary

    stderr_tail = str(response.get("stderr_tail", "") or "")
    stdout_tail = str(response.get("stdout_tail", "") or "")

    if policy_blocked:
        summary = str(policy.get("reason") or f"{action} was blocked by Tool Assist policy.")
        status = "POLICY_BLOCK"
    elif not ok:
        summary = stderr_tail[:1000] or stdout_tail[:1000] or f"{action} completed with status {status}."

    error = None
    if not ok:
        error = {
            "code": "toolset_policy_block" if policy_blocked else "toolset_action_failed",
            "message": summary[:2000],
        }

    return _base(
        ok=ok,
        status=status,
        summary=summary,
        artifacts=artifacts,
        recommended_next_tool=recommended_next_tool,
        error=error,
    )


def create_session(
    objective: str,
    target_repo: str,
    profile: str = "safe",
    output_root: str | None = None,
) -> dict[str, Any]:
    """Create a Tool Assist session and return the session YAML path."""
    if profile != "safe":
        return _error("unsupported_profile", "Only profile='safe' is supported by the backend API.", "POLICY_BLOCK")

    try:
        result = _dispatch_create_session(
            objective=objective,
            target_repo=target_repo,
            requested_by="backend_orchestrator",
            downstream_agent="tool_assist",
            output_root=output_root or "",
        )

        yaml_path = str(result["yaml_path"])
        artifacts = {
            "session_path": yaml_path,
            "session_yaml": yaml_path,
            "session_dir": str(result.get("session_dir", "")),
        }

        return _base(
            ok=True,
            status="created",
            summary="Tool Assist investigation session created.",
            artifacts=artifacts,
            recommended_next_tool="mcp_investigation_filemap",
        )
    except Exception as exc:
        return _error("create_session_failed", f"Failed to create Tool Assist session: {exc}")


def scan_directory(session_path: str, profile: str = "safe") -> dict[str, Any]:
    """Run the safe deterministic filemap scan for a session target repo."""
    if profile != "safe":
        return _error("unsupported_profile", "Only profile='safe' is supported by the backend API.", "POLICY_BLOCK")

    try:
        session_id, output_root, _yaml_path, session = _session_from_path(session_path)
        target_repo = str(session.get("request", {}).get("target_repo", ""))

        if not target_repo:
            return _error("missing_target_repo", "Session does not contain request.target_repo.")

        result = _dispatch_runner_action(
            "scan_directory",
            session_id,
            {"target_repo": target_repo, "profile": profile},
            output_root,
        )

        return _normalize_runner_response(
            result,
            action="scan_directory",
            success_summary="Filemap scan completed. Manifest artifacts are available on the session.",
            recommended_next_tool="mcp_investigation_validate_manifest",
        )
    except Exception as exc:
        return _error("scan_directory_failed", f"Failed to run filemap scan: {exc}")


def validate_manifest(session_path: str) -> dict[str, Any]:
    """Validate the session manifest produced by scan_directory."""
    try:
        session_id, output_root, _yaml_path, session = _session_from_path(session_path)
        manifest_csv = str(session.get("artifacts", {}).get("manifest_csv", "") or "")

        if not manifest_csv:
            return _error("missing_manifest_csv", "Session has no manifest_csv artifact. Run scan_directory first.")

        result = _dispatch_runner_action(
            "validate_manifest",
            session_id,
            {"manifest_csv": manifest_csv},
            output_root,
        )

        normalized = _normalize_runner_response(
            result,
            action="validate_manifest",
            success_summary="Manifest validation completed.",
            recommended_next_tool="mcp_investigation_read_report",
        )

        if normalized["status"] == "BLOCK":
            normalized["ok"] = False
            normalized["recommended_next_tool"] = ""
            normalized["error"] = {
                "code": "manifest_block",
                "message": normalized["summary"],
            }

        return normalized
    except Exception as exc:
        return _error("validate_manifest_failed", f"Failed to validate manifest: {exc}")


def read_report(session_path: str, artifact_key: str, max_chars: int = 12000) -> dict[str, Any]:
    """Verify/read a session-owned artifact through the Tool Assist read policy.

    Backend-facing behavior is compact by default:
    - return the real session artifact path
    - return char_count/content_omitted
    - do not return artifact content
    """
    try:
        session_id, output_root, _yaml_path, session = _session_from_path(session_path)
        capped = max(1, min(int(max_chars), 12000))

        artifact_path = str(session.get("artifacts", {}).get(artifact_key, "") or "")
        if not artifact_path:
            return _error(
                "missing_artifact",
                f"Session has no artifact for key {artifact_key!r}.",
            )

        result = _dispatch_read_report(
            session_id=session_id,
            artifact_key=artifact_key,
            max_chars=capped,
            output_root=output_root,
        )

        if "error" in result:
            return _error("read_report_failed", str(result["error"]))

        content = str(result.get("content", ""))
        returned_chars = min(len(content), capped)

        payload = _base(
            ok=True,
            status="PASS",
            summary=f"Read artifact {artifact_key!r}. Content omitted for compact workflow mode.",
            artifacts={artifact_key: artifact_path},
            recommended_next_tool="mcp_investigation_compile_handoff",
        )
        payload["content_omitted"] = True
        payload["char_count"] = returned_chars
        return payload

    except Exception as exc:
        return _error("read_report_failed", f"Failed to read report: {exc}")


def compile_handoff_report(session_path: str) -> dict[str, Any]:
    """Compile final handoff report, Python bundle, and archive YAML."""
    try:
        session_id, output_root, _yaml_path, _session = _session_from_path(session_path)

        report = _dispatch_compile_handoff_report(
            session_id=session_id,
            output_root=output_root,
        )
        archive = _dispatch_archive_session_yaml(
            session_id=session_id,
            output_root=output_root,
        )

        artifacts = {
            "final_markdown": str(report.get("final_markdown", "")),
            "final_python_bundle": str(report.get("final_python_bundle", "")),
            "archive_yaml": str(archive.get("archive_yaml", "")),
        }
        artifacts = {k: v for k, v in artifacts.items() if v}

        ok = bool(
            artifacts.get("final_markdown")
            and artifacts.get("final_python_bundle")
            and artifacts.get("archive_yaml")
        )

        return _base(
            ok=ok,
            status="compiled" if ok else "ERROR",
            summary=(
                "Compiled final handoff report, Python bundle, and archived session YAML."
                if ok
                else "Handoff compilation did not return all expected artifacts."
            ),
            artifacts=artifacts,
            error=None if ok else {
                "code": "compile_handoff_incomplete",
                "message": "Missing one or more handoff artifacts.",
            },
        )
    except Exception as exc:
        return _error("compile_handoff_failed", f"Failed to compile handoff: {exc}")
