# Architecture Validator Report

## Architecture Validator Report

**Status**: WARN  
**Bundle**: tests\fixtures\transcript_regressions\sample_bundle.json  
**Config**: examples\configs\python_project.json  
**Profile**: default  
**Bundle schema version**: unknown  
**Findings**: 1

## Severity Summary

- **PASS**: 0
- **INFO**: 0
- **WARN**: 1
- **FAIL**: 0

## Findings

- **[WARN]** `R-AV001` — Required path 'pyproject.toml' not found in bundle. *(confidence: HIGH)*
-   - *Evidence*: Bundle file list: ['create_file_map_v3.py', 'aletheia_tool_core/manifest.py', 'semantic_slicer_v6.0.py', 'workspace_packager_v2.3.py']
-   - *Recommendation*: Ensure 'pyproject.toml' is included in the scan scope.
