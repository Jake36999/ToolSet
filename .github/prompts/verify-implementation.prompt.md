---
name: verify-implementation
description: Forces actual terminal validation of modified files.
---

# Verify Implementation

Do not rely on editor reads or assumptions. Execute actual terminal validation commands:

## Required Terminal Steps

1. **Syntax Validation** — Run `python -m py_compile` across all modified files to prove syntactical correctness:
   ```powershell
   python -m py_compile <file1.py> <file2.py> ...
   ```
   Expected output: No errors (silent success) or explicit SyntaxError.

2. **Unit Tests** — Run the full test suite to prove logical correctness:
   ```powershell
   python -m unittest discover tests -v
   ```
   Expected output: Test count, pass/fail summary, and any failure tracebacks.

3. **Specific Tool Tests** — If modifying a specific module, run its test suite:
   ```powershell
   python -m unittest tests.test_<module> -v
   ```

## Output Format

Paste the **exact terminal responses** (full command text + complete output). Do not summarize or interpret; provide verbatim terminal logs.

## Acceptance Criteria

- All Python files compile without syntax errors
- All tests pass (or explicitly document known failures with justification)
- No import errors or missing dependencies
