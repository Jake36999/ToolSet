"""MCP stdio server for Local Tool Assist Wrapper.

Exposes the 8 approved wrapper actions as MCP tools via FastMCP.

FastMCP is an optional dependency.  The module remains importable without it;
only ``run_stdio()`` raises ``ImportError`` when FastMCP is absent.

All policy enforcement stays in ``runner.run_action`` — this layer is a thin
adapter that never bypasses the review gate.
"""

import pathlib

from local_tool_assist_mcp.session import (
    DEFAULT_OUTPUT_ROOT,
    create_session as _create_session,
    load_session,
    save_session,
)
from local_tool_assist_mcp.runner import run_action
from local_tool_assist_mcp.policy import (
    PolicyError,
    ensure_session_owned_read,
    ensure_remote_mcp_auth,
)
from local_tool_assist_mcp.compiler import (
    _sanitize,
    archive_session_yaml as _archive,
    compile_handoff_report as _compile_report,
    compile_python_bundle as _compile_bundle,
)

try:
    from mcp.server.fastmcp import FastMCP as _FastMCP
    _HAS_MCP = True
except ImportError:  # pragma: no cover
    _HAS_MCP = False
    _FastMCP = None  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The complete set of MCP-exposed action names.
APPROVED_TOOLS: frozenset = frozenset({
    "create_session",
    "scan_directory",
    "validate_manifest",
    "lint_tool_command",
    "run_semantic_slice",
    "read_report",
    "compile_handoff_report",
    "archive_session_yaml",
})

_DEFAULT_READ_LIMIT = 10_000

# Always define mcp symbol for import stability in tests.
mcp = None

# ---------------------------------------------------------------------------
# Internal path helpers
# ---------------------------------------------------------------------------

def _resolve_root(output_root: str) -> pathlib.Path:
    return pathlib.Path(output_root) if output_root else DEFAULT_OUTPUT_ROOT


def _yaml_path(session_id: str, output_root: str) -> pathlib.Path:
    return _resolve_root(output_root) / "sessions" / session_id / "session.yaml"


def _session_dir_for(session_id: str, output_root: str) -> pathlib.Path:
    return _resolve_root(output_root) / "sessions" / session_id


def _load(session_id: str, output_root: str):
    """Return (session_dict, yaml_path) or raise ValueError if not found."""
    yp = _yaml_path(session_id, output_root)
    if not yp.exists():
        raise ValueError(
            f"Session not found: {session_id!r} (expected YAML at {yp})"
        )
    return load_session(yp), yp


# ---------------------------------------------------------------------------
# Path-safety helpers for read_report
# ---------------------------------------------------------------------------

def _is_subpath(child: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _validate_read_path(path: pathlib.Path, output_root: pathlib.Path, session_dir: pathlib.Path | None = None) -> None:
    ensure_session_owned_read(path.resolve(), output_root.resolve(), session_dir.resolve() if session_dir else None)


# ---------------------------------------------------------------------------
# Dispatch functions
# Callable directly in tests; the @mcp.tool() wrappers below forward to them.
# _registry / _toolchain_root are test-injection parameters.
# ---------------------------------------------------------------------------

def _dispatch_create_session(
    objective: str,
    target_repo: str,
    requested_by: str = "user",
    downstream_agent: str = "unknown",
    output_root: str = "",
) -> dict:
    root = _resolve_root(output_root)
    sd, session_dir = _create_session(
        objective=objective,
        target_repo=target_repo,
        requested_by=requested_by,
        downstream_agent=downstream_agent,
        output_root=str(root),
    )
    yp = _yaml_path(sd["session_id"], output_root)
    save_session(sd, yp)
    return {
        "session_id": sd["session_id"],
        "session_dir": str(session_dir),
        "yaml_path": str(yp),
        "status": "created",
    }


def _dispatch_runner_action(
    action_name: str,
    session_id: str,
    params: dict,
    output_root: str = "",
    _registry=None,
    _toolchain_root=None,
) -> dict:
    sd, yp = _load(session_id, output_root)
    session_dir = _session_dir_for(session_id, output_root)
    return run_action(
        action_name=action_name,
        params=params,
        session_dict=sd,
        session_dir=session_dir,
        yaml_path=yp,
        toolchain_root=_toolchain_root,
        registry=_registry,
    )


def _dispatch_read_report(
    session_id: str,
    artifact_key: str = "",
    relative_path: str = "",
    max_chars: int = _DEFAULT_READ_LIMIT,
    output_root: str = "",
) -> dict:
    root = _resolve_root(output_root)

    if artifact_key:
        sd, _ = _load(session_id, output_root)
        path_str = sd.get("artifacts", {}).get(artifact_key, "")
        if not path_str:
            return {"error": f"Artifact key {artifact_key!r} not found or empty in session"}
        path = pathlib.Path(path_str)
    elif relative_path:
        rp = pathlib.Path(relative_path)
        if rp.is_absolute():
            raise ValueError(f"absolute paths not permitted: {relative_path!r}")
        if ".." in rp.parts:
            raise ValueError(f".. traversal not permitted: {relative_path!r}")
        path = root / relative_path
    else:
        return {"error": "Must provide artifact_key or relative_path"}

    _validate_read_path(path, root, _session_dir_for(session_id, output_root))

    if not path.exists():
        return {"error": f"File not found: {path}"}

    text = path.read_text(encoding="utf-8")
    text = _sanitize(text)
    truncated = len(text) > max_chars
    return {
        "content": text[:max_chars],
        "truncated": truncated,
        "char_count": len(text),
    }


def _dispatch_compile_handoff_report(
    session_id: str,
    output_root: str = "",
) -> dict:
    sd, yp = _load(session_id, output_root)
    root = _resolve_root(output_root)
    session_dir = _session_dir_for(session_id, output_root)
    md_path = _compile_report(sd, session_dir, root)
    bundle_path = _compile_bundle(sd, session_dir, root)
    sd["artifacts"]["final_markdown"] = str(md_path)
    sd["artifacts"]["final_python_bundle"] = str(bundle_path)
    save_session(sd, yp)
    return {
        "status": "compiled",
        "final_markdown": str(md_path),
        "final_python_bundle": str(bundle_path),
    }


def _dispatch_archive_session_yaml(
    session_id: str,
    output_root: str = "",
) -> dict:
    sd, yp = _load(session_id, output_root)
    root = _resolve_root(output_root)
    session_dir = _session_dir_for(session_id, output_root)
    archive_path = _archive(sd, session_dir, root)
    sd["artifacts"]["archive_yaml"] = str(archive_path)
    save_session(sd, yp)
    return {
        "status": "archived",
        "archive_yaml": str(archive_path),
    }


# ---------------------------------------------------------------------------
# FastMCP tool registration
# ---------------------------------------------------------------------------

if _HAS_MCP:
    mcp = _FastMCP("local-tool-assist")

    @mcp.tool()
    def create_session(
        objective: str,
        target_repo: str,
        requested_by: str = "user",
        downstream_agent: str = "unknown",
        output_root: str = "",
    ) -> dict:
        """Create a new Local Tool Assist session."""
        return _dispatch_create_session(
            objective, target_repo, requested_by, downstream_agent, output_root
        )

    @mcp.tool()
    def scan_directory(
        session_id: str,
        target_repo: str,
        profile: str = "safe",
        output_root: str = "",
    ) -> dict:
        """Scan a repository directory and produce a file manifest."""
        return _dispatch_runner_action(
            "scan_directory",
            session_id,
            {"target_repo": target_repo, "profile": profile},
            output_root,
        )

    @mcp.tool()
    def validate_manifest(
        session_id: str,
        manifest_csv: str = "",
        output_root: str = "",
    ) -> dict:
        """Validate a manifest CSV for consistency and health."""
        if not manifest_csv:
            sd, _ = _load(session_id, output_root)
            manifest_csv = sd.get("artifacts", {}).get("manifest_csv", "")
        return _dispatch_runner_action(
            "validate_manifest",
            session_id,
            {"manifest_csv": manifest_csv},
            output_root,
        )

    @mcp.tool()
    def lint_tool_command(
        session_id: str,
        command: str,
        output_root: str = "",
    ) -> dict:
        """Lint a proposed Aletheia tool command for correctness."""
        return _dispatch_runner_action(
            "lint_tool_command",
            session_id,
            {"command": command},
            output_root,
        )

    @mcp.tool()
    def run_semantic_slice(
        session_id: str,
        manifest_csv: str = "",
        target_repo: str = "",
        output_root: str = "",
    ) -> dict:
        """Run semantic slicing. Blocked unless review_state.slice_approved is true."""
        if not manifest_csv or not target_repo:
            sd, _ = _load(session_id, output_root)
            if not manifest_csv:
                manifest_csv = sd.get("artifacts", {}).get("manifest_csv", "")
            if not target_repo:
                target_repo = sd.get("request", {}).get("target_repo", "")
        return _dispatch_runner_action(
            "run_semantic_slice",
            session_id,
            {"manifest_csv": manifest_csv, "target_repo": target_repo},
            output_root,
        )

    @mcp.tool()
    def read_report(
        session_id: str,
        artifact_key: str = "",
        relative_path: str = "",
        max_chars: int = _DEFAULT_READ_LIMIT,
        output_root: str = "",
    ) -> dict:
        """Read a session-owned artifact or report file safely."""
        return _dispatch_read_report(
            session_id, artifact_key, relative_path, max_chars, output_root
        )

    @mcp.tool()
    def compile_handoff_report(
        session_id: str,
        output_root: str = "",
    ) -> dict:
        """Compile the final Markdown handoff report and Python data bundle."""
        return _dispatch_compile_handoff_report(session_id, output_root)

    @mcp.tool()
    def archive_session_yaml(
        session_id: str,
        output_root: str = "",
    ) -> dict:
        """Archive the session YAML to the archive directory."""
        return _dispatch_archive_session_yaml(session_id, output_root)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_stdio() -> None:
    """Start the MCP server in stdio mode."""
    if not _HAS_MCP:  # pragma: no cover
        raise ImportError(
            "mcp / FastMCP is required. Install with: pip install mcp"
        )
    ensure_remote_mcp_auth(__import__("os").environ.get("LTA_REMOTE_MCP_URL", ""))
    mcp.run(transport="stdio")


def _list_tools_and_exit() -> None:
    """Print approved tool names and exit 0 (used by --list-tools flag)."""
    import sys
    print("Local Tool Assist MCP — approved tools:")
    for name in sorted(APPROVED_TOOLS):
        print(f"  {name}")
    sys.exit(0)


if __name__ == "__main__":
    import argparse as _argparse
    _parser = _argparse.ArgumentParser(
        description="Local Tool Assist MCP stdio server"
    )
    _parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print approved tool names and exit without starting the server",
    )
    _args = _parser.parse_args()
    if _args.list_tools:
        _list_tools_and_exit()
    else:  # pragma: no cover
        run_stdio()
