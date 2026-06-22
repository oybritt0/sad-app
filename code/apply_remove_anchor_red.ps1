# apply_remove_anchor_red.ps1
# Removes the red-anchor color/stroke override so anchors render like any other
# building (grey in Standard mode, OSM tag in Building use mode). Keeps anchor
# DETECTION (sadStadia) intact -- it's still used by the building-type filter to
# classify anchors as 'sport'. Also removes the red 'Anchor venue' legend row and
# renames the 'Anchors' radio to 'Standard'.
#
# Safe pattern: backup, exact-string edits, verify, auto-revert on failure, redeploy.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$data = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data"
$js   = Join-Path $code "viewer\viewer.js"
$html = Join-Path $code "viewer\index.html"
foreach ($f in @($js,$html)) { if (-not (Test-Path $f)) { throw "Not found: $f" } }

$stamp = Get-Date -Format yyyyMMdd_HHmmss
Copy-Item $js   "$js.bak_anchor_$stamp"
Copy-Item $html "$html.bak_anchor_$stamp"
Write-Host "backups written (.bak_anchor_$stamp)"

$j = Get-Content $js -Raw

# helper: replace exactly once, error if 0 or >1 matches
function ReplaceOnce([string]$text, [string]$old, [string]$new, [string]$why) {
    $count = ([regex]::Matches($text, [regex]::Escape($old))).Count
    if ($count -ne 1) { throw "[$why] expected exactly 1 match, found $count" }
    return $text.Replace($old, $new)
}

# 1. FILL: remove the red branch in baseColor()  (line 1374)
$j = ReplaceOnce $j @'
  function baseColor(d) {
    if (sadStadia.has(d)) return STADIUM_RED;
    if (dominant) {
'@ @'
  function baseColor(d) {
    if (dominant) {
'@ "fill baseColor"

# 2. STROKE (outline mode): remove the red branch  (line 1389)
$j = ReplaceOnce $j @'
       .attr('stroke', d => {
         if (sadStadia.has(d)) return STADIUM_RED;
         if (dominant) {
'@ @'
       .attr('stroke', d => {
         if (dominant) {
'@ "outline stroke"

# 3. STROKE-WIDTH: anchors no longer get the 1.6x bump  (line 1396)
$j = ReplaceOnce $j @'
       .attr('stroke-width', d => sadStadia.has(d) ? owt * 1.6 : owt)
'@ @'
       .attr('stroke-width', owt)
'@ "stroke-width"

# 5. LEGEND: remove the red 'Anchor venue' row + its comment  (lines 1775-1776)
$j = ReplaceOnce $j @'
    // Anchor venues are always red
    blockRow(STADIUM_RED, 'Anchor venue', { outline: outlineMode });
'@ '' "legend anchor row"

Set-Content -Path $js -Value $j -Encoding UTF8
Write-Host "viewer.js: removed red fill/stroke/width + legend row"

# 6. index.html: rename the radio 'Anchors' -> 'Standard'
$h = Get-Content $html -Raw
$h = ReplaceOnce $h `
  '<label><input type="radio" name="bmode" value="default" checked>Anchors</label>' `
  '<label><input type="radio" name="bmode" value="default" checked>Standard</label>' `
  "radio label"
Set-Content -Path $html -Value $h -Encoding UTF8
Write-Host "index.html: 'Anchors' -> 'Standard'"

# ---- verify ----
$j2 = Get-Content $js -Raw
$h2 = Get-Content $html -Raw
$redFillGone   = -not ($j2 -match 'if \(sadStadia\.has\(d\)\) return STADIUM_RED;')
$widthGone     = -not ($j2 -match 'sadStadia\.has\(d\) \? owt \* 1\.6')
$legendGone    = -not ($j2 -match "blockRow\(STADIUM_RED, 'Anchor venue'")
$labelOk       = $h2 -match '>Standard</label>'
$detectKept    = $j2 -match 'const prog = sadStadia\.has\(d\) \? ''sport'''   # filter classification stays
$openJ = ([regex]::Matches($j2,'\{')).Count; $closeJ = ([regex]::Matches($j2,'\}')).Count

Write-Host ("red fill/stroke removed: {0}" -f $redFillGone)
Write-Host ("stroke-width bump gone:  {0}" -f $widthGone)
Write-Host ("legend row removed:      {0}" -f $legendGone)
Write-Host ("radio relabeled:         {0}" -f $labelOk)
Write-Host ("anchor detection kept:   {0}" -f $detectKept)
Write-Host ("js braces balanced:      {0}  ({1}/{2})" -f ($openJ -eq $closeJ), $openJ, $closeJ)

if (-not ($redFillGone -and $widthGone -and $legendGone -and $labelOk -and $detectKept -and ($openJ -eq $closeJ))) {
    Write-Host "VERIFICATION FAILED -- restoring backups"
    Copy-Item "$js.bak_anchor_$stamp" $js -Force
    Copy-Item "$html.bak_anchor_$stamp" $html -Force
    throw "reverted"
}

# ---- redeploy ----
Push-Location $code
python build_ui_manifest.py --data-dir $data
Pop-Location

Write-Host ""
Write-Host "DONE. Hard-refresh the viewer (Ctrl+F5). Anchors now render like any other"
Write-Host "building (grey in Standard, OSM colour in Building use). Toggle reads"
Write-Host "'Standard / Building use (OSM)'."
