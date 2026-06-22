// viewer_parcels.js — Regrid parcels integrated as a native viewer layer.
//
// Chains onto window.sadAfterRender so it composes with viewer_modules.js.
// Injects a new "Parcels" section into the existing sidebar so its checkbox,
// color-mode selector and year-built filter live alongside the other layer
// toggles instead of as a floating panel. Uses state.pathGen (the viewer's
// own projection) directly and sanitizes wrapper-ring polygons the same way
// viewer.js does for every other layer.
//
// Drop-in: <script src="viewer_parcels.js"></script> after viewer_modules.js.

(function () {
  'use strict';
  const TAG = '[parcels]';
  const d3 = window.d3;
  if (!d3) { console.warn(TAG, 'd3 not found — bailing'); return; }
  console.log(TAG, 'module loading');

  // ── style: match the viewer's light sidebar aesthetic ───────────────────────
  const CSS = `
  /* Sidebar section ------------------------------------------------ */
  #parcels-section .pcl-mode { width: 100%; padding: 5px 8px; font-family: var(--mono); font-size: 11px;
    background: #fff; color: var(--ink); border: 1px solid var(--line); margin-bottom: 10px; }
  #parcels-section .pcl-legend { margin-bottom: 8px; max-height: 200px; overflow-y: auto; }
  #parcels-section .pcl-leg-row { display: flex; align-items: center; gap: 7px; padding: 2px 0;
    cursor: pointer; font-size: 11px; color: var(--ink-soft); transition: opacity 0.15s; }
  #parcels-section .pcl-leg-row.off { opacity: 0.32; }
  #parcels-section .pcl-leg-row .sw { width: 11px; height: 11px; flex: 0 0 11px; border: 1px solid var(--line); }
  #parcels-section .pcl-leg-row .nm { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10.5px; }
  #parcels-section .pcl-leg-row .ct { color: var(--ink-faint); font-family: var(--mono); font-size: 10px; }
  #parcels-section .pcl-grad { display: flex; align-items: center; gap: 6px; font-family: var(--mono); font-size: 10px; color: var(--ink-faint); margin-bottom: 8px; }
  #parcels-section .pcl-grad .bar { height: 8px; flex: 1 1 auto; border: 1px solid var(--line); }
  #parcels-section .pcl-yr { margin: 6px 0 8px; }
  #parcels-section .pcl-yr .lab { font-family: var(--mono); font-size: 10px; color: var(--ink-faint); letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 4px; }
  #parcels-section .pcl-yr-row { display: flex; align-items: center; gap: 6px; }
  #parcels-section .pcl-yr-row input[type=range] { flex: 1 1 auto; }
  #parcels-section .pcl-yr-row .val { font-family: var(--mono); font-size: 10px; color: var(--ink); min-width: 32px; }
  #parcels-section .pcl-count { font-family: var(--mono); font-size: 10px; color: var(--ink-faint); padding-top: 6px; border-top: 1px solid var(--line-faint); }
  #parcels-section .pcl-clear { background: transparent; border: 1px solid var(--line); color: var(--ink-soft);
    padding: 3px 8px; font-family: var(--mono); font-size: 10px; cursor: pointer; margin-top: 6px; }
  #parcels-section .pcl-clear:hover { color: var(--accent); border-color: var(--accent-ring); }
  #parcels-section .pcl-status { font-family: var(--mono); font-size: 10px; color: var(--ink-faint); margin-bottom: 8px; line-height: 1.4; }
  #parcels-section.disabled-section .pcl-mode,
  #parcels-section.disabled-section .pcl-legend,
  #parcels-section.disabled-section .pcl-yr,
  #parcels-section.disabled-section .pcl-count,
  #parcels-section.disabled-section .pcl-clear { opacity: 0.35; pointer-events: none; }

  /* Parcel polygons in the canvas ---------------------------------- */
  .layer-parcels path { fill-opacity: 0.30; stroke: #f4f1ea; stroke-width: 0.6; stroke-opacity: 0.7;
    vector-effect: non-scaling-stroke; pointer-events: all; cursor: pointer; }
  .layer-parcels path:hover { stroke: #f1c50c; stroke-width: 1.8; fill-opacity: 0.55; }
  .layer-parcels path.dimmed { fill-opacity: 0.05; stroke-opacity: 0.18; }
  .layer-parcels path.selected { stroke: #f1c50c; stroke-width: 2.2; fill-opacity: 0.55; }

  /* Parcel detail card (right side, only when a parcel is clicked) -- */
  #parcel-detail { position: absolute; right: 18px; top: 18px; z-index: 50; width: 260px;
    background: #fff; color: var(--ink); border: 1px solid var(--line); padding: 14px 16px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; font-size: 11.5px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  #parcel-detail .pd-close { float: right; background: transparent; border: none; color: var(--ink-faint);
    font-size: 16px; cursor: pointer; padding: 0 4px; line-height: 1; }
  #parcel-detail .pd-close:hover { color: var(--ink); }
  #parcel-detail h3 { margin: 0 0 6px; font-family: var(--mono); font-size: 11px; font-weight: 400;
    letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink); }
  #parcel-detail .pd-addr { color: var(--ink); font-size: 12.5px; font-weight: 500; margin-bottom: 4px; }
  #parcel-detail .pd-owner { color: var(--ink-soft); font-size: 11px; margin-bottom: 10px; line-height: 1.4;
    cursor: pointer; }
  #parcel-detail .pd-owner:hover { color: var(--ink); }
  #parcel-detail dl { margin: 0; display: grid; grid-template-columns: 80px 1fr; gap: 4px 10px; font-size: 11px; }
  #parcel-detail dt { color: var(--ink-faint); font-family: var(--mono); font-size: 9.5px; letter-spacing: 0.04em;
    text-transform: uppercase; padding-top: 2px; }
  #parcel-detail dd { margin: 0; color: var(--ink); font-variant-numeric: tabular-nums; }
  #parcel-detail .pd-link { color: var(--accent); text-decoration: none; font-family: var(--mono); font-size: 10.5px; }
  #parcel-detail .pd-link:hover { text-decoration: underline; }

  /* Tooltip ------------------------------------------------------- */
  #parcel-tip { position: fixed; z-index: 60; pointer-events: none; opacity: 0; transition: opacity 0.1s;
    background: #fff; color: var(--ink); border: 1px solid var(--line); padding: 6px 9px;
    font: 11px -apple-system, "Segoe UI", Arial, sans-serif; max-width: 260px; line-height: 1.4;
    box-shadow: 0 2px 6px rgba(0,0,0,0.08); }
  #parcel-tip.on { opacity: 1; }
  #parcel-tip .nm { font-weight: 500; color: var(--ink); margin-bottom: 2px; }
  #parcel-tip .sub { color: var(--ink-soft); font-size: 10.5px; }
  `;
  const styleEl = document.createElement('style'); styleEl.textContent = CSS; document.head.appendChild(styleEl);

  // ── helpers ─────────────────────────────────────────────────────────────────
  function pick(rec, ...keys) {
    if (!rec) return null;
    const p = rec.properties || {}; const fd = p.fields || {};
    for (const k of keys) {
      const v = (k in p) ? p[k] : fd[k];
      if (v !== null && v !== undefined && v !== '') return v;
    }
    return null;
  }
  function num(v) { const n = +v; return Number.isFinite(n) ? n : null; }
  function escape(s) { return String(s).replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c])); }
  function ramp(t, scale) {
    t = Math.max(0, Math.min(1, t));
    if (scale === 'viridis') return d3.interpolateRgb('#440154', '#fde725')(t);
    if (scale === 'magma')   return d3.interpolateRgb('#000004', '#fcfdbf')(t);
    return d3.interpolateRgb('#1f4068', '#f9c74f')(t);
  }
  const PALETTE = ['#5fa8d3','#f2a154','#7fb069','#c084d3','#e4a672','#a3b18a','#d88c9a','#4a6fa5','#b56576','#9a8c98','#c9ada7','#588157'];
  function paletteFor(categories) {
    const out = {};
    categories.forEach((c, i) => { out[c] = PALETTE[i % PALETTE.length]; });
    return out;
  }

  // ── sanitization: drop wrapper rings (exact same logic as viewer.js) ─────────
  // This is THE fix for parcels rendering as full-canvas pink with a tiny notch:
  // Regrid sometimes returns each parcel with a query-bbox-sized outer ring and
  // the real parcel as a hole. Stripping those wrapper rings restores correct scale.
  function ringBbox(ring) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const c of ring) {
      if (!c || c.length < 2) continue;
      if (c[0] < minX) minX = c[0]; if (c[0] > maxX) maxX = c[0];
      if (c[1] < minY) minY = c[1]; if (c[1] > maxY) maxY = c[1];
    }
    return { sx: maxX - minX, sy: maxY - minY };
  }
  function cleanRings(rings) {
    if (!rings || rings.length <= 1) return rings;
    const stats = rings.map(r => ({ ring: r, bbox: ringBbox(r) }));
    let minSx = Infinity, minSy = Infinity;
    for (const s of stats) {
      if (s.bbox.sx > 0 && s.bbox.sx < minSx) minSx = s.bbox.sx;
      if (s.bbox.sy > 0 && s.bbox.sy < minSy) minSy = s.bbox.sy;
    }
    const TOL = 10;
    const kept = stats.filter(s => s.bbox.sx <= minSx * TOL && s.bbox.sy <= minSy * TOL);
    return kept.length > 0 ? kept.map(s => s.ring) : [stats[0].ring];
  }
  function cleanGeom(g) {
    if (!g) return null;
    if (g.type === 'Polygon') {
      const cleaned = cleanRings(g.coordinates);
      return cleaned && cleaned.length > 0 ? Object.assign({}, g, { coordinates: cleaned }) : null;
    }
    if (g.type === 'MultiPolygon') {
      const cleaned = (g.coordinates || []).map(cleanRings).filter(p => p && p.length > 0);
      return cleaned.length === 0 ? null : Object.assign({}, g, { coordinates: cleaned });
    }
    return g;
  }
  // Empirical winding correction using d3.geoArea.
  //
  // d3.geoArea returns the spherical area of a polygon in steradians (0 to 4π).
  // A correctly-wound small polygon returns a small number (~1e-10 for a parcel).
  // An inverted polygon (= "everything except this region") returns ~4π (≈12.566).
  // So: if d3.geoArea > 2π we know the ring is wound the wrong way and we reverse it.
  // This bypasses any need to reason about sign conventions ourselves.
  function ensureWinding(rings) {
    if (!rings || !rings.length) return rings;
    return rings.map((ring, i) => {
      try {
        const a = d3.geoArea({ type: 'Polygon', coordinates: [ring] });
        // Outer ring (i===0): correct = small area. Hole rings (i>=1): correct = large area
        // (because a hole on a sphere means "the world except the hole").
        const wantSmall = (i === 0);
        const isSmall = a < 6.28;  // 2π threshold
        return (wantSmall === isSmall) ? ring : ring.slice().reverse();
      } catch (e) { return ring; }
    });
  }

  function sanitizeParcels(gj) {
    if (!gj || !gj.features) return gj;
    // Phase 0: fix winding so d3.geoMercator doesn't treat polygons as inverted
    // (which is what's causing parcels to fill the entire viewport).
    let invertedBefore = 0, invertedAfter = 0;
    gj = Object.assign({}, gj, { features: gj.features.map(f => {
      let g = f.geometry; if (!g) return f;
      try {
        const a0 = d3.geoArea(g);
        if (a0 > 6.28) invertedBefore++;
      } catch (e) {}
      if (g.type === 'Polygon') {
        g = Object.assign({}, g, { coordinates: ensureWinding(g.coordinates) });
      } else if (g.type === 'MultiPolygon') {
        g = Object.assign({}, g, { coordinates: g.coordinates.map(ensureWinding) });
      }
      try {
        const a1 = d3.geoArea(g);
        if (a1 > 6.28) invertedAfter++;
      } catch (e) {}
      return Object.assign({}, f, { geometry: g });
    }) });
    console.log(TAG, '[sanitize] winding fix: inverted before =', invertedBefore,
                '; after =', invertedAfter, '; total =', gj.features.length);

    // Phase 1: clean wrapper rings within each polygon (holes-shaped-as-wrappers).
    let droppedRings = 0;
    let feats = gj.features.map(f => {
      const before = (f.geometry && f.geometry.coordinates && f.geometry.type === 'Polygon')
        ? (f.geometry.coordinates.length || 0)
        : ((f.geometry && f.geometry.coordinates) || []).reduce((a, p) => a + (p.length || 0), 0);
      const cg = cleanGeom(f.geometry);
      if (!cg) return null;
      const after = cg.type === 'Polygon' ? (cg.coordinates.length || 0)
        : cg.coordinates.reduce((a, p) => a + (p.length || 0), 0);
      droppedRings += Math.max(0, before - after);
      return Object.assign({}, f, { geometry: cg });
    }).filter(Boolean);
    // Phase 2: drop oversize sub-polygons from MultiPolygons. Regrid sometimes
    // pairs each real parcel with a sibling wrapper polygon that spans the
    // entire query box; that wrapper renders as a viewport-filling fill.
    // Detect by computing the median sub-polygon size across ALL features and
    // dropping anything more than 25× the median in either dimension.
    const polySizes = [];
    for (const f of feats) {
      const g = f.geometry; if (!g) continue;
      if (g.type === 'Polygon' && g.coordinates && g.coordinates[0]) {
        const bb = ringBbox(g.coordinates[0]);
        polySizes.push(Math.max(bb.sx, bb.sy));
      } else if (g.type === 'MultiPolygon' && g.coordinates) {
        for (const p of g.coordinates) {
          if (p && p[0]) {
            const bb = ringBbox(p[0]);
            polySizes.push(Math.max(bb.sx, bb.sy));
          }
        }
      }
    }
    let droppedPolys = 0;
    if (polySizes.length) {
      polySizes.sort((a, b) => a - b);
      const median = polySizes[Math.floor(polySizes.length / 2)];
      const limit = median * 25;
      const oversize = rings => {
        if (!rings || !rings[0]) return false;
        const bb = ringBbox(rings[0]);
        return Math.max(bb.sx, bb.sy) > limit;
      };
      feats = feats.map(f => {
        let g = f.geometry; if (!g) return null;
        if (g.type === 'MultiPolygon') {
          const kept = g.coordinates.filter(p => {
            if (oversize(p)) { droppedPolys++; return false; }
            return true;
          });
          if (!kept.length) return null;
          if (kept.length === 1) {
            // promote single remaining polygon to a Polygon for cleaner pathing
            g = { type: 'Polygon', coordinates: kept[0] };
          } else {
            g = Object.assign({}, g, { coordinates: kept });
          }
          return Object.assign({}, f, { geometry: g });
        }
        if (g.type === 'Polygon' && oversize(g.coordinates)) { droppedPolys++; return null; }
        return f;
      }).filter(Boolean);
      if (droppedPolys) {
        console.log(TAG, '[sanitize] dropped', droppedPolys,
          'oversize wrapper polygons (limit:', limit.toExponential(2),
          ', median:', median.toExponential(2) + ')');
      }
    }
    // Phase 3: repair outlier vertices within each ring (single-ring polygons
    // that survived earlier phases may still have extreme vertices that cause
    // d3 to split them into multiple sub-paths).
    let vertsDropped = 0, ringsRepaired = 0;
    feats = feats.map(f => {
      let g = f.geometry; if (!g) return f;
      function rep(rings) {
        return rings.map(r => {
          const out = repairRing(r);
          if (out !== r) { ringsRepaired++; vertsDropped += (r.length - out.length); }
          return out;
        });
      }
      if (g.type === 'Polygon') {
        g = Object.assign({}, g, { coordinates: rep(g.coordinates) });
      } else if (g.type === 'MultiPolygon') {
        g = Object.assign({}, g, { coordinates: g.coordinates.map(rep) });
      }
      return Object.assign({}, f, { geometry: g });
    });
    if (ringsRepaired) console.log(TAG, '[sanitize] repaired', ringsRepaired, 'rings; dropped', vertsDropped, 'outlier vertices');
    if (droppedRings) console.log(TAG, '[sanitize] dropped', droppedRings, 'wrapper rings');
    return Object.assign({}, gj, { features: feats });
  }

  // ── ring repair: strip outlier vertices using median + MAD ───────────────
  // Each Regrid parcel is a single-ring Polygon, but some rings contain a few
  // extreme outlier vertices far from the actual parcel. d3.geoMercator clips
  // those into separate sub-paths, producing a viewport-filling box. We detect
  // outliers via Median Absolute Deviation and drop them while keeping the
  // legitimate parcel vertices.
  function repairRing(ring) {
    if (!ring || ring.length < 5) return ring;
    const lons = ring.map(c => c[0]).slice().sort((a, b) => a - b);
    const lats = ring.map(c => c[1]).slice().sort((a, b) => a - b);
    const medLon = lons[Math.floor(lons.length / 2)];
    const medLat = lats[Math.floor(lats.length / 2)];
    const dLon = ring.map(c => Math.abs(c[0] - medLon)).slice().sort((a, b) => a - b);
    const dLat = ring.map(c => Math.abs(c[1] - medLat)).slice().sort((a, b) => a - b);
    const madLon = dLon[Math.floor(dLon.length / 2)];
    const madLat = dLat[Math.floor(dLat.length / 2)];
    // Tolerance: generous — 200× MAD, with a floor of ~0.005° (~500m). For a
    // legitimate parcel ring with low spread, MAD is near zero so the floor
    // dominates; outliers at 1°+ are dropped easily.
    const tolLon = Math.max(madLon * 200, 0.005);
    const tolLat = Math.max(madLat * 200, 0.005);
    const kept = ring.filter(c =>
      Math.abs(c[0] - medLon) <= tolLon &&
      Math.abs(c[1] - medLat) <= tolLat);
    if (kept.length < 3 || kept.length === ring.length) return kept.length >= 3 ? kept : ring;
    // Re-close the ring if filtering broke the first==last invariant
    const first = kept[0], last = kept[kept.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) kept.push([first[0], first[1]]);
    return kept;
  }

  // ── color modes ─────────────────────────────────────────────────────────────
  const MODES = {
    use: {
      label: 'Use',
      kind: 'cat',
      get: r => pick(r, 'usedesc', 'lbcs_function_desc', 'use_description') || 'Unspecified',
    },
    zoning: {
      label: 'Zoning',
      kind: 'cat',
      get: r => pick(r, 'zoning', 'zoning_type', 'zoning_description', 'lbcs_activity_desc') || 'Unspecified',
    },
    year_built: {
      label: 'Year built',
      kind: 'seq',
      get: r => { const y = num(pick(r, 'yearbuilt', 'year_built')); return (y && y > 1700 && y < 2050) ? y : null; },
      scale: 'viridis',
    },
    value_per_acre: {
      label: 'Land value / acre',
      kind: 'seq',
      get: r => {
        const v = num(pick(r, 'land_value', 'gisland_value', 'll_land_value'));
        const a = num(pick(r, 'll_gisacre', 'gisacre'));
        return (v != null && a != null && a > 0) ? v / a : null;
      },
      scale: 'magma',
      legendFmt: v => '$' + (v >= 1e6 ? (v/1e6).toFixed(1) + 'M' : (v/1e3).toFixed(0) + 'k'),
    },
    ownership: {
      label: 'Top owners',
      kind: 'owner',
      get: r => { const o = pick(r, 'owner'); return o ? String(o).trim().toUpperCase() : null; },
    },
    vacancy: {
      label: 'Vacant vs improved',
      kind: 'binary',
      get: r => num(pick(r, 'bldg_sqft', 'll_bldg_footprint_sqft', 'improvement_sqft')) > 0 ? 'Improved' : 'Vacant',
      colors: { 'Improved': '#5fa8d3', 'Vacant': '#bbb' },
    },
  };

  // ── per-viewer state ────────────────────────────────────────────────────────
  const S = {
    cache: {}, sadId: null, features: null,
    mode: 'use',
    excludedCats: new Set(),
    yearMin: null, yearMax: null, yearBounds: [null, null],
    selectedFeat: null, spotlightOwner: null,
    _diagLogged: false,
  };
  // Ensure the global layerVisible has a 'parcels' slot so the viewer's existing
  // toggle pattern works for us too.
  if (window && typeof window === 'object') {
    // we set this lazily when the viewer's state appears
  }

  // ── data load ───────────────────────────────────────────────────────────────
  async function loadParcels(sadId) {
    if (sadId in S.cache) return S.cache[sadId];
    const tries = [
      `../${sadId}/derived/parcels/parcels.geojson`,
      `/${sadId}/derived/parcels/parcels.geojson`,
    ];
    for (const url of tries) {
      try {
        const r = await fetch(url);
        if (!r.ok) continue;
        const raw = await r.json();
        // ── one-shot diagnostic: dump the structure of the first feature so we can
        // see what kind of wrapper Regrid is actually emitting. ──
        if (raw && raw.features && raw.features.length) {
          // Aggregate type/ring statistics across all features
          const typeCounts = {};
          const ringCountHist = {};
          let minBbox = Infinity, maxBbox = -Infinity;
          for (const f of raw.features) {
            const t = f.geometry && f.geometry.type;
            typeCounts[t] = (typeCounts[t] || 0) + 1;
            if (t === 'Polygon' && f.geometry.coordinates) {
              const n = f.geometry.coordinates.length;
              ringCountHist['Polygon-' + n + 'ring'] = (ringCountHist['Polygon-' + n + 'ring'] || 0) + 1;
              if (f.geometry.coordinates[0]) {
                const bb = ringBbox(f.geometry.coordinates[0]);
                const span = Math.max(bb.sx, bb.sy);
                if (span < minBbox) minBbox = span;
                if (span > maxBbox) maxBbox = span;
              }
            } else if (t === 'MultiPolygon' && f.geometry.coordinates) {
              const n = f.geometry.coordinates.length;
              ringCountHist['MultiPolygon-' + n + 'poly'] = (ringCountHist['MultiPolygon-' + n + 'poly'] || 0) + 1;
            }
          }
          console.log(TAG, '[diagnostic] geometry types:', typeCounts);
          console.log(TAG, '[diagnostic] ring/poly hist:', ringCountHist);
          console.log(TAG, '[diagnostic] outer-ring bbox span: min', minBbox.toExponential(2), 'max', maxBbox.toExponential(2));
          const f0 = raw.features[0];
          const g0 = f0.geometry;
          console.log(TAG, '[diagnostic] first feature geometry.type:', g0 && g0.type);
          if (g0 && g0.coordinates) {
            function describe(c, depth) {
              if (typeof c[0] === 'number') return 'point[' + c.length + ']';
              if (depth > 4) return 'array(' + c.length + ')';
              return 'array(' + c.length + ')[' + c.map(x => describe(x, depth+1)).slice(0, 3).join(',') + (c.length > 3 ? ',…' : '') + ']';
            }
            console.log(TAG, '[diagnostic] coords shape:', describe(g0.coordinates, 0));
            // Compute bboxes of the top-level rings/polys to see the size disparity
            function bb(coords) {
              let mnX=Infinity,mnY=Infinity,mxX=-Infinity,mxY=-Infinity;
              (function walk(c) {
                if (typeof c[0] === 'number') {
                  if (c[0]<mnX)mnX=c[0]; if (c[0]>mxX)mxX=c[0];
                  if (c[1]<mnY)mnY=c[1]; if (c[1]>mxY)mxY=c[1];
                } else for (const x of c) walk(x);
              })(coords);
              return [mnX,mnY,mxX,mxY,(mxX-mnX).toExponential(2),(mxY-mnY).toExponential(2)];
            }
            if (g0.type === 'Polygon') {
              g0.coordinates.forEach((ring, i) => console.log(TAG, '  ring['+i+'] bbox:', bb(ring), 'verts:', ring.length));
            } else if (g0.type === 'MultiPolygon') {
              g0.coordinates.forEach((poly, i) => {
                console.log(TAG, '  poly['+i+']:');
                poly.forEach((ring, j) => console.log(TAG, '    ring['+j+'] bbox:', bb(ring), 'verts:', ring.length));
              });
            }
          }
          // Also show how the first few features render after sanitize
          console.log(TAG, '[diagnostic] raw features:', raw.features.length);
        }
        const clean = sanitizeParcels(raw);
        console.log(TAG, '[diagnostic] post-sanitize features:', clean.features.length);
        // And what the first feature's geometry looks like after sanitize
        if (clean.features.length) {
          const g1 = clean.features[0].geometry;
          console.log(TAG, '[diagnostic] post-sanitize geometry type:', g1 && g1.type,
            '| polys:', g1 && g1.type === 'MultiPolygon' ? g1.coordinates.length : (g1 && g1.type === 'Polygon' ? g1.coordinates.length + ' rings' : 'n/a'));
        }
        const features = (clean && clean.features) ? clean.features : [];
        let yMin = null, yMax = null;
        for (const f of features) {
          const y = MODES.year_built.get(f);
          if (y) { if (yMin === null || y < yMin) yMin = y; if (yMax === null || y > yMax) yMax = y; }
        }
        const result = { features, yearMin: yMin, yearMax: yMax };
        S.cache[sadId] = result;
        console.log(TAG, 'loaded', features.length, 'parcels from', url);
        return result;
      } catch (e) { /* try next */ }
    }
    S.cache[sadId] = null;
    return null;
  }

  // ── classify into colored rows (with current filters applied) ────────────────
  function classify() {
    const m = MODES[S.mode];
    const rows = S.features.map(f => ({ f, v: m.get(f), cat: null, val: null, visible: true, color: '#888' }));

    if (m.kind === 'cat' || m.kind === 'binary' || m.kind === 'owner') {
      let cats = [];
      if (m.kind === 'owner') {
        const counts = {};
        rows.forEach(r => { if (r.v) counts[r.v] = (counts[r.v] || 0) + 1; });
        const top = Object.entries(counts).sort((a,b) => b[1]-a[1]).slice(0, 12).map(x => x[0]);
        const topSet = new Set(top);
        rows.forEach(r => { r.cat = (r.v && topSet.has(r.v)) ? r.v : (r.v ? 'Other' : 'Unknown'); });
        cats = top.concat(['Other', 'Unknown']);
      } else if (m.kind === 'binary') {
        rows.forEach(r => { r.cat = r.v || 'Unknown'; });
        cats = Object.keys(m.colors);
      } else {
        const counts = {};
        rows.forEach(r => { if (r.v) counts[r.v] = (counts[r.v] || 0) + 1; });
        const top = Object.entries(counts).sort((a,b) => b[1]-a[1]).slice(0, 12).map(x => x[0]);
        const topSet = new Set(top);
        rows.forEach(r => { r.cat = (r.v && topSet.has(r.v)) ? r.v : (r.v ? 'Other' : 'Unspecified'); });
        cats = top.concat(['Other', 'Unspecified']);
      }
      const used = new Set(rows.map(r => r.cat));
      cats = cats.filter(c => used.has(c));
      const pal = (m.kind === 'binary') ? m.colors : paletteFor(cats);
      rows.forEach(r => { r.color = pal[r.cat] || '#888'; });
      S._cats = cats; S._pal = pal;
    } else {
      let lo = Infinity, hi = -Infinity;
      rows.forEach(r => {
        r.val = (typeof r.v === 'number' && Number.isFinite(r.v)) ? r.v : null;
        if (r.val !== null) { if (r.val < lo) lo = r.val; if (r.val > hi) hi = r.val; }
      });
      const seqScale = (S.mode === 'value_per_acre') ? d3.scaleSqrt() : d3.scaleLinear();
      seqScale.domain([lo, hi]).range([0, 1]).clamp(true);
      rows.forEach(r => { r.color = (r.val !== null) ? ramp(seqScale(r.val), m.scale) : '#aaa'; });
      S._seqDomain = [lo, hi];
    }

    // visibility filters
    rows.forEach(r => {
      let v = true;
      if (m.kind !== 'seq' && r.cat && S.excludedCats.has(r.cat)) v = false;
      if (S.yearMin !== null || S.yearMax !== null) {
        const y = MODES.year_built.get(r.f);
        if (y === null) { v = false; }
        else {
          if (S.yearMin !== null && y < S.yearMin) v = false;
          if (S.yearMax !== null && y > S.yearMax) v = false;
        }
      }
      if (S.spotlightOwner) {
        const o = MODES.ownership.get(r.f);
        if (o !== S.spotlightOwner) v = false;
      }
      r.visible = v;
    });
    return rows;
  }

  // ── render the parcel layer using viewer's own pathGen ───────────────────────
  function renderLayer(state, mapRoot) {
    // Honor the viewer's master toggle
    if (!state.layerVisible || !state.layerVisible.parcels) {
      const layer = mapRoot.select('g.layer-parcels');
      if (!layer.empty()) layer.remove();
      return;
    }
    if (!S.features || !state.pathGen) return;

    const rows = classify();

    if (!S._diagLogged && rows.length) {
      S._diagLogged = true;
      try {
        // 1. Dump the actual geometry being projected (truncated JSON)
        const g0 = rows[0].f.geometry;
        const gJson = JSON.stringify(g0);
        console.log(TAG, 'feature[0] geometry JSON (first 400 chars):', gJson.slice(0, 400) + (gJson.length > 400 ? '… [+' + (gJson.length - 400) + ' more]' : ''));
        // 2. Compute the projected bounds via d3
        const bounds = state.pathGen.bounds(rows[0].f.geometry);
        console.log(TAG, 'feature[0] projected bounds:', bounds);
        // 3. Full projection test
        const test = state.pathGen(g0);
        console.log(TAG, 'feature[0] full path length:', test ? test.length : 0,
          '; first 200 chars:', test ? test.slice(0, 200) : 'null');
        // 4. Test feature[5] for comparison (deeper in the array)
        if (rows.length > 5) {
          const t5 = state.pathGen(rows[5].f.geometry);
          console.log(TAG, 'feature[5] path length:', t5 ? t5.length : 0,
            '; first 200 chars:', t5 ? t5.slice(0, 200) : 'null');
        }
        // 5. Survey all features: how many produce paths with extreme coords?
        let badCount = 0; let firstBadIdx = -1;
        for (let i = 0; i < rows.length; i++) {
          try {
            const t = state.pathGen(rows[i].f.geometry);
            if (t && /[ML]-?\d{6,}/.test(t)) {  // any coordinate with 6+ digits
              badCount++;
              if (firstBadIdx < 0) firstBadIdx = i;
            }
          } catch (e) {}
        }
        console.log(TAG, 'features with extreme projected coords:', badCount, 'of', rows.length, '(first bad idx:', firstBadIdx + ')');
        if (firstBadIdx >= 0) {
          const fbad = rows[firstBadIdx].f;
          console.log(TAG, 'first bad feature: type=', fbad.geometry && fbad.geometry.type,
            ', geometry JSON:', JSON.stringify(fbad.geometry).slice(0, 400));
        }
      } catch (e) { console.log(TAG, 'projection test threw:', e.message); }
    }

    let layer = mapRoot.select('g.layer-parcels');
    if (layer.empty()) {
      // Insert under #cropped_content if it exists (so CROP applies to parcels too),
      // otherwise as a direct child of #map_root.
      const cropped = mapRoot.select('#cropped_content');
      const host = !cropped.empty() ? cropped : mapRoot;
      layer = host.append('g').attr('class', 'layer-parcels').attr('id', '07_parcels');
    }

    const sel = layer.selectAll('path').data(rows, (d, i) => d.f.id || i);
    sel.exit().remove();
    const enter = sel.enter().append('path');
    enter.merge(sel)
      .attr('d', d => state.pathGen(d.f.geometry))
      .attr('fill', d => d.color)
      .classed('dimmed', d => !d.visible)
      .classed('selected', d => S.selectedFeat && d.f === S.selectedFeat)
      .on('mouseenter', (event, d) => showTip(event, d))
      .on('mousemove', moveTip)
      .on('mouseleave', hideTip)
      .on('click', (event, d) => { event.stopPropagation(); selectFeature(d.f); });
  }

  // ── tooltip ─────────────────────────────────────────────────────────────────
  let tipEl = null;
  function ensureTip() {
    if (tipEl) return tipEl;
    tipEl = document.createElement('div'); tipEl.id = 'parcel-tip';
    document.body.appendChild(tipEl); return tipEl;
  }
  function showTip(event, d) {
    const t = ensureTip(); const f = d.f;
    const nm = pick(f, 'address', 'situs_address', 'address1') || pick(f, 'parcelnumb', 'parcelnumb_no_formatting') || '(parcel)';
    const use = MODES.use.get(f);
    const ac = num(pick(f, 'll_gisacre', 'gisacre'));
    const yr = MODES.year_built.get(f);
    const own = MODES.ownership.get(f);
    t.innerHTML = `<div class="nm">${escape(nm)}</div>
      <div class="sub">${escape(use)}${ac ? ' · ' + ac.toFixed(2) + ' ac' : ''}${yr ? ' · built ' + yr : ''}</div>
      ${own ? `<div class="sub">${escape(own.slice(0, 44))}</div>` : ''}`;
    t.classList.add('on'); moveTip(event);
  }
  function moveTip(event) {
    if (!tipEl) return;
    const pad = 12;
    let x = event.clientX + pad, y = event.clientY + pad;
    const r = tipEl.getBoundingClientRect();
    if (x + r.width > innerWidth - 8) x = event.clientX - r.width - pad;
    if (y + r.height > innerHeight - 8) y = event.clientY - r.height - pad;
    tipEl.style.left = x + 'px'; tipEl.style.top = y + 'px';
  }
  function hideTip() { if (tipEl) tipEl.classList.remove('on'); }

  // ── detail card ─────────────────────────────────────────────────────────────
  function selectFeature(f) { S.selectedFeat = f; scheduleRender(); showDetail(f); }
  function closeDetail() {
    const el = document.getElementById('parcel-detail'); if (el) el.remove();
    S.selectedFeat = null; scheduleRender();
  }
  function spotlightOwner(name) {
    S.spotlightOwner = (S.spotlightOwner === name) ? null : name;
    rebuildSection(); scheduleRender();
  }
  function showDetail(f) {
    let el = document.getElementById('parcel-detail');
    if (!el) { el = document.createElement('div'); el.id = 'parcel-detail'; document.body.appendChild(el); }
    const nm = pick(f, 'address', 'situs_address', 'address1') || pick(f, 'parcelnumb', 'parcelnumb_no_formatting') || '(parcel)';
    const own = MODES.ownership.get(f);
    const fields = [
      ['Parcel #', pick(f, 'parcelnumb', 'parcelnumb_no_formatting')],
      ['Use', MODES.use.get(f)],
      ['Zoning', MODES.zoning.get(f) === 'Unspecified' ? null : MODES.zoning.get(f)],
      ['Year built', MODES.year_built.get(f)],
      ['Lot', (() => { const a = num(pick(f, 'll_gisacre', 'gisacre')); return a ? a.toFixed(2) + ' ac' : null; })()],
      ['Bldg sqft', (() => { const v = num(pick(f, 'bldg_sqft', 'll_bldg_footprint_sqft', 'improvement_sqft')); return v ? v.toLocaleString() + ' sf' : null; })()],
      ['FAR', (() => {
        const v = num(pick(f, 'bldg_sqft', 'll_bldg_footprint_sqft', 'improvement_sqft'));
        const a = num(pick(f, 'll_gisacre', 'gisacre'));
        if (!v || !a) return null;
        return (v / (a * 43560)).toFixed(2);
      })()],
      ['Land $', (() => { const v = num(pick(f, 'land_value', 'gisland_value', 'll_land_value')); return v ? '$' + Math.round(v).toLocaleString() : null; })()],
      ['Improv. $', (() => { const v = num(pick(f, 'bldg_value', 'gisbldg_value', 'improvement_value', 'll_bldg_value')); return v ? '$' + Math.round(v).toLocaleString() : null; })()],
    ].filter(([_, v]) => v !== null && v !== undefined && v !== '');
    const path = pick(f, 'path');
    el.innerHTML = `<button class="pd-close" title="close">×</button>
      <h3>Parcel</h3>
      <div class="pd-addr">${escape(nm)}</div>
      ${own ? `<div class="pd-owner" title="Spotlight all parcels for this owner">${escape(own)}</div>` : ''}
      <dl>${fields.map(([k, v]) => `<dt>${escape(k)}</dt><dd>${escape(v)}</dd>`).join('')}</dl>
      ${path ? `<p style="margin-top:10px;"><a class="pd-link" href="https://app.regrid.com${escape(path)}" target="_blank">open on regrid.com →</a></p>` : ''}`;
    el.querySelector('.pd-close').addEventListener('click', closeDetail);
    const ownEl = el.querySelector('.pd-owner');
    if (ownEl) ownEl.addEventListener('click', () => spotlightOwner(own));
  }

  // ── sidebar section: build once, then rebuild contents on state change ──────
  function ensureSection() {
    let sec = document.getElementById('parcels-section');
    if (sec) return sec;
    // Find the analysis section (last "real" data section) and insert after it
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) { console.warn(TAG, 'no .sidebar found — cannot inject section'); return null; }
    const sections = sidebar.querySelectorAll('section');
    // place before any footer-note section, otherwise append
    let footer = null;
    sections.forEach(s => { if (s.classList.contains('footer-note')) footer = s; });
    sec = document.createElement('section');
    sec.id = 'parcels-section';
    sec.innerHTML = `
      <h2>Parcels (Regrid) <span class="section-count" id="pcl-section-count"></span></h2>
      <ul class="layer-list" id="pcl-toggle-list">
        <li data-layer="parcels">
          <input type="checkbox" id="tog-parcels">
          <span class="swatch" style="background:#7fb069;color:#7fb069"></span>
          <label class="lbl" for="tog-parcels">Parcels</label>
        </li>
      </ul>
      <div id="pcl-controls" style="margin-top:12px;display:none;">
        <div class="pcl-status" id="pcl-status"></div>
        <select class="pcl-mode" id="pcl-mode"></select>
        <div class="pcl-legend" id="pcl-legend"></div>
        <div class="pcl-yr" id="pcl-yr-wrap" style="display:none;">
          <div class="lab">Year built filter</div>
          <div class="pcl-yr-row">
            <input type="range" id="pcl-yr-lo"><span class="val" id="pcl-yr-lo-v"></span>
          </div>
          <div class="pcl-yr-row">
            <input type="range" id="pcl-yr-hi"><span class="val" id="pcl-yr-hi-v"></span>
          </div>
        </div>
        <div class="pcl-count" id="pcl-count"></div>
        <button class="pcl-clear" id="pcl-clear" style="display:none;">clear filters</button>
      </div>`;
    if (footer) sidebar.insertBefore(sec, footer);
    else sidebar.appendChild(sec);

    // wire master toggle
    const cb = sec.querySelector('#tog-parcels');
    cb.addEventListener('change', () => {
      const state = window.sadViewerState || resolveViewerState();
      if (state) { state.layerVisible = state.layerVisible || {}; state.layerVisible.parcels = cb.checked; }
      document.getElementById('pcl-controls').style.display = cb.checked ? 'block' : 'none';
      if (typeof window.renderViewer === 'function') window.renderViewer();
      else if (cb.checked) requestAnimationFrame(() => triggerRender());
      else hideAll();
    });
    return sec;
  }

  function rebuildSection() {
    const sec = ensureSection(); if (!sec) return;
    const cb = sec.querySelector('#tog-parcels');
    const li = sec.querySelector('li[data-layer="parcels"]');
    const ctrl = sec.querySelector('#pcl-controls');
    const status = sec.querySelector('#pcl-status');
    const count = sec.querySelector('#pcl-section-count');

    if (!S.features) {
      li.classList.add('disabled'); cb.disabled = true;
      ctrl.style.display = 'none';
      if (status) status.textContent = '';
      if (count) count.textContent = '— no data';
      return;
    }
    li.classList.remove('disabled'); cb.disabled = false;
    if (count) count.textContent = S.features.length.toLocaleString();
    const state = window.sadViewerState;
    const on = state && state.layerVisible && state.layerVisible.parcels;
    cb.checked = !!on;
    ctrl.style.display = on ? 'block' : 'none';
    if (!on) return;

    if (status) status.textContent = S.features.length.toLocaleString() + ' parcels loaded';

    const m = MODES[S.mode];
    const rows = classify();
    const visibleCount = rows.filter(r => r.visible).length;

    // mode select
    const modeSel = sec.querySelector('#pcl-mode');
    modeSel.innerHTML = Object.entries(MODES).map(([k, v]) =>
      `<option value="${k}"${k===S.mode?' selected':''}>${v.label}</option>`).join('');
    modeSel.onchange = () => { S.mode = modeSel.value; S.excludedCats.clear(); rebuildSection(); scheduleRender(); };

    // legend
    const legHost = sec.querySelector('#pcl-legend'); legHost.innerHTML = '';
    if (m.kind === 'cat' || m.kind === 'binary' || m.kind === 'owner') {
      const counts = {}; rows.forEach(r => { counts[r.cat] = (counts[r.cat] || 0) + 1; });
      S._cats.forEach(c => {
        const off = S.excludedCats.has(c);
        const row = document.createElement('div');
        row.className = 'pcl-leg-row' + (off ? ' off' : '');
        row.innerHTML = `<span class="sw" style="background:${S._pal[c]}"></span>
          <span class="nm" title="${escape(c)}">${escape(c.length > 22 ? c.slice(0,21)+'…' : c)}</span>
          <span class="ct">${counts[c] || 0}</span>`;
        row.addEventListener('click', () => {
          if (S.excludedCats.has(c)) S.excludedCats.delete(c); else S.excludedCats.add(c);
          rebuildSection(); scheduleRender();
        });
        legHost.appendChild(row);
      });
    } else {
      const [lo, hi] = S._seqDomain;
      const stops = [0, 0.25, 0.5, 0.75, 1].map(t => ramp(t, m.scale)).join(',');
      const fmtFn = m.legendFmt || (v => Math.round(v).toLocaleString());
      const grad = document.createElement('div');
      grad.className = 'pcl-grad';
      grad.innerHTML = `<span>${(isFinite(lo)) ? fmtFn(lo) : '—'}</span>
        <div class="bar" style="background:linear-gradient(to right, ${stops})"></div>
        <span>${(isFinite(hi)) ? fmtFn(hi) : '—'}</span>`;
      legHost.appendChild(grad);
    }

    // year filter (only when year data exists)
    const yWrap = sec.querySelector('#pcl-yr-wrap');
    const yb = S.yearBounds;
    if (yb[0] && yb[1] && yb[0] !== yb[1]) {
      yWrap.style.display = 'block';
      const lo = S.yearMin !== null ? S.yearMin : yb[0];
      const hi = S.yearMax !== null ? S.yearMax : yb[1];
      const ylo = sec.querySelector('#pcl-yr-lo'), yhi = sec.querySelector('#pcl-yr-hi');
      ylo.min = yb[0]; ylo.max = yb[1]; ylo.value = lo;
      yhi.min = yb[0]; yhi.max = yb[1]; yhi.value = hi;
      sec.querySelector('#pcl-yr-lo-v').textContent = lo;
      sec.querySelector('#pcl-yr-hi-v').textContent = hi;
      const sync = (which) => {
        let a = +ylo.value, b = +yhi.value;
        if (a > b) { if (which === 'lo') b = a, yhi.value = b; else a = b, ylo.value = a; }
        sec.querySelector('#pcl-yr-lo-v').textContent = a;
        sec.querySelector('#pcl-yr-hi-v').textContent = b;
        S.yearMin = a; S.yearMax = b; scheduleRender();
        // update count inline
        const c = classify().filter(r => r.visible).length;
        sec.querySelector('#pcl-count').textContent = c.toLocaleString() + ' of ' + S.features.length.toLocaleString() + ' shown';
        const clr = sec.querySelector('#pcl-clear');
        clr.style.display = (S.excludedCats.size || S.yearMin !== null || S.yearMax !== null || S.spotlightOwner) ? 'inline-block' : 'none';
      };
      ylo.oninput = () => sync('lo'); yhi.oninput = () => sync('hi');
    } else {
      yWrap.style.display = 'none';
    }

    // owner spotlight indicator (shown above count)
    sec.querySelector('#pcl-count').textContent = visibleCount.toLocaleString() + ' of ' + S.features.length.toLocaleString() + ' shown'
      + (S.spotlightOwner ? ' · spotlight: ' + (S.spotlightOwner.length > 24 ? S.spotlightOwner.slice(0,23)+'…' : S.spotlightOwner) : '');

    const clr = sec.querySelector('#pcl-clear');
    const hasFilters = S.excludedCats.size || S.yearMin !== null || S.yearMax !== null || S.spotlightOwner;
    clr.style.display = hasFilters ? 'inline-block' : 'none';
    clr.onclick = () => {
      S.excludedCats.clear(); S.yearMin = null; S.yearMax = null; S.spotlightOwner = null;
      rebuildSection(); scheduleRender();
    };
  }

  function hideAll() {
    const sec = document.getElementById('parcels-section');
    if (sec) sec.querySelector('#pcl-controls').style.display = 'none';
    closeDetail();
    const tip = document.getElementById('parcel-tip'); if (tip) tip.classList.remove('on');
  }

  // ── re-render scheduler (called from filter changes) ────────────────────────
  function scheduleRender() {
    const state = window.sadViewerState; if (!state) return;
    const mr = d3.select('#map_root'); if (mr.empty()) return;
    renderLayer(state, mr);
  }
  function triggerRender() {
    // Provoke a full viewer re-render so cropped_content gets rebuilt around us.
    if (typeof window.renderViewer === 'function') window.renderViewer();
    else if (typeof window.render === 'function') window.render();
  }
  function resolveViewerState() {
    return window.sadViewerState || window.state || null;
  }

  // ── hook chain ──────────────────────────────────────────────────────────────
  const prevHook = window.sadAfterRender;
  console.log(TAG, 'installing sadAfterRender hook (prev type:', typeof prevHook, ')');
  window.sadAfterRender = function (state, _d3, mapRoot) {
    try { if (prevHook) prevHook(state, _d3, mapRoot); } catch (e) { console.warn(TAG, 'chained hook threw', e); }
    // Expose state for our scheduler if it's not exposed already
    if (state && !window.sadViewerState) window.sadViewerState = state;
    const sadId = state && state.currentSadId;
    if (!sadId) return;
    // Ensure section exists
    ensureSection();
    // Ensure default state.layerVisible.parcels = false (off by default like other layers)
    state.layerVisible = state.layerVisible || {};
    if (!('parcels' in state.layerVisible)) state.layerVisible.parcels = false;

    if (sadId !== S.sadId) {
      S.sadId = sadId; S.features = null;
      S.excludedCats.clear(); S.yearMin = null; S.yearMax = null;
      S.spotlightOwner = null; S.selectedFeat = null;
      S._diagLogged = false;
      closeDetail();
      const layer = mapRoot.select('g.layer-parcels'); if (!layer.empty()) layer.remove();
      rebuildSection();
      loadParcels(sadId).then(data => {
        if (S.sadId !== sadId) return;
        if (!data) { S.features = null; rebuildSection(); return; }
        S.features = data.features;
        S.yearBounds = [data.yearMin, data.yearMax];
        rebuildSection();
        if (state.layerVisible.parcels) renderLayer(state, mapRoot);
      });
    } else {
      // Same SAD, re-render parcel layer onto fresh #map_root
      if (S.features) renderLayer(state, mapRoot);
      rebuildSection();
    }
  };
})();
