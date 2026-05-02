# Local Tool Assist MCP Wrapper — Provider Integrations Addendum

This document extends the base architecture in
`deep-research-report (8).md` to cover explicit connection points and
workflows for OpenAI API, OpenAI Agents SDK, Claude API, and Claude MCP
connector.

**Nothing in this document changes the core design.**
The Aletheia tools remain unchanged.  `local_tool_assist_mcp/` remains a
sibling package at the ToolSet root.  `aletheia_toolchain/` remains the
canonical managed toolchain.  Generated outputs stay outside
`aletheia_toolchain/`.  The wrapper exposes narrow domain actions, not
arbitrary shell.

---

## 1. Provider-Neutral Adapter Architecture

Every model provider calls the **same eight wrapper actions**.  Provider
adapters translate model/tool-call protocol shapes into those calls.
They do not duplicate Aletheia logic.

### Canonical wrapper action set

| Action | Description |
|---|---|
| `create_session` | Initialise a session record and return `session_path` |
| `scan_directory` | Run `create_file_map_v3.py`, return manifest and health paths |
| `validate_manifest` | Run `manifest_doctor.py`, return report paths |
| `lint_tool_command` | Run `tool_command_linter.py` on a command string, return report path |
| `run_semantic_slice` | Run `semantic_slicer_v7.0.py` against a validated manifest |
| `read_report` | Read a generated artifact by key, return content |
| `compile_handoff_report` | Assemble final Markdown + Python bundle |
| `archive_session_yaml` | Write canonical session YAML to archive |

### Extended package layout

```text
ToolSet/
├── aletheia_toolchain/            ← managed toolchain (unchanged)
├── local_tool_assist_mcp/
│   ├── mcp_server.py
│   ├── runner.py
│   ├── session.py
│   ├── compiler.py
│   ├── tool_registry.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── openai_responses_adapter.py
│   │   ├── openai_agents_adapter.py
│   │   ├── claude_messages_adapter.py
│   │   └── claude_mcp_adapter_notes.md
│   └── tests/
└── local_tool_assist_outputs/
```

### Provider adapter interface

```python
from abc import ABC, abstractmethod


class ProviderAdapter(ABC):
    """Translate provider tool-call protocol into wrapper actions."""

    @abstractmethod
    def list_tools(self) -> list[dict]:
        """Return provider-formatted tool definitions for the 8 wrapper actions."""
        ...

    @abstractmethod
    def execute_tool(self, name: str, arguments: dict) -> dict:
        """Execute one wrapper action; return a JSON-serialisable result dict."""
        ...

    @abstractmethod
    def run_guided_workflow(self, objective: str, target_repo: str) -> dict:
        """Run the full guided workflow; return final artifact paths."""
        ...
```

Concrete adapters: `OpenAIResponsesAdapter`, `OpenAIAgentsMCPAdapter`,
`ClaudeMessagesAdapter`.  `ClaudeRemoteMCPDeploymentNotes` is a notes
module, not a runnable adapter.

---

## 2. OpenAI API Connection Paths

### 2A. OpenAI Responses API with function tools

Use this when the wrapper runs inside the user's local Python application
and the application owns tool execution.

**Flow:**

1. Application sends a Responses API request with JSON-schema function
   tools describing the 8 wrapper actions.
2. Model emits `function_call` output items.
3. Local adapter executes wrapper methods via `ToolRunner`.
4. Adapter returns `function_call_output` items back to OpenAI.
5. Model summarises or requests next tool call.

**Minimal adapter:**

```python
from openai import OpenAI
from local_tool_assist_mcp.runner import ToolRunner
from local_tool_assist_mcp.session import LocalToolAssistSession, SessionPaths

client = OpenAI()  # uses OPENAI_API_KEY from env
_PATHS = SessionPaths()
_RUNNER = ToolRunner(_PATHS)

TOOLS = [
    {
        "type": "function",
        "name": "create_session",
        "description": "Initialise a new Tool Assist investigation session.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "objective": {"type": "string"},
                "target_repo": {"type": "string"},
            },
            "required": ["objective", "target_repo"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "scan_directory",
        "description": "Run create_file_map_v3 and return manifest paths.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_path": {"type": "string"},
                "profile": {"type": "string", "default": "safe"},
            },
            "required": ["session_path"],
        },
        "strict": True,
    },
    # ... validate_manifest, lint_tool_command, run_semantic_slice,
    #     read_report, compile_handoff_report, archive_session_yaml
]


def execute_wrapper_tool(name: str, arguments: dict) -> dict:
    """Map tool name → wrapper method; enforce policy before execution."""
    # Policy: require_user_review_before_refine stops here for slice
    if name == "run_semantic_slice":
        _check_review_gate()
    dispatch = {
        "create_session": lambda a: _create_session(a),
        "scan_directory": lambda a: _scan(a),
        # ...
    }
    return dispatch[name](arguments)


def run_responses_loop(objective: str, target_repo: str) -> dict:
    messages = [{"role": "user", "content": f"Investigate {target_repo}: {objective}"}]
    while True:
        response = client.responses.create(model="gpt-4o", tools=TOOLS, input=messages)
        if response.output[-1].type == "message":
            break
        tool_outputs = []
        for item in response.output:
            if item.type == "function_call":
                import json
                result = execute_wrapper_tool(item.name, json.loads(item.arguments))
                tool_outputs.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": json.dumps(result),
                })
        messages.extend(response.output)
        messages.extend(tool_outputs)
    return {"status": "complete"}
```

**Constraints:**
- The OpenAI model does not execute local files directly.
- The local application owns and controls tool execution.
- Use `strict: True` in tool schemas to enforce shape.
- Require explicit review gate before `run_semantic_slice`.
- Never expose an arbitrary shell action.

---

### 2B. OpenAI Agents SDK with local stdio MCP

Use this when the OpenAI agent process can launch the local MCP server as
a child process.  The Agents SDK supports stdio, Streamable HTTP, SSE, and
hosted MCP transports.

**Flow:**

1. Python agent starts `local_tool_assist_mcp.mcp_server` using
   `MCPServerStdio`.
2. Agent registers only the allowed tools via `allowed_tools`.
3. Agent runs the guided workflow.
4. Agents SDK traces tool calls automatically.
5. Final report is compiled locally.

**Adapter:**

```python
import asyncio
from agents import Agent, Runner
from agents.mcp import MCPServerStdio
import os

TOOLSET_ROOT = os.environ["TOOLSET_ROOT"]

async def run_agents_sdk_workflow(objective: str, target_repo: str) -> str:
    server_params = {
        "command": "python",
        "args": [
            f"{TOOLSET_ROOT}/local_tool_assist_mcp/mcp_server.py",
            "--transport", "stdio",
        ],
        "env": {
            "PYTHONUNBUFFERED": "1",
            "TOOLSET_ROOT": TOOLSET_ROOT,
            "LTA_OUTPUT_ROOT": os.environ.get("LTA_OUTPUT_ROOT", ""),
            "LTA_REQUIRE_REVIEW": "1",
        },
    }
    async with MCPServerStdio(name="local-tool-assist", params=server_params) as mcp_server:
        agent = Agent(
            name="ToolAssistAgent",
            instructions=(
                "You are an investigation agent.  Use the provided tools in order: "
                "create_session → scan_directory → validate_manifest → "
                "compile_handoff_report.  Stop for user review before run_semantic_slice."
            ),
            mcp_servers=[mcp_server],
        )
        result = await Runner.run(
            agent,
            f"Investigate {target_repo}: {objective}",
        )
    return result.final_output
```

The Agents SDK automatically calls `list_tools()` and `call_tool()` on
the MCP server; no manual callback dispatch is needed.

**LM Studio note:** If LM Studio is the model host (not OpenAI's API), use the
LM Studio `/api/v1/chat` plugin integration (type `"plugin"`) with
the server already registered in `mcp.json`, rather than the Agents SDK.
The Agents SDK is for use with the OpenAI API endpoint.

---

### 2C. OpenAI hosted / remote MCP path

Use this only if the wrapper is deployed as a remote Streamable HTTP MCP
server.  This is **not** recommended for private local repository analysis.

**Flow:**

1. Deploy or tunnel a constrained remote MCP endpoint (e.g. via ngrok or
   a private HTTPS server).
2. Configure the OpenAI Responses API with a `hosted_tool` entry pointing
   to the server URL.
3. Apply approval policies and restrict exposed tools.
4. Never expose raw filesystem or shell primitives through this endpoint.

**Warning:** Unless the network/security boundary is intentionally
designed, exposing a local-filesystem-backed MCP server remotely creates
serious attack surface.  Read/report-only tools are the maximum safe
exposure for a remote endpoint.

---

## 3. Claude API Connection Paths

### 3A. Claude Messages API with client-executed tools

Use this when the local Python application executes wrapper calls and
returns `tool_result` blocks to Claude.  This is the primary supported
Claude integration mode.

**How Claude tool use works (current API):**

1. Send a `POST /v1/messages` request with a `tools` array describing
   wrapper actions.
2. Claude returns a response with `stop_reason: "tool_use"` and one or
   more `tool_use` content blocks.
3. The local adapter executes the corresponding wrapper method.
4. The adapter sends a follow-up request containing the original messages,
   Claude's assistant response, and a `user` message with `tool_result`
   blocks.
5. Loop until Claude returns `stop_reason: "end_turn"`.

**Claude tool_use block shape:**

```json
{
  "type": "tool_use",
  "id": "toolu_01A09q90qw90lq917835lq9",
  "name": "scan_directory",
  "input": {
    "session_path": "/path/to/session.yaml",
    "profile": "safe"
  }
}
```

**Claude tool_result block shape (client sends this back):**

```json
{
  "type": "tool_result",
  "tool_use_id": "toolu_01A09q90qw90lq917835lq9",
  "content": "{\"manifest_csv\": \"/abs/path/file_map.csv\", \"exit_code\": \"0\"}"
}
```

**Minimal adapter:**

```python
import json
import os
import anthropic
from local_tool_assist_mcp.runner import ToolRunner
from local_tool_assist_mcp.session import LocalToolAssistSession, SessionPaths

client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY from env
_PATHS = SessionPaths()
_RUNNER = ToolRunner(_PATHS)

CLAUDE_TOOLS = [
    {
        "name": "create_session",
        "description": "Initialise a new Tool Assist investigation session.",
        "input_schema": {
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "target_repo": {"type": "string"},
            },
            "required": ["objective", "target_repo"],
        },
    },
    {
        "name": "scan_directory",
        "description": "Run create_file_map_v3 and return manifest paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_path": {"type": "string"},
                "profile": {"type": "string"},
            },
            "required": ["session_path"],
        },
    },
    # ... validate_manifest, lint_tool_command, run_semantic_slice,
    #     read_report, compile_handoff_report, archive_session_yaml
]


def execute_wrapper_tool(name: str, arguments: dict) -> dict:
    """Policy-enforced dispatch to wrapper methods."""
    if name == "run_semantic_slice" and _requires_review():
        return {"blocked": True, "reason": "review_required_before_slice"}
    dispatch = {
        "create_session": lambda a: _create_session(a),
        "scan_directory": lambda a: _scan(a),
        "validate_manifest": lambda a: _validate(a),
        "lint_tool_command": lambda a: _lint(a),
        "run_semantic_slice": lambda a: _slice(a),
        "read_report": lambda a: _read(a),
        "compile_handoff_report": lambda a: _compile(a),
        "archive_session_yaml": lambda a: _archive(a),
    }
    return dispatch[name](arguments)


def run_claude_tool_loop(objective: str, target_repo: str) -> str:
    messages = [{"role": "user", "content": f"Investigate {target_repo}: {objective}"}]
    while True:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            tools=CLAUDE_TOOLS,
            messages=messages,
        )
        if response.stop_reason == "end_turn":
            return next(
                (b.text for b in response.content if b.type == "text"), ""
            )
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_wrapper_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})
    return ""
```

**Constraints:**
- Claude does not execute local code itself.
- The local application must enforce wrapper policy (review gate, no shell).
- Use narrow, explicit tool schemas.
- Keep `require_user_review_before_refine: true` as a default.

---

### 3B. Claude API remote MCP connector

Use this only when the wrapper is deployed as a remote MCP server
reachable by Anthropic infrastructure.

**Flow:**

1. Host wrapper as a Streamable HTTP or SSE MCP server with auth.
2. Configure the Claude API request with an `mcp_servers` entry.
3. Allowlist only the read/report tools (no `run_semantic_slice` remotely).
4. Supply OAuth or Bearer auth.
5. Account for Anthropic data retention implications — prompts and tool
   results pass through Anthropic's servers.

**Warning:** The Claude API MCP connector is for **remote** MCP servers
only.  A local private stdio server cannot be registered with the Claude
API connector directly.  For local stdio, use the Messages API tool loop
(path 3A) or a local application bridge (e.g. Claude Desktop).

---

### 3C. Claude Desktop / local MCP note

Claude Desktop supports local MCP servers via its `claude_desktop_config.json`
registration, using the same stdio pattern as LM Studio's `mcp.json`.
This is useful for interactive development and local testing, but it is a
**different integration mode** from the Claude API remote MCP connector.

| Mode | Where it runs | Transport | Use case |
|---|---|---|---|
| Claude Desktop local MCP | Local machine | stdio | Interactive dev, personal use |
| Claude API remote MCP | Anthropic infra | Streamable HTTP / SSE | Remote/team deployment |
| Claude Messages client loop | Local app | HTTPS + local callbacks | Application-embedded integration |

---

## 4. Provider Comparison Table

| Provider path | Best use | Transport | Local filesystem access | Human approval point | Risk |
|---|---|---|---|---|---|
| LM Studio local MCP | Local offline/dev | stdio | Yes, local only | wrapper policy | Low |
| OpenAI Responses function tools | App-controlled loop | HTTPS + local callbacks | Via local app | app policy | Low/Medium |
| OpenAI Agents SDK stdio MCP | Python agent + local MCP | stdio | Yes, local only | SDK + tool policy | Low |
| OpenAI hosted MCP | Remote/public MCP | Streamable HTTP | Only if exposed | hosted approval | Medium/High |
| Claude Messages tools | App-controlled loop | HTTPS + local callbacks | Via local app | app policy | Low/Medium |
| Claude remote MCP connector | Remote MCP server | Streamable HTTP/SSE | Only if exposed | allowlist + auth | Medium/High |

---

## 5. Provider-Specific Workflow Examples

### OpenAI Responses workflow

1. Application calls `OpenAIResponsesAdapter.run_guided_workflow(objective, repo)`.
2. Adapter sends request → model calls `create_session` → adapter executes → returns `session_path`.
3. Model calls `scan_directory` → adapter executes → returns manifest paths.
4. Model calls `validate_manifest` → adapter executes → returns doctor report path.
5. Adapter enforces review gate — does not pass `run_semantic_slice` until reviewed.
6. After review, model calls `run_semantic_slice` → adapter executes.
7. Model calls `compile_handoff_report` → adapter executes → returns final bundle paths.
8. Model calls `archive_session_yaml`.
9. Adapter returns `final_handoff_bundle.py` path to caller.

### Claude Messages workflow

1. User asks Claude to investigate a repository.
2. Claude requests `create_session` via `tool_use` block.
3. Local app executes; sends `tool_result` with `session_path`.
4. Claude requests `scan_directory`.
5. Local app executes; sends manifest paths as `tool_result`.
6. Claude requests `validate_manifest`.
7. Review gate fires — app returns `{"blocked": true, "reason": "review_required"}`.
8. Claude surfaces "review required" to the user.
9. User approves; app clears review gate.
10. Workflow resumes with `run_semantic_slice` → `compile_handoff_report`.
11. Claude returns `stop_reason: "end_turn"` with a summary.

### Remote MCP workflow (both providers)

1. Start wrapper in Streamable HTTP mode:
   ```
   python -m local_tool_assist_mcp.mcp_server --transport streamable-http --port 8443
   ```
2. Expose only read/report tools; never expose `run_semantic_slice` or
   `scan_directory` without auth.
3. Configure provider with server URL, allowlist, and OAuth/Bearer token.
4. Run session with manual approval checks at the review gate.
5. Archive outputs; confirm no artifacts remain in `aletheia_toolchain/`.

---

## 6. Security and Policy Requirements

Every provider adapter must enforce all of the following:

- **No arbitrary shell** — every adapter maps to a known wrapper action only.
- **No unrestricted file read/write** — `read_report` reads from
  session-owned artifact paths only.
- **No generated outputs inside `aletheia_toolchain/`** — verified at
  `SessionPaths` construction time.
- **Manifest before slice** — `run_semantic_slice` checks `session.artifacts`
  for `manifest_doctor_json` before proceeding; returns `PolicyError` otherwise.
- **Linter before generated command execution** — if the session ever
  produces a command string, `lint_tool_command` must be called first.
- **Approval before expensive or broad scans** — `require_user_review_before_refine`
  must be `True` by default; can only be set `False` in an explicitly
  flagged development mode.
- **Redaction before handoff** — `compile_handoff_report` applies redaction;
  raw log content is never included in the handoff bundle.
- **Session YAML for every run** — `archive_session_yaml` must be called
  before any handoff artifact is considered complete.
- **Provider API keys only in environment variables** — never in session
  YAML, never in handoff bundle.
- **No API keys written to session artifacts** — runner's `_safe_env()`
  whitelist must exclude all `*_API_KEY` and `*_TOKEN` variables.
- **No remote MCP exposure without auth** — Streamable HTTP mode must
  refuse to start without an `LTA_MCP_AUTH_TOKEN` env var unless
  `LTA_DEV_MODE=1` is explicitly set.

---

## 7. Environment Variables

| Variable | Used by | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI adapters | OpenAI API authentication |
| `ANTHROPIC_API_KEY` | Claude adapters | Claude API authentication |
| `LM_STUDIO_BASE_URL` | LM Studio adapter | Local LM Studio server URL (default: `http://localhost:1234`) |
| `LTA_OUTPUT_ROOT` | All adapters | Wrapper output root (default: `ToolSet/local_tool_assist_outputs`) |
| `TOOLSET_ROOT` | All adapters | Absolute path to `ToolSet/` root |
| `LTA_REQUIRE_REVIEW` | All adapters | `1` = force review gate before `run_semantic_slice` (default: `1`) |
| `LTA_DEV_MODE` | Remote MCP mode | `1` = allow Streamable HTTP without auth token (dev only) |
| `LTA_MCP_AUTH_TOKEN` | Streamable HTTP mode | Bearer token for remote MCP endpoint |

All variables except `LTA_DEV_MODE` must be set before the wrapper starts.
The runner's `_safe_env()` method must never forward `*_API_KEY` or
`*_TOKEN` variables to child Aletheia processes.

---

## 8. Implementation Roadmap Extension

The following phases extend the original rollout plan from the base report:

| Phase | Goal | Deliverable |
|---|---|---|
| A | Provider-neutral wrapper core | `runner.py`, `session.py`, `compiler.py`, `tool_registry.py` |
| B | LM Studio MCP registration | `mcp_server.py` stdio mode, `mcp.json` entry |
| C | OpenAI Responses adapter | `adapters/openai_responses_adapter.py`, function tool schemas |
| D | Claude Messages adapter | `adapters/claude_messages_adapter.py`, tool_use/tool_result loop |
| E | OpenAI Agents SDK adapter | `adapters/openai_agents_adapter.py`, `MCPServerStdio` integration |
| F | Optional Streamable HTTP MCP server | `mcp_server.py` HTTP mode, auth middleware |
| G | Remote provider MCP hardening | Auth enforcement, allowlist, rate limiting |
| H | End-to-end multi-provider regression tests | `tests/test_provider_contracts.py` |

Phases A–D are required for initial usefulness.  Phases E–H are optional
enhancements.  Do not begin Phase F before Phase D is stable.

---

## 9. Acceptance Criteria

The provider integration layer is complete when all of the following hold:

- **OpenAI Responses adapter** can complete `create_session → scan_directory → validate_manifest → compile_handoff_report` using local callback execution against a real repository.
- **Claude Messages adapter** can complete the same workflow using `tool_use` / `tool_result` blocks.
- **OpenAI Agents SDK** can connect to the local stdio MCP wrapper via `MCPServerStdio` and call all 8 tools.
- **LM Studio** can connect through `mcp.json` stdio registration and call all 8 tools.
- **No adapter bypasses wrapper policy** — attempting `run_semantic_slice` without a validated manifest returns a `PolicyError` result, not an exception.
- **No adapter writes outputs inside `aletheia_toolchain/`** — verified by a test that asserts no path in `session.artifacts.values()` contains `aletheia_toolchain`.
- **Remote MCP mode refuses to start without `LTA_MCP_AUTH_TOKEN`** unless `LTA_DEV_MODE=1`.
- **All provider workflows produce the same session YAML structure** — schema-validated by `local_tool_assist_session.schema.json`.
- **API keys are not present in any session artifact** — verified by a test that reads `final_handoff_bundle.py` and asserts no `_API_KEY` or `_TOKEN` pattern appears.
- **The handoff bundle produced by any provider path** is self-contained and can be passed to another agent without access to the original session cache.

---

## References

- OpenAI Agents SDK MCP documentation: https://openai.github.io/openai-agents-python/mcp/
- OpenAI Responses API tool calling: https://platform.openai.com/docs/guides/tools
- Claude tool use: https://docs.anthropic.com/en/docs/tool-use
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- Base architecture report: `deep-research-report (8).md`
- Toolchain overview: `docs/toolchain_overview.md`
- Tool Assist schemas: `docs/tool_assist_schemas.md`
