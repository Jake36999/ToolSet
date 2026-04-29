---
name: scaffold-regression-fixtures
description: Automatically updates or creates seed files for regression testing.
---

# Scaffold Regression Fixtures Skill

## Purpose
Whenever a new analysis tool or file mapper is modified, this skill ensures that regression test fixtures in `tests/fixtures/transcript_regressions/` remain valid data files and are properly scaffolded to test known transcript risks.

Fixtures are NOT documentation; they are actual seed data for end-to-end testing.

## When to Invoke
- Adding a new tool to the ToolSet
- Modifying CLI arguments or command flags
- Updating file-mapping, manifest parsing, or output logic
- Enhancing error handling or edge-case coverage
- After a phase is complete (to capture regression baselines)

## Fixture Directory Structure

```
tests/fixtures/transcript_regressions/
├── README.md                          ← Fixture metadata (link this, don't embed)
├── sample_manifest.csv                ← Valid file-map export
├── sample_command.ps1                 ← PowerShell CLI invocation example
├── sample_bundle.json                 ← Semantic slicer JSON output
├── sample_workspace.json              ← Workspace packager output
└── edge_cases/
    ├── malformed_manifest.csv         ← Duplicate paths (should warn)
    ├── empty_directory.csv            ← Empty workspace result
    └── oversized_files.csv            ← Files exceeding 1.5 MB limit
```

## Workflow

### Step 1: Identify Transcript Risks
Before updating fixtures, list the regression risks this tool/version addresses:

**Common ToolSet risks:**
- Wrong `-o` vs `--out` flag variant
- Missing manifest input file
- Broad `.` directory scans including `.venv` or `node_modules`
- Polluted manifests (duplicates, oversized files, binaries)
- Encoding errors or permission-denied files
- Inconsistent mtime formatting (ISO 8601)
- Incomplete SHA1 fingerprints

**Document:** Which risks does this version's test suite need to catch?

### Step 2: Create/Update Baseline Fixtures

#### Fixture 1: `sample_manifest.csv`
A valid, minimal file-map export for regression baseline.

**Requirements:**
- Must be a valid CSV with headers: `root | rel_path | abs_path | ext | size | mtime_iso | sha1`
- Represent at least 5 files across 3 directories
- Include mixed extensions (`.py`, `.md`, `.csv`, `.json`)
- Include one excluded file path (e.g., `__pycache__/module.pyc`) to verify filtering
- Include realistic file sizes (bytes)
- Use ISO 8601 timestamps: `2026-04-29T12:34:56`
- Use lowercase hex SHA1 hashes: `abc123...`

**Validation:**
```powershell
python -c "
import csv
with open('tests/fixtures/transcript_regressions/sample_manifest.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        assert len(row['sha1']) == 40, f'SHA1 invalid: {row[\"sha1\"]}'
        assert 'T' in row['mtime_iso'], f'ISO8601 invalid: {row[\"mtime_iso\"]}'
    print('✓ Fixture valid')
"
```

#### Fixture 2: `sample_command.ps1`
PowerShell script demonstrating correct CLI invocation.

**Template:**
```powershell
# Invocation demonstrating correct flag usage
python create_file_map_v3.py `
    --root .\tests\fixtures\transcript_regressions `
    --profile default `
    --out .\tests\fixtures\transcript_regressions\sample_manifest.csv

# Verify output exists
if (Test-Path .\tests\fixtures\transcript_regressions\sample_manifest.csv) {
    Write-Host "✓ Manifest generated successfully"
} else {
    Write-Host "✗ Manifest generation failed"
    exit 1
}
```

**Keep it:**
- Minimal (5-10 lines)
- Uses correct flag names (`--root`, not `-r` or `-d`)
- Demonstrates both single-file and directory inputs
- Includes success/failure validation

#### Fixture 3: `sample_bundle.json` (optional, for semantic_slicer)
Example JSON output from semantic analysis.

**Include:**
- Tool metadata (version, timestamp)
- At least one analysis layer (e.g., system topology)
- A realistic file path and hash
- Enough structure to be parseable by a downstream tool

#### Edge Case Fixtures
In `edge_cases/`:
- `malformed_manifest.csv` — Contains duplicate absolute paths (triggers validator warning)
- `empty_directory.csv` — Result of scanning an empty dir (minimal headers only)
- `oversized_files.csv` — File exceeding 1.5 MB (should have sha1 = null)

### Step 3: Document Fixtures

Create or update [tests/fixtures/transcript_regressions/README.md](tests/fixtures/transcript_regressions/README.md):

```markdown
# Regression Test Fixtures

**Purpose:** Baseline seed data for end-to-end testing of ToolSet analyzers.

**IMPORTANT:** These are DATA FILES, not documentation. Do NOT edit them as Markdown; treat them as test inputs.

## Fixtures

- `sample_manifest.csv` — Valid file-map export (5 files, 3 dirs, mixed extensions)
- `sample_command.ps1` — Correct CLI invocation example
- `sample_bundle.json` — Semantic slicer JSON output template
- `edge_cases/*` — Malformed/edge-case inputs for validator testing

## Usage

```powershell
# Run against sample fixture
python manifest_doctor.py --manifest sample_manifest.csv --out report.json

# Verify it parses correctly
python -m unittest tests.test_manifest -v
```

## Validation

All fixtures must pass:
- CSV: Valid headers, parseable by `csv.DictReader()`
- JSON: Valid structure, serializable by `json.loads()`
- PowerShell: Executable without errors or file I/O issues

## Regression Risks Covered

- ✓ Duplicate path detection
- ✓ Missing file handling
- ✓ Oversized file fingerprinting
- ✓ ISO8601 timestamp parsing
- ✓ Binary file filtering
```

### Step 4: Validate Fixtures Are Real Data

**CRITICAL:** Verify fixtures are parseable, not Markdown reports:

```powershell
# Test CSV fixture
python -c "import csv; csv.DictReader(open('tests/fixtures/transcript_regressions/sample_manifest.csv')); print('✓ CSV valid')"

# Test JSON fixture (if present)
python -c "import json; json.load(open('tests/fixtures/transcript_regressions/sample_bundle.json')); print('✓ JSON valid')"

# Run end-to-end test
python manifest_doctor.py --manifest tests/fixtures/transcript_regressions/sample_manifest.csv --out /tmp/report.json
echo "Exit code: $LASTEXITCODE"
```

### Step 5: Register Fixtures in Tests

Update `tests/test_manifest.py` (or relevant test) to load fixtures:

```python
import unittest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "transcript_regressions"

class TestManifestWithFixtures(unittest.TestCase):
    def test_load_sample_manifest(self):
        """Regression: sample manifest loads without errors."""
        manifest_path = FIXTURES_DIR / "sample_manifest.csv"
        # Load and validate
        manifest = load_manifest(str(manifest_path))
        self.assertGreaterEqual(len(manifest), 5, "Expected at least 5 files in sample")
        self.assertIn("sha1", manifest[0], "SHA1 column missing")
```

## Rejection Criteria

**REJECT** if ANY occur:
- [ ] Fixture files contain Markdown (e.g., `# Fixture Data` headers)
- [ ] CSV is not parseable by `csv.DictReader()`
- [ ] JSON is not valid per `json.loads()`
- [ ] PowerShell script references non-existent files
- [ ] Fixture CSV differs from schema: `root | rel_path | abs_path | ext | size | mtime_iso | sha1`
- [ ] Tests do not reference or validate fixtures
- [ ] Missing documentation (README.md) linking fixtures to regression risks

## Acceptance Checklist

Before marking complete:
- [ ] Fixtures are valid data files (pass parsers)
- [ ] CSV schema matches baseline exactly
- [ ] All fixtures referenced in test suite
- [ ] README.md documents purpose and usage
- [ ] Edge cases include malformed examples
- [ ] Terminal validation provided (verbatim parser output)
- [ ] No Markdown or narrative text in fixture files themselves

## Examples

**✅ APPROVED:** Adding `edge_cases/large_manifest.csv` with 1000 files (realistic stress test)
**❌ REJECTED:** Adding `fixtures/FIXTURE_NOTES.md` documenting the manifest structure (use README, not extra files)
**❌ REJECTED:** Fixture `sample_manifest.csv` contains header comments: `# This is the sample manifest` (breaks CSV parsing)
**✅ APPROVED:** Updating fixtures to match Phase 2 tool enhancements (keep CSV schema identical)

