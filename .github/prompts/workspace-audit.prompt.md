---
name: workspace-audit
description: Audits the workspace to ensure phase compliance and file hygiene.
---

# Workspace Audit

Perform a strict workspace audit and report the following:

1. **Current directory** — Run `Get-Location` to confirm active workspace.
2. **List of new files created** — Run `git status --short` or list recent files.
3. **List of modified files** — Check file modification dates or `git diff`.
4. **Any files found outside approved phase scope** — Cross-check against Phase Lock Rule.
5. **Presence of generated/cache files** — Search for `.mypy_cache`, `__pycache__`, bundles, file maps, or stale build artifacts.
6. **Fixture validation** — Verify whether fixtures in `tests/fixtures/transcript_regressions/` are valid data files (CSV, PowerShell scripts) or accidental Markdown reports.

## Output Format

Present findings as a structured checklist with clear PASS/WARN/FAIL status for each item.
