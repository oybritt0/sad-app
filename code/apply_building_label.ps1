# apply_building_label.ps1
# Relabels the building color-mode radio from "Dominant POI" to "Building use (OSM)"
# and updates the matching hint text. PURE LABEL CHANGE -- no logic touched. The
# coloring already works off OSM tags (buildingTagProgram, untagged->grey); only
# the user-facing strings were stale. value="dominant" is left unchanged because
# the code keys on that string.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$data = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data"
$html = Join-Path $code "viewer\index.html"
$js   = Join-Path $code "viewer\viewer.js"
foreach ($f in @($html,$js)) { if (-not (Test-Path $f)) { throw "Not found: $f" } }

$stamp = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item $html "$html.bak_label_$stamp"
Copy-Item $js   "$js.bak_label_$stamp"
Write-Host "backups written (.bak_label_$stamp)"

# ---- index.html: the radio label text only ----
$h = Get-Content $html -Raw
$oldLabel = '<label><input type="radio" name="bmode" value="dominant">Dominant POI</label>'
$newLabel = '<label><input type="radio" name="bmode" value="dominant">Building use (OSM)</label>'
if ($h.Contains($newLabel)) {
    Write-Host "index.html label already updated."
} elseif ($h.Contains($oldLabel)) {
    $h = $h.Replace($oldLabel, $newLabel)
    Set-Content -Path $html -Value $h -Encoding UTF8
    Write-Host "index.html: radio relabeled -> 'Building use (OSM)'"
} else {
    throw "Could not find the 'Dominant POI' radio label in index.html (markup may differ)"
}

# ---- viewer.js: the hint text ----
$j = Get-Content $js -Raw
$oldHint = "Type colors show in Dominant POI mode"
$newHint = "Type colors show in Building use (OSM) mode"
if ($j.Contains($newHint)) {
    Write-Host "viewer.js hint already updated."
} elseif ($j.Contains($oldHint)) {
    $j = $j.Replace($oldHint, $newHint)
    Set-Content -Path $js -Value $j -Encoding UTF8
    Write-Host "viewer.js: hint text updated"
} else {
    Write-Host "NOTE: hint text '$oldHint' not found (already changed or worded differently) -- skipping, not fatal"
}

# ---- verify ----
$h2 = Get-Content $html -Raw
$okLabel = $h2.Contains($newLabel)
$stillOld = $h2.Contains('>Dominant POI<')
Write-Host ("radio relabeled:        {0}" -f $okLabel)
Write-Host ("old label gone:         {0}" -f (-not $stillOld))
if (-not $okLabel -or $stillOld) {
    Write-Host "VERIFICATION FAILED -- restoring backups"
    Copy-Item "$html.bak_label_$stamp" $html -Force
    Copy-Item "$js.bak_label_$stamp" $js -Force
    throw "reverted"
}

# ---- redeploy ----
Push-Location $code
python build_ui_manifest.py --data-dir $data
Pop-Location

Write-Host ""
Write-Host "DONE. Hard-refresh the viewer (Ctrl+F5). The building color toggle now reads"
Write-Host "'Anchors / Building use (OSM)'. Behavior is unchanged (it already colored by"
Write-Host "OSM tag, untagged = grey)."
