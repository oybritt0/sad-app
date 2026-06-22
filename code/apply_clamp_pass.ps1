# apply_clamp_pass.ps1
# Adds clampWildCoords() to viewer_export_shrink.js and calls it inside the
# XMLSerializer patch (right after fixWildClipPath), so EVERY export self-cleans
# wild coordinates -- no post-processing needed. Clamp mode preserves the
# boundary outline (unlike drop). Backs up, verifies, redeploys.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$data = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data"
$file = Join-Path $code "viewer\viewer_export_shrink.js"
if (-not (Test-Path $file)) { throw "Not found: $file" }

$bak = "$file.bak_clamp_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $file $bak
Write-Host "backup: $bak"

$src = Get-Content $file -Raw

if ($src -match 'function clampWildCoords') {
    Write-Host "clampWildCoords already present -- skipping function insert."
} else {
    # insert the function definition just before 'function fixWildClipPath'
    $anchor = '  function fixWildClipPath(svgRoot, stats) {'
    $idx = $src.IndexOf($anchor)
    if ($idx -lt 0) { throw "anchor 'function fixWildClipPath' not found" }

    $fn = @'
  // Clamp ANY path/circle coordinate that falls wildly outside the artboard
  // (e.g. the sad_boundary dim-overlay rectangle, the crop wrapper ring). Clamp
  // -- not drop -- so multi-subpath elements keep their real outline. This is
  // the in-export equivalent of the standalone sanitize_export_svg.py clamp pass
  // and makes exports open in Illustrator's GPU preview without post-processing.
  function clampWildCoords(svgRoot, stats) {
    const svg = svgRoot.tagName && svgRoot.tagName.toLowerCase() === 'svg'
                ? svgRoot
                : (svgRoot.querySelector && svgRoot.querySelector('svg')) || svgRoot;
    let W = 0, H = 0;
    const vb = (svg.getAttribute && svg.getAttribute('viewBox')) || '';
    const vbn = vb.split(/[\s,]+/).map(parseFloat).filter(isFinite);
    if (vbn.length === 4) { W = vbn[2]; H = vbn[3]; }
    if (!W || !H) {
      W = parseFloat(svg.getAttribute && svg.getAttribute('width')) || 2000;
      H = parseFloat(svg.getAttribute && svg.getAttribute('height')) || 2000;
    }
    const pad = 0.10;
    const xlo = -pad * W, xhi = (1 + pad) * W;
    const ylo = -pad * H, yhi = (1 + pad) * H;
    const trigger = Math.max(xhi, yhi) * 3;
    const clamp = (v, lo, hi) => (v < lo ? lo : (v > hi ? hi : v));
    let pathsFixed = 0, circlesFixed = 0;

    const paths = svgRoot.querySelectorAll ? svgRoot.querySelectorAll('path') : [];
    paths.forEach(p => {
      const d = p.getAttribute('d'); if (!d) return;
      const nums = d.match(/-?\d+(?:\.\d+)?/g); if (!nums) return;
      let mx = 0; for (const n of nums) { const a = Math.abs(+n); if (a > mx) mx = a; }
      if (mx <= trigger) return;
      let idx = 0;
      const nd = d.replace(/-?\d+(?:\.\d+)?/g, m => {
        let v = parseFloat(m);
        v = (idx % 2 === 0) ? clamp(v, xlo, xhi) : clamp(v, ylo, yhi);
        idx++;
        return String(Math.round(v * 10) / 10);
      });
      p.setAttribute('d', nd);
      pathsFixed++;
    });

    const circles = svgRoot.querySelectorAll ? svgRoot.querySelectorAll('circle') : [];
    circles.forEach(c => {
      const cx = parseFloat(c.getAttribute('cx')), cy = parseFloat(c.getAttribute('cy'));
      let changed = false;
      if (isFinite(cx) && (cx < -trigger || cx > trigger)) { c.setAttribute('cx', clamp(cx, xlo, xhi)); changed = true; }
      if (isFinite(cy) && (cy < -trigger || cy > trigger)) { c.setAttribute('cy', clamp(cy, ylo, yhi)); changed = true; }
      if (changed) circlesFixed++;
    });

    if (pathsFixed || circlesFixed) {
      stats.coordsClamped = { paths: pathsFixed, circles: circlesFixed };
    }
  }

'@
    $src = $src.Substring(0, $idx) + $fn + $src.Substring($idx)
    Write-Host "inserted clampWildCoords() definition"
}

# wire the call: after the fixWildClipPath(node, stats) call line
$callAnchor = 'fixWildClipPath(node, stats);'
if ($src -notmatch [regex]::Escape('clampWildCoords(node, stats);')) {
    $ci = $src.IndexOf($callAnchor)
    if ($ci -lt 0) { throw "call site 'fixWildClipPath(node, stats);' not found" }
    $lineEnd = $src.IndexOf("`n", $ci)
    if ($lineEnd -lt 0) { $lineEnd = $ci + $callAnchor.Length }
    $insert = "`n        clampWildCoords(node, stats);   // clamp wild path/circle coords (boundary rect etc.)"
    $src = $src.Substring(0, $lineEnd) + $insert + $src.Substring($lineEnd)
    Write-Host "wired clampWildCoords(node, stats) call after fixWildClipPath"
} else {
    Write-Host "clampWildCoords call already wired -- skipping."
}

Set-Content -Path $file -Value $src -Encoding UTF8

# verify
$chk = Get-Content $file -Raw
$hasDef  = $chk -match 'function clampWildCoords'
$hasCall = $chk -match [regex]::Escape('clampWildCoords(node, stats);')
Write-Host ("clampWildCoords defined: {0}" -f $hasDef)
Write-Host ("clampWildCoords called:  {0}" -f $hasCall)
if (-not ($hasDef -and $hasCall)) { throw "verification failed -- restore from $bak" }

# redeploy
Push-Location $code
python build_ui_manifest.py --data-dir $data
Pop-Location

Write-Host ""
Write-Host "DONE. Hard-refresh the viewer (Ctrl+F5), then export a drawn district."
Write-Host "It should open in Illustrator with NO post-processing. Verify max coord:"
Write-Host '  $svg = Get-ChildItem "$HOME\Downloads\59_Drawn-district_Philadelphia_viewer_export*.svg" | Sort LastWriteTime -Desc | Select -First 1'
Write-Host '  python -c "import re; t=open(r''PATH'',encoding=''utf-8'',errors=''replace'').read(); t=re.sub(r''data:image/[^;]+;base64,[A-Za-z0-9+/=]+'','''',t); n=[abs(float(x)) for x in re.findall(r''-?\d+\.?\d*'',t)]; print(''max coord:'', f''{max(n):,.0f}'')"'
