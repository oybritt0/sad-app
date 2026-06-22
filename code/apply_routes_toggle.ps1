# apply_routes_toggle.ps1
# The transit_routes layer renders + loads, but had no menu toggle:
# buildLayerToggles() uses a hardcoded activityLayers list, and makeLayerLi()
# needs a LAYER_META entry. Add both so the toggle appears.
# Safe pattern: backup, exact edits, verify, redeploy, auto-revert.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$vj   = Join-Path $code "viewer\viewer.js"
if (-not (Test-Path $vj)) { throw "Not found: $vj" }

$bak = "$vj.bak_routestog_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $vj $bak
Write-Host "backup: $bak"

$src = Get-Content $vj -Raw

# (1) LAYER_META entry — insert right after the transit (stations) line.
$mold = "  transit:      { label: 'Transit stations',   swatch: 'block',  color: '#5b9bd5' },"
$mnew = "  transit:      { label: 'Transit stations',   swatch: 'block',  color: '#5b9bd5' },`r`n  transit_routes: { label: 'Transit routes',  swatch: 'line',   color: '#5b9bd5' },"
$mc = ([regex]::Matches($src,[regex]::Escape($mold))).Count
if ($mc -ne 1) { throw "expected 1 transit LAYER_META line, found $mc" }
$src = $src.Replace($mold,$mnew)
Write-Host "LAYER_META.transit_routes added"

# (2) activityLayers list — add transit_routes
$aold = "  const activityLayers = ['transit'];"
$anew = "  const activityLayers = ['transit', 'transit_routes'];"
$ac = ([regex]::Matches($src,[regex]::Escape($aold))).Count
if ($ac -ne 1) { throw "expected 1 activityLayers line, found $ac" }
$src = $src.Replace($aold,$anew)
Write-Host "activityLayers now includes transit_routes"

Set-Content -Path $vj -Value $src -Encoding UTF8

# verify + brace balance
$chk = Get-Content $vj -Raw
$okMeta = $chk -match "transit_routes: \{ label: 'Transit routes'"
$okList = $chk -match "activityLayers = \['transit', 'transit_routes'\]"
$open  = ([regex]::Matches($chk,'\{')).Count
$close = ([regex]::Matches($chk,'\}')).Count
Write-Host ("LAYER_META entry present: {0}" -f $okMeta)
Write-Host ("activityLayers updated:   {0}" -f $okList)
Write-Host ("brace balance: {0} / {1}" -f $open,$close)
if (-not ($okMeta -and $okList) -or ($open -ne $close)) {
    Copy-Item $bak $vj -Force; throw "verify failed; reverted"
}

Write-Host "redeploying viewer..."
Push-Location $code
python build_ui_manifest.py --data-dir "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data" | Out-Null
Pop-Location
Write-Host "DONE. Ctrl+F5, load Detroit, the 'Transit routes' toggle is under the Activity group."
