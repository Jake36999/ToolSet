# Aletheia Toolchain — Workspace Notes

## Canonical Root

All managed toolchain code lives under:

```
D:\Aletheia_project\DEV_TOOLS\ToolSet\aletheia_toolchain\
```

Legacy tools (`semantic_slicer_*.py`, `workspace_packager_v2.3.py`, `create_file_map_v2.py`,
`notebook_packager.py`) live at the ToolSet root and must not be moved or modified by this upgrade.

---

## Directory Structure

```
aletheia_toolchain/
├── aletheia_tool_core/                    # Shared package — Phase 1
│   ├── __init__.py
│   ├── config.py
│   ├── manifest.py
│   ├── reports.py
│   └── security.py
├── docs/                                  # Documentation — Phase 12 + agent schema
│   ├── testing.md
│   ├── toolchain_overview.md              # All 13 tools, status semantics, two-root model
│   ├── agent_workflows.md                 # Canonical command sequences for 12 workflows
│   ├── tool_assist_schemas.md             # OIR/TAER patterns, handover separation rules
│   └── local_tool_assist_provider_integrations.md  # OpenAI/Claude API adapter design
├── examples/
│   └── configs/
│       ├── python_project.json
│       ├── polyglot_runtime.json
│       └── training_pipeline.json
├── scripts/                               # CI driver — Phase 12
│   └── run_ci_checks.py
├── tests/
│   ├── test_config.py
│   ├── test_manifest.py
│   ├── test_reports.py
│   ├── test_security.py
│   ├── test_create_file_map_v3.py
│   ├── test_manifest_doctor.py
│   ├── test_tool_command_linter.py
│   ├── test_semantic_slicer_v7.py
│   ├── test_architecture_validator.py
│   ├── test_runtime_end_watcher.py
│   ├── test_runtime_forensics.py
│   ├── test_pipeline_gatekeeper.py
│   ├── test_bundle_diff_auditor.py
│   ├── test_workspace_packager_v2_4.py
│   ├── test_notebook_packager_v3_1.py
│   ├── test_regression_fixtures.py        # Phase 12
│   ├── test_e2e_pipeline.py               # Phase 12
│   └── fixtures/
│       └── transcript_regressions/
│           ├── sample_manifest.csv
│           ├── sample_command.ps1
│           ├── sample_bundle.json
│           ├── README.md
│           ├── commands/                  # Phase 12 command regression fixtures
│           │   ├── cmd_v2_o_flag.json
│           │   ├── cmd_v3_o_flag.json
│           │   ├── cmd_broad_scan.json
│           │   └── cmd_manifest_plus_dot.json
│           └── edge_cases/
│               ├── empty_directory.csv
│               ├── malformed_manifest.csv
│               ├── oversized_files.csv
│               └── polluted_manifest.csv  # Phase 12
├── test_artifacts/                        # CI failure artifacts — gitignored
├── _quarantine/                           # Superseded drafts — do not import
├── architecture_validator.py              # Phase 7
├── bundle_diff_auditor.py                 # Phase 10
├── create_file_map_v3.py                  # Phase 2
├── manifest_doctor.py                     # Phase 3
├── notebook_packager_v3.1.py              # Phase 11
├── oom_forensics_reporter.py              # Phase 9
├── pipeline_gatekeeper.py                 # Phase 10
├── runtime_end_watcher.py                 # Phase 8
├── runtime_packager.py                    # Phase 9
├── runtime_slice_correlator.py            # Phase 9
├── semantic_project_config.schema.json    # Phase 5
├── semantic_slicer_v7.0.py                # Phase 6
├── tool_command_linter.py                 # Phase 4
├── workspace_packager_v2.4.py             # Phase 11
└── WORKSPACE_NOTES.md
```

---

## Phase Gate Rules

A phase may not begin until:
1. An authorized user explicitly approves it in writing.
2. All tests for the preceding phase pass from `aletheia_toolchain/` as the working directory.

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | COMPLETE | `aletheia_tool_core` shared package |
| Phase 2 | COMPLETE | `create_file_map_v3.py` — 27/27 tests |
| Phase 3 | COMPLETE | `manifest_doctor.py` — 37/37 tests |
| Phase 4 | COMPLETE | `tool_command_linter.py` — 64/64 tests, rules R001–R007 |
| Phase 5 | COMPLETE | `semantic_project_config.schema.json` + config helpers — 90/90 tests |
| Phase 6 | COMPLETE | `semantic_slicer_v7.0.py` config integration — 118/118 tests |
| Phase 7 | COMPLETE | `architecture_validator.py` — 154/154 tests, rules R-AV001–R-AV010 |
| Phase 8 | COMPLETE | `runtime_end_watcher.py` — 179/179 tests, 7-artifact output |
| Phase 9 | COMPLETE | OOM forensics, slice correlator, runtime packager — 209/209 tests |
| Phase 10 | COMPLETE | `pipeline_gatekeeper.py`, `bundle_diff_auditor.py` — 244/244 tests |
| Phase 11 | COMPLETE | `workspace_packager_v2.4.py`, `notebook_packager_v3.1.py` — 274/274 tests |
| Phase 12 | COMPLETE | CI + end-to-end regression suite — 289/289 tests |
| Agent schema | COMPLETE | docs/toolchain_overview.md, agent_workflows.md, tool_assist_schemas.md |

---

## Known Limitations

### Bundle-extraction redaction false-positive

`sanitize_content` (in `aletheia_tool_core.security`) uses Shannon entropy +
keyword detection to redact secrets.  The expression `lambda x: x["file"]`
exceeds the entropy threshold and contains `"key"`, so it appears as
`[REDACTED_HIGH_ENTROPY]` in review bundles.

**The source file is never modified.**  This is a known false-positive.

---

## How to Run Tests

From `aletheia_toolchain/` as the working directory:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Do NOT run from the ToolSet root or DEV_TOOLS root.

---

## Hard Rules for Agents

1. **Python files must contain Python.** Never write prose or Markdown into a `.py` file.
2. **Work from `aletheia_toolchain/` as CWD.** All CLI invocations and test runs use this directory.
3. **Copy before delete.** Verify destination files before removing the source.
4. **No stray output artifacts.** Bundle outputs, CSV files, and slicer results must not be written inside this directory (except under `test_artifacts/` for CI).
5. **No legacy tools inside `aletheia_toolchain/`.** `create_file_map_v2.py`, legacy packagers, and slicers live at the ToolSet root only.
6. **Quarantine before delete.** Unapproved code goes to `_quarantine/`, not the trash.
