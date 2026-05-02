# Aletheia Toolchain — Testing Guide

## Running the full test suite

From the `aletheia_toolchain/` directory:

```
python -m unittest discover -s tests -p "test_*.py" -v
```

Expected: **289+ tests**, all passing.

## Running a single module

```
python -m unittest discover -s tests -p "test_manifest_doctor.py" -v
```

## CI script

The `scripts/run_ci_checks.py` driver wraps the standard unittest runner and
prints a colour-coded summary:

```
python scripts/run_ci_checks.py --verbose
```

Optional JUnit XML output (requires `unittest-xml-reporting`):

```
python scripts/run_ci_checks.py --junit-xml test_artifacts/results.xml
```

## Test structure

| Module | Phase | What it covers |
|---|---|---|
| `test_config.py` | 5 | `aletheia_tool_core.config` helpers |
| `test_manifest.py` | 1 | `aletheia_tool_core.manifest` helpers |
| `test_reports.py` | 1 | `aletheia_tool_core.reports` helpers |
| `test_security.py` | 1 | `aletheia_tool_core.security` helpers |
| `test_create_file_map_v3.py` | 2 | `create_file_map_v3.py` — profiles, health status |
| `test_manifest_doctor.py` | 3 | `manifest_doctor.py` — checks, status, Markdown report |
| `test_tool_command_linter.py` | 4 | `tool_command_linter.py` — rules R001–R007 |
| `test_semantic_slicer_v7.py` | 6 | `semantic_slicer_v7.0.py` — config integration, safety blocks |
| `test_architecture_validator.py` | 7 | `architecture_validator.py` — rules R-AV001–R-AV010 |
| `test_runtime_end_watcher.py` | 8 | `runtime_end_watcher.py` — 7-artifact output, timeout, metrics |
| `test_runtime_forensics.py` | 9 | OOM reporter, slice correlator, runtime packager |
| `test_pipeline_gatekeeper.py` | 10 | `pipeline_gatekeeper.py` — gate policy, PASS/WARN/BLOCK |
| `test_bundle_diff_auditor.py` | 10 | `bundle_diff_auditor.py` — CURRENT/STALE/INCOMPLETE |
| `test_workspace_packager_v2_4.py` | 11 | `workspace_packager_v2.4.py` |
| `test_notebook_packager_v3_1.py` | 11 | `notebook_packager_v3.1.py` |
| `test_regression_fixtures.py` | 12 | Transcript regression: R001–R004, polluted manifest |
| `test_e2e_pipeline.py` | 12 | End-to-end pipeline, watcher artifacts, golden snapshot |

## Fixtures

All test fixtures live under `tests/fixtures/transcript_regressions/`.

| Path | Purpose |
|---|---|
| `sample_manifest.csv` | Canonical 6-row healthy manifest |
| `sample_bundle.json` | Reference slicer output bundle |
| `sample_command.ps1` | Reference PS1 command file |
| `commands/cmd_v2_o_flag.json` | R001 regression: `create_file_map_v2 -o` |
| `commands/cmd_v3_o_flag.json` | R002 regression: `create_file_map_v3 -o` |
| `commands/cmd_broad_scan.json` | R003 regression: slicer broad scan |
| `commands/cmd_manifest_plus_dot.json` | R004 regression: slicer `--manifest + .` |
| `edge_cases/empty_directory.csv` | Header-only manifest |
| `edge_cases/malformed_manifest.csv` | Duplicate rows |
| `edge_cases/oversized_files.csv` | Large file, null SHA1 |
| `edge_cases/polluted_manifest.csv` | Bundle artifact + suspicious directory |

## Failure artifacts

`test_artifacts/` is excluded from version control (see `.gitignore`).  On
CI, the directory is uploaded as a build artifact when any step fails so that
the failing output can be inspected without re-running locally.

The `TestE2EFailureArtifact` test class writes `test_artifacts/e2e_block_gatekeeper.json`
to exercise this path.

## Known limitations

### Bundle-extraction redaction false-positive

The `sanitize_content` helper in `aletheia_tool_core.security` uses Shannon
entropy + keyword detection to redact potential secrets.  The expression
`lambda x: x["file"]` exceeds the entropy threshold and contains the keyword
`"key"`, so it appears as `[REDACTED_HIGH_ENTROPY]` in review bundles generated
by `workspace_packager_v2.4.py` or `notebook_packager_v3.1.py`.

**The source file is never modified.**  The redaction only affects the
generated review bundle.  This is a known false-positive; the heuristic is
intentionally conservative.

## Legacy tools

The following files live at the `ToolSet/` root and are **not** managed by
this toolchain.  Do not move, rename, or modify them:

- `create_file_map_v2.py`
- `semantic_slicer_v6.0.py`
- `workspace_packager_v2.3.py`
- `notebook_packager.py`
