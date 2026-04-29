# Aletheia ToolSet: Chat Customizations Guide

This document describes all Copilot chat customizations—prompts, agents, and skills—that help AI developers understand the codebase and work productively within project constraints.

**Last Updated:** April 29, 2026  
**Status:** Phase 1 complete, Phase 2 ready

---

## Quick Reference Matrix

| Type | Name | Invocation | Purpose | Use When |
|------|------|-----------|---------|----------|
| **Prompt** | workspace-audit | `/workspace-audit` | Scans workspace for phase violations, stray files, cache pollution | Before committing; checking for compliance |
| **Prompt** | verify-implementation | `/verify-implementation` | Forces terminal validation: syntax + unit tests + proof | After implementing code changes |
| **Prompt** | end-of-phase-report | `/end-of-phase-report` | Generates structured phase completion report | When finishing a development phase |
| **Agent** | gatekeeper | `@gatekeeper` | Strict auditor: rejects unproven claims, enforces phase boundaries | Final review before merge; phase gate checks |
| **Skill** | manifest-schema-guardian | `@manifest-schema-guardian` | Ensures CSV schema & exclusion lists stay intact | Modifying file-mapping or manifest logic |
| **Skill** | scaffold-regression-fixtures | `@scaffold-regression-fixtures` | Validates regression fixtures are real data, not Markdown | Adding tools or modifying parsers |

---

## File Locations

All customizations are stored in `.github/`:

```
.github/
├── copilot-instructions.md                    ← Universal workspace rules (auto-applied)
├── CUSTOMIZATIONS.md                          ← This file
├── prompts/
│   ├── workspace-audit.prompt.md
│   ├── verify-implementation.prompt.md
│   └── end-of-phase-report.prompt.md
├── agents/
│   └── gatekeeper.agent.md
└── skills/
    ├── manifest-schema-guardian.SKILL.md
    └── scaffold-regression-fixtures.SKILL.md
```

---

## Core Rules (Always Active)

These rules from [.github/copilot-instructions.md](.github/copilot-instructions.md) apply automatically to all Copilot interactions:

### Workspace Root Rule
- Active project root is strictly: `D:\Aletheia_project\DEV_TOOLS\ToolSet`
- No files created, edited, moved, or deleted outside this root
- Always verify: `Get-Location`, `Test-Path .\create_file_map_v2.py`, `Test-Path .\semantic_slicer_v6.0.py`

### Phase Lock Rule
- Current approved phase must be explicitly stated by user
- **Phase 2 Allowed:** `create_file_map_v3.py`, `tests/test_create_file_map_v3.py`, fixtures (if needed)
- **Phase 2 Forbidden:** `manifest_doctor.py`, `tool_command_linter.py`, `semantic_slicer_v7.0.py`, watcher/gatekeeper tools
- If a task requires a forbidden file, request explicit approval

### Architectural Constraints
- **Standard-Library First:** Zero third-party dependencies
- **Backward Compatibility:** Preserve existing tool CLI behaviors, CSV schemas, and command flags
- **CSV Schema Lock:** Must remain `root | rel_path | abs_path | ext | size | mtime_iso | sha1`

---

## Detailed Usage Guide

### 1. Workspace Audit Prompt (`/workspace-audit`)

**File:** [.github/prompts/workspace-audit.prompt.md](.github/prompts/workspace-audit.prompt.md)

**Purpose:**  
Audits the workspace to ensure phase compliance, detect stray files, and catch cache pollution before you commit.

**What It Does:**
1. Checks current directory
2. Lists new files created during session
3. Lists modified files
4. Flags files outside approved phase scope
5. Detects generated/cache files (`.mypy_cache`, `__pycache__`, bundles)
6. Validates fixture structure

**How to Use:**
```
/workspace-audit
```

**Example Workflow:**
```
You: /workspace-audit

Output:
✓ Current directory: D:\Aletheia_project\DEV_TOOLS\ToolSet
✓ New files: create_file_map_v3.py
✓ Modified files: aletheia_tool_core/manifest.py
⚠ Cache found: .mypy_cache/ (recommend delete)
⚠ Fixture check: tests/fixtures/transcript_regressions/sample_manifest.csv valid ✓
```

**When to Use:**
- Before committing changes
- After running tools manually
- To verify no accidental files outside workspace root
- To catch stale `.pyc` or bundle files

---

### 2. Verify Implementation Prompt (`/verify-implementation`)

**File:** [.github/prompts/verify-implementation.prompt.md](.github/prompts/verify-implementation.prompt.md)

**Purpose:**  
Forces actual terminal validation—no editor assumptions. Proves code is syntactically and logically correct.

**What It Does:**
1. Runs `python -m py_compile` on all modified files (syntax validation)
2. Runs `python -m unittest discover tests -v` (unit test validation)
3. Captures exact terminal output verbatim

**How to Use:**
```
/verify-implementation
```

**Example Workflow:**
```
You: /verify-implementation

Output:
Running syntax validation:
$ python -m py_compile create_file_map_v3.py aletheia_tool_core/manifest.py
[Silent success = all files compile]

Running unit tests:
$ python -m unittest discover tests -v
test_manifest (tests.test_manifest.TestManifest) ... ok
test_security (tests.test_security.TestSecurityKernel) ... ok
...
Ran 23 tests in 0.456s
OK
```

**When to Use:**
- After implementing a new feature
- Before submitting code for review
- To validate all changes are syntactically correct
- To prove tests pass (not assumptions)

---

### 3. End-of-Phase Report Prompt (`/end-of-phase-report`)

**File:** [.github/prompts/end-of-phase-report.prompt.md](.github/prompts/end-of-phase-report.prompt.md)

**Purpose:**  
Generates a strictly formatted completion report with exact test counts and terminal logs.

**What It Does:**
1. Lists all changed files (created/modified)
2. Includes exact terminal output from validation commands
3. Reports test tally (must not be assumption)
4. Documents unresolved risks
5. Generates acceptance checklist

**How to Use:**
```
/end-of-phase-report
```

**Example Output:**
```
## Changed Files
| File | Status | Reason |
|------|--------|--------|
| create_file_map_v3.py | Created | New Phase 2 tool |
| aletheia_tool_core/manifest.py | Modified | Enhanced CSV validation |

## Terminal Output
[Exact command + output pasted here]

## Test Results
- Total tests discovered: 25
- Tests passed: 25
- Tests failed: 0

## Unresolved Risks
- None

## Acceptance Checklist
- [x] All modified files pass syntax validation
- [x] All unit tests pass
- [x] No files created outside workspace root
...
```

**When to Use:**
- When completing a development phase
- Before merging to main branch
- To create formal phase completion record
- To document technical debt or limitations

---

### 4. Gatekeeper Agent (`@gatekeeper`)

**File:** [.github/agents/gatekeeper.agent.md](.github/agents/gatekeeper.agent.md)

**Purpose:**  
Stringent validation gatekeeper. Rejects unproven implementations, enforces phase boundaries, ensures terminal proof exists.

**Role:**  
You are NOT a feature developer—you are a strict auditor. Your job is to reject implementations that fail any criterion.

**Strict Rejection Criteria:**
- Terminal unavailable or unproofed
- Evidence only from editor diagnostics (no terminal output)
- Test output lacks exact command text or pass/fail summary
- Files created outside workspace root
- Forbidden phase files present
- CSV schema changed
- Fixtures contain Markdown instead of valid data
- Third-party dependencies added
- Existing CLI behavior modified

**How to Use:**
```
@gatekeeper Review the current phase implementation for phase 2 completion.
```

**Example Workflow:**
```
You: @gatekeeper Review Phase 2 implementation

Gatekeeper Output:
[CHECKING...]
- Terminal output: ✓ Provided
- Syntax validation: ✓ Passed
- Unit tests: ✓ 25 passed, 0 failed
- File scope: ✓ All within workspace root
- Phase lock: ✓ No forbidden files
- CSV schema: ✓ Unchanged
- Third-party deps: ✓ None added

[PASS]

Generating End-of-Phase Report...
[Report generated]
```

**Or Rejection Example:**
```
You: @gatekeeper Review Phase 2 implementation

Gatekeeper Output:
[CHECKING...]
- Terminal output: ✗ Missing
- Syntax validation: ✗ No terminal proof

[DENY]

**Rejection Reason:** Evidence consists only of editor diagnostics; no terminal output provided.

**Required Action:** Run `/verify-implementation` and provide exact terminal output.
```

**When to Use:**
- Final review before merging
- Phase completion gate
- When you need strict, unforgiving validation
- To formally document code review

---

### 5. Manifest Schema Guardian Skill (`@manifest-schema-guardian`)

**File:** [.github/skills/manifest-schema-guardian.SKILL.md](.github/skills/manifest-schema-guardian.SKILL.md)

**Purpose:**  
Ensures any modifications to file-mapping logic rigorously preserve baseline constraints—CSV schemas, exclusion lists, diagnostic outputs.

**What It Validates:**
1. CSV columns unchanged: `root | rel_path | abs_path | ext | size | mtime_iso | sha1`
2. IGNORE_DIRS and IGNORE_EXTENSIONS unchanged
3. File size limits preserved (1.5 MB per file, 50 MB hash threshold)
4. SHA1 fingerprinting algorithm unchanged
5. Diagnostic outputs not removed

**Rejection Criteria:**
- CSV columns renamed, reordered, or dropped
- New columns added to schema
- Exclusion lists modified
- File size limits changed
- Fingerprinting algorithm altered
- Diagnostic outputs removed
- Tests fail or missing

**How to Use:**
```
@manifest-schema-guardian I'm updating create_file_map_v3.py to add verbose logging.

Here's my change: [paste code changes]
```

**Example Workflow:**
```
You: @manifest-schema-guardian I'm adding a --verbose flag to show detailed file scanning.

Skill Output:
✓ Verbose flag is opt-in (doesn't break CSV)
✓ CSV columns unchanged
✓ IGNORE_DIRS intact
✓ File size limits preserved
✓ Tests pass

[APPROVED]
```

**When to Use:**
- Modifying `create_file_map_v3.py`
- Updating manifest parsing in `aletheia_tool_core/manifest.py`
- Adding new file analysis features
- Refactoring file filtering logic

---

### 6. Scaffold Regression Fixtures Skill (`@scaffold-regression-fixtures`)

**File:** [.github/skills/scaffold-regression-fixtures.SKILL.md](.github/skills/scaffold-regression-fixtures.SKILL.md)

**Purpose:**  
Ensures regression test fixtures in `tests/fixtures/transcript_regressions/` are valid data files (not Markdown) and properly scaffolded for end-to-end testing.

**What It Validates:**
1. Fixtures are parseable (CSV valid, JSON valid, PowerShell executable)
2. CSV schema matches baseline exactly
3. All fixtures referenced in test suite
4. No Markdown embedded in fixture files
5. Edge cases included (malformed, empty, oversized)
6. README.md documents purpose and usage

**Rejection Criteria:**
- Fixture files contain Markdown (e.g., `# Fixture Data`)
- CSV not parseable by `csv.DictReader()`
- JSON invalid
- Schema differs from baseline
- Tests don't reference fixtures
- Missing documentation

**How to Use:**
```
@scaffold-regression-fixtures I've added a new oversized files test case.

Here's what I added: [describe fixture changes]
```

**Example Workflow:**
```
You: @scaffold-regression-fixtures I'm updating fixtures for Phase 2 semantic_slicer changes.

Skill Output:
✓ sample_manifest.csv: Valid CSV (5 files, 3 dirs)
✓ sample_command.ps1: Executable
✓ edge_cases/malformed_manifest.csv: Valid data
✓ README.md: Linked, not embedded

[APPROVED]
```

**When to Use:**
- Adding a new tool to ToolSet
- Modifying CLI arguments or command flags
- Updating file-mapping or manifest logic
- Enhancing error handling or edge-case coverage
- After a phase completes (capture regression baselines)

---

## Typical Development Workflow

Here's how to use these customizations together in a typical development session:

### Phase 2 Development Example: Create File Map v3

**Step 1: Start Work**
```powershell
cd D:\Aletheia_project\DEV_TOOLS\ToolSet
# Begin implementing create_file_map_v3.py
```

**Step 2: Quick Compliance Check**
```
User: /workspace-audit
```
Output: Verifies you're in correct directory, no stray files.

**Step 3: Implement Feature**
```powershell
# Write code in create_file_map_v3.py
# Update tests in tests/test_create_file_map_v3.py
```

**Step 4: Schema Validation**
```
User: @manifest-schema-guardian I've updated create_file_map_v3.py to add better error handling.

[Paste relevant code changes]
```
Skill ensures CSV schema unchanged.

**Step 5: Fixture Validation**
```
User: @scaffold-regression-fixtures I've added tests for oversized files.

[Describe fixture changes]
```
Skill ensures fixtures are valid data.

**Step 6: Terminal Proof**
```
User: /verify-implementation
```
Output: Syntax + test validation with exact terminal output.

**Step 7: Phase Completion**
```
User: /end-of-phase-report
```
Output: Structured report with test counts, risks, checklist.

**Step 8: Final Gate Review**
```
User: @gatekeeper Review Phase 2 implementation.
```
Output: `[PASS]` or `[DENY]` with detailed criteria.

---

## Integration with Existing Documentation

These customizations **supplement** (not replace) existing project docs:

| Customization | Links To | Why |
|---------------|----------|-----|
| copilot-instructions.md | Phase 1 README, AGENTS.md | Reference architecture & constraints |
| manifest-schema-guardian | aletheia_tool_core/manifest.py, tests/ | Source of truth for CSV schema |
| scaffold-regression-fixtures | tests/fixtures/transcript_regressions/ | Fixture baseline & patterns |
| gatekeeper | All of above | Orchestrates validation across tools |

---

## Team Onboarding

### For New Developers
1. **Read:** This file (CUSTOMIZATIONS.md)
2. **Read:** [.github/copilot-instructions.md](.github/copilot-instructions.md) (understand Phase Lock Rule)
3. **Read:** [AGENTS.md](../AGENTS.md) (understand ToolSet architecture)
4. **Run:** `/workspace-audit` to verify your environment
5. **Invoke:** `@gatekeeper` before first PR to understand validation expectations

### For Code Reviews
1. Request `/verify-implementation` output from PR author
2. Have author run `@gatekeeper` to confirm compliance
3. Review structured output from `/end-of-phase-report`
4. Check against [.github/copilot-instructions.md](.github/copilot-instructions.md) Phase Lock Rule

### For Phase Gating
1. At phase end, invoke `@gatekeeper Review Phase [N] implementation`
2. Wait for `[PASS]` before moving to next phase
3. Archive `/end-of-phase-report` output in commit message or wiki

---

## Troubleshooting

### "I don't see my customizations in chat"

**Solution:** Customizations are workspace-scoped, so:
1. Open the correct workspace: `D:\Aletheia_project\DEV_TOOLS\ToolSet`
2. Reload VS Code or open Chat tab fresh
3. Verify `.github/` folder exists and files are present
4. Check Command Palette: "Chat: Open Customizations" to manually refresh

### "Gatekeeper rejected my code but I think it's fine"

**Why:** Gatekeeper is intentionally strict. If it says `[DENY]`, fix the specific criterion listed before resubmitting.

**Common rejections:**
- Terminal output missing → Run `/verify-implementation` first
- CSV schema changed → Use `@manifest-schema-guardian` to review
- Fixture contains Markdown → Use `@scaffold-regression-fixtures` to validate

### "I need to modify a Phase 2 forbidden file"

**Solution:** Request explicit approval in chat:
```
User: I need to create manifest_doctor.py for Phase 2. Can I proceed?
```
Copilot will flag this against Phase Lock Rule and ask for confirmation.

---

## Next Steps

After Phase 2 completion:
1. Archive this session's `/end-of-phase-report` 
2. Update Phase Lock Rule in [.github/copilot-instructions.md](.github/copilot-instructions.md) for Phase 3
3. Add new skill files as new tool patterns emerge
4. Run `/workspace-audit` before each phase transition

---

## Files Summary

| File | Purpose | Scope |
|------|---------|-------|
| [.github/copilot-instructions.md](.github/copilot-instructions.md) | Universal rules | Project-wide (always active) |
| [.github/prompts/workspace-audit.prompt.md](.github/prompts/workspace-audit.prompt.md) | Compliance auditing | On-demand via `/workspace-audit` |
| [.github/prompts/verify-implementation.prompt.md](.github/prompts/verify-implementation.prompt.md) | Terminal validation | On-demand via `/verify-implementation` |
| [.github/prompts/end-of-phase-report.prompt.md](.github/prompts/end-of-phase-report.prompt.md) | Phase completion | On-demand via `/end-of-phase-report` |
| [.github/agents/gatekeeper.agent.md](.github/agents/gatekeeper.agent.md) | Code review gating | On-demand via `@gatekeeper` |
| [.github/skills/manifest-schema-guardian.SKILL.md](.github/skills/manifest-schema-guardian.SKILL.md) | Schema validation | On-demand via `@manifest-schema-guardian` |
| [.github/skills/scaffold-regression-fixtures.SKILL.md](.github/skills/scaffold-regression-fixtures.SKILL.md) | Fixture validation | On-demand via `@scaffold-regression-fixtures` |

---

**Last Updated:** April 29, 2026  
**Maintained By:** AI Development Team  
**Questions?** Refer to [.github/copilot-instructions.md](.github/copilot-instructions.md) or the individual customization files.
