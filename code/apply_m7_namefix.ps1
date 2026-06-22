# apply_m7_namefix.ps1
# Fix module_07_cross_sad_compare.py render_anchor_summary: an anchor with no
# 'name' falls back to building_id, which for freshly-pulled heavy districts is
# an INT (OSM way id). 'name[:30]' then throws "'int' object is not
# subscriptable". Coerce to str before slicing.
#
# Safe pattern: backup, exact-string edit, verify, auto-revert on failure.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$file = Join-Path $code "module_07_cross_sad_compare.py"
if (-not (Test-Path $file)) { throw "Not found: $file" }

$bak = "$file.bak_namefix_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $file $bak
Write-Host "backup: $bak"

$src = Get-Content $file -Raw

# coerce name to string at the source (line ~381). Handles both the name and
# the building_id fallback, so the [:30] slice on line ~388 is always safe.
$old = "            name = a.get('name') or a.get('building_id', '?')"
$new = "            name = str(a.get('name') or a.get('building_id', '?'))"

$count = ([regex]::Matches($src, [regex]::Escape($old))).Count
if ($count -eq 0) {
    # tolerate differing leading whitespace
    $old2 = "name = a.get('name') or a.get('building_id', '?')"
    $new2 = "name = str(a.get('name') or a.get('building_id', '?'))"
    $count2 = ([regex]::Matches($src, [regex]::Escape($old2))).Count
    if ($count2 -ne 1) { throw "expected 1 match for name= line, found $count2 (check the file manually)" }
    $src = $src.Replace($old2, $new2)
} elseif ($count -eq 1) {
    $src = $src.Replace($old, $new)
} else {
    throw "expected 1 match, found $count"
}

Set-Content -Path $file -Value $src -Encoding UTF8
Write-Host "patched: coerced anchor name to str() before slicing"

# verify
$chk = Get-Content $file -Raw
$fixed = $chk -match "name = str\(a\.get\('name'\) or a\.get\('building_id', '\?'\)\)"
$oldGone = -not ($chk -match "name = a\.get\('name'\) or a\.get\('building_id', '\?'\)(?!\))")
Write-Host ("str() coercion present: {0}" -f $fixed)
if (-not $fixed) {
    Write-Host "VERIFICATION FAILED -- restoring backup"
    Copy-Item $bak $file -Force
    throw "reverted"
}
# py-compile check
Push-Location $code
python -c "import py_compile; py_compile.compile(r'$file', doraise=True); print('py-compile OK')"
Pop-Location

Write-Host ""
Write-Host "DONE. Re-run cross-sad (with --force) to regenerate M7/M8/M9:"
Write-Host '  python batch_run_pipeline.py --data-dir <DATA> --stage cross-sad --force'
