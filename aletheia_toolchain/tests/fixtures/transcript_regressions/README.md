# Regression Test Fixtures

**Purpose:** Baseline seed data for end-to-end testing of ToolSet analyzers (create_file_map, manifest_doctor, semantic_slicer, etc.).

**CRITICAL:** These are **DATA FILES**, not documentation. Do NOT edit them as Markdown. They must remain parseable as valid CSV, JSON, or PowerShell scripts.

---

## Fixtures Overview

| File | Format | Purpose | Used By |
|------|--------|---------|---------|
| `sample_manifest.csv` | CSV | Valid file-map export (5 files, 3 dirs, mixed extensions) | manifest_doctor, test suite |
| `sample_command.ps1` | PowerShell | Correct CLI invocation demonstrating proper flags | Integration tests |
| `sample_bundle.json` | JSON | Semantic slicer output structure template | semantic_slicer tests |
| `README.md` | Markdown | This documentation | Team reference |

---

## Detailed Fixture Specifications

### `sample_manifest.csv`

**Schema (immutable):**
```
root | rel_path | abs_path | ext | size | mtime_iso | sha1
```

**Sample Content:**
- 5 files across 3 directories (aletheia_tool_core, tests, root)
- Mixed extensions: `.py`, `.md`, `.csv`
- Realistic file sizes (1.8 KB to 5.6 KB)
- ISO 8601 timestamps: `2026-04-29T12:34:56`
- Lowercase hex SHA1 hashes (40 characters)

**Validation:**
```powershell
python -c "
import csv
with open('sample_manifest.csv') as f:
    reader = csv.DictReader(f)
    headers = reader.fieldnames
    assert headers == ['root', 'rel_path', 'abs_path', 'ext', 'size', 'mtime_iso', 'sha1'], 'Schema mismatch!'
    rows = list(reader)
    assert len(rows) == 5, f'Expected 5 rows, got {len(rows)}'
    for row in rows:
        assert len(row['sha1']) == 40, f'Invalid SHA1: {row[\"sha1\"]}'
    print('✓ sample_manifest.csv valid')
"
```

**Regression Risks Tested:**
- ✓ CSV parsing with correct headers
- ✓ Path normalization (Windows backslashes vs forward slashes)
- ✓ Timestamp ISO 8601 compliance
- ✓ SHA1 hash format validation

---

### `sample_command.ps1`

**Purpose:** Demonstrate correct Phase 2 CLI invocation patterns for `create_file_map_v3.py`.

**Content Covers:**
- Standard flag usage: `--root`, `--profile`, `--out`
- Correct directory paths (relative and absolute)
- Output validation (CSV generation check)
- Success/failure handling

**Validation:**
```powershell
# Syntax check
powershell -NoProfile -Command "& { . .\sample_command.ps1 -Verbose }"

# Or run directly (cleans up after itself)
.\sample_command.ps1
```

**Regression Risks Tested:**
- ✓ Correct `--root` vs `--roots` flag
- ✓ `--profile` option existence
- ✓ `--out` vs `-o` flag consistency
- ✓ CSV output generation
- ✓ Exit code on success (0) and failure (1)

---

### `sample_bundle.json`

**Purpose:** Template of semantic_slicer_v6.0 JSON output for version baseline and structure validation.

**Schema Layers Included:**
- `metadata` — Tool version, timestamp, target directory
- `layer_1_system_topology` — File/directory tree
- `layer_1_5_architecture_context` — Dependencies and entry points
- `layer_1_7_import_graph` — Module relationships
- `layer_2_code_intelligence` — Slices, call graphs
- `layer_2_2_semantic_density` — LOC, complexity metrics
- `layer_2_5_slice_dependency_graph` — Risk scoring
- `layer_2_8_patch_target_validation` — Fingerprints
- `layer_2_9_structural_motifs` — IO patterns, pure compute
- `layer_x_uncertainties` — Dynamic imports, syntax errors
- `statistics` — Scan summary

**Validation:**
```powershell
python -c "
import json
with open('sample_bundle.json') as f:
    bundle = json.load(f)
    assert 'metadata' in bundle, 'Missing metadata'
    assert bundle['metadata']['tool'] == 'semantic_slicer_v6.0', 'Wrong tool'
    assert 'layer_1_system_topology' in bundle, 'Missing layer 1'
    print('✓ sample_bundle.json valid JSON')
"
```

**Regression Risks Tested:**
- ✓ JSON structure validity
- ✓ Required top-level keys
- ✓ Metadata versioning
- ✓ Analysis layer completeness

---

## Edge Case Fixtures

For robustness, edge case fixtures should be created in `edge_cases/` subdirectory:

### `edge_cases/malformed_manifest.csv`
**Purpose:** Test error detection (duplicate paths).

```csv
root,rel_path,abs_path,ext,size,mtime_iso,sha1
.,file1.py,./file1.py,.py,100,2026-04-29T00:00:00,abc123
.,file1.py,./file1.py,.py,100,2026-04-29T00:00:00,abc123
```

**Expected:** manifest_doctor should flag **duplicate absolute paths** with WARN status.

### `edge_cases/empty_directory.csv`
**Purpose:** Test minimal output (no files scanned).

```csv
root,rel_path,abs_path,ext,size,mtime_iso,sha1
```

**Expected:** manifest_doctor accepts with PASS (no violations for empty manifest).

### `edge_cases/oversized_files.csv`
**Purpose:** Test handling of files exceeding 1.5 MB (no SHA1).

```csv
root,rel_path,abs_path,ext,size,mtime_iso,sha1
.,largefile.bin,./largefile.bin,.bin,1572864,2026-04-29T00:00:00,
```

**Expected:** manifest_doctor accepts (size > 1.5 MB, SHA1 nulled is valid).

---

## Usage in Test Suite

### Load Fixture in Unit Tests
```python
import unittest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "transcript_regressions"

class TestManifestWithFixtures(unittest.TestCase):
    def test_load_sample_manifest(self):
        """Regression: sample manifest loads without errors."""
        manifest_path = FIXTURES_DIR / "sample_manifest.csv"
        
        # Use your loader
        from aletheia_tool_core.manifest import load_manifest
        manifest = load_manifest(str(manifest_path))
        
        self.assertGreaterEqual(len(manifest), 5, "Expected at least 5 files")
        self.assertIn("sha1", manifest[0], "SHA1 column missing")
        print(f"✓ Loaded {len(manifest)} files from fixture")
```

### Run Fixture-Based Tests
```powershell
# All fixture tests
python -m unittest tests.test_manifest -v

# Specific test
python -m unittest tests.test_manifest.TestManifestWithFixtures.test_load_sample_manifest -v
```

---

## Maintenance Rules

### When to Update Fixtures

**DO update** if:
- A tool's CSV schema is formally changed (e.g., add new column)
- Timestamp format is standardized (e.g., UTC vs local)
- Semantic slicer output structure is enhanced

**DO NOT update** if:
- You're adding a feature flag (`--verbose`, `--format json`)
- You're optimizing internal logic
- You're refactoring code without changing CLI/output

### Validation Before Commit

Before updating any fixture, run:

```powershell
# CSV validation
python -c "import csv; csv.DictReader(open('sample_manifest.csv')); print('✓ CSV valid')"

# JSON validation
python -c "import json; json.load(open('sample_bundle.json')); print('✓ JSON valid')"

# PowerShell syntax
powershell -NoProfile -Command "& { Get-Content .\sample_command.ps1 | powershell -Command -; exit `$? }"

# Run tests against fixtures
python -m unittest discover tests -v -k fixture
```

---

## Regression Risks Covered

| Risk | Fixture | Detection |
|------|---------|-----------|
| Wrong `--root` vs `--roots` flag | sample_command.ps1 | PowerShell test runs without error |
| Missing manifest input | (implicit) | Test fails if fixture missing |
| Incorrect CSV schema | sample_manifest.csv | CSV parse check + header validation |
| Timestamp non-compliance | sample_manifest.csv | ISO 8601 format assertion |
| SHA1 format invalid | sample_manifest.csv | Regex: `^[a-f0-9]{40}$` |
| File paths mangled | sample_manifest.csv | Cross-check rel_path ↔ abs_path |
| Binary/large file handling | edge_cases/oversized_files.csv | Null SHA1 accepted |
| Duplicate detection | edge_cases/malformed_manifest.csv | manifest_doctor warns |
| Empty/minimal input | edge_cases/empty_directory.csv | manifest_doctor passes |

---

## Related Skills & Docs

- **Skill:** `@scaffold-regression-fixtures` — Automation for fixture updates
- **Skill:** `@manifest-schema-guardian` — CSV schema protection
- **Prompt:** `/verify-implementation` — Terminal-based fixture validation
- **Guide:** [.github/CUSTOMIZATIONS.md](../../CUSTOMIZATIONS.md) — When to invoke fixtures in workflow

---

## Changelog

| Date | Change | Reason |
|------|--------|--------|
| 2026-04-29 | Initial Phase 2 fixtures (sample_manifest.csv, sample_command.ps1, sample_bundle.json) | Establish regression baseline |

---

**Last Updated:** 2026-04-29  
**Status:** Phase 2 baseline established  
**Next Review:** End of Phase 2 (after manifest_doctor integration)
