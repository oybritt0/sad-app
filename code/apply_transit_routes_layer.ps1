# apply_transit_routes_layer.ps1
# Adds the transit-routes VIEWER layer (module 14 data side already done):
#   (1) build_ui_manifest.py: register transit_routes layer
#   (2) viewer.js: add transit_routes:false default (off, menu toggle)
#   (3) viewer.js: renderTransitRoutes() — mode-colored lines, fill:none
#   (4) viewer.js: dispatch call in the 05_activity group, after transit
# Safe pattern: timestamped backups, exact-string edits, verify, redeploy,
# auto-revert on failure.

$ErrorActionPreference = 'Stop'
$code = "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\code"
$vj   = Join-Path $code "viewer\viewer.js"
$bm   = Join-Path $code "build_ui_manifest.py"
foreach ($f in @($vj,$bm)) { if (-not (Test-Path $f)) { throw "Not found: $f" } }

$stamp = Get-Date -Format yyyyMMdd_HHmmss
$vbak = "$vj.bak_routes_$stamp"; Copy-Item $vj $vbak
$mbak = "$bm.bak_routes_$stamp"; Copy-Item $bm $mbak
Write-Host "backups:`n  $vbak`n  $mbak"

# ---------- (1) manifest: register transit_routes ----------
$msrc = Get-Content $bm -Raw
$mold = "    ('transit',       'derived/transit/transit_stations.geojson',   'activity'),"
$mnew = "    ('transit',       'derived/transit/transit_stations.geojson',   'activity'),`r`n    ('transit_routes','derived/transit/transit_routes.geojson',     'activity'),"
$mc = ([regex]::Matches($msrc,[regex]::Escape($mold))).Count
if ($mc -ne 1) { throw "manifest: expected 1 transit-stations line, found $mc" }
$msrc = $msrc.Replace($mold,$mnew)
Set-Content -Path $bm -Value $msrc -Encoding UTF8
Write-Host "manifest: transit_routes layer registered"

# ---------- (2) viewer: off-by-default flag ----------
$vsrc = Get-Content $vj -Raw
$fold = "    roads: true, pois: false, transit: false, walkshed: false,"
$fnew = "    roads: true, pois: false, transit: false, transit_routes: false, walkshed: false,"
$fc = ([regex]::Matches($vsrc,[regex]::Escape($fold))).Count
if ($fc -ne 1) { throw "viewer: expected 1 layerVisible line, found $fc" }
$vsrc = $vsrc.Replace($fold,$fnew)
Write-Host "viewer: transit_routes default OFF added"

# ---------- (3) viewer: dispatch call after transit block ----------
$dold = @'
  if (state.layerVisible.transit && state.data.transit) {
    renderTransit(actG.append('g').attr('id', 'transit'),
                   state.data.transit);
  }
'@
$dnew = @'
  if (state.layerVisible.transit && state.data.transit) {
    renderTransit(actG.append('g').attr('id', 'transit'),
                   state.data.transit);
  }
  if (state.layerVisible.transit_routes && state.data.transit_routes) {
    renderTransitRoutes(actG.append('g').attr('id', 'transit_routes'),
                   state.data.transit_routes);
  }
'@
$dc = ([regex]::Matches($vsrc,[regex]::Escape($dold))).Count
if ($dc -ne 1) { throw "viewer: expected 1 transit dispatch block, found $dc" }
$vsrc = $vsrc.Replace($dold,$dnew)
Write-Host "viewer: transit_routes dispatch call added"

# ---------- (4) viewer: renderTransitRoutes function (after renderTransit) ----------
$rold = @'
  sel.append('title').text(d => transitName(d.feature.properties));
}
'@
$rnew = @'
  sel.append('title').text(d => transitName(d.feature.properties));
}

// ─── Transit routes (GTFS shapes, colored by mode) ──────────────────────────
const TRANSIT_MODE_COLOR = {
  subway: '#e8743b', rail: '#e8743b', tram: '#cdbb4f',
  bus: '#5b9bd5', ferry: '#4aa3a3', other: '#9aa0a8',
};
function renderTransitRoutes(g, gj) {
  const feats = (gj.features || []).filter(f =>
    f.geometry && (f.geometry.type === 'LineString' || f.geometry.type === 'MultiLineString'));
  console.log('[render] transit_routes: ' + feats.length + ' routes');
  if (feats.length === 0) return;
  const colorFor = f => {
    const m = (f.properties && f.properties.mode) || 'other';
    return TRANSIT_MODE_COLOR[m] || TRANSIT_MODE_COLOR.other;
  };
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
}
'@
$rc = ([regex]::Matches($vsrc,[regex]::Escape($rold))).Count
if ($rc -ne 1) { throw "viewer: expected 1 renderTransit tail anchor, found $rc" }
$vsrc = $vsrc.Replace($rold,$rnew)
Set-Content -Path $vj -Value $vsrc -Encoding UTF8
Write-Host "viewer: renderTransitRoutes() added"

# ---------- verify + brace balance ----------
$chk = Get-Content $vj -Raw
$hasFn   = $chk -match "function renderTransitRoutes\(g, gj\)"
$hasDisp = $chk -match "renderTransitRoutes\(actG\.append"
$hasFlag = $chk -match "transit_routes: false"
$open  = ([regex]::Matches($chk,'\{')).Count
$close = ([regex]::Matches($chk,'\}')).Count
$mok   = (Get-Content $bm -Raw) -match "transit_routes"
Write-Host ("renderTransitRoutes present: {0}" -f $hasFn)
Write-Host ("dispatch present:            {0}" -f $hasDisp)
Write-Host ("off-by-default flag present: {0}" -f $hasFlag)
Write-Host ("manifest entry present:      {0}" -f $mok)
Write-Host ("brace balance: {0} open / {1} close" -f $open,$close)
if (-not ($hasFn -and $hasDisp -and $hasFlag -and $mok) -or ($open -ne $close)) {
    Write-Host "VERIFY FAILED -> restoring backups"
    Copy-Item $vbak $vj -Force; Copy-Item $mbak $bm -Force
    throw "reverted both"
}

# py-compile the manifest builder
Push-Location $code
python -c "import py_compile; py_compile.compile(r'$bm', doraise=True); print('build_ui_manifest py-compile OK')"
if ($LASTEXITCODE -ne 0) { Pop-Location; Copy-Item $vbak $vj -Force; Copy-Item $mbak $bm -Force; throw "py-compile failed; reverted" }

Write-Host "rebuilding manifest + redeploying viewer..."
python build_ui_manifest.py --data-dir "C:\Users\rbritain\Documents\ROD\Search Tool\ROD\Detroit_Test\data"
Pop-Location

Write-Host ""
Write-Host "DONE. Ctrl+F5, load a district with transit (e.g. Detroit), toggle 'transit_routes' in the layer menu."
