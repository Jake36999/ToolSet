"""Claude Messages API adapter for Local Tool Assist MCP Wrapper.

Integration model (local callback loop)
----------------------------------------
Claude never executes local files.  The local application owns dispatch:

  1. Application sends a Claude Messages API request with tool definitions
     describing the 8 approved wrapper actions.
  2. Claude returns ``stop_reason: "tool_use"`` with ``tool_use`` content
     blocks.
  3. This adapter executes only approved wrapper dispatch functions for
     each ``tool_use`` block.
  4. Adapter sends a follow-up request containing a ``tool_result`` user
     message for each executed tool.
  5. Repeat until Claude returns ``stop_reason: "end_turn"``.

No Aletheia script is called directly.  All policy enforcement (review gate,
output boundary) stays in ``runner.run_action``.

Claude tool schema shape (Messages API)
-----------------------------------------
Unlike OpenAI, Claude uses ``input_schema`` (not ``parameters``) and does
not wrap schemas in a ``{"type": "function"}`` object::

    {
        "name": "create_session",
        "description": "...",
        "input_schema": {
            "type": "object",
            "properties": {...},
            "required": [...]
        }
    }

Optional dependency
-------------------
``anthropic`` is optional.  This module is importable without it.
``run_claude_tool_loop`` raises ``ImportError`` if the package is absent.
``run_claude_tool_loop`` raises ``RuntimeError`` if ``ANTHROPIC_API_KEY``
is not set in the environment.
"""

import json
import os

from local_tool_assist_mcp.adapters.base import ProviderAdapter
from local_tool_assist_mcp.mcp_server import (
    APPROVED_TOOLS,
    _dispatch_archive_session_yaml,
    _dispatch_compile_handoff_report,
    _dispatch_create_session,
    _dispatch_read_report,
    _dispatch_runner_action,
)

try:
    import anthropic as _anthropic_module
    _HAS_ANTHROPIC = True
except ImportError:  # pragma: no cover
    _HAS_ANTHROPIC = False
    _anthropic_module = None  # type: ignore

# ---------------------------------------------------------------------------
# Tool schemas — Claude Messages API format
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: list = [
    {
        "name": "create_session",
        "description": (
            "Create a new Local Tool Assist session. "
            "Returns session_id, session_dir, and yaml_path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "objective":        {"type": "string", "description": "Investigation objective."},
                "target_repo":      {"type": "string", "description": "Absolute path to target repository."},
                "requested_by":     {"type": "string", "description": "Requester name (default: user)."},
                "downstream_agent": {"type": "string", "description": "Agent type: builder|reviewer|tool_assist|unknown."},
                "output_root":      {"type": "string", "description": "Output root override (leave empty for default)."},
            },
            "required": ["objective", "target_repo"],
        },
    },
    {
        "name": "scan_directory",
        "description": "Scan a repository directory and produce a file manifest CSV and health report.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id":  {"type": "string", "description": "Session ID from create_session."},
                "target_repo": {"type": "string", "description": "Absolute path to repository to scan."},
                "profile":     {"type": "string", "description": "Scan profile: safe (default) or python."},
                "output_root": {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id", "target_repo"],
        },
    },
    {
        "name": "validate_manifest",
        "description": "Validate a manifest CSV for consistency and health.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id":   {"type": "string", "description": "Session ID."},
                "manifest_csv": {"type": "string", "description": "Path to manifest CSV (uses session artifact if omitted)."},
                "output_root":  {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "lint_tool_command",
        "description": "Lint a proposed Aletheia tool command string for correctness.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id":  {"type": "string", "description": "Session ID."},
                "command":     {"type": "string", "description": "Command string to lint (analysed, never executed)."},
                "output_root": {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id", "command"],
        },
    },
    {
        "name": "run_semantic_slice",
        "description": (
            "Run semantic slicing on a validated manifest. "
            "Returns POLICY_BLOCK unless review_state.slice_approved is true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id":   {"type": "string", "description": "Session ID."},
                "manifest_csv": {"type": "string", "description": "Path to manifest CSV (uses session artifact if omitted)."},
                "target_repo":  {"type": "string", "description": "Target repository path (uses session request if omitted)."},
                "output_root":  {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "read_report",
        "description": "Read a session-owned artifact or report file safely (redacted, capped).",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id":    {"type": "string", "description": "Session ID."},
                "artifact_key":  {"type": "string", "description": "Artifact key from session (e.g. manifest_csv)."},
                "relative_path": {"type": "string", "description": "Relative path under output root."},
                "max_chars":     {"type": "integer", "description": "Maximum characters to return (default 10000)."},
                "output_root":   {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "compile_handoff_report",
        "description": "Compile the final Markdown handoff report and Python data bundle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id":  {"type": "string", "description": "Session ID."},
                "output_root": {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "archive_session_yaml",
        "description": "Archive the session YAML to the archive directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id":  {"type": "string", "description": "Session ID."},
                "output_root": {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
        },
    },
]

_SCHEMA_BY_NAME: dict = {s["name"]: s for s in _TOOL_SCHEMAS}

# ---------------------------------------------------------------------------
# Module-level public API
# ---------------------------------------------------------------------------

def get_claude_tool_schemas() -> list:
    """Return a list of Claude Messages API-format tool schema dicts.

    Each entry has ``name``, ``description``, and ``input_schema``.
    The returned list contains exactly the 8 approved wrapper actions.
    """
    return list(_TOOL_SCHEMAS)


def dispatch_claude_tool_call(
    name: str,
    input: "dict",
    *,
    output_root: str = "",
    _registry=None,
    _toolchain_root=None,
) -> dict:
    """Dispatch one Claude tool_use block to the appropriate wrapper function.

    Parameters
    ----------
    name:
        Tool name from the Claude ``tool_use`` block; must be one of the
        approved wrapper actions.
    input:
        Decoded input dict from the Claude ``tool_use`` block.
    output_root:
        Output root override (passed to wrapper dispatch helpers).
    _registry, _toolchain_root:
        Test injection parameters — allow fake-script registries.

    Returns
    -------
    dict
        JSON-serialisable result from the wrapper dispatch function.
        Unknown tools return ``{"status": "ERROR", "error": "..."}``.
        Non-dict input returns ``{"status": "ERROR", "error": "..."}``.
        POLICY_BLOCK results from run_semantic_slice are returned unchanged.
    """
    if not isinstance(input, dict):
        return {
            "status": "ERROR",
            "error": f"input must be a dict, got {type(input).__name__}",
        }

    if name not in APPROVED_TOOLS:
        return {
            "status": "ERROR",
            "error": f"Unknown tool: {name!r}. Allowed: {sorted(APPROVED_TOOLS)}",
        }

    args = input
    effective_root = args.get("output_root", "") or output_root

    if name == "create_session":
        return _dispatch_create_session(
            objective=args.get("objective", ""),
            target_repo=args.get("target_repo", ""),
            requested_by=args.get("requested_by", "user"),
            downstream_agent=args.get("downstream_agent", "unknown"),
            output_root=effective_root,
        )

    if name == "scan_directory":
        return _dispatch_runner_action(
            "scan_directory",
            session_id=args["session_id"],
            params={
                "target_repo": args.get("target_repo", ""),
                "profile":     args.get("profile", "safe"),
            },
            output_root=effective_root,
            _registry=_registry,
            _toolchain_root=_toolchain_root,
        )

    if name == "validate_manifest":
        return _dispatch_runner_action(
            "validate_manifest",
            session_id=args["session_id"],
            params={"manifest_csv": args.get("manifest_csv", "")},
            output_root=effective_root,
            _registry=_registry,
            _toolchain_root=_toolchain_root,
        )

    if name == "lint_tool_command":
        return _dispatch_runner_action(
            "lint_tool_command",
            session_id=args["session_id"],
            params={"command": args.get("command", "")},
            output_root=effective_root,
            _registry=_registry,
            _toolchain_root=_toolchain_root,
        )

    if name == "run_semantic_slice":
        return _dispatch_runner_action(
            "run_semantic_slice",
            session_id=args["session_id"],
            params={
                "manifest_csv": args.get("manifest_csv", ""),
                "target_repo":  args.get("target_repo", ""),
            },
            output_root=effective_root,
            _registry=_registry,
            _toolchain_root=_toolchain_root,
        )

    if name == "read_report":
        return _dispatch_read_report(
            session_id=args["session_id"],
            artifact_key=args.get("artifact_key", ""),
            relative_path=args.get("relative_path", ""),
            max_chars=args.get("max_chars", 10_000),
            output_root=effective_root,
        )

    if name == "compile_handoff_report":
        return _dispatch_compile_handoff_report(
            session_id=args["session_id"],
            output_root=effective_root,
        )

    if name == "archive_session_yaml":
        return _dispatch_archive_session_yaml(
            session_id=args["session_id"],
            output_root=effective_root,
        )

    return {"status": "ERROR", "error": f"Unhandled tool: {name!r}"}  # pragma: no cover


def run_claude_tool_loop(
    objective: str,
    target_repo: str,
    model: str = "claude-opus-4-7",
    output_root: str = "",
    max_turns: int = 20,
) -> dict:
    """Run the full guided workflow via Claude Messages API.

    This is a local callback loop — Claude executes nothing locally.
    The application dispatches each ``tool_use`` block through
    ``dispatch_claude_tool_call`` and returns results as ``tool_result``
    blocks in a ``user`` message.

    Requires
    --------
    - ``pip install anthropic``
    - ``ANTHROPIC_API_KEY`` environment variable set

    Parameters
    ----------
    objective:
        Investigation objective passed to ``create_session``.
    target_repo:
        Absolute path to the repository to analyse.
    model:
        Claude model ID (default: ``claude-opus-4-7``).
    output_root:
        Override for the LTA output root directory.
    max_turns:
        Safety limit on tool-call turns (default: 20).

    Returns
    -------
    dict
        ``{"status": "complete", "final_output": "...", "turns": N}``
    """
    if not _HAS_ANTHROPIC:  # pragma: no cover
        raise ImportError("anthropic package required: pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Export it before calling run_claude_tool_loop()."
        )

    client = _anthropic_module.Anthropic(api_key=api_key)
    tools = get_claude_tool_schemas()
    messages = [
        {
            "role": "user",
            "content": (
                f"Investigate the repository at {target_repo!r}.\n"
                f"Objective: {objective}\n\n"
                "Follow this workflow: create_session → scan_directory → "
                "validate_manifest → (stop for user review) → run_semantic_slice → "
                "compile_handoff_report → archive_session_yaml. "
                "Return the final_python_bundle path when done."
            ),
        }
    ]

    turns = 0
    final_output = ""

    while turns < max_turns:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=tools,
            messages=messages,
        )
        turns += 1

        if response.stop_reason == "end_turn":
            final_output = next(
                (b.text for b in response.content if b.type == "text"), ""
            )
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = dispatch_claude_tool_call(
                    block.name,
                    block.input,
                    output_root=output_root,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })

        if not tool_results:
            final_output = next(
                (b.text for b in response.content if b.type == "text"), ""
            )
            break

        messages = list(messages) + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ]

    return {"status": "complete", "final_output": final_output, "turns": turns}


# ---------------------------------------------------------------------------
# ClaudeMessagesAdapter class (ProviderAdapter implementation)
# ---------------------------------------------------------------------------

class ClaudeMessagesAdapter(ProviderAdapter):
    """Claude Messages API adapter implementing the ProviderAdapter interface.

    Claude integration model: local callback loop — Claude does not execute
    local files.  The adapter translates ``tool_use`` blocks into approved
    wrapper dispatch calls and returns ``tool_result`` blocks.

    Parameters
    ----------
    output_root:
        Override for the LTA output root directory.
    model:
        Claude model ID used in ``run_guided_workflow``.
    """

    def __init__(
        self,
        output_root: str = "",
        model: str = "claude-opus-4-7",
    ) -> None:
        self._output_root = output_root
        self._model = model

    def list_tools(self) -> list:
        """Return Claude Messages API-format tool schema dicts."""
        return get_claude_tool_schemas()

    def execute_tool(self, name: str, arguments: dict) -> dict:
        """Execute one approved wrapper action.

        Parameters
        ----------
        name:
            Tool name; must be one of the 8 approved actions.
        arguments:
            Decoded input dict.

        Returns
        -------
        dict
            JSON-serialisable wrapper result.
        """
        return dispatch_claude_tool_call(
            name, arguments, output_root=self._output_root
        )

    def run_guided_workflow(self, objective: str, target_repo: str) -> dict:
        """Run the full guided workflow via Claude Messages API.

        Requires ``ANTHROPIC_API_KEY`` environment variable.
        Raises ``RuntimeError`` if the API key is not set.
        """
        return run_claude_tool_loop(
            objective=objective,
            target_repo=target_repo,
            model=self._model,
            output_root=self._output_root,
        )
