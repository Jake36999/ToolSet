"""Guided Local Tool Assist workflow orchestration.

This module provides a deterministic orchestration flow that always emits
well-defined status events so higher-level callers can gate execution.
"""

GUIDED_STATUS_EVENTS = (
    "INTAKE_CREATED",
    "SCAN_COMPLETE",
    "MANIFEST_PASS",
    "MANIFEST_WARN",
    "MANIFEST_BLOCK",
    "REVIEW_REQUIRED",
    "SLICE_COMPLETE",
    "REPORT_COMPILED",
    "ARCHIVED",
    "ERROR",
)


def run_guided_repository_investigation(
    objective: str,
    target_repo: str,
    profile: str = "default",
    allow_slice: bool = False,
    output_root: str = "",
    _registry=None,
    _toolchain_root=None,
) -> dict:
    """Run the guided repository workflow with deterministic status events."""
    events = []
    try:
        from local_tool_assist_mcp.mcp_server import (
            _dispatch_archive_session_yaml,
            _dispatch_compile_handoff_report,
            _dispatch_create_session,
            _dispatch_runner_action,
        )

        created = _dispatch_create_session(
            objective=objective,
            target_repo=target_repo,
            requested_by="user",
            downstream_agent="tool_assist",
            output_root=output_root,
        )
        session_id = created["session_id"]
        events.append({"event": "INTAKE_CREATED", "session_id": session_id})

        scan_profile = "safe" if profile == "default" else profile
        scan = _dispatch_runner_action(
            "scan_directory",
            session_id=session_id,
            params={"target_repo": target_repo, "profile": scan_profile},
            output_root=output_root,
            _registry=_registry,
            _toolchain_root=_toolchain_root,
        )
        events.append({"event": "SCAN_COMPLETE", "status": scan.get("status", "ERROR")})

        manifest_csv = scan.get("artifacts", {}).get("manifest_csv", "")
        validate = _dispatch_runner_action(
            "validate_manifest",
            session_id=session_id,
            params={"manifest_csv": manifest_csv},
            output_root=output_root,
            _registry=_registry,
            _toolchain_root=_toolchain_root,
        )
        manifest_status = str(validate.get("status", "ERROR"))
        event_name = f"MANIFEST_{manifest_status}" if manifest_status in {"PASS", "WARN", "BLOCK"} else "ERROR"
        events.append({"event": event_name, "status": manifest_status})

        if manifest_status == "BLOCK":
            archived = _dispatch_archive_session_yaml(session_id=session_id, output_root=output_root)
            events.append({"event": "ARCHIVED", "archive_yaml": archived.get("archive_yaml", "")})
            return {
                "status": "blocked",
                "session_id": session_id,
                "events": events,
                "artifacts": {"archive_yaml": archived.get("archive_yaml", "")},
            }

        if not allow_slice:
            events.append({"event": "REVIEW_REQUIRED", "reason": "allow_slice is false"})
            return {"status": "review_required", "session_id": session_id, "events": events}

        slice_result = _dispatch_runner_action(
            "run_semantic_slice",
            session_id=session_id,
            params={"manifest_csv": manifest_csv, "target_repo": target_repo},
            output_root=output_root,
            _registry=_registry,
            _toolchain_root=_toolchain_root,
        )
        if slice_result.get("status") == "POLICY_BLOCK":
            events.append({"event": "REVIEW_REQUIRED", "reason": "slice approval required"})
            return {
                "status": "review_required",
                "session_id": session_id,
                "events": events,
                "policy": slice_result.get("policy", {}),
            }

        events.append({"event": "SLICE_COMPLETE", "status": slice_result.get("status", "ERROR")})

        report = _dispatch_compile_handoff_report(session_id=session_id, output_root=output_root)
        events.append({"event": "REPORT_COMPILED", "status": report.get("status", "ERROR")})

        archived = _dispatch_archive_session_yaml(session_id=session_id, output_root=output_root)
        events.append({"event": "ARCHIVED", "archive_yaml": archived.get("archive_yaml", "")})

        return {
            "status": "complete",
            "session_id": session_id,
            "events": events,
            "artifacts": {
                "final_markdown": report.get("final_markdown", ""),
                "final_python_bundle": report.get("final_python_bundle", ""),
                "archive_yaml": archived.get("archive_yaml", ""),
            },
        }
    except Exception as exc:
        events.append({"event": "ERROR", "error": str(exc)})
        return {"status": "error", "events": events, "error": str(exc)}
