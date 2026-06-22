# apply_m14_pipeline_step.ps1
# Wire module_14_transit_routes.py into the per-SAD pipeline as step "M14",
# alongside M13 (transit stations). Uses --discover (reuses the GTFS cache from
# M21). marker = transit/transit_routes.geojson; needs only manifest.json so it
# runs independently of buildings. Safe pattern: backup, exact edit, verify,
# py-compile, auto-revert.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$bpln = Join-Path $code "batch_run_pipeline.py"
if (-not (Test-Path $bpln)) { throw "Not found: $bpln" }

$bak = "$bpln.bak_m14_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $bpln $bak
Write-Host "backup: $bak"

$src = Get-Content $bpln -Raw

# Anchor on the M13 (transit stations) module dict and insert M14 right after.
# M13's needs is ['manifest.json'] per the earlier grep (L153 region). We match
# the M13 block by its script name and trailing close, ASCII-only.
$old = @'
        'needs':  ['transit/transit_stations.geojson'],
    },
'@

# That 'needs' line belongs to M16 (per earlier grep L153). Too risky to anchor
# there. Instead anchor on M13's script line, which is unique.
# Find M13 dict by its script and insert M14 after its closing brace.
$anchor = "        'script': 'module_13_transit_stations.py',"
if (([regex]::Matches($src,[regex]::Escape($anchor))).Count -ne 1) {
    throw "could not uniquely find M13 script line"
}

# We need to insert after M13's full dict. Locate the dict's closing '    },'
# that follows the M13 script line. Do a targeted regex from the anchor to the
# next '    },'.
$pattern = "(?s)(        'script': 'module_13_transit_stations\.py',.*?\n    \},\n)"
$m = [regex]::Match($src, $pattern)
if (-not $m.Success) { throw "could not bound M13 dict block" }

$m14block = @'

    # ---- Transit routes (GTFS shapes) — sibling to M13 stations ------------
    {
        'name':   'M14',
        'script': 'module_14_transit_routes.py',
        'args':   ['--derived', '{derived}', '--source', '{source}', '--discover'],
        'marker': 'transit/transit_routes.geojson',
        'needs':  ['manifest.json'],
    },
'@

$src = $src.Substring(0, $m.Index + $m.Length) + $m14block + $src.Substring($m.Index + $m.Length)
Set-Content -Path $bpln -Value $src -Encoding UTF8
Write-Host "inserted M14 step after M13"

# verify + compile
$chk = Get-Content $bpln -Raw
$ok = $chk -match "'name':   'M14'"
$okscript = $chk -match "module_14_transit_routes\.py"
Write-Host ("M14 present: {0}" -f $ok)
Write-Host ("script ref:  {0}" -f $okscript)
if (-not ($ok -and $okscript)) { Copy-Item $bak $bpln -Force; throw "verify failed; reverted" }
Push-Location $code
python -c "import py_compile; py_compile.compile(r'$bpln', doraise=True); print('py-compile OK')"
if ($LASTEXITCODE -ne 0) { Pop-Location; Copy-Item $bak $bpln -Force; throw "py-compile failed; reverted" }
Pop-Location
Write-Host ""
Write-Host "DONE. Test on Detroit (already has routes; --force to regen):"
Write-Host '  python batch_run_pipeline.py --data-dir <DATA> --stage per-sad --force --sads 32_District-Detroit_Detroit-MI --modules "M14"'
