# Local Tool Assist MCP Wrapper — Backend Contract

**Phase:** Pre-LTA-1 — Contract Lock
**Date:** 2026-05-01
**Status:** Implementation contract — no runtime code in this document

---

## 1. Scope: Backend Only

The Local Tool Assist MCP Wrapper is a **backend control layer** around the existing Aletheia
toolchain. It is not a UI, desktop app, chat interface, or replacement for any existing tool.

The user-facing interface remains the host model environment (LM Studio, Claude, OpenAI, or
another MCP/tool-capable host). The wrapper exists to expose a narrow, policy-checked action
surface that host models can call safely.

**Non-goals — never build:**
- Custom web UI or desktop app
- Replacement chat interface
- Generic file browser
- Arbitrary command runner
- Autonomous background agent loop
- Broad filesystem read/write/delete tools
- Provider-specific duplication of Aletheia logic
- Remote MCP as a default or first-class target

---

## 2. Package Boundary

Wrapper runtime code lives exclusively here:

```
D:\Aletheia_project\DEV_TOOLS\ToolSet\local_tool_assist_mcp\
```

Do **not** place wrapper runtime code inside:

```
D:\Aletheia_project\DEV_TOOLS\ToolSet\aletheia_toolchain\
```

`aletheia_toolchain/` is the managed deterministic toolchain. `local_tool_assist_mcp/` is a
sibling backend controller around it. The boundary must remain clean.

---

## 3. Output Boundary

All runtime session artifacts, reports, and logs are written here:

```
D:\Aletheia_project\DEV_TOOLS\ToolSet\local_tool_assist_outputs\
├── sessions\       # per-session working directories
├── intermediate\   # manifest CSV, doctor JSON, linter JSON, slicer JSON
├── reports\        # final Markdown and Python handoff bundle
├── archive\        # final session YAML archive
└── logs\           # subprocess stdout/stderr logs
```

**No generated manifests, slicer bundles, JSON reports, Markdown reports, or `.py` handoff
bundles may be written inside `aletheia_toolchain/`.**

The `local_tool_assist_outputs/` root and all five subdirectories are **created automatically**
by `create_session` (using `pathlib.Path.mkdir(parents=True, exist_ok=True)`). They must not
be pre-created manually or assumed to exist.

---

## 4. Approved Wrapper Actions

The wrapper exposes exactly these eight actions in v1:

| Action | Backing behavior | Backing script |
|---|---|---|
| `create_session` | Create session directory and YAML | wrapper internal |
| `scan_directory` | Create manifest CSV and health report | `create_file_map_v3.py` |
| `validate_manifest` | Validate manifest before slicing | `manifest_doctor.py` |
| `lint_tool_command` | Lint generated or proposed commands | `tool_command_linter.py` |
| `run_semantic_slice` | Deterministic semantic extraction | `semantic_slicer_v7.0.py` |
| `read_report` | Safely read a session-owned artifact | wrapper internal |
| `compile_handoff_report` | Compile final `.md` and `.py` bundle | wrapper internal |
| `archive_session_yaml` | Write canonical session YAML archive | wrapper internal |

Provider adapters must call only these wrapper actions. They must not call Aletheia scripts
directly.

The following actions are deferred to v1.1 after the first safe local loop is proven:

```
architecture_validate
pipeline_gate
audit_bundle_staleness
package_workspace
package_notebook
runtime_watch
runtime_forensics
```

---

## 5. Default Guided Workflow

```
1.  create_session
2.  scan_directory
3.  validate_manifest
4.  stop for review (populate review_state in session YAML)
5.  run_semantic_slice   ← only if review_state.slice_approved == true
6.  read_report or summarize intermediate artifacts
7.  compile_handoff_report
8.  archive_session_yaml
9.  return final artifact paths
```

Each session produces incremental artifacts:

```
01_intake.md
02_manifest_health.json
03_manifest_doctor.json
04_scan_summary.md
05_findings.json
06_handoff_summary.md
```

Then compiled outputs:

```
archive/session_<session_id>.yaml
reports/final_handoff_report.md
reports/final_handoff_bundle.py
```

The `.py` handoff bundle must be valid Python containing **data constants only**:

```python
SESSION_YAML = r"""..."""
MANIFEST_HEALTH_JSON = r"""..."""
MANIFEST_DOCTOR_JSON = r"""..."""
SLICER_REPORT_JSON = r"""..."""
FINAL_SUMMARY_MD = r"""..."""
```

No prose is written directly into executable Python outside string constants.

---

## 6. Review Gate Rule

`run_semantic_slice` must refuse to run unless:

```yaml
review_state:
  slice_approved: true
```

or the environment explicitly sets `LTA_DEV_MODE=1`.

Development mode must be clearly marked in the session YAML and the final report.

**The gate check lives in `runner.py`, not in `mcp_server.py` or any adapter.**
This ensures all callers — MCP server, provider adapters, direct Python callers, unit tests —
inherit the enforcement without duplicating it. `runner.run_action("run_semantic_slice", ...)`
reads the session YAML and returns a structured `POLICY_BLOCK` result if the gate is not
satisfied.

---

## 7. Safe Runner Rules

Every Aletheia subprocess call must use:

```python
subprocess.run(
    argv,
    shell=False,
    cwd=TOOLCHAIN_ROOT,
    timeout=timeout_seconds,
    capture_output=True,
    text=True,
    env=safe_env,
)
```

Where `TOOLCHAIN_ROOT` is:

```
D:\Aletheia_project\DEV_TOOLS\ToolSet\aletheia_toolchain\
```

The runner must:

- maintain an **allowlist** of known scripts — reject all other tool names
- reject arbitrary shell commands
- enforce `shell=False` always
- use bounded timeouts (no infinite waits)
- capture stdout and stderr into the session log
- redact stdout/stderr tails before persisting (via `aletheia_tool_core.sanitize_content` or
  a wrapper-internal fallback)
- return JSON-compatible structured results using the minimum result shape below
- **scrub provider API keys and tokens** from the child process environment — strip all
  environment variables matching `*_API_KEY`, `*_TOKEN`, `*_SECRET`

Minimum action result shape:

```json
{
  "action": "validate_manifest",
  "status": "PASS|WARN|BLOCK|POLICY_BLOCK|ERROR",
  "returncode": 0,
  "started_at": "ISO-8601",
  "ended_at": "ISO-8601",
  "stdout_tail": "...",
  "stderr_tail": "...",
  "artifacts": {
    "json_report": "...",
    "markdown_report": "..."
  },
  "policy": {
    "blocked": false,
    "reason": ""
  }
}
```

`POLICY_BLOCK` status is returned when the runner refuses the action due to a policy violation
(e.g., review gate not satisfied). It is distinct from `BLOCK` (Aletheia tool verdict) and
`ERROR` (subprocess failure).

---

## 8. Python Import Boundary

### 8.1 Test runner roots

LTA wrapper tests run from the **ToolSet root**:

```
cd D:\Aletheia_project\DEV_TOOLS\ToolSet
python -m unittest discover -s local_tool_assist_mcp/tests -p "test_*.py" -v
```

Aletheia toolchain tests continue to run from `aletheia_toolchain/`:

```
cd D:\Aletheia_project\DEV_TOOLS\ToolSet\aletheia_toolchain
python -m unittest discover -s tests -p "test_*.py" -v
```

These are separate test commands. Both must pass before any LTA phase is approved.

### 8.2 `aletheia_tool_core` reuse

The wrapper modules may import from `aletheia_tool_core` (e.g., `sanitize_content`,
`write_json_report`, `write_markdown_report`) to avoid duplicating utility logic. Because
`aletheia_tool_core/` lives inside `aletheia_toolchain/`, the wrapper adds
`TOOLCHAIN_ROOT` to `sys.path` at startup:

```python
import sys
import pathlib

TOOLCHAIN_ROOT = pathlib.Path(__file__).parent.parent / "aletheia_toolchain"
if str(TOOLCHAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLCHAIN_ROOT))

try:
    from aletheia_tool_core.security import sanitize_content
except ImportError:
    def sanitize_content(text, **_):  # no-op fallback
        return text
```

This coupling is intentional and documented. The fallback ensures the wrapper degrades
gracefully if `aletheia_tool_core` is unavailable. `TOOLCHAIN_ROOT` is resolved once at
module load time.

---

## 9. Provider Adapter Boundary

Provider adapters are translators only. They convert provider-specific tool-call formats
into wrapper action calls.

They must not:
- duplicate Aletheia tool logic
- call Aletheia scripts directly
- expose raw filesystem access
- expose arbitrary shell execution
- bypass the review gate
- write artifacts outside the session/output root

Approved provider paths (v1):

| Provider | Integration path |
|---|---|
| LM Studio | Local MCP stdio via `mcp.json` — **primary v1 target** |
| OpenAI | Responses API callback loop or Agents SDK with local stdio MCP |
| Claude | Messages API `tool_use`/`tool_result` callback loop |
| Remote MCP | Notes only — disabled by default, auth required, not v1 |

---

## 10. LTA-2 Fake-Script Fixture Plan

Runner tests must not require the real Aletheia tools to be runnable. LTA-2 will add
minimal fake scripts under `local_tool_assist_mcp/tests/fixtures/`:

| Fixture | Behavior |
|---|---|
| `fake_scanner.py` | Exits 0; reads `--out` arg; writes a minimal two-row CSV to that path |
| `fake_doctor.py` | Exits 0 or 2 based on `FAKE_DOCTOR_EXIT` env var; reads `--out`; writes minimal JSON |
| `fake_linter.py` | Exits 0; reads `--out`; writes `{"errors": [], "warnings": []}` |
| `fake_slicer.py` | Exits 0; reads `-o`; writes `{"layers": {}, "manifest_path": ""}` |

`tool_registry.py` will expose a test-mode constructor (or environment variable override)
that substitutes these fake paths for the real tool script paths. Integration tests use
fake scripts; contract tests that call real tools are opt-in and clearly labeled.

---

## 11. Environment Variables

| Variable | Used by | Purpose |
|---|---|---|
| `TOOLSET_ROOT` | all wrapper modules | Absolute path to `ToolSet/` (optional override) |
| `LTA_OUTPUT_ROOT` | all wrapper modules | Override output root |
| `LTA_REQUIRE_REVIEW` | all adapters | Force review gate before slicing |
| `LTA_DEV_MODE` | local/debug only | Permit explicitly marked dev-mode bypasses |
| `LTA_MCP_AUTH_TOKEN` | remote MCP only | Bearer token for remote server |
| `OPENAI_API_KEY` | OpenAI adapters | OpenAI authentication |
| `ANTHROPIC_API_KEY` | Claude adapters | Claude authentication |
| `LM_STUDIO_BASE_URL` | LM Studio docs/adapter | Local LM Studio API URL |

The runner strips all child-process environment variables matching `*_API_KEY`, `*_TOKEN`,
`*_SECRET`.

---

## 12. CLI Contract Locks (from LTA-0)

See [`cli_contracts.md`](cli_contracts.md) for full argparse inventory.

Critical runner rules derived from LTA-0:

| Risk | Runner rule |
|---|---|
| Tools 4/8/9 use `-o`/`--output`; others use `--out` | Hardcode the correct flag per tool in the registry. No generic output-flag abstraction. |
| `bundle_diff_auditor` uses `--current-manifest` | Use exact flag when adding v1.1 bundle-audit action. |
| Slicer writes into target repo without explicit `-o` | Treat missing slicer `-o` as a policy error; always pass explicit output path. |
| Slicer `sys.exit(string)` on some failures | Always capture stderr; attach to session step log; do not rely on returncode alone. |
| `manifest_doctor` exit 2 for both schema error and BLOCK | Read JSON report; check `report.get("error")` to distinguish. |
| `create_file_map_v3` defaults to CWD if `--out` not given | Always pass explicit `--out`. |

---

## 13. Phase Summary

| Phase | Deliverable | Gate condition |
|---|---|---|
| Pre-LTA-1 (this) | Cleanup + `backend_contract.md` | Stray artifacts gone; contract written |
| LTA-1 | Package skeleton + session schema + `pyproject.toml` | Session YAML round-trips; schema validates; paths outside toolchain; tests pass |
| LTA-2 | Safe runner + tool registry + fake-script fixtures | Unknown tools rejected; `shell=False`; gate enforced in runner; API keys stripped |
| LTA-3 | Report compiler + archive | Final `.md`, `.yaml`, `.py` bundle; redaction applied; artifacts outside toolchain |
| LTA-4 | MCP stdio server | Only registry actions exposed; no shell tools; review gate surfaced |
| LTA-5 | LM Studio docs + `mcp.json` | Local stdio registration documented; troubleshooting included |
| LTA-6 | OpenAI Responses adapter | Optional dependency; dispatches only registry actions; no live API in tests |
| LTA-7 | Claude Messages adapter | Optional dependency; dispatches only registry actions; no live API in tests |
| LTA-8 | Optional HTTP MCP mode | Disabled by default; auth required unless `LTA_DEV_MODE=1` |
| LTA-9 | End-to-end regression | Full artifact check; both test suites pass; no artifacts in toolchain |
