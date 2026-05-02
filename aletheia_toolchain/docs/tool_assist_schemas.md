# Aletheia Toolchain — Tool Assist OIR / TAER Reference

This document defines how Tool Assist agents should reason about and invoke
the Aletheia toolchain.  It covers:

- Observation–Interpretation–Response (OIR) patterns for each tool
- Tool Assist Evidence Report (TAER) templates for common outcomes
- Handover separation rules
- Schema for passing evidence artifacts to builder / reviewer agents

---

## Handover Separation Rules

These rules govern what information crosses role boundaries.

1. **Handovers are role-neutral.** Do not include Tool Assist governance
   process rules, phase gate decisions, or internal policy references in
   builder or reviewer prompts.

2. **Pass evidence artifacts, not process rules.** A builder prompt should
   receive the JSON report paths and a concise task constraint derived from
   the evidence — not the internal Tool Assist criteria that produced it.

3. **Do not leak gate decisions.** Whether a gate passed or failed is an
   internal Tool Assist concern.  A builder agent receives: the artifact
   list, the failing file paths, and the constraint ("fix the missing_files
   listed in doctor_report.json before re-running").

4. **Reviewer prompts receive summary evidence only.**  Full log files and
   raw metrics JSON are kept as supporting artifacts; reviewers get the
   human-readable summary (`runtime_summary.md`) and the report JSON top-level
   fields.

---

## OIR Patterns

### Discovery scan

**Observation:** `manifest.csv` produced; `health_report.json` may exist.

**Interpretation:**
- Check `create_file_map_v3.py` exit code (0=pass, 2=block, 1=error).
- If `health_report.json` exists, check `status`.
- `WARN` on suspicious dirs or oversized files is expected for first scans.

**Response:**
- `PASS/WARN`: proceed to manifest validation.
- `BLOCK`: surface blocking rows before proceeding.

---

### Manifest validation

**Observation:** `doctor_report.json` produced.

**Interpretation:**
- `status` field: `PASS | WARN | BLOCK`
- `summary.missing_files` > 0 → BLOCK (paths don't exist on disk)
- `summary.bundle_artifacts` > 0 → WARN (bundle output polluting manifest)
- `summary.suspicious_paths` > 0 → WARN (env dirs leaked into scan)

**Response:**
- `PASS`: proceed.
- `WARN`: note the `recommended_exclude_additions` and pass to builder if
  the exclusion list needs updating.
- `BLOCK`: halt pipeline; pass `findings.missing_files` to builder as
  the constraint.

---

### Command linting

**Observation:** `lint_report.json` produced.

**Interpretation:**
- `errors[]`: each entry has `rule_id`, `message`, `fragment`, `autofix`.
- `warnings[]`: same structure; non-blocking.
- BLOCK = exit code 2; WARN = exit code 0.

**Response:**
- `BLOCK` (R001 or R004): do not execute the command; pass `autofix` to builder.
- `WARN` (R002, R003, R005, R006, R007): note warnings; builder may proceed
  if warnings are understood and accepted.

---

### Architecture validation

**Observation:** `arch_report.json` produced.

**Interpretation:**
- `status`: `PASS | WARN | FAIL`
- Rule IDs R-AV001 through R-AV010 appear in `findings[]`.

**Response:**
- `FAIL`: surface failing rule IDs and file paths to builder.
- `WARN`: note in handover but allow proceeding.

---

### Pipeline gate

**Observation:** `gate_report.json` produced.

**Interpretation:**
- `status`: `PASS | WARN | BLOCK`
- `failed_gates[]`: list of dicts `{gate_id, label, outcome, report_path, overrideable}`
- `overrideable_gates[]`: gates that failed but are marked overrideable
- `recommended_next_action`: human-readable summary

**Response:**
- `BLOCK` with non-overrideable gate: halt; pass `recommended_next_action`
  and the relevant `report_path` to builder.
- `BLOCK` with all overrideable: surface to reviewer for explicit override
  decision.
- `PASS/WARN`: proceed; include `warning_gates[]` in handover note if non-empty.

---

### Runtime watcher

**Observation:** 7-artifact directory produced under `--out-dir`.

**Interpretation:**
- `runtime_metrics.json` → `exit_code`, `wall_seconds`, `peak_rss_mb`
- `runtime_summary.md` → human-readable overview
- Non-zero exit code → process failed or timed out

**Response:**
- Exit 0: pass `runtime_summary.md` as evidence to reviewer.
- Exit 1 (timeout): escalate; pass `runtime_metrics.json` + `stderr_tail.txt`.
- For memory analysis: proceed to OOM forensics.

---

### OOM / runtime forensics

**Observation:** `oom_report.json` and `correlation_report.json` produced.

**Interpretation:**
- `oom_report.json`.`overall_memory_risk`: `NONE | LOW | MEDIUM | HIGH`
- `correlation_report.json`.`correlations[]`: top-K source files associated
  with the failure
- `correlation_report.json`.`degraded_mode`: true if no source slices available

**Response:**
- `NONE/LOW`: pass summary to reviewer; no builder action required.
- `MEDIUM/HIGH`: pass `oom_report.json` top-level fields + `correlations[]`
  to builder as memory constraint evidence.

---

### Bundle staleness audit

**Observation:** `audit_report.json` produced.

**Interpretation:**
- `status`: `CURRENT | STALE | INCOMPLETE`
- `INCOMPLETE`: files in manifest not in bundle → must re-slice
- `STALE`: fingerprint drift → should re-slice
- `CURRENT`: bundle is up to date

**Response:**
- `INCOMPLETE/STALE`: pass constraint "re-slice before use" to builder with
  the list of affected paths.
- `CURRENT`: proceed.

---

## TAER Templates

A Tool Assist Evidence Report summarises what happened and what the next
agent needs.  TAERs are role-neutral — they do not reference Tool Assist
process decisions.

### TAER: Manifest BLOCK

```
## Evidence: Manifest validation BLOCK

Manifest:  manifest.csv
Report:    doctor_report.json

Blocking findings:
  - missing_files: [list from doctor_report.json findings.missing_files]

Recommended constraint for builder:
  The files listed above do not exist at the declared abs_path.  Create or
  move them before re-running the discovery scan.

Supporting evidence:
  - doctor_report.json (full findings)
```

### TAER: Linter BLOCK

```
## Evidence: Command linter BLOCK

Command:   [the linted command string]
Report:    lint_report.json

Blocking rule:  [rule_id] — [message]
Autofix:        [autofix field]

Constraint for builder:
  Apply the autofix before executing this command.
```

### TAER: Pipeline BLOCK

```
## Evidence: Pipeline gate BLOCK

Report:  gate_report.json

Failed gates:
  - [gate_id]: [outcome] — see [report_path]

Recommended next action: [recommended_next_action from gate_report.json]

Supporting evidence:
  - gate_report.json
  - [individual report_path for each failed gate]
```

### TAER: Runtime HIGH memory risk

```
## Evidence: OOM forensics — HIGH memory risk

Watcher dir:    runs/<name>/
OOM report:     oom_report.json
Correlations:   correlation_report.json

overall_memory_risk: HIGH

Top correlated source files:
  [list from correlation_report.json correlations[].file]

Constraint for builder:
  Address memory usage in the correlated files before the next run.
  See oom_report.json suggested_rerun_commands for profiling options.
```

---

## Evidence Artifact Index

When passing evidence to a downstream agent, include only these fields.
Do not pass raw log files or full metric dumps.

| Tool | Artifact | Pass to builder | Pass to reviewer |
|---|---|---|---|
| `manifest_doctor` | `doctor_report.json` | `findings.missing_files`, `recommended_action` | `status`, `summary`, `recommended_action` |
| `tool_command_linter` | `lint_report.json` | `errors[]` with `autofix` | `status`, `warnings[]` |
| `architecture_validator` | `arch_report.json` | failing rule IDs + file paths | `status`, `summary` |
| `pipeline_gatekeeper` | `gate_report.json` | `failed_gates[]`, `recommended_next_action` | `status`, `warning_gates[]` |
| `oom_forensics_reporter` | `oom_report.json` | `overall_memory_risk`, `suggested_rerun_commands` | `overall_memory_risk` |
| `runtime_slice_correlator` | `correlation_report.json` | `correlations[]` top-3 | `degraded_mode`, count |
| `runtime_end_watcher` | `runtime_summary.md` | `runtime_summary.md` (if failure) | `runtime_summary.md` |
| `bundle_diff_auditor` | `audit_report.json` | `status`, affected paths | `status` |

---

## Two-Root Model — Agent Guidance

```
ToolSet/                          ← workspace root
├── create_file_map_v2.py         ← LEGACY — R001 BLOCK if used with -o
├── semantic_slicer_v6.0.py       ← LEGACY — superseded by v7.0
├── workspace_packager_v2.3.py    ← LEGACY — superseded by v2.4
├── notebook_packager.py          ← LEGACY — superseded by v3.1
└── aletheia_toolchain/           ← managed toolchain root (use this)
    ├── create_file_map_v3.py
    ├── manifest_doctor.py
    ├── tool_command_linter.py
    ├── semantic_slicer_v7.0.py
    ├── architecture_validator.py
    ├── runtime_end_watcher.py
    ├── oom_forensics_reporter.py
    ├── runtime_slice_correlator.py
    ├── runtime_packager.py
    ├── pipeline_gatekeeper.py
    ├── bundle_diff_auditor.py
    ├── workspace_packager_v2.4.py
    └── notebook_packager_v3.1.py
```

**Rule for agents:** Always invoke tools from the `aletheia_toolchain/`
directory.  Never invoke legacy tools from `ToolSet/` root unless
explicitly instructed — and if a legacy tool is invoked, always run
`tool_command_linter.py` on the command first.

---

## Redaction Caveat for Review Agents

When reading a workspace bundle or notebook produced by
`workspace_packager_v2.4.py` or `notebook_packager_v3.1.py`:

- `[REDACTED_HIGH_ENTROPY]` in the bundle output means a line in the
  source was flagged by the entropy heuristic.
- Common false-positive: `lambda x: x["file"]` (contains `"key"`, high entropy).
- The source file was **not modified**.

If a code line is suspiciously redacted, read the source file directly
(e.g. via the `Read` tool at the `abs_path` from the manifest) rather than
relying on the bundle content.
