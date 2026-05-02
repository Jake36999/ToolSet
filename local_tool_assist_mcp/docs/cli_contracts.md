# Local Tool Assist MCP Wrapper — CLI Contract Notes

**Phase:** LTA-0 — CLI Contract Inspection  
**Date:** 2026-05-01  
**Source:** Direct argparse inspection of all 9 tool files in `aletheia_toolchain/`  
**Status:** Documentation only — no wrapper code created in this phase

---

## CWD Convention (applies to all tools)

All tools use `from aletheia_tool_core.<module> import ...` at the top level.
This means the Python import system must be able to resolve the
`aletheia_tool_core` package.  The correct `cwd` for every subprocess
invocation is:

```
D:\Aletheia_project\DEV_TOOLS\ToolSet\aletheia_toolchain\
```

The runner must set `cwd=TOOLCHAIN_ROOT` for every tool call.  Running from
any other directory will fail with `ModuleNotFoundError: No module named 'aletheia_tool_core'`.

---

## Tool 1 — `create_file_map_v3.py`

**Wrapper action:** `scan_directory`  
**Script path:** `aletheia_toolchain/create_file_map_v3.py`

### Required flags for wrapper action

```
python create_file_map_v3.py
    --roots <target_repo_abs_path>
    --out   <session_intermediate_dir>/file_map.csv
    --health-report <session_intermediate_dir>/file_map_health.json
    --hash
    --profile safe
```

### Full argparse inventory

| Flag | Type | Default | Required | Notes |
|---|---|---|---|---|
| `--roots` | nargs="+" | — | No (defaults to `["."]`) | Directories to scan |
| `-o` / `--out` | str | `"file_map.csv"` | No | CSV output path |
| `--include-exts` | str | None | No | Comma-separated extensions |
| `--exclude-dirs` | str | None | No | Comma-separated dirs to exclude |
| `--hash` | flag | False | No | Compute SHA1 |
| `--profile` | str | `"default"` | No | Built-in scan profile |
| `--health-report` | str | None | No | Optional JSON health report path |
| `--max-file-size` | int | DEFAULT | No | Max file size in bytes; 0=no limit |
| `--fail-on-pollution` | flag | False | No | Exit 2 if suspicious paths found |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Profile load error or CSV write error |
| 2 | `--fail-on-pollution` triggered (suspicious paths detected) |

### Output files

| File | Controlled by | Notes |
|---|---|---|
| CSV manifest | `--out` | Default `file_map.csv` in CWD |
| Health JSON | `--health-report` | Only written if flag provided |

### **CRITICAL: Output path risk**

If `--out` is not provided, the CSV is written to `file_map.csv` in the
**process CWD** (`aletheia_toolchain/`).  The wrapper **must always** supply
an explicit `--out` pointing to the session intermediate directory.  Same
applies to `--health-report`.

---

## Tool 2 — `manifest_doctor.py`

**Wrapper action:** `validate_manifest`  
**Script path:** `aletheia_toolchain/manifest_doctor.py`

### Required flags for wrapper action

```
python manifest_doctor.py
    --manifest <session_intermediate_dir>/file_map.csv
    --out      <session_intermediate_dir>/manifest_doctor.json
    --markdown-out <session_intermediate_dir>/manifest_doctor.md
```

### Full argparse inventory

| Flag | Type | Default | Required | Notes |
|---|---|---|---|---|
| `--manifest` | str | — | **Yes** | Path to manifest CSV |
| `--config` | str | None | No | JSON config for thresholds |
| `--required-path` | append | `[]` | No | Repeatable; rel_path must contain this |
| `--required-ext` | append | `[]` | No | Repeatable; extension must appear |
| `--max-rows-soft` | int | 0 | No | 0 = no limit |
| `--max-rows-hard` | int | 0 | No | 0 = no limit |
| `--max-file-size` | int | 0 | No | 0 = no limit |
| `--out` | str | `"manifest_doctor_report.json"` | No | JSON report path |
| `--markdown-out` | str | None | No | Markdown report path |

### Exit codes

| Code | Meaning | Notes |
|---|---|---|
| 0 | PASS or WARN | Wrapper must read JSON to distinguish |
| 1 | manifest not found, config error, CSV read error | |
| 2 | Schema error in CSV **or** status == BLOCK | Both map to 2 |

### Output report structure (top-level keys)

```json
{
  "status": "PASS|WARN|BLOCK",
  "manifest_path": "...",
  "summary": { "row_count": 0, "missing_files": 0, "suspicious_paths": 0, "bundle_artifacts": 0, ... },
  "findings": { "missing_files": [], "suspicious_paths": [], "bundle_artifacts": [], ... },
  "recommended_action": "...",
  "recommended_exclude_additions": []
}
```

**Note:** `bundle_artifacts` and `suspicious_paths` are nested under `findings`,
not at the top level.  Do not read `report["bundle_artifacts"]`; read
`report["findings"]["bundle_artifacts"]`.

---

## Tool 3 — `tool_command_linter.py`

**Wrapper action:** `lint_tool_command`  
**Script path:** `aletheia_toolchain/tool_command_linter.py`

### Required flags for wrapper action

```
python tool_command_linter.py
    --command "<command_string>"
    --out     <session_intermediate_dir>/command_lint.json
```

### Full argparse inventory

**Mutually exclusive group (required=True):**

| Flag | Metavar | Notes |
|---|---|---|
| `--command` | CMD | Inline command string |
| `--command-file` | PATH | `.ps1`, `.sh`, or plain text file |

**Other flags:**

| Flag | Default | Notes |
|---|---|---|
| `--config` | None | Optional JSON config; may disable rules |
| `--out` | `"command_lint_report.json"` | JSON report path |
| `--rewrite-out` | None | Optional rewrite suggestions text |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | PASS or WARN |
| 1 | Config file error or command file not found |
| 2 | BLOCK |

### Output report structure

```json
{
  "status": "PASS|WARN|BLOCK",
  "errors": [{"rule_id": "R001", "message": "...", "fragment": "...", "autofix": "..."}],
  "warnings": [{"rule_id": "R002", ...}],
  "autofix_suggestions": [...]
}
```

**Note:** Rule violations are in `errors[]` (BLOCK-level) and `warnings[]`
(WARN-level).  The report does NOT have a single `findings[]` array.

---

## Tool 4 — `semantic_slicer_v7.0.py`

**Wrapper action:** `run_semantic_slice`  
**Script path:** `aletheia_toolchain/semantic_slicer_v7.0.py`

### Required flags for wrapper action

```
python semantic_slicer_v7.0.py
    --manifest  <session_intermediate_dir>/file_map.csv
    -o          <session_intermediate_dir>/semantic_slice.json
    --base-dir  <target_repo_abs_path>
    --format    json
    --deterministic
```

### Full argparse inventory (selected)

| Flag | Default | Notes |
|---|---|---|
| `paths` | nargs="*" | Positional; conflicts with `--manifest` unless `--allow-path-merge-with-manifest` |
| `--format` | `"text"` | `json` or `text` |
| `-o` / `--output` | None | Explicit output file path |
| `--base-dir` | `"."` | Base directory; resolved with `Path(...).resolve()` |
| `--manifest` | None | CSV or TXT file list |
| `--deterministic` | False | Omit timestamp (always use in wrapper) |
| `--config` | None | Semantic project config JSON |
| `--task-profile` | None | Profile from `--config`; requires `--config` |
| `--validate-only` | False | Print scan strategy and exit 0; no bundle written |
| `--allow-path-merge-with-manifest` | False | Required if using both `--manifest` and positional paths |
| `--no-redaction` | False | Disable secret redaction (do not use in wrapper) |
| `--ignore-dirs` | DEFAULT_IGNORE_DIRS | Directories to skip |
| `--ignore-exts` | DEFAULT_IGNORE_EXTENSIONS | Extensions to skip |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (`--validate-only` exits 0 before writing bundle) |
| sys.exit(msg) | Safety block, config error, no files found — non-zero; Python prints message to stderr |

**Note:** The slicer uses `sys.exit(string_message)` rather than `sys.exit(int)` for error
paths.  The wrapper must treat any non-zero returncode as failure and capture
stderr for the step log.

### **CRITICAL: Default output path risk**

If `-o` is not provided, the slicer writes the bundle to:

```
<base_dir>/<basename>_bundle.json
```

This means the bundle is written **inside the target repository** unless an
explicit `-o` path is given.  The wrapper **must always** supply `-o` with a
path inside the session intermediate directory.

### **CRITICAL: Flag naming inconsistency**

The slicer uses `-o` / `--output` for its output flag.  **All other tools use
`--out`.**  The runner must not pass `--out` to the slicer; it will be ignored
without error.

### **Safety block: `--manifest` + positional paths**

Combining `--manifest` with positional path arguments causes an immediate
`sys.exit()` unless `--allow-path-merge-with-manifest` is also passed.
The wrapper must never combine both; pass only `--manifest`.

---

## Tool 5 — `architecture_validator.py`

**Wrapper action:** Not in initial 4-action set; future extension  
**Script path:** `aletheia_toolchain/architecture_validator.py`

### Important distinction

This tool takes a **slicer JSON bundle** (`--bundle`), not a manifest CSV.
It cannot be called directly after `scan_directory`; it requires a completed
`run_semantic_slice` output as its input.

### Required flags

```
python architecture_validator.py
    --bundle  <session_intermediate_dir>/semantic_slice.json
    --out     <session_intermediate_dir>/arch_report.json
    [--config <config.json>]
    [--profile <name>]
    [--markdown-out <arch_report.md>]
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | PASS or WARN |
| 2 | FAIL |
| sys.exit(msg) | File not found, JSON error, config error |

---

## Tool 6 — `pipeline_gatekeeper.py`

**Wrapper action:** Not in initial 4-action set; future extension  
**Script path:** `aletheia_toolchain/pipeline_gatekeeper.py`

### Required flags

```
python pipeline_gatekeeper.py
    --out <session_reports_dir>/gate_report.json
    [--manifest-report  <manifest_doctor.json>]
    [--validator-report <arch_report.json>]
    [--runtime-report   <oom_report.json>]
    [--policy           <policy.json>]
    [--markdown-out     <gate_report.md>]
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | PASS or WARN |
| 1 | Policy file error or evaluation error |
| 2 | BLOCK |

### Output report `failed_gates` structure

```json
"failed_gates": [
  {
    "gate_id": "manifest",
    "label": "Manifest health",
    "outcome": "BLOCK",
    "report_status": "BLOCK",
    "report_path": "...",
    "overrideable": false
  }
]
```

**Note:** `failed_gates` is a list of **dicts**, not strings.
Use `[g["gate_id"] for g in report["failed_gates"]]` to get IDs.

---

## Tool 7 — `bundle_diff_auditor.py`

**Wrapper action:** Not in initial 4-action set; future extension  
**Script path:** `aletheia_toolchain/bundle_diff_auditor.py`

### **MISMATCH: manifest flag name**

The manifest CSV input flag is **`--current-manifest`**, not `--manifest`.
This differs from every other manifest-consuming tool.

```
python bundle_diff_auditor.py
    --old-bundle       <old_bundle.json>       # required
    --current-manifest <file_map.csv>          # required — NOT --manifest
    --out              <audit_report.json>     # required
    [--new-bundle      <new_bundle.json>]
    [--markdown-out    <audit_report.md>]
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (CURRENT, STALE, or INCOMPLETE — read JSON to determine) |
| 1 | File not found or JSON/OS error |

---

## Tool 8 — `workspace_packager_v2.4.py`

**Wrapper action:** Not in initial 4-action set; future extension  
**Script path:** `aletheia_toolchain/workspace_packager_v2.4.py`

### Flag summary

```
python workspace_packager_v2.4.py
    <path>                        # positional, default "."
    [--manifest   <csv>]
    [--config     <json>]
    [--profile    <name>]
    [--staging-dir <dir>]
    [--format     text|json|xml]  # default text
    [-o / --output <file>]        # uses -o/--output, not --out
```

### Exit codes: 0=success, 1=error

### **Note:** uses `-o`/`--output`, not `--out`

---

## Tool 9 — `notebook_packager_v3.1.py`

**Wrapper action:** Not in initial 4-action set; future extension  
**Script path:** `aletheia_toolchain/notebook_packager_v3.1.py`

### Flag summary

```
python notebook_packager_v3.1.py
    <path>                        # positional, default "."
    [--manifest   <csv>]
    [--config     <json>]
    [--profile    <name>]
    [--staging-dir <dir>]
    [--requirements-mode auto|off|required]  # default auto
    [-o / --output <file.ipynb>]             # uses -o/--output, not --out
```

### Exit codes: 0=success, 1=error (incl. requirements-mode=required + missing requirements.txt)

### **Note:** uses `-o`/`--output`, not `--out`

---

## Recommended Wrapper Action Mapping

The following table shows exact flag sequences the `runner.py` `ToolRunner`
methods must use for each wrapper action.  All paths must be absolute.
CWD for all subprocesses: `aletheia_toolchain/`.

| Wrapper action | Script | Minimum argv (wrapper-controlled) |
|---|---|---|
| `scan_directory` | `create_file_map_v3.py` | `--roots <target_repo> --out <session/intermediate/file_map.csv> --health-report <session/intermediate/file_map_health.json> --hash --profile safe` |
| `validate_manifest` | `manifest_doctor.py` | `--manifest <session/intermediate/file_map.csv> --out <session/intermediate/manifest_doctor.json> --markdown-out <session/intermediate/manifest_doctor.md>` |
| `lint_tool_command` | `tool_command_linter.py` | `--command <command_string> --out <session/intermediate/command_lint.json>` |
| `run_semantic_slice` | `semantic_slicer_v7.0.py` | `--manifest <session/intermediate/file_map.csv> -o <session/intermediate/semantic_slice.json> --base-dir <target_repo> --format json --deterministic` |

---

## Unresolved CLI Mismatches and Risks

### RISK-1: Output flag inconsistency

Tools 4, 8, 9 use `-o` / `--output`; tools 1, 2, 3, 5, 6, 7 use `--out`.
The runner must use the correct flag per tool.  A single generic `--out`
argument will silently fail on the slicer and the packagers.

**Recommendation:** Hardcode the correct flag in each runner method.  Do not
use a shared `output_flag` constant.

### RISK-2: `bundle_diff_auditor.py` manifest flag name

The flag is `--current-manifest`, not `--manifest`.  Any wrapper code that
passes `--manifest` to this tool will receive an unrecognised-argument error.

**Recommendation:** Use `--current-manifest` in the `BundleAuditAction` runner
method when that tool is added to the wrapper.

### RISK-3: `semantic_slicer_v7.0.py` default output path

Without an explicit `-o`, the slicer writes the bundle into the target
repository's root directory.  This violates the wrapper's output-boundary
rule.  The runner **must** always pass `-o` to the slicer.

**Recommendation:** Treat missing `-o` as a policy error in the runner, not
just a default.  Assert that `args["-o"]` is always set before invoking the
slicer.

### RISK-4: `semantic_slicer_v7.0.py` non-integer exit codes

The slicer calls `sys.exit(string_message)` on several error paths.  Python
prints the string to stderr and exits with code 1, but the returncode is 1
regardless of the message.  The wrapper must capture stderr for the session
log when returncode != 0.

**Recommendation:** In `ToolRunner._run()`, always capture stderr and attach
it to the `SessionStep.outputs["command"]["stderr"]`.

### RISK-5: `manifest_doctor.py` exit 2 for both schema errors and BLOCK status

The runner receives exit code 2 for two different conditions:
- Malformed CSV (schema error) → report has `"status": "BLOCK"` and `"error": "..."` key
- Normal BLOCK evaluation → report has `"status": "BLOCK"` with full findings

In both cases the JSON report is written.  The wrapper should read the
JSON and check for the `"error"` key to distinguish the two.

**Recommendation:** In `validate_manifest` result, check for `report.get("error")`
to surface schema errors distinctly from BLOCK evaluations.

### RISK-6: `create_file_map_v3.py` default output in CWD

Without explicit `--out`, the CSV is written to `file_map.csv` in the
subprocess CWD (`aletheia_toolchain/`).  This violates the output-boundary
rule.  The runner must always pass explicit `--out`.

---

## Notes for future actions (not initial 4)

| Tool | Dependency chain | Note |
|---|---|---|
| `architecture_validator.py` | Requires slicer bundle first | Cannot call directly after `scan_directory`; needs `run_semantic_slice` output |
| `pipeline_gatekeeper.py` | Requires 1–3 prior JSON reports | None required by default policy; any combination of manifest/validator/runtime reports |
| `bundle_diff_auditor.py` | Requires old bundle + current manifest | Use `--current-manifest`, not `--manifest` |
| `workspace_packager_v2.4.py` | Standalone | Uses `-o/--output`, not `--out` |
| `notebook_packager_v3.1.py` | Standalone | Uses `-o/--output`, not `--out` |

---

## Summary: What Every Runner Method Must Always Do

1. Set `cwd=TOOLCHAIN_ROOT` (absolute path to `aletheia_toolchain/`).
2. Always provide explicit output paths — never rely on defaults.
3. Use the correct output flag per tool: `--out` for tools 1–3, 5–7; `-o` for tools 4, 8–9.
4. Capture stdout and stderr; attach to session step log.
5. Treat any non-zero returncode as failure; read the JSON report for status details.
6. For the slicer: always pass `--manifest <csv>` and explicit `-o <output>`.
7. Never pass `shell=True` to `subprocess.run`.
8. Never forward `*_API_KEY`, `*_TOKEN`, or `*_SECRET` env vars to subprocesses.
