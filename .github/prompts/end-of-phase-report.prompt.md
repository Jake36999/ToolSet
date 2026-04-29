---
name: end-of-phase-report
description: Generates a strictly formatted End-of-Phase Report.
---

# End-of-Phase Report

Generate a comprehensive End-of-Phase Report with the following exact markdown structure:

## Report Sections

### 1. Changed Files

List all files modified or created during this phase. Use format:

```
| File | Status | Reason |
|------|--------|--------|
| path/to/file.py | Created | New shared utility |
| path/to/existing.py | Modified | Refactored for clarity |
```

### 2. Terminal Output

Paste the exact terminal output from ALL validation commands executed:
- Syntax validation (`python -m py_compile`)
- Unit test results (`python -m unittest discover tests -v`)
- Any other verification commands

Preserve full command text and all output verbatim.

### 3. Test Results

State the exact tally of tests:
- Total tests discovered
- Tests passed (exact count)
- Tests failed (exact count, with failure names)
- **CRITICAL:** This must not be an assumption. Include the exact test runner output line showing pass/fail summary.

### 4. Unresolved Risks

List any architectural risks, technical debt, or phase leaks:
- Future-phase features accidentally included? Document them.
- Deprecated patterns still in use?
- Known limitations or caveats?
- Backward compatibility concerns?

### 5. Acceptance Checklist

```
- [ ] All modified files pass syntax validation
- [ ] All unit tests pass
- [ ] No files created outside `D:\Aletheia_project\DEV_TOOLS\ToolSet`
- [ ] No forbidden phase files present
- [ ] CSV schemas unchanged (`root`, `rel_path`, `abs_path`, `ext`, `size`, `mtime_iso`, `sha1`)
- [ ] Fixtures are valid data files (not Markdown)
- [ ] Zero third-party dependencies added
- [ ] Existing tool CLI behavior preserved
```

## Generation Rules

- Be concise but complete
- Use exact counts and terminal output, not estimates
- Link to relevant documentation where appropriate
- Flag any departures from the Phase Lock Rule
