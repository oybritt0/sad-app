# apply_string_clamp.ps1
# Adds a STRING-LEVEL coordinate clamp to the export, applied to the serialized
# SVG text right before it's returned -- the safe injection point (no DOM timing,
# pure string op, fallback-safe). Makes drawn-district exports open in
# Illustrator with NO post-processing, and CANNOT break the export button: any
# error in the clamp falls back to the original string.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$data = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data"
$file = Join-Path $code "viewer\viewer_export_shrink.js"
if (-not (Test-Path $file)) { throw "Not found: $file" }

$bak = "$file.bak_strclamp_$(Get-Date -Format yyyyMMdd_HHmmss)"
Copy-Item $file $bak
Write-Host "backup: $bak"

$src = Get-Content $file -Raw

# ---- 1. insert clampSvgString() definition before the serializer patch ----
if ($src -match 'function clampSvgString') {
    Write-Host "clampSvgString already present -- skipping definition."
} else {
    $anchor = '  const Orig = XMLSerializer.prototype.serializeToString;'
    $idx = $src.IndexOf($anchor)
    if ($idx -lt 0) { throw "anchor 'const Orig = XMLSerializer...' not found" }
    $fn = @'
  // String-level coordinate clamp, applied to the SERIALIZED svg text (after all
  // DOM passes). Clamps any d=/cx/cy coordinate wildly outside the artboard into
  // it -- e.g. the sad_boundary dim-overlay rectangle that forces Illustrator
  // into outline preview. Operates only on attribute values, never on data: URIs
  // (so the satellite raster is untouched). Fallback-safe: returns the original
  // string on any error, so it can never break the export download.
  function clampSvgString(svgText) {
    try {
      if (typeof svgText !== 'string' || svgText.lastIndexOf('<svg', 200) === -1) return svgText;
      const vbm = svgText.match(/viewBox="\s*([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s*"/);
      let W = 0, H = 0;
      if (vbm) { W = parseFloat(vbm[3]); H = parseFloat(vbm[4]); }
      if (!W || !H) {
        const wm = svgText.match(/\bwidth="([\d.]+)"/), hm = svgText.match(/\bheight="([\d.]+)"/);
        W = wm ? parseFloat(wm[1]) : 2000; H = hm ? parseFloat(hm[1]) : 2000;
      }
      const pad = 0.10;
      const xlo = -pad*W, xhi = (1+pad)*W, ylo = -pad*H, yhi = (1+pad)*H;
      const trigger = Math.max(xhi, yhi) * 3;
      const clampv = (v, lo, hi) => (v < lo ? lo : (v > hi ? hi : v));
      svgText = svgText.replace(/(\sd=")([^"]*)(")/g, (full, p1, d, p3) => {
        const nums = d.match(/-?\d+(?:\.\d+)?/g);
        if (!nums) return full;
        let mx = 0; for (const n of nums) { const a = Math.abs(+n); if (a > mx) mx = a; }
        if (mx <= trigger) return full;
        let idx = 0;
        const nd = d.replace(/-?\d+(?:\.\d+)?/g, m => {
          let v = parseFloat(m);
          v = (idx++ % 2 === 0) ? clampv(v, xlo, xhi) : clampv(v, ylo, yhi);
          return String(Math.round(v*10)/10);
        });
        return p1 + nd + p3;
      });
      svgText = svgText.replace(/\bcx="(-?\d+(?:\.\d+)?)"/g, (full, n) => {
        const v = parseFloat(n); return (v < -trigger || v > trigger) ? ('cx="' + clampv(v,xlo,xhi) + '"') : full;
      });
      svgText = svgText.replace(/\bcy="(-?\d+(?:\.\d+)?)"/g, (full, n) => {
        const v = parseFloat(n); return (v < -trigger || v > trigger) ? ('cy="' + clampv(v,ylo,yhi) + '"') : full;
      });
      return svgText;
    } catch (e) {
      return svgText;
    }
  }

'@
    $src = $src.Substring(0, $idx) + $fn + $src.Substring($idx)
    Write-Host "inserted clampSvgString() definition"
}

# ---- 2. wrap the serializer return so SVG exports get clamped ----
$oldReturn = '    return Orig.call(this, node);'
if ($src -match 'clampSvgString\(__out\)') {
    Write-Host "return already wrapped -- skipping."
} else {
    $newReturn = @'
    {
      const __out = Orig.call(this, node);
      try {
        const __isSvg = node && node.nodeType === 1 && node.tagName &&
                        node.tagName.toLowerCase() === 'svg' &&
                        node.querySelector('[id^="0"], #map_root, [id="page_background"]');
        return __isSvg ? clampSvgString(__out) : __out;
      } catch (e) { return __out; }
    }
'@
    # replace ONLY the first occurrence (the serializer's return)
    $pos = $src.IndexOf($oldReturn)
    if ($pos -lt 0) { throw "serializer return line not found" }
    $src = $src.Substring(0, $pos) + $newReturn + $src.Substring($pos + $oldReturn.Length)
    Write-Host "wrapped serializer return with clampSvgString"
}

Set-Content -Path $file -Value $src -Encoding UTF8

# ---- 3. verify ----
$chk = Get-Content $file -Raw
$hasDef = $chk -match 'function clampSvgString'
$hasWrap = $chk -match 'clampSvgString\(__out\)'
$open = ([regex]::Matches($chk,'\{')).Count
$close = ([regex]::Matches($chk,'\}')).Count
Write-Host ("clampSvgString defined: {0}" -f $hasDef)
Write-Host ("return wrapped:         {0}" -f $hasWrap)
Write-Host ("braces balanced:        {0}  ({1}/{2})" -f ($open -eq $close), $open, $close)
if (-not ($hasDef -and $hasWrap -and ($open -eq $close))) {
    Write-Host "VERIFICATION FAILED -- restoring backup"
    Copy-Item $bak $file -Force
    throw "patch reverted; file restored from $bak"
}

# ---- 4. redeploy ----
Push-Location $code
python build_ui_manifest.py --data-dir $data
Pop-Location

Write-Host ""
Write-Host "DONE. Hard-refresh the viewer (Ctrl+F5), then export any district."
Write-Host "Allow multiple downloads if the browser prompts. The file should open"
Write-Host "in Illustrator with full preview and NO post-processing."
