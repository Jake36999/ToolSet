# Regression fixture: Phase 2 create_file_map_v3.py invocation example
# Demonstrates correct CLI flags and expected success behavior

# Standard invocation
python create_file_map_v3.py `
    --roots .\tests\fixtures\transcript_regressions `
    --profile default `
    --out .\tests\fixtures\transcript_regressions\test_output.csv

# Verify output exists and is valid CSV
if (Test-Path .\tests\fixtures\transcript_regressions\test_output.csv) {
    Write-Host "✓ Manifest generated successfully"
    
    # Quick CSV validation
    $csv = Import-Csv .\tests\fixtures\transcript_regressions\test_output.csv
    Write-Host "✓ CSV parsed: $($csv.Count) rows"
    Write-Host "✓ Headers: $($csv[0].PSObject.Properties.Name -join ', ')"
} else {
    Write-Host "✗ Manifest generation failed"
    exit 1
}

# Alternative: Python invocation (create_file_map_v3.py with standard profile)
python create_file_map_v3.py --roots . --profile python --out manifest_python.csv

# Cleanup
Remove-Item .\tests\fixtures\transcript_regressions\test_output.csv -ErrorAction SilentlyContinue
Remove-Item .\manifest_python.csv -ErrorAction SilentlyContinue
