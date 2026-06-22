# apply_findrod_fix.ps1
# Fix find_rod_places() in batch_run_pipeline.py: it returned the alphabetically
# FIRST .geojson in ROD_Search/, which on re-pulled districts is a stale
# "2km_..._unverified.geojson" (old extent) instead of the fresh puller output
# "overture_places.geojson". M3 then clipped stale POIs -> "no places after
# clipping" / wrong-extent rod_places. Prefer the canonical overture_places.geojson.
#
# Safe pattern: backup, exact-string edit, verify, py-compile, auto-revert.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$file = Join-Path $code "batch_run_pipeline.py"
if (-not (Test-Path $file)) { throw "Not found: $file" }

$bak = "$file.bak_findrod_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $file $bak
Write-Host "backup: $bak"

$src = Get-Content $file -Raw

$old = @'
    rod_dir = data_dir / sad_id / '03_ROD-Search-Tool' / 'ROD_Search'
    if rod_dir.exists():
        candidates = sorted(rod_dir.glob('*.geojson'))
        if candidates:
            return candidates[0]
'@

$new = @'
    rod_dir = data_dir / sad_id / '03_ROD-Search-Tool' / 'ROD_Search'
    if rod_dir.exists():
        # Prefer the canonical puller output. Picking the alphabetically-first
        # geojson grabbed stale "2km_..._unverified.geojson" exports (old extent)
        # over the freshly-pulled overture_places.geojson, feeding M3 wrong POIs.
        canonical = rod_dir / 'overture_places.geojson'
        if canonical.exists() and canonical.stat().st_size > 0:
            return canonical
        candidates = sorted(rod_dir.glob('*.geojson'))
        if candidates:
            return candidates[0]
'@

$count = ([regex]::Matches($src, [regex]::Escape($old))).Count
if ($count -ne 1) { throw "expected exactly 1 match for find_rod_places body, found $count" }
$src = $src.Replace($old, $new)
Set-Content -Path $file -Value $src -Encoding UTF8
Write-Host "patched: find_rod_places prefers overture_places.geojson"

# verify
$chk = Get-Content $file -Raw
$ok = $chk -match "canonical = rod_dir / 'overture_places\.geojson'"
Write-Host ("canonical-preference present: {0}" -f $ok)
if (-not $ok) { Copy-Item $bak $file -Force; throw "verification failed; reverted" }
Push-Location $code
python -c "import py_compile; py_compile.compile(r'$file', doraise=True); print('py-compile OK')"
Pop-Location
Write-Host "DONE."
