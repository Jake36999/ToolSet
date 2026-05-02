# Aletheia Developer Toolchain — Overview

This document describes the complete Aletheia toolchain (Phases 1–12).
It is the primary reference for agents, builders, and reviewers who need
to know what tools exist, what each one does, and how they relate.

---

## Two-Root Model

The workspace has two distinct roots.  **Never mix them.**

| Root | Contains | Managed by |
|---|---|---|
| `ToolSet/` | Legacy tools, workspace-level CI context | Frozen — do not modify |
| `ToolSet/aletheia_toolchain/` | Upgraded managed tools (Phases 1–12) | This toolchain |

All commands in this document run from `aletheia_toolchain/` as CWD unless
otherwise specified.

### Legacy tools (ToolSet root — do not modify)

| File | Version | Notes |
|---|---|---|
| `create_file_map_v2.py` | v2 | Superseded by v3; R001 BLOCK if `-o` used |
| `semantic_slicer_v6.0.py` | v6.0 | Superseded by v7.0 |
| `workspace_packager_v2.3.py` | v2.3.1 | Superseded by v2.4 |
| `notebook_packager.py` | v3 | Superseded by v3.1 |

---

## Managed Tools (aletheia_toolchain/)

### Phase 1 — Shared package

**`aletheia_tool_core/`** — Internal helpers imported by all managed tools.

| Module | Key exports |
|---|---|
| `config.py` | `load_json_config()`, `validate_config()`, `resolve_profile()`, `resolve_precedence()` |
| `manifest.py` | `load_manifest_csv()`, `DEFAULT_SUSPICIOUS_DIRECTORIES` |
| `reports.py` | `write_json_report()`, `write_markdown_report()` |
| `security.py` | `is_binary_file()`, `sanitize_content()` |

All managed tools import from `aletheia_tool_core` with a stdlib-only
fallback so they remain runnable even when the package is unavailable.

---

### Phase 2 — Discovery

**`create_file_map_v3.py`** — Builds a manifest CSV of project files.

```
python create_file_map_v3.py --roots <DIR> [DIR...] --out <file.csv>
    [--profile <name>] [--config <config.json>]
    [--include-exts <.py .md ...>] [--exclude-dirs <__pycache__ ...>]
    [--hash] [--health-report] [--max-file-size <bytes>]
    [--fail-on-pollution]
```

Output columns: `root, rel_path, abs_path, ext, size, mtime_iso, sha1`

Health status in report: `PASS | WARN | BLOCK`

Built-in profiles: `default, safe, python, python_project, polyglot_runtime, training_pipeline, node_python_runtime`

---

### Phase 3 — Manifest validation

**`manifest_doctor.py`** — Validates a manifest CSV before use by downstream tools.

```
python manifest_doctor.py --manifest <file.csv> --out <report.json>
    [--config <config.json>] [--markdown-out <report.md>]
```

Output report fields: `status, manifest_path, summary, findings, recommended_action`

`findings` keys: `missing_files, suspicious_paths, bundle_artifacts, oversize_files, missing_required_paths, missing_required_exts`

Status: `PASS | WARN | BLOCK`  
Exit: 0 (PASS/WARN), 2 (BLOCK), 1 (invocation error)

---

### Phase 4 — Command linting

**`tool_command_linter.py`** — Validates tool invocations before execution.

```
python tool_command_linter.py --command "<CMD>" --out <report.json>
# or
python tool_command_linter.py --command-file <script.ps1> --out <report.json>
    [--config <config.json>] [--rewrite-out <suggestions.txt>]
```

Report fields: `status, errors[], warnings[], autofix_suggestions[]`

Rules:

| Rule | Trigger | Status |
|---|---|---|
| R001 | `create_file_map_v2` with `-o` | BLOCK |
| R002 | `create_file_map_v3` with `-o` | WARN |
| R003 | Slicer positional `.` without `--manifest` | WARN |
| R004 | Slicer `--manifest` + positional `.` | BLOCK |
| R005 | Slicer automated extraction without `--deterministic` | WARN |
| R006 | Output path matches re-ingestion pattern | WARN |
| R007 | Command file has broad slicer without manifest_doctor step | WARN |

Exit: 0 (PASS/WARN), 2 (BLOCK), 1 (error)

---

### Phase 5 — Project configuration

**`semantic_project_config.schema.json`** — JSON Schema for project config files.

**`examples/configs/`** — Reference configs for common project types:
- `python_project.json`
- `polyglot_runtime.json`
- `training_pipeline.json`

Config files are consumed by `create_file_map_v3`, `manifest_doctor`,
`tool_command_linter`, and `semantic_slicer_v7.0` via `--config + --profile`.

---

### Phase 6 — Semantic slicing

**`semantic_slicer_v7.0.py`** — Extracts a structured source bundle from a
manifest-scoped project.

```
python semantic_slicer_v7.0.py [paths...] --out <bundle.py>
    [--manifest <file.csv>] [--config <config.json>] [--task-profile <name>]
    [--deterministic] [--validate-only]
    [--allow-path-merge-with-manifest]
```

Safety block: `--manifest` + positional paths exits non-zero unless
`--allow-path-merge-with-manifest` is present.

Config-wired: `exclude_dirs, include_exts` whitelist, `deterministic` default,
`append_rules` default, `max_file_size` override.

---

### Phase 7 — Architecture validation

**`architecture_validator.py`** — Validates project structure against declared
expectations.

```
python architecture_validator.py --manifest <file.csv>
    [--config <config.json>] [--out <report.json>] [--markdown-out <report.md>]
    [--plugin <module>]
```

Rules R-AV001 through R-AV010. Status: `PASS | WARN | FAIL`

---

### Phase 8 — Runtime observation

**`runtime_end_watcher.py`** — Wraps a subprocess, streams its output, and
writes a 7-artifact bundle on exit.

```
python runtime_end_watcher.py --name <label> --out-dir <dir>
    [--timeout <seconds>] [--sample-seconds <n>]
    [--metrics-mode none|basic|full]
    [--python-faultevidence] [--python-tracemalloc]
    --cmd <executable> [args...]
```

Output artifacts (all in `--out-dir`):

| File | Content |
|---|---|
| `stdout.log` | Full process stdout |
| `stderr.log` | Full process stderr |
| `runtime_metrics.json` | Timing, exit code, metrics |
| `timeline.csv` | Metric samples (one row per interval) |
| `stdout_tail.txt` | Last 50 lines of stdout, redacted |
| `stderr_tail.txt` | Last 50 lines of stderr, redacted |
| `runtime_summary.md` | Human-readable Markdown summary |

Exit: 0 (success), 1 (timeout / start failure)

---

### Phase 9 — Runtime forensics

Three tools that analyse `runtime_end_watcher` output:

**`oom_forensics_reporter.py`** — Diagnoses OOM risk from runtime metrics.

```
python oom_forensics_reporter.py --metrics-json <runtime_metrics.json>
    [--out <report.json>]
```

Heuristic findings OOM-001 through OOM-030. Confidence: HIGH/MEDIUM/LOW.
Report field: `overall_memory_risk` (NONE/LOW/MEDIUM/HIGH).
Exit: 0 always (even HIGH risk); exit 1 on invocation error.

**`runtime_slice_correlator.py`** — Correlates runtime failures to source slices.

```
python runtime_slice_correlator.py --watcher-dir <dir> --bundle-json <bundle.py>
    [--out <report.json>]
```

Top-K=5 correlations. Degrades gracefully without slices.

**`runtime_packager.py`** — Packages watcher output for handover/review.

```
python runtime_packager.py --watcher-dir <dir> [--out <package.json>]
```

Redacts tail content; never includes full log content.

---

### Phase 10 — Pipeline gatekeeping and bundle audit

**`pipeline_gatekeeper.py`** — Aggregates tool reports and issues a PASS/WARN/BLOCK verdict.

```
python pipeline_gatekeeper.py --out <report.json>
    [--manifest-report <doctor.json>]
    [--validator-report <validator.json>]
    [--runtime-report <oom.json>]
    [--policy <policy.json>]
    [--markdown-out <report.md>]
```

Report fields: `status, failed_gates[], warning_gates[], overrideable_gates[], missing_reports[], recommended_next_action, input_report_summary`

`failed_gates` entries are dicts: `{gate_id, label, outcome, report_status, report_path, overrideable}`

Exit: 0 (PASS/WARN), 2 (BLOCK), 1 (invocation error)

**`bundle_diff_auditor.py`** — Compares an old slicer bundle against the current manifest.

```
python bundle_diff_auditor.py --old-bundle <bundle.py> --manifest <file.csv>
    [--new-bundle <bundle.py>] [--out <report.json>]
```

Status: `CURRENT | STALE | INCOMPLETE`

Priority: INCOMPLETE > STALE > CURRENT

---

### Phase 11 — Packaging

**`workspace_packager_v2.4.py`** — Bundles workspace files for review.

```
python workspace_packager_v2.4.py <path> [--out <file>] [--format text|json|xml]
    [--manifest <file.csv>] [--config <config.json>] [--profile <name>]
    [--staging-dir <dir>]
```

**`notebook_packager_v3.1.py`** — Packages a project as a Colab-ready notebook.

```
python notebook_packager_v3.1.py <path> [-o <file.ipynb>]
    [--manifest <file.csv>] [--config <config.json>] [--profile <name>]
    [--staging-dir <dir>]
    [--requirements-mode auto|off|required]
```

`--requirements-mode`:
- `auto` — install if `requirements.txt` present (default)
- `off` — never generate pip install cell
- `required` — exit 1 if `requirements.txt` absent

---

### Phase 12 — CI and regression suite

**`scripts/run_ci_checks.py`** — Local + CI test driver.

```
python scripts/run_ci_checks.py [--verbose] [--junit-xml <path>]
```

Exit: 0 (all pass), 1 (any fail)

**`.github/workflows/ci.yml`** — GitHub Actions matrix CI.
Matrix: `windows-latest` + `ubuntu-latest` × Python `3.10` + `3.11`.
Uploads `test_artifacts/` as a build artifact on failure.

---

## Status Semantics

All tools that produce a `status` field use the same three-value scale:

| Value | Meaning |
|---|---|
| `PASS` | No issues; safe to proceed |
| `WARN` | Non-fatal issues; review before proceeding |
| `BLOCK` | At least one blocking issue; must not proceed without fixing |

Exit code conventions:

| Code | Meaning |
|---|---|
| 0 | PASS or WARN |
| 1 | Invocation error / I/O error |
| 2 | BLOCK |

---

## Known Limitations

### Review-bundle redaction false-positive

`sanitize_content` (in `aletheia_tool_core.security`) uses Shannon entropy +
keyword detection to redact secrets.  The expression `lambda x: x["file"]`
contains the keyword `"key"` and exceeds the entropy threshold, so it appears
as `[REDACTED_HIGH_ENTROPY]` in bundles produced by `workspace_packager_v2.4.py`
or `notebook_packager_v3.1.py`.

**The source file is never modified.**

When a review bundle contains `[REDACTED_HIGH_ENTROPY]` in a location that
looks like source code rather than a credential, use direct file extraction
(e.g. `Read` tool on the source path) instead of reading from the bundle.
