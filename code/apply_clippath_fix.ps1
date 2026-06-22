# apply_clippath_fix.ps1
# Replaces the buggy fixWildClipPath in viewer_export_shrink.js with the robust
# M/m-aware version, backs up the original, redeploys, and verifies.

$ErrorActionPreference = 'Stop'

$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$data = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data"
$file = Join-Path $code "viewer\viewer_export_shrink.js"

if (-not (Test-Path $file)) { throw "Not found: $file" }

# ---- 1. backup once ----
$bak = "$file.bak_clippath_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $file $bak
Write-Host "backup: $bak"

# ---- 2. read, locate the function, replace it ----
$src = Get-Content $file -Raw

$startMarker = 'function fixWildClipPath(svgRoot, stats) {'
$startIdx = $src.IndexOf($startMarker)
if ($startIdx -lt 0) { throw "Could not find 'function fixWildClipPath(svgRoot, stats) {' in $file" }

# find the matching closing brace by counting braces from the opening {
$braceStart = $src.IndexOf('{', $startIdx)
$depth = 0
$endIdx = -1
for ($i = $braceStart; $i -lt $src.Length; $i++) {
    $ch = $src[$i]
    if ($ch -eq '{') { $depth++ }
    elseif ($ch -eq '}') {
        $depth--
        if ($depth -eq 0) { $endIdx = $i; break }
    }
}
if ($endIdx -lt 0) { throw "Could not find end of fixWildClipPath function" }

$oldFunc = $src.Substring($startIdx, $endIdx - $startIdx + 1)

$newFunc = @'
function fixWildClipPath(svgRoot, stats) {
    const cp = svgRoot.querySelector('clipPath[id="crop-inside"] path, ' +
                                      '[id="crop-inside"] path');
    if (!cp) return;
    const d = cp.getAttribute('d') || '';
    // Split into subpaths at each moveto. D3 emits an absolute 'M' for the
    // first subpath but often a RELATIVE 'm' for later ones -- the old code
    // searched only for 'M' and so missed (and kept) the wild rectangle.
    const parts = d.match(/[Mm][^Mm]*/g);
    if (!parts || parts.length < 2) return;
    // Artboard size from the viewBox; fall back to width/height.
    const svg = svgRoot.tagName && svgRoot.tagName.toLowerCase() === 'svg'
                ? svgRoot : svgRoot.querySelector('svg') || svgRoot.ownerDocument.documentElement;
    let W = 0, H = 0;
    const vb = (svg.getAttribute && svg.getAttribute('viewBox')) || '';
    const vbn = vb.split(/[\s,]+/).map(parseFloat).filter(isFinite);
    if (vbn.length === 4) { W = vbn[2]; H = vbn[3]; }
    if (!W || !H) {
      W = parseFloat(svg.getAttribute && svg.getAttribute('width')) || 2000;
      H = parseFloat(svg.getAttribute && svg.getAttribute('height')) || 2000;
    }
    const pad = 0.10;
    const limit = Math.max((1 + pad) * W, (1 + pad) * H) * 3;
    // Keep only subpaths whose coordinates fit within (a padded) artboard;
    // drop any subpath with runaway coordinates (the dim-overlay rectangle).
    const kept = parts.filter(sp => {
      const nums = (sp.match(/-?\d+(?:\.\d+)?/g) || []).map(parseFloat);
      if (!nums.length) return false;
      const mx = Math.max.apply(null, nums.map(Math.abs));
      return mx <= limit;
    });
    if (kept.length === parts.length) return;   // nothing wild
    let out = kept.join(' ').trim();
    if (out && !/[Zz]\s*$/.test(out)) out += 'Z';
    if (out) {
      cp.setAttribute('d', out);
      stats.clipPathFixed = true;
    }
  }
'@

$src = $src.Substring(0, $startIdx) + $newFunc + $src.Substring($endIdx + 1)
Set-Content -Path $file -Value $src -Encoding UTF8
Write-Host "patched: $file"

# ---- 3. verify the marker is present and old bug is gone ----
$check = Get-Content $file -Raw
$hasNew = ($check -match 'Split into subpaths at each moveto')
$hasOldBug = ($check -match "indexOf\('M', firstM \+ 1\)")
Write-Host ("new version present: {0}" -f $hasNew)
Write-Host ("old bug removed:     {0}" -f (-not $hasOldBug))
if (-not $hasNew -or $hasOldBug) { throw "Verification failed -- inspect $file (restore from $bak if needed)" }

# ---- 4. redeploy: rebuild manifest copies viewer assets into data\_ui ----
Push-Location $code
python build_ui_manifest.py --data-dir $data
Pop-Location

Write-Host ""
Write-Host "DONE. Next: hard-refresh the viewer (Ctrl+F5), re-export 59, and check max coord:"
Write-Host '  $svg = Get-ChildItem "$HOME\Downloads\59_Drawn-district_Philadelphia_viewer_export*.svg" | Sort LastWriteTime -Desc | Select -First 1'
Write-Host '  python -c "import re; t=open(r''$($svg.FullName)'',encoding=''utf-8'',errors=''replace'').read(); n=[abs(float(x)) for x in re.findall(r''-?\d+\.?\d*'',t)]; print(''max coord:'', f''{max(n):,.0f}'')"'
