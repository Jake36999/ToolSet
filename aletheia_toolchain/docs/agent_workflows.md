# Aletheia Toolchain — Agent Workflow Examples

This document shows the canonical command sequences for each workflow pattern
supported by the Aletheia toolchain.  All commands run from
`aletheia_toolchain/` as CWD unless noted.

For full CLI reference see [toolchain_overview.md](toolchain_overview.md).

---

## 1. Discovery scan

Generate a manifest CSV from a project directory.

```powershell
# Basic scan — default profile
python create_file_map_v3.py --roots D:\myproject --out manifest.csv --hash

# With project config
python create_file_map_v3.py --roots D:\myproject --out manifest.csv `
    --config examples/configs/python_project.json --profile default --hash
```

Output: `manifest.csv` with columns `root, rel_path, abs_path, ext, size, mtime_iso, sha1`

---

## 2. Manifest validation before slicing

Always validate a manifest before passing it to downstream tools.

```powershell
python manifest_doctor.py --manifest manifest.csv --out doctor_report.json
```

Check `doctor_report.json`:
- If `status` is `BLOCK` — do not proceed; fix the listed `findings.missing_files`
- If `status` is `WARN` — review `findings.suspicious_paths` and `findings.bundle_artifacts`
- If `status` is `PASS` — proceed

---

## 3. Command linting before execution

Lint any tool invocation string before running it.

```powershell
# Inline command
python tool_command_linter.py \
    --command "python semantic_slicer_v7.0.py --manifest manifest.csv src/" \
    --out lint_report.json

# Script file
python tool_command_linter.py \
    --command-file my_pipeline.ps1 \
    --out lint_report.json
```

Check `lint_report.json`:
- `errors[]` — BLOCK-level findings; do not run without fixing
- `warnings[]` — WARN-level findings; review before running
- `autofix_suggestions[]` — suggested rewrites

Key rules:
- R001: `create_file_map_v2 -o` → BLOCK (use `--out` instead)
- R004: slicer `--manifest + positional .` → BLOCK (ambiguous scope)

---

## 4. Precision slice

Slice a project using a validated manifest.

```powershell
python semantic_slicer_v7.0.py --manifest manifest.csv --deterministic \
    --out bundle.py
```

With config:

```powershell
python semantic_slicer_v7.0.py --manifest manifest.csv --deterministic \
    --config examples/configs/python_project.json --task-profile ci \
    --out bundle.py
```

Do not pass positional path arguments together with `--manifest` unless you
also pass `--allow-path-merge-with-manifest`.

---

## 5. Architecture validation

Validate project structure against declared expectations.

```powershell
python architecture_validator.py --manifest manifest.csv \
    --config examples/configs/python_project.json \
    --out arch_report.json --markdown-out arch_report.md
```

Status `FAIL` in `arch_report.json` indicates structural violations.

---

## 6. Pipeline gatekeeping

Combine doctor + validator + OOM reports into a single PASS/WARN/BLOCK verdict.

```powershell
python pipeline_gatekeeper.py \
    --manifest-report doctor_report.json \
    --validator-report arch_report.json \
    --out gate_report.json
```

Exit code 2 = BLOCK.  Check `gate_report.json`:
- `failed_gates[]` — each entry has `gate_id`, `outcome`, `report_path`
- `recommended_next_action` — human-readable next step

With custom policy:

```powershell
python pipeline_gatekeeper.py \
    --manifest-report doctor_report.json \
    --policy my_policy.json \
    --out gate_report.json
```

---

## 7. Runtime watcher run

Wrap any subprocess to capture timing, logs, and memory metrics.

```powershell
python runtime_end_watcher.py \
    --name "training_run_001" \
    --out-dir runs/training_run_001 \
    --metrics-mode full \
    --timeout 3600 \
    --cmd python train.py --config configs/base.yaml
```

After the run, `runs/training_run_001/` contains:
`stdout.log, stderr.log, runtime_metrics.json, timeline.csv,`
`stdout_tail.txt, stderr_tail.txt, runtime_summary.md`

---

## 8. OOM / runtime forensics

Analyse a completed watcher run for memory risk and correlate failures to source.

```powershell
# Step 1 — OOM risk report
python oom_forensics_reporter.py \
    --metrics-json runs/training_run_001/runtime_metrics.json \
    --out oom_report.json

# Step 2 — Slice correlation (requires a source bundle)
python runtime_slice_correlator.py \
    --watcher-dir runs/training_run_001 \
    --bundle-json bundle.py \
    --out correlation_report.json

# Step 3 — Package the watcher run for handover
python runtime_packager.py \
    --watcher-dir runs/training_run_001 \
    --out runtime_package.json
```

`oom_report.json` field `overall_memory_risk`: NONE / LOW / MEDIUM / HIGH.
Exit is always 0 (even for HIGH risk); only exits 1 on invocation error.

---

## 9. Post-implementation verification

After adding or changing code, run the full verification sequence.

```powershell
# 1. Re-scan
python create_file_map_v3.py --roots . --out manifest.csv --hash

# 2. Validate manifest
python manifest_doctor.py --manifest manifest.csv --out doctor_report.json

# 3. Validate architecture
python architecture_validator.py --manifest manifest.csv \
    --out arch_report.json

# 4. Gate
python pipeline_gatekeeper.py \
    --manifest-report doctor_report.json \
    --validator-report arch_report.json \
    --out gate_report.json

# 5. Run tests
python -m unittest discover -s tests -p "test_*.py" -v
```

---

## 10. Bundle staleness audit

Check whether an existing slicer bundle is still current.

```powershell
python bundle_diff_auditor.py \
    --old-bundle bundle.py \
    --manifest manifest.csv \
    --out audit_report.json
```

Status in `audit_report.json`:
- `CURRENT` — bundle matches manifest; safe to use
- `STALE` — fingerprints drifted; re-slice before use
- `INCOMPLETE` — manifest has files not in the bundle; re-slice required

---

## 11. Workspace packaging

Bundle workspace files for context handover or review.

```powershell
# Path mode — all files under src/
python workspace_packager_v2.4.py src/ --format json --out workspace_bundle.json

# Manifest mode — only manifest-listed files
python workspace_packager_v2.4.py src/ \
    --manifest manifest.csv \
    --format json --out workspace_bundle.json

# With config
python workspace_packager_v2.4.py src/ \
    --config examples/configs/python_project.json --profile ci \
    --format json --out workspace_bundle.json

# Staging dir (output written to dir, filename auto-generated)
python workspace_packager_v2.4.py src/ \
    --staging-dir review_staging --format json
```

**Redaction note:** `sanitize_content` may redact high-entropy Python lines
(e.g. `lambda x: x["file"]`) as `[REDACTED_HIGH_ENTROPY]` in the output
bundle.  The source file is never modified.  Use direct file extraction
if a source line appears suspiciously redacted.

---

## 12. Notebook packaging

Package a project as a self-extracting Colab notebook.

```powershell
# Basic
python notebook_packager_v3.1.py src/ -o project.ipynb

# Manifest-scoped
python notebook_packager_v3.1.py src/ \
    --manifest manifest.csv -o project.ipynb

# With requirements install
python notebook_packager_v3.1.py src/ \
    --requirements-mode required -o project.ipynb

# With config + staging
python notebook_packager_v3.1.py src/ \
    --config examples/configs/python_project.json --profile ci \
    --staging-dir notebooks/
```

`--requirements-mode required` exits 1 if `requirements.txt` is absent from
the collected files.

---

## Workflow: Full pre-submission pipeline

Canonical sequence before submitting a PR or handing off to a reviewer:

```powershell
# 1. Lint planned commands
python tool_command_linter.py --command-file planned_commands.ps1 \
    --out lint_report.json

# 2. Scan
python create_file_map_v3.py --roots . --out manifest.csv --hash

# 3. Validate manifest
python manifest_doctor.py --manifest manifest.csv --out doctor_report.json

# 4. Validate architecture
python architecture_validator.py --manifest manifest.csv \
    --out arch_report.json

# 5. Gate
python pipeline_gatekeeper.py \
    --manifest-report doctor_report.json \
    --validator-report arch_report.json \
    --out gate_report.json

# 6. Audit bundle staleness
python bundle_diff_auditor.py \
    --old-bundle existing_bundle.py \
    --manifest manifest.csv \
    --out audit_report.json

# 7. Run test suite
python -m unittest discover -s tests -p "test_*.py" -v
```

Stop if any step produces `BLOCK` or exits with code 2.
