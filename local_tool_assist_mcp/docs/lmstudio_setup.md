# Local Tool Assist MCP — LM Studio Setup

**Phase:** LTA-5
**Date:** 2026-05-01
**Status:** Local registration guide

---

## 1. Purpose — Backend Only

The Local Tool Assist MCP Wrapper is a **backend control layer** around the Aletheia
developer toolchain. It exposes a narrow, policy-checked set of tool actions over stdio.

There is no custom web UI, desktop app, or chat interface. The user-facing interface is
your existing LM Studio model session. The wrapper exists to let a locally-running LLM
call Aletheia tools safely without exposing arbitrary shell execution or raw filesystem
access.

**This is not a replacement for any existing Aletheia tool.** It wraps them.

---

## 2. LM Studio Local MCP via Stdio

LM Studio supports local MCP servers registered via a `mcp.json` configuration file.
The Local Tool Assist server runs as a child process communicating over stdin/stdout
using the MCP JSON-RPC protocol. No network port is opened.

This is the primary v1 target. Remote MCP (HTTP) is disabled by default and is not
documented here.

---

## 3. Project Paths

| Role | Path |
|------|------|
| ToolSet root | `D:\Aletheia_project\DEV_TOOLS\ToolSet` |
| Wrapper package | `D:\Aletheia_project\DEV_TOOLS\ToolSet\local_tool_assist_mcp` |
| Output root | `D:\Aletheia_project\DEV_TOOLS\ToolSet\local_tool_assist_outputs` |
| Aletheia toolchain | `D:\Aletheia_project\DEV_TOOLS\ToolSet\aletheia_toolchain` |

All session artifacts, reports, and archives are written under
`local_tool_assist_outputs\`. **Nothing is ever written inside `aletheia_toolchain\`.**

---

## 4. Installation

### Option A — Editable install (recommended)

Run once from the ToolSet root:

```powershell
cd D:\Aletheia_project\DEV_TOOLS\ToolSet\local_tool_assist_mcp
pip install -e .
```

This makes `local_tool_assist_mcp` importable from any working directory and is required
for LM Studio to find the package without `PYTHONPATH` manipulation.

### Option B — Run from ToolSet root (no install)

If you cannot install, you must launch the server from `D:\Aletheia_project\DEV_TOOLS\ToolSet`
so that `local_tool_assist_mcp` is on `sys.path`. The `cwd` field in `mcp.json` handles this.

### Dependencies

Core runtime (added to `pyproject.toml`):

```
PyYAML>=6.0
jsonschema>=4.0
```

MCP server transport (optional, required for stdio registration):

```powershell
pip install mcp
```

The module remains importable without `mcp` installed. Only `run_stdio()` raises
`ImportError` when the package is absent.

### Verify the install

```powershell
cd D:\Aletheia_project\DEV_TOOLS\ToolSet
python -m local_tool_assist_mcp.mcp_server --list-tools
```

Expected output:

```
Local Tool Assist MCP — approved tools:
  archive_session_yaml
  compile_handoff_report
  create_session
  lint_tool_command
  read_report
  run_semantic_slice
  scan_directory
  validate_manifest
```

---

## 5. Example `mcp.json`

A ready-to-use example is at:

```
local_tool_assist_mcp\examples\mcp.json
```

Content:

```json
{
  "mcpServers": {
    "aletheia-local-tool-assist": {
      "command": "python",
      "args": ["-m", "local_tool_assist_mcp.mcp_server"],
      "cwd": "D:\\Aletheia_project\\DEV_TOOLS\\ToolSet",
      "env": {
        "TOOLSET_ROOT": "D:\\Aletheia_project\\DEV_TOOLS\\ToolSet",
        "LTA_OUTPUT_ROOT": "D:\\Aletheia_project\\DEV_TOOLS\\ToolSet\\local_tool_assist_outputs",
        "LTA_REQUIRE_REVIEW": "1"
      }
    }
  }
}
```

> **Note:** LM Studio's exact `mcp.json` field names may vary between releases. If
> registration fails, check your LM Studio release notes for the current MCP config schema.
> The fields above follow the convention current as of LM Studio 0.3.x / 2026-05.

---

## 6. Registering the Server in LM Studio

1. Copy `examples\mcp.json` to your LM Studio MCP config location. The typical path is:
   - `%APPDATA%\LM Studio\mcp.json` (Windows)
   - Or merge the `"mcpServers"` block into an existing `mcp.json`
2. Restart LM Studio or reload the MCP config.
3. The server named `aletheia-local-tool-assist` should appear in the tool panel.
4. Select a model that supports tool/function calling.

---

## 7. Querying the Tool Assist Agent in LM Studio

Once registered, prompt the model with the tool surface in mind. Example system prompt:

```
You are a developer tool assistant with access to the Aletheia toolchain via the
aletheia-local-tool-assist MCP server. Use create_session first, then scan_directory,
then validate_manifest. Wait for user review before calling run_semantic_slice.
After approval, run the slice and call compile_handoff_report to produce the final bundle.
```

The model will issue tool calls; the server dispatches them to the wrapper; results are
returned as tool responses.

---

## 8. Expected Workflow

```
1.  create_session        — start a session (returns session_id)
2.  scan_directory        — build file manifest
3.  validate_manifest     — check manifest health
4.  [USER REVIEW]         — set review_state.slice_approved = true in session YAML,
                             or set review_state via a provider-specific tool call
5.  run_semantic_slice    — extract context (POLICY_BLOCK if not approved)
6.  read_report           — read intermediate artifacts (optional)
7.  compile_handoff_report — produce final .md report + .py data bundle
8.  archive_session_yaml  — write canonical session YAML archive
9.  [RETURN]              — pass final_python_bundle path to downstream agent
```

The final `.py` bundle path is returned in the `compile_handoff_report` response as
`final_python_bundle`. It is a valid Python file containing only data constants
(`SESSION_YAML`, `ARTIFACT_INDEX_JSON`, `STEP_RESULTS_JSON`, `FINAL_SUMMARY_MD`).
Pass this path to the downstream builder or reviewer agent for context.

---

## 9. Output Locations

```
local_tool_assist_outputs\
├── sessions\<session_id>\       — per-session working directory + session YAML
├── intermediate\                — manifest CSV, doctor JSON, linter JSON, slicer JSON
├── reports\                     — final_handoff_report_<id>.md + final_handoff_bundle_<id>.py
├── archive\                     — session_<id>.yaml (archived copy)
└── logs\                        — reserved for future subprocess log capture
```

The output root is created automatically on first `create_session` call. You do not need
to create it in advance.

---

## 10. Security Rules

| Rule | Enforcement |
|------|-------------|
| No arbitrary shell execution | `shell=False` always; no `shell` / `execute_command` MCP tool |
| No raw filesystem read/write | Only `read_report` (path-safety checked, redacted, capped) |
| No outputs inside `aletheia_toolchain\` | Enforced in runner, compiler, and MCP layer |
| API keys not passed to child processes | Runner strips `*_API_KEY`, `*_TOKEN`, `*_SECRET` from child env |
| Review gate before slicing | `run_semantic_slice` returns `POLICY_BLOCK` unless `slice_approved = true` |
| No delete/move/overwrite tools | Not exposed — artifact paths are append-only per session |

To approve slicing, set `review_state.slice_approved: true` in the session YAML at
`local_tool_assist_outputs\sessions\<session_id>\session.yaml` and re-call
`run_semantic_slice`.

---

## 11. Troubleshooting

### `ImportError: No module named 'mcp'`

Install the MCP package:

```powershell
pip install mcp
```

### `ModuleNotFoundError: No module named 'local_tool_assist_mcp'`

Either run from `D:\Aletheia_project\DEV_TOOLS\ToolSet` (so the package is on `sys.path`),
or install the package in editable mode:

```powershell
cd D:\Aletheia_project\DEV_TOOLS\ToolSet\local_tool_assist_mcp
pip install -e .
```

Confirm the `cwd` in `mcp.json` points to the ToolSet root, **not** to
`aletheia_toolchain\`.

### `ValueError: Session not found`

The `session_id` passed to a tool does not match any saved YAML under
`local_tool_assist_outputs\sessions\`. Always call `create_session` first and pass the
returned `session_id` to subsequent tools.

### Output root missing or `PermissionError`

The output root is created automatically by `create_session`. If you see permission
errors, check that the `LTA_OUTPUT_ROOT` env path is writable and not inside a
protected system directory.

### `POLICY_BLOCK` on `run_semantic_slice`

The session's `review_state.slice_approved` is `false`. Open the session YAML:

```
local_tool_assist_outputs\sessions\<session_id>\session.yaml
```

Set `slice_approved: true` under `review_state:`, save, and retry.

To bypass in development only:

```powershell
$env:LTA_DEV_MODE = "1"
```

### Running the test suites

**LTA wrapper tests (from ToolSet root):**

```powershell
cd D:\Aletheia_project\DEV_TOOLS\ToolSet
python -m unittest discover -s local_tool_assist_mcp/tests -p "test_*.py" -t . -v
```

**Aletheia toolchain tests (from aletheia_toolchain root):**

```powershell
cd D:\Aletheia_project\DEV_TOOLS\ToolSet\aletheia_toolchain
python -m unittest discover -s tests -p "test_*.py" -v
```

Both suites must pass before any LTA change is considered complete.
