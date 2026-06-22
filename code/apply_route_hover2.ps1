# apply_route_hover2.ps1
# Previous hover patch failed to match (em-dash mojibake in the title string).
# This version anchors ONLY on ASCII-safe text: it replaces the visible-line
# `const sel = ...` chain (no special chars) with hit-line + visible-line, and
# leaves the existing `sel.append('title')...` block untouched by retargeting
# the variable. We rename the visible selection and add a hit-line that the
# existing title attaches to.
#
# Strategy: the existing code is
#     const sel = g.selectAll('path').data(feats).join('path') ...chain...;
#     sel.append('title').text(...);
# We replace the `const sel = g.selectAll('path')...` line-start so that:
#   - a transparent wide hit-line is created as `sel` (so the existing
#     sel.append('title') lands on the hit-line — easy target), and
#   - the visible thin line is drawn separately with pointer-events none.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$vj   = Join-Path $code "viewer\viewer.js"
if (-not (Test-Path $vj)) { throw "Not found: $vj" }

$bak = "$vj.bak_routehover2_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $vj $bak
Write-Host "backup: $bak"

$src = Get-Content $vj -Raw

# ASCII-only anchor: the visible-line selection chain. Replace it so `sel`
# becomes the transparent hit-line (carrying the existing title), and add the
# visible thin line separately with pointer-events:none.
$old = @'
  const sel = g.selectAll('path').data(feats).join('path')
    .attr('d', state.pathGen)
    .attr('fill', 'none')
    .attr('stroke', colorFor)
    .attr('stroke-width', 1.4)
    .attr('stroke-opacity', 0.85)
    .attr('stroke-linejoin', 'round')
    .attr('stroke-linecap', 'round');
'@

$new = @'
  // Visible thin line (no pointer events; the hit-line below catches hover).
  g.selectAll('path.route-line').data(feats).join('path')
    .attr('class', 'route-line')
    .attr('d', state.pathGen)
    .attr('fill', 'none')
    .attr('stroke', colorFor)
    .attr('stroke-width', 1.4)
    .attr('stroke-opacity', 0.85)
    .attr('stroke-linejoin', 'round')
    .attr('stroke-linecap', 'round')
    .style('pointer-events', 'none');
  // Transparent wide hit-line on top catches the mouse; existing title binds here.
  const sel = g.selectAll('path.route-hit').data(feats).join('path')
    .attr('class', 'route-hit')
    .attr('d', state.pathGen)
    .attr('fill', 'none')
    .attr('stroke', 'transparent')
    .attr('stroke-width', 12)
    .attr('stroke-linecap', 'round')
    .style('cursor', 'pointer');
'@

$c = ([regex]::Matches($src,[regex]::Escape($old))).Count
if ($c -ne 1) { throw "expected 1 match for visible-line chain, found $c (paste the function so I can re-anchor)" }
$src = $src.Replace($old,$new)
Set-Content -Path $vj -Value $src -Encoding UTF8
Write-Host "patched: hit-line added; existing title now binds to wide transparent line"

# verify + brace balance
$chk = Get-Content $vj -Raw
$okHit  = $chk -match "route-hit"
$okLine = $chk -match "route-line"
$open  = ([regex]::Matches($chk,'\{')).Count
$close = ([regex]::Matches($chk,'\}')).Count
Write-Host ("hit-line present:   {0}" -f $okHit)
Write-Host ("visible-line class: {0}" -f $okLine)
Write-Host ("brace balance: {0} / {1}" -f $open,$close)
if (-not ($okHit -and $okLine) -or ($open -ne $close)) {
    Copy-Item $bak $vj -Force; throw "verify failed; reverted"
}

Write-Host "redeploying viewer..."
Push-Location $code
python build_ui_manifest.py --data-dir "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data" | Out-Null
Pop-Location
Write-Host ""
Write-Host "DONE. Now in the browser: F12 -> Network tab -> check 'Disable cache' -> reload"
Write-Host "(viewer.js is cached; that checkbox forces a fresh fetch). Then hover a Detroit route."
