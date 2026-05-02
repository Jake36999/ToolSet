"""OpenAI Responses API adapter for Local Tool Assist MCP Wrapper.

Integration model (local callback loop)
----------------------------------------
The model provider (OpenAI) never executes local files.  The local
application owns tool dispatch:

  1. Application sends an OpenAI Responses request with JSON-schema tool
     definitions for the 8 approved wrapper actions.
  2. Model emits ``function_call`` output items.
  3. This adapter executes only approved wrapper dispatch functions.
  4. Adapter returns ``function_call_output`` items back to OpenAI.
  5. Model summarises or requests the next tool call.

No Aletheia script is called directly.  All policy enforcement (review gate,
output boundary) stays in ``runner.run_action``.

Optional dependency
-------------------
``openai`` is optional.  This module is importable without it.
``run_openai_tool_loop`` raises ``ImportError`` if the package is absent.
``run_openai_tool_loop`` raises ``RuntimeError`` if ``OPENAI_API_KEY`` is
not set in the environment.
"""

import json
import os
from typing import Optional

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
    import openai as _openai_module
    _HAS_OPENAI = True
except ImportError:  # pragma: no cover
    _HAS_OPENAI = False
    _openai_module = None  # type: ignore

# ---------------------------------------------------------------------------
# Tool schemas — OpenAI Responses / function-tool format
#
# Note: strict mode (strict=True) requires ALL properties to be listed in
# "required" and "additionalProperties" to be False.  The schemas below
# use optional fields and therefore set strict=False.  To enable strict
# mode, promote every optional parameter to required (and handle defaults
# application-side) or use nullable union types.
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: list = [
    {
        "type": "function",
        "name": "create_session",
        "description": (
            "Create a new Local Tool Assist session. "
            "Returns session_id, session_dir, and yaml_path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective":         {"type": "string", "description": "Investigation objective."},
                "target_repo":       {"type": "string", "description": "Absolute path to target repository."},
                "requested_by":      {"type": "string", "description": "Requester name (default: user)."},
                "downstream_agent":  {"type": "string", "description": "Agent type: builder|reviewer|tool_assist|unknown."},
                "output_root":       {"type": "string", "description": "Output root override (leave empty for default)."},
            },
            "required": ["objective", "target_repo"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "scan_directory",
        "description": "Scan a repository directory and produce a file manifest CSV and health report.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id":  {"type": "string", "description": "Session ID from create_session."},
                "target_repo": {"type": "string", "description": "Absolute path to repository to scan."},
                "profile":     {"type": "string", "description": "Scan profile: safe (default) or python."},
                "output_root": {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id", "target_repo"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "validate_manifest",
        "description": "Validate a manifest CSV for consistency and health.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id":   {"type": "string", "description": "Session ID."},
                "manifest_csv": {"type": "string", "description": "Path to manifest CSV (uses session artifact if omitted)."},
                "output_root":  {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "lint_tool_command",
        "description": "Lint a proposed Aletheia tool command string for correctness.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id":  {"type": "string", "description": "Session ID."},
                "command":     {"type": "string", "description": "Command string to lint (analysed, never executed)."},
                "output_root": {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id", "command"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "run_semantic_slice",
        "description": (
            "Run semantic slicing on a validated manifest. "
            "Blocked by POLICY_BLOCK unless review_state.slice_approved is true."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id":   {"type": "string", "description": "Session ID."},
                "manifest_csv": {"type": "string", "description": "Path to manifest CSV (uses session artifact if omitted)."},
                "target_repo":  {"type": "string", "description": "Target repository path (uses session request if omitted)."},
                "output_root":  {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "read_report",
        "description": "Read a session-owned artifact or report file safely (redacted, capped).",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id":    {"type": "string", "description": "Session ID."},
                "artifact_key":  {"type": "string", "description": "Artifact key from session (e.g. manifest_csv)."},
                "relative_path": {"type": "string", "description": "Relative path under output root."},
                "max_chars":     {"type": "integer", "description": "Maximum characters to return (default 10000)."},
                "output_root":   {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "compile_handoff_report",
        "description": "Compile the final Markdown handoff report and Python data bundle.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id":  {"type": "string", "description": "Session ID."},
                "output_root": {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "archive_session_yaml",
        "description": "Archive the session YAML to the archive directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id":  {"type": "string", "description": "Session ID."},
                "output_root": {"type": "string", "description": "Output root override."},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "strict": False,
    },
]

# Quick lookup: name → schema
_SCHEMA_BY_NAME: dict = {s["name"]: s for s in _TOOL_SCHEMAS}

# ---------------------------------------------------------------------------
# Module-level public API
# ---------------------------------------------------------------------------

def get_openai_tool_schemas() -> list:
    """Return a list of OpenAI Responses-format tool schema dicts.

    Each entry has ``type``, ``name``, ``description``, and ``parameters``.
    The returned list contains exactly the 8 approved wrapper actions.
    """
    return list(_TOOL_SCHEMAS)


def dispatch_openai_tool_call(
    name: str,
    arguments: "dict | str",
    *,
    output_root: str = "",
    _registry=None,
    _toolchain_root=None,
) -> dict:
    """Dispatch one OpenAI tool call to the appropriate wrapper function.

    Parameters
    ----------
    name:
        Tool name; must be one of the approved wrapper actions.
    arguments:
        Tool arguments as a ``dict`` or a JSON-encoded string.
    output_root:
        Output root override (passed to wrapper dispatch helpers).
    _registry, _toolchain_root:
        Test injection parameters — allow fake-script registries.

    Returns
    -------
    dict
        JSON-serialisable result from the wrapper dispatch function.
        Unknown tools return ``{"status": "ERROR", "error": "..."}``.
        Invalid JSON strings return ``{"status": "ERROR", "error": "..."}``.
        POLICY_BLOCK results from run_semantic_slice are returned unchanged.
    """
    # Parse arguments
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError as exc:
            return {"status": "ERROR", "error": f"Invalid JSON arguments: {exc}"}
    elif isinstance(arguments, dict):
        args = dict(arguments)
    else:
        return {"status": "ERROR", "error": "arguments must be a dict or JSON string"}

    # Validate tool name
    if name not in APPROVED_TOOLS:
        return {
            "status": "ERROR",
            "error": f"Unknown tool: {name!r}. Allowed: {sorted(APPROVED_TOOLS)}",
        }

    effective_root = args.get("output_root", "") or output_root

    # Dispatch to the correct wrapper function
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

    # Should not be reached given the APPROVED_TOOLS check above
    return {"status": "ERROR", "error": f"Unhandled tool: {name!r}"}  # pragma: no cover


def run_openai_tool_loop(
    objective: str,
    target_repo: str,
    model: str = "gpt-4o",
    output_root: str = "",
    max_turns: int = 20,
) -> dict:
    """Run the full guided workflow via OpenAI Responses API.

    This is a local callback loop — OpenAI executes nothing locally.
    The application dispatches each ``function_call`` output through
    ``dispatch_openai_tool_call`` and returns results as
    ``function_call_output`` items.

    Requires
    --------
    - ``pip install openai``
    - ``OPENAI_API_KEY`` environment variable set

    Parameters
    ----------
    objective:
        Investigation objective passed to ``create_session``.
    target_repo:
        Absolute path to the repository to analyse.
    model:
        OpenAI model ID (default: ``gpt-4o``).
    output_root:
        Override for the LTA output root directory.
    max_turns:
        Safety limit on tool-call turns (default: 20).

    Returns
    -------
    dict
        ``{"status": "complete", "final_output": "...", "turns": N}``
    """
    if not _HAS_OPENAI:  # pragma: no cover
        raise ImportError("openai package required: pip install openai")

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set. "
            "Export it before calling run_openai_tool_loop()."
        )

    client = _openai_module.OpenAI(api_key=api_key)
    tools = get_openai_tool_schemas()
    messages = [
        {
            "role": "user",
            "content": (
                f"Investigate the repository at {target_repo!r}.\n"
                f"Objective: {objective}\n\n"
                "Follow this workflow: create_session → scan_directory → "
                "validate_manifest → (wait for user review) → run_semantic_slice → "
                "compile_handoff_report → archive_session_yaml. "
                "Return the final_python_bundle path when done."
            ),
        }
    ]

    turns = 0
    final_output = ""

    while turns < max_turns:
        response = client.responses.create(
            model=model,
            tools=tools,
            input=messages,
        )
        turns += 1

        # Check if the model finished with a message
        last = response.output[-1] if response.output else None
        if last is None or getattr(last, "type", None) == "message":
            final_output = getattr(last, "content", "") if last else ""
            break

        # Process function_call items
        tool_outputs = []
        for item in response.output:
            if getattr(item, "type", None) == "function_call":
                result = dispatch_openai_tool_call(
                    item.name,
                    item.arguments,
                    output_root=output_root,
                )
                tool_outputs.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(result),
                })

        if not tool_outputs:
            break  # No tool calls — model is done

        messages = list(messages) + list(response.output) + tool_outputs

    return {"status": "complete", "final_output": final_output, "turns": turns}


# ---------------------------------------------------------------------------
# OpenAIResponsesAdapter class (ProviderAdapter implementation)
# ---------------------------------------------------------------------------

class OpenAIResponsesAdapter(ProviderAdapter):
    """OpenAI Responses API adapter implementing the ProviderAdapter interface.

    OpenAI integration model: local callback loop — the model provider
    does not execute local files.  The adapter translates ``function_call``
    output items into approved wrapper dispatch calls.

    Parameters
    ----------
    output_root:
        Override for the LTA output root directory.
    model:
        OpenAI model ID used in ``run_guided_workflow``.
    """

    def __init__(
        self,
        output_root: str = "",
        model: str = "gpt-4o",
    ) -> None:
        self._output_root = output_root
        self._model = model

    def list_tools(self) -> list:
        """Return OpenAI Responses-format tool schema dicts."""
        return get_openai_tool_schemas()

    def execute_tool(self, name: str, arguments: dict) -> dict:
        """Execute one approved wrapper action.

        Parameters
        ----------
        name:
            Tool name; must be one of the 8 approved actions.
        arguments:
            Decoded argument dict.

        Returns
        -------
        dict
            JSON-serialisable wrapper result.
        """
        return dispatch_openai_tool_call(
            name, arguments, output_root=self._output_root
        )

    def run_guided_workflow(self, objective: str, target_repo: str) -> dict:
        """Run the full guided workflow via OpenAI Responses API.

        Requires ``OPENAI_API_KEY`` environment variable.
        Raises ``RuntimeError`` if the API key is not set.
        """
        return run_openai_tool_loop(
            objective=objective,
            target_repo=target_repo,
            model=self._model,
            output_root=self._output_root,
        )
