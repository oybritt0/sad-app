# apply_route_hover.ps1
# Route hover unreliable because the visible line is only 1.4px (tiny hit
# target). Add an invisible ~10px-wide transparent "hit line" under each route
# that carries the <title>, so hovering anywhere near the line works. Visible
# line stays thin. Safe pattern: backup, exact edit, verify, redeploy, revert.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$vj   = Join-Path $code "viewer\viewer.js"
if (-not (Test-Path $vj)) { throw "Not found: $vj" }

$bak = "$vj.bak_routehover_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $vj $bak
Write-Host "backup: $bak"

$src = Get-Content $vj -Raw

# Replace the single-path render body with hit-line + visible-line.
$old = @'
  const sel = g.selectAll('path').data(feats).join('path')
    .attr('d', state.pathGen)
    .attr('fill', 'none')
    .attr('stroke', colorFor)
    .attr('stroke-width', 1.4)
    .attr('stroke-opacity', 0.85)
    .attr('stroke-linejoin', 'round')
    .attr('stroke-linecap', 'round');
  sel.append('title').text(d => {
    const p = d.properties || {};
    const nm = p.short_name || p.long_name || p.route_id || 'route';
    const md = p.mode || 'other';
    return nm + ' (' + md + ')' + (p.served ? ' — enters SAD' : '');
  });
'@

$new = @'
  const titleFor = d => {
    const p = d.properties || {};
    const nm = p.short_name || p.long_name || p.route_id || 'route';
    const md = p.mode || 'other';
    return nm + ' (' + md + ')' + (p.served ? ' — enters SAD' : '');
  };
  // Invisible wide hit-line first (easy mouse target), carries the tooltip.
  const hit = g.selectAll('path.route-hit').data(feats).join('path')
    .attr('class', 'route-hit')
    .attr('d', state.pathGen)
    .attr('fill', 'none')
    .attr('stroke', 'transparent')
    .attr('stroke-width', 10)
    .attr('stroke-linecap', 'round')
    .style('cursor', 'pointer');
  hit.append('title').text(titleFor);
  // Visible thin line on top (no pointer events so the hit-line catches hover).
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
'@

$c = ([regex]::Matches($src,[regex]::Escape($old))).Count
if ($c -ne 1) { throw "expected 1 match for the route render body, found $c" }
$src = $src.Replace($old,$new)
Set-Content -Path $vj -Value $src -Encoding UTF8
Write-Host "patched: added transparent hit-line for reliable route hover"

# verify + brace balance
$chk = Get-Content $vj -Raw
$okHit  = $chk -match "route-hit"
$okLine = $chk -match "route-line"
$open  = ([regex]::Matches($chk,'\{')).Count
$close = ([regex]::Matches($chk,'\}')).Count
Write-Host ("hit-line present:  {0}" -f $okHit)
Write-Host ("line class present:{0}" -f $okLine)
Write-Host ("brace balance: {0} / {1}" -f $open,$close)
if (-not ($okHit -and $okLine) -or ($open -ne $close)) {
    Copy-Item $bak $vj -Force; throw "verify failed; reverted"
}

Write-Host "redeploying viewer..."
Push-Location $code
python build_ui_manifest.py --data-dir "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data" | Out-Null
Pop-Location
Write-Host "DONE. Ctrl+F5, hover any route line in Detroit — tooltip should appear."
