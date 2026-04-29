---
name: manifest-schema-guardian
description: Ensures any modifications to file-mapping logic rigorously preserve baseline constraints.
---

# Manifest Schema Guardian Skill

## Purpose
Whenever interacting with manifest files or file-mapping logic (e.g., `create_file_map_v3.py`), this skill ensures CSV schemas, exclusion lists, and diagnostic outputs remain intact and compliant with Phase 1 baseline constraints.

## When to Invoke
- Modifying `create_file_map_v3.py` or related file-mapping tools
- Updating CSV parsing or validation logic in `aletheia_tool_core/manifest.py`
- Adding new file analysis features
- Refactoring file filtering or exclusion logic
- Adding new diagnostic or reporting outputs

## Workflow

### Step 1: Schema Validation
Before any manifest-related change, verify the exact CSV schema:
```
root | rel_path | abs_path | ext | size | mtime_iso | sha1
```

**Check:**
- All 7 columns present in implementation
- Column order unchanged
- Data types preserved (string, int, ISO8601 timestamp, hex hash)
- No columns renamed, dropped, or reordered

### Step 2: Exclusion List Verification
Confirm that file filtering has not been modified:

**Required exclusion directories:**
- `.git`, `__pycache__`, `.venv`, `node_modules`, `dist`, `build`, `.mypy_cache`, `failed_workspaces`

**Required exclusion extensions:**
- Binary: `.png`, `.exe`, `.zip`, `.tar`, `.gz`, `.pyc`, `.class`, `.o`, `.so`, `.dll`
- Archives and other: `.jar`, `.iso`, `.dmg`

**Required file size limit:** 1.5 MB per file (50 MB for hashing threshold)

**Check:**
- IGNORE_DIRS constant unchanged
- IGNORE_EXTENSIONS constant unchanged
- MAX_FILE_SIZE_BYTES constant unchanged
- No new "convenience" filters added
- Hash limit behavior preserved

### Step 3: Baseline Constraint Cross-Reference
Cross-check implementation against Phase 1 baseline:

**Source of truth:** [aletheia_toolchain/README.md](aletheia_toolchain/README.md) and [aletheia_tool_core/manifest.py](aletheia_tool_core/manifest.py)

**Verify:**
- No diagnostic outputs removed or silently dropped
- File scanning logic produces same granularity
- Error handling for missing/permission-denied files preserved
- Fingerprinting algorithm (SHA1) unchanged

### Step 4: Test Against Baseline
After implementation, validate with:
```powershell
# Test basic CSV generation
python create_file_map_v3.py --root . --profile default --out test_manifest.csv

# Validate generated CSV
python -c "
import csv
with open('test_manifest.csv') as f:
    reader = csv.DictReader(f)
    headers = reader.fieldnames
    print(f'Headers: {headers}')
    assert headers == ['root', 'rel_path', 'abs_path', 'ext', 'size', 'mtime_iso', 'sha1'], 'Schema changed!'
    print('✓ Schema intact')
"

# Run manifest validation tests
python -m unittest tests.test_manifest -v
```

## Rejection Criteria

**REJECT** the change if ANY of these occur:
- [ ] CSV columns renamed, reordered, or dropped
- [ ] New columns added to CSV schema
- [ ] IGNORE_DIRS or IGNORE_EXTENSIONS modified
- [ ] MAX_FILE_SIZE_BYTES or hash limits changed
- [ ] File fingerprinting algorithm (SHA1) altered
- [ ] Diagnostic outputs removed without explicit approval
- [ ] Tests fail or new tests added without documenting rationale

## Acceptance Checklist

Before marking complete, verify:
- [ ] All 7 CSV columns present and in correct order
- [ ] Exclusion lists unchanged (run grep to verify)
- [ ] File size limits preserved (1.5 MB, 50 MB hash threshold)
- [ ] Unit tests pass: `python -m unittest tests.test_manifest -v`
- [ ] Schema validation passes (test CSV generation)
- [ ] Terminal output from validation provided verbatim

## Examples

**✅ APPROVED:** Adding new metadata column with `--verbose` flag (opt-in, doesn't break CSV)
**❌ REJECTED:** Renaming `abs_path` to `absolute_path` (breaks backward compatibility)
**❌ REJECTED:** Removing `.git` from IGNORE_DIRS (loses security filtering)
**✅ APPROVED:** Optimizing SHA1 calculation with same algorithm and output format

