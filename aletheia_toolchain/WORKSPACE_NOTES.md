# Aletheia Toolchain — Workspace Notes

## Canonical Root

All managed toolchain code lives under:

```
D:\Aletheia_project\DEV_TOOLS\ToolSet\aletheia_toolchain\
```

Legacy tools (`semantic_slicer_*.py`, `workspace_packager_*.py`, `create_file_map_v2.py`,
`notebook_packager.py`) live at the ToolSet root and must not be moved or modified by this upgrade.

---

## Directory Structure

```
aletheia_toolchain/
├── aletheia_tool_core/          # Shared package — Phase 1
│   ├── __init__.py
│   ├── config.py
│   ├── manifest.py
│   ├── reports.py
│   └── security.py
├── tests/                       # Test suite — Phase 1 + 2
│   ├── test_config.py
│   ├── test_manifest.py
│   ├── test_reports.py
│   ├── test_security.py
│   ├── test_create_file_map_v3.py
│   └── fixtures/
│       └── transcript_regressions/
│           ├── sample_manifest.csv
│           ├── sample_command.ps1
│           ├── sample_bundle.json
│           ├── README.md
│           └── edge_cases/
│               ├── empty_directory.csv
│               ├── malformed_manifest.csv
│               └── oversized_files.csv
├── _quarantine/                 # Unapproved phase work — do not import
│   ├── manifest_doctor.py       # Phase 3 (unapproved as of 2026-04-29)
│   └── test_manifest_doctor.py
├── create_file_map_v3.py        # Phase 2 entry point
└── WORKSPACE_NOTES.md           # This file
```

---

## Phase Gate Rules

A phase may not begin until:
1. An authorized user explicitly approves it in writing.
2. All tests for the preceding phase pass from `aletheia_toolchain/` as the working directory.

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | COMPLETE | `aletheia_tool_core` shared package + unit tests |
| Phase 2 | COMPLETE (pending full test pass) | `create_file_map_v3.py` |
| Phase 3 | QUARANTINED — not approved | `manifest_doctor.py` |

---

## Quarantine Policy

Files placed in `_quarantine/` are preserved but isolated:
- No `__init__.py` exists in `_quarantine/` — it is not a Python package.
- It is excluded from `unittest discover` invocations.
- Contents may only be promoted to active phases after explicit written approval.

---

## How to Run Tests

From `aletheia_toolchain/` as the working directory:

```bash
cd aletheia_toolchain
python -m unittest discover -s tests -p "test_*.py" -v
```

Do NOT run from the ToolSet root or DEV_TOOLS — this will produce stale `__pycache__`
entries at the wrong level and may resolve imports incorrectly.

---

## Hard Rules for Agents

1. **Python files must contain Python.** Never write prose or Markdown into a `.py` file.
2. **Work from `aletheia_toolchain/` as CWD.** All CLI invocations and test runs use this directory.
3. **Copy before delete.** Verify destination files before removing the source.
4. **No stray output artifacts.** Bundle outputs, CSV files, and slicer results must not be written inside this directory.
5. **No legacy tools inside `aletheia_toolchain/`.** `create_file_map_v2.py`, legacy packagers, and slicers live at the ToolSet root only.
6. **Quarantine before delete.** Unapproved code goes to `_quarantine/`, not the trash.
