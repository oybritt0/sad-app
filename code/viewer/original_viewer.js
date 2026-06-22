/* SAD Pipeline Viewer (V2)
 *
 * Vector-first map renderer using d3 v7. Renders all geometry into a
 * hierarchically-named SVG tree so exports open cleanly in Illustrator.
 *
 * Layer hierarchy (matches the export structure):
 *   <g id="01_background">
 *   <g id="02_base">         (parks, parking, roads)
 *   <g id="03_analysis">     (heatmap, walkshed)
 *   <g id="04_buildings">
 *   <g id="05_activity">     (pois subgrouped by category, transit)
 *   <g id="06_boundary">     (SAD boundary — always on top)
 *   <g id="07_chrome">       (scale bar, north arrow — in HTML overlay)
 */

'use strict';

// ─── Constants ───────────────────────────────────────────────────────────────

const ROSSETTI_ORDER = [
  'retail_food_entertainment', 'office', 'sport', 'residential',
  'hotel', 'parking', 'open_space', 'other',
];

const PROGRAM_LABELS = {
  'sport':                     'Sport',
  'residential':               'Residential',
  'hotel':                     'Hotel',
  'retail_food_entertainment': 'Retail / F&B',
  'office':                    'Office',
  'parking':                   'Parking',
  'open_space':                'Open space',
  'other':                     'Other',
};

const PROGRAM_COLORS = {
  'sport':                     '#d62728',
  'residential':               '#2ca02c',
  'hotel':                     '#9467bd',
  'retail_food_entertainment': '#ff7f0e',
  'office':                    '#1f77b4',
  'parking':                   '#8c564b',
  'open_space':                '#bcbd22',
  'other':                     '#7f7f7f',
};

const LAYER_META = {
  sad_boundary: { label: 'SAD boundary',       swatch: 'dashed', color: '#f1c50c' },
  buildings:    { label: 'Buildings',          swatch: 'block',  color: '#999' },
  parking:      { label: 'Parking lots',       swatch: 'block',  color: '#C8B8A6' },
  parks:        { label: 'Parks / open space', swatch: 'block',  color: '#A8D5BA' },
  roads:        { label: 'Roads',              swatch: 'line',   color: '#bbb' },
  pois:         { label: 'POI dots',           swatch: 'block',  color: '#D97757' },
  heatmap:      { label: 'POI density heatmap',swatch: 'block',  color: '#D97757' },
  transit:      { label: 'Transit stations',   swatch: 'block',  color: '#5b9bd5' },
  walkshed:     { label: 'Walkshed',           swatch: 'block',  color: '#7BB661' },
};

const M_PER_FT = 0.3048;

// Heatmap gradient stops — KPF dark aesthetic, matches M12 amenity density
// (transparent navy at low end -> coral -> KPF yellow -> pale highlight).
const HEAT_STOPS = [
  { t: 0.00, color: 'rgba(40, 40, 120, 0.00)' },   // transparent — no data
  { t: 0.12, color: 'rgba(58, 90, 230, 0.48)' },    // bright blue
  { t: 0.30, color: 'rgba(40, 200, 220, 0.64)' },   // cyan
  { t: 0.50, color: 'rgba(120, 230, 90, 0.74)' },   // green
  { t: 0.70, color: 'rgba(250, 220, 60, 0.84)' },   // yellow
  { t: 0.86, color: 'rgba(250, 135, 40, 0.90)' },   // orange
  { t: 1.00, color: 'rgba(238, 50, 40, 0.96)' },    // red
];

const HEAT_INTERPOLATOR = d3.scaleLinear()
  .domain(HEAT_STOPS.map(s => s.t))
  .range(HEAT_STOPS.map(s => s.color))
  .interpolate(d3.interpolateRgb);

// ─── Geometry sanitization ───────────────────────────────────────────────────
// Some source GeoJSONs encode features as a "wrapper rectangle" outer ring
// with the actual feature as a hole, leaving each polygon rendering as
// "background-fill with a small notch." This is CRS-independent: for any
// polygon with multiple rings, if one ring's bbox is much larger than the
// smallest ring's bbox, treat the larger ring as a wrapper and drop it.

function sanitizeFeatureCollection(gj) {
  if (!gj || !gj.features) return gj;
  let droppedRings = 0, droppedFeatures = 0;

  function ringBbox(ring) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const c of ring) {
      if (!c || c.length < 2) continue;
      if (c[0] < minX) minX = c[0];
      if (c[0] > maxX) maxX = c[0];
      if (c[1] < minY) minY = c[1];
      if (c[1] > maxY) maxY = c[1];
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
    // Drop any ring more than 10x larger in either dimension than the
    // smallest ring's dimension. Real polygons-with-holes never have
    // such a ratio between outer and hole rings (typically <5x).
    const TOL = 10;
    const kept = stats.filter(s =>
      s.bbox.sx <= minSx * TOL && s.bbox.sy <= minSy * TOL);
    droppedRings += stats.length - kept.length;
    return kept.length > 0 ? kept.map(s => s.ring) : [stats[0].ring];
  }

  function cleanGeom(g) {
    if (!g) return null;
    if (g.type === 'Polygon') {
      const cleaned = cleanRings(g.coordinates);
      return cleaned && cleaned.length > 0
        ? Object.assign({}, g, { coordinates: cleaned })
        : null;
    }
    if (g.type === 'MultiPolygon') {
      const cleaned = (g.coordinates || [])
        .map(cleanRings)
        .filter(p => p && p.length > 0);
      return cleaned.length === 0 ? null
        : Object.assign({}, g, { coordinates: cleaned });
    }
    if (g.type === 'GeometryCollection') {
      const geos = (g.geometries || []).map(cleanGeom).filter(Boolean);
      return geos.length === 0 ? null
        : Object.assign({}, g, { geometries: geos });
    }
    return g;  // Points / LineStrings / MultiPoints — pass through
  }

  const cleanFeatures = gj.features.map(f => {
    const cg = cleanGeom(f.geometry);
    if (!cg) { droppedFeatures++; return null; }
    return Object.assign({}, f, { geometry: cg });
  }).filter(Boolean);

  if (droppedRings > 0 || droppedFeatures > 0) {
    console.log('[sanitize] dropped ' + droppedRings + ' wrapper rings, '
                 + droppedFeatures + ' empty features');
  }
  return Object.assign({}, gj, { features: cleanFeatures });
}

// ─── State ───────────────────────────────────────────────────────────────────

const state = {
  manifest: null,
  currentSadId: null,
  data: {},
  layerVisible: {
    sad_boundary: true, buildings: true, parking: true, parks: true,
    roads: true, pois: false, transit: false, walkshed: false,
  },
  programOn: Object.fromEntries(ROSSETTI_ORDER.map(k => [k, true])),
  crop: 'both',
  boundaryColor: '#f1c50c',
  poiSize: 2.6,
  transitSize: 7,
  walkshedColor: '#7BB661',
  walkshedOpacity: 0.22,
  buildingColorMode: 'default',  // 'default' (grey + red SAD anchors) | 'dominant' (by POI program)
  buildingColor: '#4a4a4a',      // the standard building grey (user-adjustable)
  buildingStyle: 'fill',         // 'fill' | 'outline'
  buildingFillOpacity: 0.92,
  buildingOutlineWeight: 0.6,
  buildingOutlineColor: '#888888',
  canvasBg: '#0a0a0a',
  satellite: { on: false, opacity: 0.9, provider: 'esri', token: '' },
  heatmap: { on: false, category: '__all', bandwidthFt: 250, intensity: 0.75 },
  projection: null,
  pathGen: null,
  bbox: null,
  transform: d3.zoomIdentity,
};

// ─── Bootstrap ───────────────────────────────────────────────────────────────

(async function init() {
  try {
    const r = await fetch('manifest.json');
    if (!r.ok) throw new Error('manifest.json -- ' + r.status);
    state.manifest = await r.json();
  } catch (e) {
    showError('Failed to load manifest. Did you run build_ui_manifest.py?\n' + e.message);
    return;
  }
  populateSadSelector();
  buildLayerToggles();
  buildPoiCategoryFilter();
  buildHeatSwatch();
  wireControls();
  if (state.manifest.sads.length > 0) {
    // Default to District Detroit if available, else first SAD
    const detroit = state.manifest.sads.find(s =>
      (s.sad_id || '').toLowerCase().includes('district-detroit') ||
      (s.sad_name || '').toLowerCase().includes('district detroit'));
    const defaultSad = detroit || state.manifest.sads[0];
    await selectSad(defaultSad.sad_id);
  }
})();

function showError(msg) {
  const map = document.getElementById('map');
  map.innerHTML = '';
  const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  t.setAttribute('x', '50%'); t.setAttribute('y', '50%');
  t.setAttribute('text-anchor', 'middle'); t.setAttribute('fill', '#888');
  t.setAttribute('font-size', '13'); t.textContent = msg;
  map.appendChild(t);
}

// ─── UI population ───────────────────────────────────────────────────────────

function populateSadSelector() {
  const sel = document.getElementById('sad-select');
  sel.innerHTML = '';
  for (const s of state.manifest.sads) {
    const opt = document.createElement('option');
    opt.value = s.sad_id;
    opt.textContent = s.sad_name || s.sad_id;
    sel.appendChild(opt);
  }
}

function buildLayerToggles() {
  const lists = {
    boundary: document.getElementById('boundary-layers'),
    base:     document.getElementById('base-layers'),
    pois:     document.getElementById('poi-layers'),
    heatmap:  document.getElementById('heatmap-layers'),
    activity: document.getElementById('activity-layers'),
    analysis: document.getElementById('analysis-layers'),
  };
  Object.values(lists).forEach(el => { if (el) el.innerHTML = ''; });

  const boundaryLayers = ['sad_boundary'];
  const baseLayers     = ['buildings', 'roads', 'parking', 'parks'];
  const poiLayers      = ['pois'];
  const heatmapLayers  = ['heatmap'];
  const activityLayers = ['transit'];
  const analysisLayers = ['walkshed'];

  for (const k of boundaryLayers) lists.boundary.appendChild(makeLayerLi(k));

  // Color picker for the SAD boundary stroke
  const colorLi = document.createElement('li');
  colorLi.className = 'color-row';
  colorLi.innerHTML =
    '<span class="row-label">Color</span>' +
    '<input type="color" id="boundary-color" value="' + state.boundaryColor + '">';
  lists.boundary.appendChild(colorLi);

  for (const k of baseLayers)     lists.base.appendChild(makeLayerLi(k));
  if (lists.pois) for (const k of poiLayers) lists.pois.appendChild(makeLayerLi(k));
  if (lists.heatmap) for (const k of heatmapLayers) lists.heatmap.appendChild(makeLayerLi(k));
  for (const k of activityLayers) lists.activity.appendChild(makeLayerLi(k));
  for (const k of analysisLayers) lists.analysis.appendChild(makeLayerLi(k));
}

function makeLayerLi(key) {
  const meta = LAYER_META[key];
  if (!meta) return document.createElement('li');
  const li = document.createElement('li');
  li.dataset.layer = key;
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.id = 'tog-' + key;
  // Heatmap state lives in state.heatmap.on, not state.layerVisible
  cb.checked = key === 'heatmap' ? !!state.heatmap.on : !!state.layerVisible[key];
  cb.addEventListener('change', () => {
    state.layerVisible[key] = cb.checked;
    if (key === 'heatmap') {
      state.heatmap.on = cb.checked;
      console.log('[heatmap toggle]', cb.checked,
                  '· pois loaded:', !!state.data.pois,
                  '· feature count:', state.data.pois ? state.data.pois.features.length : 0);
    }
    render();
  });
  li.appendChild(cb);
  const sw = document.createElement('span');
  sw.className = 'swatch' + (meta.swatch === 'line' ? ' line'
                              : meta.swatch === 'dashed' ? ' dashed' : '');
  sw.style.background = meta.swatch === 'dashed' ? 'transparent' : meta.color;
  sw.style.color = meta.color;
  li.appendChild(sw);
  const lbl = document.createElement('label');
  lbl.className = 'lbl'; lbl.setAttribute('for', cb.id);
  lbl.textContent = meta.label;
  li.appendChild(lbl);
  return li;
}

function buildPoiCategoryFilter() {
  const ul = document.getElementById('poi-category-checks');
  ul.innerHTML = '';
  for (const cat of ROSSETTI_ORDER) {
    const li = document.createElement('li');
    li.dataset.cat = cat;
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = 'poi-cat-' + cat;
    cb.checked = state.programOn[cat] !== false;
    cb.addEventListener('change', () => {
      state.programOn[cat] = cb.checked;
      render();
    });
    li.appendChild(cb);
    const sw = document.createElement('span');
    sw.className = 'sw-dot';
    sw.style.background = PROGRAM_COLORS[cat];
    li.appendChild(sw);
    const lbl = document.createElement('label');
    lbl.setAttribute('for', cb.id);
    lbl.textContent = PROGRAM_LABELS[cat];
    li.appendChild(lbl);
    ul.appendChild(li);
  }
}

function buildHeatSwatch() {
  // Build a small CSS gradient preview of the heatmap palette
  const css = HEAT_STOPS.map(s => s.color + ' ' + (s.t * 100) + '%').join(', ');
  document.querySelectorAll('.heat-swatch').forEach(el => {
    el.style.background = 'linear-gradient(90deg, ' + css + ')';
  });

  // Populate heatmap category dropdown
  const sel = document.getElementById('heatmap-category');
  if (sel) {
    sel.innerHTML = '<option value="__all">All POIs</option>';
    for (const cat of ROSSETTI_ORDER) {
      const opt = document.createElement('option');
      opt.value = cat;
      opt.textContent = PROGRAM_LABELS[cat];
      sel.appendChild(opt);
    }
  }
}

function wireControls() {
  document.getElementById('sad-select').addEventListener('change', e => {
    selectSad(e.target.value);
  });
  document.querySelectorAll('input[name="crop"]').forEach(el => {
    el.addEventListener('change', e => {
      if (e.target.checked) { state.crop = e.target.value; render(); }
    });
  });
  // POI master toggle
  const togPois = document.getElementById('tog-pois');
  if (togPois) {
    togPois.addEventListener('change', e => {
      state.layerVisible.pois = e.target.checked;
      const wrap = document.getElementById('poi-filter-wrap');
      if (wrap) wrap.style.opacity = e.target.checked ? '1' : '0.4';
      render();
    });
  }
  // POI bulk actions
  document.getElementById('poi-all').addEventListener('click', () => {
    ROSSETTI_ORDER.forEach(c => state.programOn[c] = true);
    document.querySelectorAll('#poi-category-checks input').forEach(cb => cb.checked = true);
    render();
  });
  document.getElementById('poi-none').addEventListener('click', () => {
    ROSSETTI_ORDER.forEach(c => state.programOn[c] = false);
    document.querySelectorAll('#poi-category-checks input').forEach(cb => cb.checked = false);
    render();
  });
  // Heatmap
  const hmOn   = document.getElementById('tog-heatmap');
  const hmCat  = document.getElementById('heatmap-category');
  const hmBw   = document.getElementById('heatmap-bandwidth');
  const hmInt  = document.getElementById('heatmap-intensity');
  const hmBwV  = document.getElementById('heatmap-bandwidth-value');
  const hmIntV = document.getElementById('heatmap-intensity-value');
  if (hmOn) hmOn.addEventListener('change', () => { state.heatmap.on = hmOn.checked; render(); });
  if (hmCat) hmCat.addEventListener('change', () => { state.heatmap.category = hmCat.value; if (state.heatmap.on) render(); });
  if (hmBw) {
    hmBw.addEventListener('input', () => { state.heatmap.bandwidthFt = +hmBw.value; if (hmBwV) hmBwV.textContent = hmBw.value + ' ft'; });
    hmBw.addEventListener('change', () => { if (state.heatmap.on) render(); });
  }
  if (hmInt) {
    hmInt.addEventListener('input', () => { state.heatmap.intensity = +hmInt.value; if (hmIntV) hmIntV.textContent = hmInt.value; });
    hmInt.addEventListener('change', () => { if (state.heatmap.on) render(); });
  }

  const exportBtn = document.getElementById('export-svg');
  if (exportBtn) exportBtn.addEventListener('click', exportSvg);
  const fitBtn = document.getElementById('fit-zoom');
  if (fitBtn) fitBtn.addEventListener('click', fitZoom);

  // Boundary color picker
  const boundaryColorInput = document.getElementById('boundary-color');
  if (boundaryColorInput) {
    boundaryColorInput.addEventListener('input', e => {
      state.boundaryColor = e.target.value;
      // Update boundary swatch in sidebar
      const sw = document.querySelector('#boundary-layers .swatch');
      if (sw) sw.style.color = state.boundaryColor;
      render();
    });
  }

  // POI / transit size sliders
  const poiSize = document.getElementById('poi-size');
  const transitSize = document.getElementById('transit-size');
  if (poiSize) {
    poiSize.addEventListener('input', e => {
      state.poiSize = +e.target.value;
      document.getElementById('poi-size-value').textContent = e.target.value;
      render();
    });
  }
  if (transitSize) {
    transitSize.addEventListener('input', e => {
      state.transitSize = +e.target.value;
      document.getElementById('transit-size-value').textContent = e.target.value;
      render();
    });
  }

  // Walkshed color + opacity
  const wsColor = document.getElementById('walkshed-color');
  const wsOpacity = document.getElementById('walkshed-opacity');
  if (wsColor) {
    wsColor.addEventListener('input', e => {
      state.walkshedColor = e.target.value;
      render();
    });
  }
  if (wsOpacity) {
    wsOpacity.addEventListener('input', e => {
      state.walkshedOpacity = +e.target.value;
      document.getElementById('walkshed-opacity-value').textContent = e.target.value;
      render();
    });
  }

  // Canvas background color picker
  const canvasBg = document.getElementById('canvas-bg');
  if (canvasBg) {
    canvasBg.addEventListener('input', e => {
      state.canvasBg = e.target.value;
      document.documentElement.style.setProperty('--map-bg', e.target.value);
      render();
    });
  }

  // Satellite imagery
  const satTog = document.getElementById('tog-satellite');
  const satOpac = document.getElementById('satellite-opacity');
  const satOpacV = document.getElementById('satellite-opacity-value');
  const satProv = document.getElementById('satellite-provider');
  const satTok = document.getElementById('satellite-token');
  const satTokRow = document.getElementById('satellite-token-row');
  if (satTog) {
    satTog.addEventListener('change', e => {
      state.satellite.on = e.target.checked;
      render();
    });
  }
  if (satOpac) {
    satOpac.addEventListener('input', e => {
      state.satellite.opacity = +e.target.value;
      if (satOpacV) satOpacV.textContent = (+e.target.value).toFixed(2);
      if (state.satellite.on) render();
    });
  }
  if (satProv) {
    satProv.addEventListener('change', e => {
      state.satellite.provider = e.target.value;
      if (satTokRow) satTokRow.style.display = e.target.value === 'mapbox' ? '' : 'none';
      if (state.satellite.on) render();
    });
  }
  if (satTok) {
    satTok.addEventListener('change', e => {
      state.satellite.token = e.target.value.trim();
      if (state.satellite.on) render();
    });
  }

  // Building color mode (Anchors = grey + red SAD anchors · Dominant POI = program colors)
  document.querySelectorAll('input[name="bmode"]').forEach(el => {
    el.addEventListener('change', e => {
      if (e.target.checked) {
        state.buildingColorMode = e.target.value;
        render();
      }
    });
  });
  // Standard building color (affects the grey base only; SAD anchors stay red)
  const bColor = document.getElementById('building-color');
  if (bColor) {
    bColor.addEventListener('input', e => {
      state.buildingColor = e.target.value;
      render();
    });
  }

  // Building style: fill vs outline, with opacity / line weight / line color
  const fillOpacRow = document.getElementById('building-fill-opacity-row');
  const owtRow = document.getElementById('building-outline-weight-row');
  const ocolRow = document.getElementById('building-outline-color-row');
  function syncBuildingStyleRows() {
    const outline = state.buildingStyle === 'outline';
    if (fillOpacRow) fillOpacRow.style.display = outline ? 'none' : '';
    if (owtRow) owtRow.style.display = outline ? '' : 'none';
    if (ocolRow) ocolRow.style.display = outline ? '' : 'none';
  }
  document.querySelectorAll('input[name="bstyle"]').forEach(el => {
    el.addEventListener('change', e => {
      if (e.target.checked) {
        state.buildingStyle = e.target.value;
        syncBuildingStyleRows();
        render();
      }
    });
  });
  const bFillOpac = document.getElementById('building-fill-opacity');
  const bFillOpacV = document.getElementById('building-fill-opacity-value');
  if (bFillOpac) {
    bFillOpac.addEventListener('input', e => {
      state.buildingFillOpacity = +e.target.value;
      if (bFillOpacV) bFillOpacV.textContent = (+e.target.value).toFixed(2);
      render();
    });
  }
  const bOwt = document.getElementById('building-outline-weight');
  const bOwtV = document.getElementById('building-outline-weight-value');
  if (bOwt) {
    bOwt.addEventListener('input', e => {
      state.buildingOutlineWeight = +e.target.value;
      if (bOwtV) bOwtV.textContent = (+e.target.value).toFixed(2);
      render();
    });
  }
  const bOcol = document.getElementById('building-outline-color');
  if (bOcol) {
    bOcol.addEventListener('input', e => {
      state.buildingOutlineColor = e.target.value;
      render();
    });
  }
  syncBuildingStyleRows();

  // Initial state: POI filter wrap muted (since POIs off by default)
  document.getElementById('poi-filter-wrap').style.opacity = '0.4';
}

// ─── SAD loading ─────────────────────────────────────────────────────────────

async function selectSad(sadId) {
  state.currentSadId = sadId;
  document.getElementById('sad-select').value = sadId;
  document.getElementById('loading').classList.remove('hide');
  state.data = {};
  state.transform = d3.zoomIdentity;
  d3.select('#map').call(zoomBehavior.transform, d3.zoomIdentity);

  const sad = state.manifest.sads.find(s => s.sad_id === sadId);
  if (!sad) {
    document.getElementById('loading').classList.add('hide');
    return;
  }

  updateSadMeta(sad);
  setupProjection(sad);

  const loads = [];
  for (const [key, info] of Object.entries(sad.layers)) {
    loads.push(
      fetch('../' + info.path)
        .then(r => r.ok ? r.json() : null)
        .then(j => {
          if (!j) { console.warn('[layer ' + key + '] file not loaded: ' + info.path); return; }
          const cleaned = sanitizeFeatureCollection(j);
          state.data[key] = cleaned;
          const n = (cleaned.features || []).length;
          console.log('[layer ' + key + '] ' + n + ' features (path: ' + info.path + ')');
        })
        .catch(err => console.warn('[layer ' + key + '] failed: ' + info.path, err))
    );
  }
  await Promise.all(loads);

  // Identify sports venues via combined morphology + POI analysis (once per SAD)
  computeStadiumSet();
  computeSadStadiumSet(sad);
  updateStadiumIndex(sad);

  // Update toggle availability and update POI count badge
  for (const key of Object.keys(LAYER_META)) {
    const li = document.querySelector('li[data-layer="' + key + '"]');
    if (!li) continue;
    const cb = li.querySelector('input[type="checkbox"]');
    // Heatmap is derived from POI data — enable it whenever POIs are loaded.
    const hasData = key === 'heatmap' ? !!state.data.pois : !!state.data[key];
    if (!hasData) {
      li.classList.add('disabled');
      if (cb) cb.disabled = true;
    } else {
      li.classList.remove('disabled');
      if (cb) cb.disabled = false;
    }
  }
  // POIs disable
  const poiCb = document.getElementById('tog-pois');
  if (poiCb) {
    poiCb.disabled = !state.data.pois;
  }

  // Update POI count
  const poiCountEl = document.getElementById('pois-count');
  if (poiCountEl) {
    if (state.data.pois && state.data.pois.features) {
      poiCountEl.textContent = '(' + state.data.pois.features.length + ')';
    } else {
      poiCountEl.textContent = '(0)';
    }
  }

  document.getElementById('loading').classList.add('hide');
  render();
}

function updateSadMeta(sad) {
  // Populate the tabular topbar columns: Anchor, Typology, Index
  const anchorEl = document.getElementById('anchor-value');
  const typologyEl = document.getElementById('typology-value');
  const indexEl = document.getElementById('index-value');
  if (anchorEl) anchorEl.textContent = sad.anchor_venue || '—';
  if (typologyEl) typologyEl.textContent = sad.primary_typology || '—';
  // Index coordinates are filled by updateStadiumIndex() after data + stadiumSet load
  if (indexEl && !state.stadiumSet) indexEl.textContent = '…';
  updateStadiumIndex(sad);
}

function updateStadiumIndex(sad) {
  const indexEl = document.getElementById('index-value');
  if (!indexEl) return;
  let coords = null;
  // Largest detected stadium's centroid
  const stadia = state.stadiumSet;
  if (stadia && stadia.size) {
    let bestArea = 0, bestFeat = null;
    for (const f of stadia) {
      let a = 0;
      try { a = Math.abs(d3.geoArea(f)); } catch (e) { a = 0; }
      if (a > bestArea) { bestArea = a; bestFeat = f; }
    }
    if (bestFeat) {
      try { coords = d3.geoCentroid(bestFeat); } catch (e) { coords = null; }
    }
  }
  if (!coords && sad && sad.sad_bbox) {
    const [minx, miny, maxx, maxy] = sad.sad_bbox;
    coords = [(minx + maxx) / 2, (miny + maxy) / 2];
  }
  if (coords) {
    const lng = coords[0], lat = coords[1];
    const latS = (lat >= 0 ? lat.toFixed(4) + '°N' : (-lat).toFixed(4) + '°S');
    const lngS = (lng >= 0 ? lng.toFixed(4) + '°E' : (-lng).toFixed(4) + '°W');
    indexEl.textContent = latS + '  ' + lngS;
  } else {
    indexEl.textContent = '—';
  }
}

// ─── Projection setup ────────────────────────────────────────────────────────

function setupProjection(sad) {
  const wrap = document.getElementById('map');
  const w = wrap.clientWidth || 800;
  const h = wrap.clientHeight || 600;
  wrap.setAttribute('viewBox', '0 0 ' + w + ' ' + h);

  let bbox = sad.sad_bbox;
  if (sad.extent_bbox) bbox = sad.extent_bbox;
  if (!bbox) return;

  const [minx, miny, maxx, maxy] = bbox;
  const cx = (minx + maxx) / 2;
  const cy = (miny + maxy) / 2;
  state.bbox = bbox;

  const projection = d3.geoMercator()
    .center([cx, cy]).scale(1).translate([w / 2, h / 2]);
  const p1 = projection([minx, miny]);
  const p2 = projection([maxx, maxy]);
  const dx = Math.abs(p2[0] - p1[0]);
  const dy = Math.abs(p2[1] - p1[1]);
  const padding = 40;
  const s = Math.min((w - 2 * padding) / dx, (h - 2 * padding) / dy);
  projection.scale(s);

  state.projection = projection;
  state.pathGen = d3.geoPath(projection);
}

// ─── Rendering ───────────────────────────────────────────────────────────────

function render() {
  const svg = d3.select('#map');
  svg.selectAll('*').remove();
  if (!state.currentSadId || !state.projection) return;

  // ── Defs (clipPaths, masks)
  const defs = svg.append('defs');
  const sadGj = state.data['sad_boundary'];
  if (sadGj) {
    defs.append('clipPath').attr('id', 'crop-inside')
        .append('path').attr('d', state.pathGen(sadGj));
  }
  if (sadGj && state.crop === 'outside') {
    const mask = defs.append('mask').attr('id', 'crop-outside');
    // Cover a huge area in white...
    const w = svg.node().clientWidth || 800;
    const h = svg.node().clientHeight || 600;
    mask.append('rect')
        .attr('x', -2 * w).attr('y', -2 * h)
        .attr('width', 5 * w).attr('height', 5 * h)
        .attr('fill', 'white');
    // ...then punch out the SAD polygon in black
    mask.append('path').attr('d', state.pathGen(sadGj)).attr('fill', 'black');
  }

  // ── 01: Background
  const bgG = svg.append('g').attr('id', '01_background');
  const w = svg.node().clientWidth || 800;
  const h = svg.node().clientHeight || 600;
  bgG.append('rect').attr('x', 0).attr('y', 0)
     .attr('width', w).attr('height', h)
     .attr('fill', state.canvasBg || '#0a0a0a');

  // ── Map root (zoom transform applies here)
  const root = svg.append('g').attr('id', 'map_root')
                  .attr('transform', state.transform);

  // ── 00: Satellite backdrop (under everything, pans/zooms with the map)
  if (state.satellite.on && state.bbox) {
    renderSatellite(root.append('g').attr('id', '00_satellite'));
  }

  // ── Cropped content
  const cropped = root.append('g').attr('id', 'cropped_content');
  if (state.crop === 'inside' && sadGj) {
    cropped.attr('clip-path', 'url(#crop-inside)');
  } else if (state.crop === 'outside' && sadGj) {
    cropped.attr('mask', 'url(#crop-outside)');
  }

  // ── 02: Base map layers (parks, parking, roads)
  const baseG = cropped.append('g').attr('id', '02_base');
  if (state.layerVisible.parks && state.data.parks) {
    renderPolygons(baseG.append('g').attr('id', 'parks'),
                    state.data.parks, '#A8D5BA', 0.92,
                    '#7BAA88', 0.4);
  }
  if (state.layerVisible.parking && state.data.parking) {
    renderPolygons(baseG.append('g').attr('id', 'parking'),
                    state.data.parking, '#C8B8A6', 0.85,
                    '#9B8A78', 0.4);
  }
  if (state.layerVisible.roads && state.data.roads) {
    renderLines(baseG.append('g').attr('id', 'roads'),
                 state.data.roads, '#aaaaaa', 0.75, 0.75);
  }

  // ── 04: Buildings (rendered before the analysis overlay)
  if (state.layerVisible.buildings && state.data.buildings) {
    const blgG = cropped.append('g').attr('id', '04_buildings');
    renderBuildings(blgG.append('g').attr('id', 'buildings'),
                     state.data.buildings);
  }

  // ── 03: Analysis overlay (heatmap, walkshed) — drawn ABOVE buildings so the
  //        density core near the venues is not hidden under stadium footprints
  const anaG = cropped.append('g').attr('id', '03_analysis');
  if (state.heatmap.on && state.data.pois) {
    renderHeatmap(anaG.append('g').attr('id', 'heatmap'));
  }
  if (state.layerVisible.walkshed && state.data.walkshed) {
    renderWalkshed(anaG.append('g').attr('id', 'walkshed'),
                    state.data.walkshed);
  }

  // ── 05: Activity layers (POIs subgrouped by category, transit)
  const actG = cropped.append('g').attr('id', '05_activity');
  if (state.layerVisible.pois && state.data.pois) {
    renderPois(actG.append('g').attr('id', 'pois'), state.data.pois);
  }
  if (state.layerVisible.transit && state.data.transit) {
    renderTransit(actG.append('g').attr('id', 'transit'),
                   state.data.transit);
  }

  // ── 06: SAD boundary (always on top, NOT cropped)
  if (state.layerVisible.sad_boundary && sadGj) {
    const bndG = root.append('g').attr('id', '06_boundary');
    renderSadBoundary(bndG.append('g').attr('id', 'sad_boundary'), sadGj);
  }

  updateScaleChrome();

  // Hook for non-invasive add-ons (viewer_modules.js): draw extra projected
  // layers into #map_root so they pan/zoom and redraw with everything else.
  if (window.sadAfterRender) {
    try { window.sadAfterRender(state, d3, d3.select('#map_root')); }
    catch (e) { console.warn('[modules] afterRender failed', e); }
  }
}

// ─── Layer renderers ─────────────────────────────────────────────────────────

function isProjectionSane(feature) {
  // Drop features whose projected bbox is >10× the viewport in either
  // dimension. Catches features stored in a CRS other than lat/lng,
  // which otherwise project to absurd sizes and fill the background.
  if (!state.pathGen) return true;
  let bounds;
  try { bounds = state.pathGen.bounds(feature); }
  catch (e) { return false; }
  if (!bounds || !isFinite(bounds[0][0]) || !isFinite(bounds[1][0])) return false;
  const svg = document.getElementById('map');
  const vW = svg.clientWidth || 800, vH = svg.clientHeight || 600;
  const w = bounds[1][0] - bounds[0][0];
  const h = bounds[1][1] - bounds[0][1];
  if (w > vW * 10 || h > vH * 10) return false;
  return true;
}

function filterSane(features) {
  if (!features) return [];
  const before = features.length;
  const after = features.filter(isProjectionSane);
  if (after.length < before) {
    console.warn('[render] filtered ' + (before - after.length)
                 + ' off-CRS features (probably stored in non-WGS84)');
  }
  return after;
}

function renderPolygons(g, gj, fill, opacity, stroke, strokeOpacity) {
  const feats = filterSane(gj.features);
  const outline = state.buildingStyle === 'outline';
  const sel = g.selectAll('path').data(feats).join('path').attr('d', state.pathGen);
  if (outline) {
    // Outline mode — stroke with the layer's own color, no fill (shows satellite)
    sel.attr('fill', 'none')
       .attr('stroke', fill)
       .attr('stroke-width', state.buildingOutlineWeight != null ? state.buildingOutlineWeight : 0.6)
       .attr('stroke-opacity', 1);
  } else {
    const fo = state.buildingFillOpacity != null ? state.buildingFillOpacity : opacity;
    sel.attr('fill', fill)
       .attr('fill-opacity', fo)
       .attr('stroke', stroke || 'none')
       .attr('stroke-opacity', strokeOpacity || 1)
       .attr('stroke-width', 0.6);
  }
}

function renderLines(g, gj, color, opacity, weight) {
  g.selectAll('path').data(gj.features).join('path')
    .attr('d', state.pathGen)
    .attr('fill', 'none')
    .attr('stroke', color)
    .attr('stroke-width', weight)
    .attr('stroke-opacity', opacity)
    .attr('stroke-linecap', 'round')
    .attr('stroke-linejoin', 'round');
}

function walkshedLabel(props) {
  if (!props) return null;
  if (props.minutes != null) return props.minutes + ' min';
  if (props.walk_minutes != null) return props.walk_minutes + ' min';
  if (props.tier != null) return String(props.tier) + ' min';
  if (props.time_min != null) return props.time_min + ' min';
  if (props.band != null) return String(props.band) + ' min';
  if (props.label) return String(props.label);
  if (props.distance_ft != null) return Math.round(props.distance_ft) + ' ft';
  if (props.feet != null) return Math.round(props.feet) + ' ft';
  return null;
}

function renderWalkshed(g, gj) {
  const features = (gj.features || []).filter(f => f.geometry);
  console.log('[render] walkshed: ' + features.length + ' polygons');
  if (features.length === 0) return;
  const color = state.walkshedColor || '#7BB661';
  const opac  = state.walkshedOpacity != null ? state.walkshedOpacity : 0.22;

  // Polygons
  const polyG = g.append('g').attr('class', 'walkshed-polygons');
  polyG.selectAll('path').data(features).join('path')
    .attr('d', state.pathGen)
    .attr('fill', color)
    .attr('fill-opacity', opac)
    .attr('stroke', color)
    .attr('stroke-width', 1.6)
    .attr('stroke-opacity', Math.min(1, opac * 4));

  // Labels — one per polygon, centered above the polygon top edge.
  // Stroked behind the fill so they read over any underlying color.
  const labels = features.map(f => {
    const text = walkshedLabel(f.properties);
    if (!text) return null;
    let bounds;
    try { bounds = state.pathGen.bounds(f); }
    catch (e) { return null; }
    if (!bounds || !isFinite(bounds[0][0])) return null;
    return {
      text: text,
      x: (bounds[0][0] + bounds[1][0]) / 2,
      y: bounds[0][1] - 6,
    };
  }).filter(Boolean);

  if (labels.length === 0 && features.length > 0) {
    console.warn('[walkshed] no labelable properties — feature[0] props:',
                 Object.keys(features[0].properties || {}));
  }

  const labelG = g.append('g').attr('class', 'walkshed-labels');
  const FONT = '"SF Mono", "JetBrains Mono", "Consolas", monospace';
  // Two stacked editable-text layers give a legible halo WITHOUT paint-order
  // (which Illustrator ignores, leaving a heavy stroke painted over the glyphs).
  // Behind: a dark stroked-only copy. Front: the colored fill copy.
  function styleCommon(sel) {
    return sel.attr('x', d => d.x).attr('y', d => d.y)
      .attr('text-anchor', 'middle')
      .attr('font-family', FONT).attr('font-size', 11)
      .attr('font-weight', 600).attr('letter-spacing', '0.08em')
      .text(d => d.text.toUpperCase());
  }
  const haloG = labelG.append('g').attr('class', 'walkshed-label-halo');
  styleCommon(haloG.selectAll('text').data(labels).join('text'))
    .attr('fill', 'none')
    .attr('stroke', '#0a0a0a')
    .attr('stroke-width', 3)
    .attr('stroke-linejoin', 'round');
  const fillG = labelG.append('g').attr('class', 'walkshed-label-fill');
  styleCommon(fillG.selectAll('text').data(labels).join('text'))
    .attr('fill', color);
}

function buildingProgram(props) {
  const ROOF_TO_PROG = {
    stadium: 'sport', sports_centre: 'sport',
    parking: 'parking', garage: 'parking',
    apartments: 'residential', residential: 'residential',
    house: 'residential', terrace: 'residential', dormitory: 'residential',
    hotel: 'hotel',
    office: 'office', commercial: 'office',
    retail: 'retail_food_entertainment',
    supermarket: 'retail_food_entertainment',
    school: 'other', civic: 'other', church: 'other',
  };
  const tag = (props.building || '').toLowerCase();
  if (ROOF_TO_PROG[tag]) return ROOF_TO_PROG[tag];
  const dom = (props.dominant_program_inside || '').toLowerCase();
  if (dom) return dom;
  return 'other';
}

const STADIUM_RED = '#d62728';
const EARTH_R = 6371008.8;  // mean earth radius (m)

// Tag/name-based detection (cheap, definitive when present).
function isStadiumByTag(props) {
  if (!props) return false;
  const b = (props.building || '').toLowerCase();
  if (b === 'stadium' || b === 'sports_centre' || b === 'sports_hall'
      || b === 'arena' || b === 'pavilion') return true;
  if ((props.leisure || '').toLowerCase() === 'stadium') return true;
  if ((props.amenity || '').toLowerCase() === 'stadium') return true;
  if (props.sport) return true;
  if (buildingProgram(props) === 'sport') return true;
  const name = (props.name || props.NAME || '').toLowerCase();
  if (name) {
    if (/\b(stadium|arena|ballpark|coliseum|fieldhouse|sportsplex)\b/.test(name)) return true;
    if (/\b(comerica park|little caesars|ford field|joe louis)\b/.test(name)) return true;
  }
  return false;
}

// Real footprint area (m^2), assumes lat/lng geometry.
function buildingAreaM2(f) {
  try {
    const a = d3.geoArea(f);
    if (!isFinite(a) || a <= 0 || a > 0.01) return 0;
    return a * EARTH_R * EARTH_R;
  } catch (e) { return 0; }
}

function haversineM(a, b) {
  const dLat = (b[1] - a[1]) * Math.PI / 180;
  const dLng = (b[0] - a[0]) * Math.PI / 180;
  const la1 = a[1] * Math.PI / 180, la2 = b[1] * Math.PI / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_R * Math.asin(Math.min(1, Math.sqrt(h)));
}

function buildingPerimeterM(f) {
  const g = f.geometry;
  let rings = [];
  if (!g) return 0;
  if (g.type === 'Polygon') rings = g.coordinates;
  else if (g.type === 'MultiPolygon') rings = g.coordinates.flat();
  else return 0;
  let perim = 0;
  for (const ring of rings) {
    for (let i = 1; i < ring.length; i++) perim += haversineM(ring[i - 1], ring[i]);
  }
  return perim;
}

// Polsby-Popper compactness: 1.0 = perfect circle, lower = more irregular.
// Stadia/arenas are blobby (high); sprawling industrial/convention forms are low.
function compactness(f) {
  const area = buildingAreaM2(f);
  const perim = buildingPerimeterM(f);
  if (perim <= 0) return 0;
  return (4 * Math.PI * area) / (perim * perim);
}

// Combined classifier — populates state.stadiumSet once per SAD load.
//
// Requires a COMBINATION, never a single signal:
//   • Morphology prerequisite: footprint >= MIN_AREA  (excludes small gyms; parks
//     aren't in the buildings layer at all, and open_space-program buildings are
//     explicitly rejected)
//   AND one confirming sport signal:
//     • an explicit venue tag (building=stadium/arena, leisure/amenity=stadium,
//       sport=*, or pipeline program=sport), OR
//     • at least one sport-category POI located INSIDE the footprint (POI analysis)
//   • Narrow morphology-only fallback: a very large AND highly compact enclosed
//     form (dome/arena) — gated tightly so convention centers / warehouses
//     (large but rectangular, low compactness) and parks never qualify.
const MIN_STADIUM_AREA = 4000;     // m^2 — major-venue floor
const HUGE_STADIUM_AREA = 22000;   // m^2 — enclosed-arena fallback floor
const HUGE_COMPACTNESS = 0.55;     // Polsby-Popper for the fallback
const MASSIVE_STADIUM_AREA = 28000; // m^2 — at this size it's a venue regardless

function classifyStadium(building, sportInside) {
  const props = building.properties || {};
  const area = buildingAreaM2(building);
  // Morphology prerequisite
  if (area < MIN_STADIUM_AREA) return false;
  // Never flag open space / parks / parking
  const prog = buildingProgram(props);
  if (prog === 'open_space' || prog === 'parking') return false;

  // Sport signal A — explicit tags
  const b = (props.building || '').toLowerCase();
  const leis = (props.leisure || '').toLowerCase();
  const amen = (props.amenity || '').toLowerCase();
  const hasTag = b === 'stadium' || b === 'arena' || b === 'sports_centre'
              || b === 'sports_hall' || leis === 'stadium' || amen === 'stadium'
              || !!props.sport || prog === 'sport';
  if (hasTag) return true;                      // large + explicit sport tag

  // Sport signal B — POI analysis (sport POI physically inside footprint)
  if (sportInside >= 1) return true;            // large + sport POI inside

  // Morphology fallback — enclosed arena/dome form
  if (area > HUGE_STADIUM_AREA && compactness(building) > HUGE_COMPACTNESS) return true;

  // Massive footprint — at 28,000+ m^2 a non-open-space building in a SAD is a
  // venue (SoFi ~70k, State Farm ~50k). Convention centers this big are rare and
  // the open_space/parking exclusion above already filtered the obvious non-venues.
  if (area > MASSIVE_STADIUM_AREA) return true;

  return false;
}

function computeStadiumSet() {
  const set = new Set();
  const buildings = state.data.buildings && state.data.buildings.features;
  if (!buildings) { state.stadiumSet = set; return; }

  // Pre-extract sport POI points for containment testing
  const sportPts = [];
  const pois = (state.data.pois && state.data.pois.features) || [];
  for (const p of pois) {
    if (!p.geometry || p.geometry.type !== 'Point') continue;
    const cat = (p.properties && p.properties.rossetti_category) || '';
    if (cat === 'sport') sportPts.push(p.geometry.coordinates);
  }

  let byTag = 0, byPoi = 0, byShape = 0;
  for (const bld of buildings) {
    if (!bld.geometry) continue;
    const area = buildingAreaM2(bld);
    if (area < MIN_STADIUM_AREA) continue;  // skip cheap; most buildings exit here

    // Count sport POIs inside (bbox prefilter, then exact containment)
    let sportInside = 0;
    if (sportPts.length) {
      let bb;
      try { bb = d3.geoBounds(bld); } catch (e) { bb = null; }
      if (bb) {
        const [[mnLng, mnLat], [mxLng, mxLat]] = bb;
        for (const c of sportPts) {
          if (c[0] < mnLng || c[0] > mxLng || c[1] < mnLat || c[1] > mxLat) continue;
          if (d3.geoContains(bld, c)) { sportInside++; if (sportInside >= 2) break; }
        }
      }
    }

    if (!classifyStadium(bld, sportInside)) continue;
    set.add(bld);
    // Tally which signal caught it (for diagnostics)
    const props = bld.properties || {};
    const prog = buildingProgram(props);
    const hasTag = ['stadium','arena','sports_centre','sports_hall']
                     .includes((props.building || '').toLowerCase())
                   || (props.leisure || '').toLowerCase() === 'stadium'
                   || (props.amenity || '').toLowerCase() === 'stadium'
                   || !!props.sport || prog === 'sport';
    if (hasTag) byTag++;
    else if (sportInside >= 1) byPoi++;
    else byShape++;
  }

  state.stadiumSet = set;
  console.log('[stadia] ' + set.size + ' venues · tag+area: ' + byTag
              + ', POI+area: ' + byPoi + ', shape+area: ' + byShape);
}

// ── Planar point-in-polygon (winding-agnostic, robust to wrapper-ring artifacts)
function pointInRing(pt, ring) {
  let inside = false;
  const x = pt[0], y = pt[1];
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1];
    const xj = ring[j][0], yj = ring[j][1];
    const hit = ((yi > y) !== (yj > y)) &&
                (x < (xj - xi) * (y - yi) / ((yj - yi) || 1e-12) + xi);
    if (hit) inside = !inside;
  }
  return inside;
}

// Build a containment tester from the SAD boundary, keeping only rings that fall
// within a sane multiple of the manifest bbox (drops world-rect wrapper rings
// that survive into some boundary files and corrupt geoContains).
function makeSadInteriorTest(sad) {
  const bbox = sad && sad.sad_bbox;
  const sadGj = state.data.sad_boundary;
  const rings = [];
  if (sadGj && sadGj.features) {
    let padx = Infinity, pady = Infinity, xmin, ymin, xmax, ymax;
    if (bbox) {
      [xmin, ymin, xmax, ymax] = bbox;
      padx = (xmax - xmin) * 2;
      pady = (ymax - ymin) * 2;
    }
    const within = (ring) => {
      if (!bbox) return true;
      for (const c of ring) {
        if (c[0] < xmin - padx || c[0] > xmax + padx) return false;
        if (c[1] < ymin - pady || c[1] > ymax + pady) return false;
      }
      return true;
    };
    for (const f of sadGj.features) {
      const g = f.geometry;
      if (!g) continue;
      const polys = g.type === 'Polygon' ? [g.coordinates]
                  : g.type === 'MultiPolygon' ? g.coordinates : [];
      for (const poly of polys)
        for (const ring of poly)
          if (within(ring)) rings.push(ring);
    }
  }
  if (rings.length === 0) return () => true;  // no usable boundary → keep all
  return (pt) => {
    let inside = false;
    for (const ring of rings) if (pointInRing(pt, ring)) inside = !inside;
    return inside;
  };
}

// Of all detected venues, keep only those whose centroid falls inside the SAD
// boundary — these are the district anchors that get colored red.
function computeSadStadiumSet(sad) {
  const set = new Set();
  const all = state.stadiumSet || new Set();
  const inside = makeSadInteriorTest(sad);
  for (const b of all) {
    try {
      const c = d3.geoCentroid(b);
      if (inside(c)) set.add(b);
    } catch (e) { /* skip */ }
  }
  state.sadStadiumSet = set;
  console.log('[stadia] ' + set.size + ' of ' + all.size + ' venues inside the SAD');
}

function satelliteUrl(minLng, minLat, maxLng, maxLat, w, h) {
  if (state.satellite.provider === 'mapbox' && state.satellite.token) {
    // Mapbox Static Images API (max 1280x1280). bbox in [minLng,minLat,maxLng,maxLat]
    const W = Math.min(1280, w), H = Math.min(1280, h);
    return 'https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/['
      + minLng + ',' + minLat + ',' + maxLng + ',' + maxLat + ']/'
      + W + 'x' + H + '?access_token=' + encodeURIComponent(state.satellite.token)
      + '&attribution=false&logo=false';
  }
  // Esri World Imagery export (no key). Render in Web Mercator (3857) to match
  // the viewer's d3.geoMercator projection.
  const W = Math.min(2048, w), H = Math.min(2048, h);
  return 'https://server.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export'
    + '?bbox=' + minLng + ',' + minLat + ',' + maxLng + ',' + maxLat
    + '&bboxSR=4326&imageSR=3857&size=' + W + ',' + H
    + '&format=jpg&f=image';
}

function renderSatellite(g) {
  const [minLng, minLat, maxLng, maxLat] = state.bbox;
  const tl = state.projection([minLng, maxLat]);
  const br = state.projection([maxLng, minLat]);
  if (!tl || !br || !isFinite(tl[0]) || !isFinite(br[0])) return;
  const x = Math.min(tl[0], br[0]);
  const y = Math.min(tl[1], br[1]);
  const w = Math.abs(br[0] - tl[0]);
  const h = Math.abs(br[1] - tl[1]);
  if (w < 1 || h < 1) return;
  const url = satelliteUrl(minLng, minLat, maxLng, maxLat,
                           Math.round(w * 2), Math.round(h * 2));
  const img = g.append('image')
    .attr('x', x).attr('y', y)
    .attr('width', w).attr('height', h)
    .attr('preserveAspectRatio', 'none')
    .attr('crossorigin', 'anonymous')
    .attr('opacity', state.satellite.opacity);
  img.attr('href', url).attr('xlink:href', url);
}

function renderBuildings(g, gj) {
  const feats = filterSane(gj.features);
  const sadStadia = state.sadStadiumSet || new Set();
  const dominant = state.buildingColorMode === 'dominant';
  const grey = state.buildingColor || '#4a4a4a';
  const outline = state.buildingStyle === 'outline';
  const fillOpac = state.buildingFillOpacity != null ? state.buildingFillOpacity : 0.92;
  const owt = state.buildingOutlineWeight != null ? state.buildingOutlineWeight : 0.6;
  const ocol = state.buildingOutlineColor || '#888888';

  function baseColor(d) {
    if (sadStadia.has(d)) return STADIUM_RED;
    if (dominant) return PROGRAM_COLORS[buildingProgram(d.properties || {})] || PROGRAM_COLORS.other;
    return grey;
  }

  const sel = g.selectAll('path').data(feats).join('path').attr('d', state.pathGen);

  if (outline) {
    // Outline mode — no fill so satellite/base shows through; anchors stay red.
    // Stroke follows the active color mode (program colors in Dominant POI).
    sel.attr('fill', 'none')
       .attr('stroke', d => {
         if (sadStadia.has(d)) return STADIUM_RED;
         if (dominant) return PROGRAM_COLORS[buildingProgram(d.properties || {})] || PROGRAM_COLORS.other;
         return ocol;
       })
       .attr('stroke-width', d => sadStadia.has(d) ? owt * 1.6 : owt)
       .attr('stroke-opacity', 1);
  } else {
    sel.attr('fill', d => baseColor(d))
       .attr('fill-opacity', fillOpac)
       .attr('stroke', '#555')
       .attr('stroke-width', 0.25);
  }
}

function renderSadBoundary(g, gj) {
  const color = state.boundaryColor || '#f1c50c';
  g.selectAll('path').data(gj.features).join('path')
    .attr('d', state.pathGen)
    .attr('fill', 'none')
    .attr('stroke', color)
    .attr('stroke-width', 2.4)
    .attr('stroke-dasharray', '5 3')
    .attr('stroke-linejoin', 'round');
}

function poiName(p) {
  if (!p) return '';
  return p.name || p.NAME || p.brand || p.operator || p.amenity || p.shop || '';
}

function renderPois(parent, gj) {
  // Sub-group each program category for Illustrator-friendly export
  const byCat = {};
  for (const f of (gj.features || [])) {
    if (!f.geometry || f.geometry.type !== 'Point') continue;
    const cat = (f.properties && f.properties.rossetti_category) || 'other';
    if (state.programOn[cat] === false) continue;
    (byCat[cat] = byCat[cat] || []).push(f);
  }
  for (const cat of ROSSETTI_ORDER) {
    const pts = byCat[cat];
    if (!pts || pts.length === 0) continue;
    const catG = parent.append('g').attr('id', 'pois_' + cat);
    const sel = catG.selectAll('circle').data(pts).join('circle')
      .attr('cx', d => state.pathGen.centroid(d)[0])
      .attr('cy', d => state.pathGen.centroid(d)[1])
      .attr('r', state.poiSize)
      .attr('fill', PROGRAM_COLORS[cat])
      .attr('fill-opacity', 0.9)
      .attr('stroke', 'white')
      .attr('stroke-width', 0.4);
    sel.append('title').text(d => {
      const nm = poiName(d.properties);
      return nm ? (nm + '  ·  ' + PROGRAM_LABELS[cat]) : PROGRAM_LABELS[cat];
    });
  }
}

function transitName(props) {
  if (!props) return 'Transit station';
  return props.name || props.NAME || props.station || props.stop_name
       || props.ref || 'Transit station';
}

function renderTransit(g, gj) {
  const points = [];
  for (const f of (gj.features || [])) {
    if (!f.geometry) continue;
    if (f.geometry.type === 'Point') {
      points.push({ feature: f, coords: f.geometry.coordinates });
    } else if (f.geometry.type === 'MultiPoint') {
      for (const c of f.geometry.coordinates) {
        points.push({ feature: f, coords: c });
      }
    }
  }
  console.log('[render] transit: ' + points.length + ' stations');
  if (points.length === 0) return;
  const sel = g.selectAll('circle').data(points).join('circle')
    .attr('cx', d => state.projection(d.coords)[0])
    .attr('cy', d => state.projection(d.coords)[1])
    .attr('r', state.transitSize)
    .attr('fill', '#5b9bd5')
    .attr('stroke', 'white')
    .attr('stroke-width', 1.8);
  sel.append('title').text(d => transitName(d.feature.properties));
}

// ─── Heatmap ─────────────────────────────────────────────────────────────────

function renderHeatmap(g) {
  if (!state.data.pois) return;
  const cat = state.heatmap.category;
  const pts = (state.data.pois.features || []).filter(f => {
    if (!f.geometry || f.geometry.type !== 'Point') return false;
    if (cat === '__all') return true;
    return ((f.properties && f.properties.rossetti_category) || 'other') === cat;
  });
  if (pts.length < 4) return;
  const points = pts.map(f => state.pathGen.centroid(f));

  const svg = document.getElementById('map');
  const w = svg.clientWidth || 800;
  const h = svg.clientHeight || 600;

  // Convert bandwidth ft -> pixels via projection scale at centroid
  const [minx, , maxx, ] = state.bbox;
  const cy = (state.bbox[1] + state.bbox[3]) / 2;
  const mPerDegLng = 111320 * Math.cos(cy * Math.PI / 180);
  const p0 = state.projection([minx, cy]);
  const p1 = state.projection([maxx, cy]);
  const totalMeters = (maxx - minx) * mPerDegLng;
  const totalPx = Math.abs(p1[0] - p0[0]);
  const pxPerMeter = totalPx / totalMeters;
  const bwPx = Math.max(8,
    Math.min(120, state.heatmap.bandwidthFt * M_PER_FT * pxPerMeter));

  const contours = d3.contourDensity()
    .x(d => d[0]).y(d => d[1])
    .size([w, h]).bandwidth(bwPx)
    .thresholds(14)(points);

  if (contours.length === 0) return;
  const maxVal = d3.max(contours, c => c.value);
  const intensity = state.heatmap.intensity;

  // For each contour, sample from gradient. Split the interpolated color into a
  // hex fill + numeric fill-opacity — Illustrator does NOT parse rgba() in the
  // fill attribute and would render it black. The gradient alpha (opacity of the
  // sampled color) is multiplied by the user's intensity slider.
  g.selectAll('path').data(contours).join('path')
    .attr('d', d3.geoPath())
    .attr('stroke', 'none')
    .each(function(d) {
      const c = d3.color(HEAT_INTERPOLATOR(d.value / maxVal));
      const sel = d3.select(this);
      if (c) {
        const a = (c.opacity != null ? c.opacity : 1) * intensity;
        sel.attr('fill', c.formatHex()).attr('fill-opacity', a);
      } else {
        sel.attr('fill', '#888888').attr('fill-opacity', intensity);
      }
    });
}

// ─── Zoom + pan ──────────────────────────────────────────────────────────────

const zoomBehavior = d3.zoom()
  .scaleExtent([0.3, 32])
  .on('zoom', evt => {
    state.transform = evt.transform;
    d3.select('#map_root').attr('transform', evt.transform);
    updateScaleChrome();
  });

d3.select('#map').call(zoomBehavior);

function fitZoom() {
  state.transform = d3.zoomIdentity;
  d3.select('#map').call(zoomBehavior.transform, d3.zoomIdentity);
}

// ─── Scale chrome ────────────────────────────────────────────────────────────

function updateScaleChrome() {
  const el = document.getElementById('scale-bar');
  if (!state.bbox || !state.projection) { el.innerHTML = ''; return; }
  const [minx, , maxx, ] = state.bbox;
  const cy = (state.bbox[1] + state.bbox[3]) / 2;
  const mPerDegLng = 111320 * Math.cos(cy * Math.PI / 180);
  const p0 = state.projection([minx, cy]);
  const p1 = state.projection([maxx, cy]);
  const totalMeters = (maxx - minx) * mPerDegLng;
  const totalPx = Math.abs(p1[0] - p0[0]);
  const pxPerMeter = (totalPx * state.transform.k) / totalMeters;
  const pxPerFt = pxPerMeter * M_PER_FT;
  const targetPx = 110;
  const targetFt = targetPx / pxPerFt;
  const niceFt = pickNiceFt(targetFt);
  const barPx = niceFt * pxPerFt;
  const label = niceFt >= 1000 ? (niceFt / 1000).toFixed(1) + 'k ft'
                                : Math.round(niceFt) + ' ft';
  el.innerHTML = '<div class="bar" style="width:' + barPx.toFixed(0)
                + 'px"></div>' + label;
}

function pickNiceFt(n) {
  const nice = [25, 50, 100, 150, 200, 300, 500, 750, 1000, 1500,
                 2000, 3000, 5000, 7500, 10000];
  for (const v of nice) if (v >= n * 0.9) return v;
  return 20000;
}

// ─── SVG export ──────────────────────────────────────────────────────────────

const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs, text) {
  const n = document.createElementNS(SVGNS, tag);
  if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  return n;
}

// Base-scale pixels-per-foot (zoom independent — export is unzoomed)
function basePxPerFt() {
  if (!state.bbox || !state.projection) return null;
  const [minx, , maxx, ] = state.bbox;
  const cy = (state.bbox[1] + state.bbox[3]) / 2;
  const mPerDegLng = 111320 * Math.cos(cy * Math.PI / 180);
  const p0 = state.projection([minx, cy]);
  const p1 = state.projection([maxx, cy]);
  const totalMeters = (maxx - minx) * mPerDegLng;
  const totalPx = Math.abs(p1[0] - p0[0]);
  if (!isFinite(totalPx) || totalMeters <= 0) return null;
  return (totalPx / totalMeters) * M_PER_FT;
}

// Embed the topbar logo as a data URI so it travels inside the exported SVG.
function logoDataUri() {
  const img = document.getElementById('brand-logo');
  if (!img || !img.complete || !img.naturalWidth) return null;
  try {
    const c = document.createElement('canvas');
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    c.getContext('2d').drawImage(img, 0, 0);
    return { uri: c.toDataURL('image/png'),
             w: img.naturalWidth, h: img.naturalHeight };
  } catch (e) {
    console.warn('[export] logo embed failed:', e);
    return null;
  }
}

// Build the legend / scale bar / north arrow as labeled layers beside the map.
// Returns { groups: [<g>...], width } so the caller can size the canvas.
function buildExportChrome(sad, mapW, mapH) {
  const LEG_W = 252, GUT = 34, PAD = 24;
  const x0 = mapW + GUT;          // left edge of the legend column
  const cx = x0 + PAD;            // text/swatch left
  const SW = 16;                  // swatch box
  const LBL = cx + SW + 12;       // label x
  const ROW = 23;                 // row pitch
  const SANS = 'system-ui,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif';
  const MONO = '"SF Mono","JetBrains Mono","Consolas",monospace';
  const INK = '#0a0a0a', MUTE = '#888';
  const outlineMode = state.buildingStyle === 'outline';

  const legend = svgEl('g', {
    id: 'legend', 'inkscape:label': 'legend', 'inkscape:groupmode': 'layer',
  });

  let y = PAD + 6;

  // Logo at the very top of the legend column (embedded PNG)
  const logo = logoDataUri();
  if (logo) {
    const maxW = LEG_W - PAD * 2;
    const lw = Math.min(maxW, 132);
    const lh = lw * (logo.h / logo.w);
    const img = svgEl('image', {
      x: cx, y: y, width: lw, height: lh,
      preserveAspectRatio: 'xMinYMin meet',
    });
    img.setAttribute('href', logo.uri);
    legend.appendChild(img);
    y += lh + 18;
  }

  // Title block — wrap to the column width so long SAD names don't run off the
  // page; shrink the font if a single word is wider than the column.
  const titleText = sad.sad_name || sad.sad_id;
  const titleMaxW = LEG_W - PAD * 2;
  const measure = (s, size) => s.length * size * 0.56;   // Arial-bold avg advance
  let titleSize = 16;
  const words = String(titleText).split(/\s+/);
  const widest = words.reduce((a, b) => measure(b, 16) > measure(a, 16) ? b : a, '');
  if (measure(widest, 16) > titleMaxW) {
    titleSize = Math.max(10, Math.floor(16 * titleMaxW / measure(widest, 16)));
  }
  const titleLines = [];
  let cur = '';
  const maxChars = Math.max(4, Math.floor(titleMaxW / (titleSize * 0.56)));
  for (let wd of words) {
    // hard-break a single token that is itself wider than the column
    while (measure(wd, titleSize) > titleMaxW) {
      const head = wd.slice(0, maxChars);
      if (cur) { titleLines.push(cur); cur = ''; }
      titleLines.push(head);
      wd = wd.slice(maxChars);
    }
    const test = cur ? cur + ' ' + wd : wd;
    if (measure(test, titleSize) > titleMaxW && cur) { titleLines.push(cur); cur = wd; }
    else cur = test;
  }
  if (cur) titleLines.push(cur);
  const titleLH = titleSize * 1.18;
  titleLines.forEach((ln, i) => {
    legend.appendChild(svgEl('text', {
      x: cx, y: y + i * titleLH, 'font-family': SANS, 'font-size': titleSize,
      'font-weight': 700, fill: INK,
    }, ln));
  });
  y += titleLines.length * titleLH - (titleLH - titleSize);
  const subBits = [];
  if (sad.anchor) subBits.push(sad.anchor);
  if (sad.typology) subBits.push(sad.typology);
  if (subBits.length) {
    legend.appendChild(svgEl('text', {
      x: cx, y: y, 'font-family': MONO, 'font-size': 8.5,
      'letter-spacing': '0.06em', fill: MUTE,
    }, subBits.join('  ·  ').toUpperCase()));
    y += 6;
  }
  y += 14;
  legend.appendChild(svgEl('line', {
    x1: cx, y1: y, x2: x0 + LEG_W - PAD, y2: y,
    stroke: '#e0e0e0', 'stroke-width': 1,
  }));
  y += 22;

  function sectionHeader(txt) {
    legend.appendChild(svgEl('text', {
      x: cx, y: y, 'font-family': MONO, 'font-size': 8.5,
      'font-weight': 600, 'letter-spacing': '0.12em', fill: MUTE,
    }, txt));
    y += ROW - 3;
  }
  function rowLabel(txt) {
    legend.appendChild(svgEl('text', {
      x: LBL, y: y + SW * 0.72, 'font-family': SANS, 'font-size': 11.5,
      fill: INK,
    }, txt));
  }
  // swatch variants ---------------------------------------------------------
  function blockRow(color, label, opts) {
    opts = opts || {};
    if (opts.outline) {
      legend.appendChild(svgEl('rect', {
        x: cx, y: y, width: SW, height: SW, fill: 'none',
        stroke: color, 'stroke-width': 1.4,
      }));
    } else {
      legend.appendChild(svgEl('rect', {
        x: cx, y: y, width: SW, height: SW, fill: color,
        'fill-opacity': opts.opacity != null ? opts.opacity : 1,
        stroke: opts.stroke || 'none',
        'stroke-width': opts.strokeWidth || 0,
      }));
    }
    rowLabel(label); y += ROW;
  }
  function dashRow(color, label) {
    const yc = y + SW / 2;
    legend.appendChild(svgEl('line', {
      x1: cx, y1: yc, x2: cx + SW, y2: yc, stroke: color,
      'stroke-width': 2.4, 'stroke-dasharray': '4 2.5',
    }));
    rowLabel(label); y += ROW;
  }
  function lineRow(color, label) {
    const yc = y + SW / 2;
    legend.appendChild(svgEl('line', {
      x1: cx, y1: yc, x2: cx + SW, y2: yc, stroke: color,
      'stroke-width': 2.2,
    }));
    rowLabel(label); y += ROW;
  }
  function dotRow(color, label) {
    legend.appendChild(svgEl('circle', {
      cx: cx + SW / 2, cy: y + SW / 2, r: 5.5, fill: color,
    }));
    rowLabel(label); y += ROW;
  }

  // ── LAYERS section --------------------------------------------------------
  const V = state.layerVisible;
  sectionHeader('LAYERS');

  if (V.sad_boundary) dashRow(state.boundaryColor || '#f1c50c', 'SAD boundary');

  if (V.buildings && state.data.buildings) {
    // Anchor venues are always red
    blockRow(STADIUM_RED, 'Anchor venue', { outline: outlineMode });
    if (state.buildingColorMode === 'dominant') {
      blockRow('#cccccc', 'Buildings — by program', { outline: outlineMode, stroke: '#bbb', strokeWidth: 0.5 });
      ROSSETTI_ORDER.forEach(c => {
        // indent program swatches slightly under the buildings header
        const yc = y;
        legend.appendChild(svgEl('rect', {
          x: cx + 10, y: yc, width: SW - 4, height: SW - 4,
          fill: outlineMode ? 'none' : PROGRAM_COLORS[c],
          stroke: PROGRAM_COLORS[c], 'stroke-width': outlineMode ? 1.4 : 0,
        }));
        legend.appendChild(svgEl('text', {
          x: LBL + 6, y: yc + (SW - 4) * 0.78, 'font-family': SANS,
          'font-size': 10.5, fill: '#444',
        }, PROGRAM_LABELS[c]));
        y += ROW - 4;
      });
    } else {
      blockRow(state.buildingColor || '#4a4a4a', 'Buildings', { outline: outlineMode });
    }
  }
  if (V.parks && state.data.parks)
    blockRow('#A8D5BA', 'Parks / open space', { outline: outlineMode, opacity: state.buildingFillOpacity });
  if (V.parking && state.data.parking)
    blockRow('#C8B8A6', 'Parking lots', { outline: outlineMode, opacity: state.buildingFillOpacity });
  if (V.roads && state.data.roads)
    lineRow('#aaaaaa', 'Streets');
  if (V.walkshed && state.data.walkshed) {
    const wc = state.walkshedColor || '#7BB661';
    blockRow(wc, 'Walkshed', { opacity: Math.max(0.35, state.walkshedOpacity || 0.22), stroke: wc, strokeWidth: 1.2 });
  }
  if (V.transit && state.data.transit)
    dotRow('#5b9bd5', 'Transit stations');

  // ── POI categories (only those toggled on) --------------------------------
  if (V.pois && state.data.pois) {
    const cats = ROSSETTI_ORDER.filter(c => state.programOn[c] !== false);
    if (cats.length) {
      y += 6; sectionHeader('POI CATEGORIES');
      cats.forEach(c => dotRow(PROGRAM_COLORS[c], PROGRAM_LABELS[c]));
    }
  }

  // ── Heatmap note ----------------------------------------------------------
  if (state.heatmap.on && state.data.pois) {
    y += 6; sectionHeader('DENSITY HEATMAP');
    const catLabel = state.heatmap.category === '__all'
      ? 'All POIs' : (PROGRAM_LABELS[state.heatmap.category] || state.heatmap.category);
    // gradient bar
    const gradId = 'legend-heat-grad';
    const defs = svgEl('defs');
    const lg = svgEl('linearGradient', { id: gradId, x1: '0', y1: '0', x2: '1', y2: '0' });
    HEAT_STOPS.forEach(s => {
      const c = d3.color(s.color);
      lg.appendChild(svgEl('stop', {
        offset: (s.t * 100) + '%',
        'stop-color': c ? c.formatHex() : '#000000',
        'stop-opacity': c && c.opacity != null ? c.opacity : 1,
      }));
    });
    defs.appendChild(lg); legend.appendChild(defs);
    const barW = LEG_W - PAD * 2;
    // Dark backing so the alpha-ramp gradient reads exactly as it does on the
    // dark map canvas (its high end is pale cream and would vanish on white).
    legend.appendChild(svgEl('rect', {
      x: cx, y: y, width: barW, height: 9, fill: '#0a0a0a',
    }));
    legend.appendChild(svgEl('rect', {
      x: cx, y: y, width: barW, height: 9, fill: 'url(#' + gradId + ')',
      stroke: '#ddd', 'stroke-width': 0.5,
    }));
    y += 9;
    legend.appendChild(svgEl('text', {
      x: cx, y: y + 11, 'font-family': MONO, 'font-size': 8, fill: MUTE,
    }, 'LOW'));
    legend.appendChild(svgEl('text', {
      x: cx + barW, y: y + 11, 'font-family': MONO, 'font-size': 8,
      fill: MUTE, 'text-anchor': 'end',
    }, 'HIGH'));
    y += 16;
    legend.appendChild(svgEl('text', {
      x: cx, y: y + 4, 'font-family': SANS, 'font-size': 11, fill: INK,
    }, 'Showing: ' + catLabel + ' density'));
    y += ROW;
  }

  // ── divider before chrome -------------------------------------------------
  y += 10;
  legend.appendChild(svgEl('line', {
    x1: cx, y1: y, x2: x0 + LEG_W - PAD, y2: y,
    stroke: '#e0e0e0', 'stroke-width': 1,
  }));
  y += 28;

  const groups = [legend];
  const colInner = LEG_W - PAD * 2;

  // ── Scale bar (own layer) — sized to fit WITHIN the column ----------------
  const pxPerFt = basePxPerFt();
  if (pxPerFt) {
    const scaleG = svgEl('g', {
      id: 'scale_bar', 'inkscape:label': 'scale_bar', 'inkscape:groupmode': 'layer',
    });
    // largest "nice" footage whose bar still fits the column width
    const maxFt = colInner / pxPerFt;
    const niceArr = [25, 50, 100, 150, 200, 300, 500, 750, 1000, 1500,
                     2000, 3000, 5000, 7500, 10000];
    let niceFt = niceArr[0];
    for (const v of niceArr) { if (v <= maxFt) niceFt = v; else break; }
    const barPx = Math.min(niceFt * pxPerFt, colInner);
    const by = y;
    scaleG.appendChild(svgEl('line', {
      x1: cx, y1: by, x2: cx + barPx, y2: by, stroke: INK, 'stroke-width': 2,
    }));
    [0, barPx / 2, barPx].forEach(tx => scaleG.appendChild(svgEl('line', {
      x1: cx + tx, y1: by - 4, x2: cx + tx, y2: by + 4, stroke: INK, 'stroke-width': 1.4,
    })));
    const m = Math.round(niceFt * M_PER_FT);
    const ftLabel = niceFt >= 1000 ? (niceFt / 1000) + 'k ft' : niceFt + ' ft';
    scaleG.appendChild(svgEl('text', {
      x: cx, y: by + 16, 'font-family': MONO, 'font-size': 9, fill: INK,
    }, '0'));
    scaleG.appendChild(svgEl('text', {
      x: cx + barPx, y: by + 16, 'font-family': MONO, 'font-size': 9,
      fill: INK, 'text-anchor': 'end',
    }, ftLabel + '  (' + m + ' m)'));
    groups.push(scaleG);
    y = by + 16;
  }

  // ── North arrow (own layer) — stacked below the scale bar, centered -------
  y += 30;                                  // clear gap, no collision
  const northG = svgEl('g', {
    id: 'north_arrow', 'inkscape:label': 'north_arrow', 'inkscape:groupmode': 'layer',
  });
  const ncx = x0 + LEG_W / 2;               // column center (away from scale label)
  const nTop = y;                           // "N" baseline
  const aTip = y + 8, aBot = y + 32;        // arrowhead tip → base
  northG.appendChild(svgEl('text', {
    x: ncx, y: nTop, 'font-family': SANS, 'font-size': 12,
    'font-weight': 700, fill: INK, 'text-anchor': 'middle',
  }, 'N'));
  northG.appendChild(svgEl('polygon', {
    points: ncx + ',' + aTip + ' ' + (ncx - 7) + ',' + aBot + ' '
            + ncx + ',' + (aBot - 6) + ' ' + (ncx + 7) + ',' + aBot,
    fill: INK,
  }));
  groups.push(northG);
  const chromeBottom = aBot + 10;

  return {
    groups, width: LEG_W + GUT, bottom: Math.max(y, chromeBottom),
    attrX: cx, attrRight: x0 + LEG_W - PAD, colInner,
  };
}

// Load an (possibly cross-origin) image URL and return a PNG data URI.
// Requires the host to send CORS headers (Esri & Mapbox both do); falls back
// to null if the canvas is tainted or the load fails.
function imageUrlToDataUri(url) {
  return new Promise(resolve => {
    const im = new Image();
    im.crossOrigin = 'anonymous';
    let done = false;
    const finish = v => { if (!done) { done = true; resolve(v); } };
    const timer = setTimeout(() => { console.warn('[export] satellite load timed out'); finish(null); }, 8000);
    im.onload = () => {
      clearTimeout(timer);
      try {
        const c = document.createElement('canvas');
        c.width = im.naturalWidth; c.height = im.naturalHeight;
        c.getContext('2d').drawImage(im, 0, 0);
        finish(c.toDataURL('image/png'));
      } catch (e) { console.warn('[export] satellite embed failed:', e); finish(null); }
    };
    im.onerror = () => { clearTimeout(timer); console.warn('[export] satellite load failed'); finish(null); };
    im.src = url;
  });
}

// Rewrite every font-family in the export tree to Illustrator-safe fonts so the
// missing-font dialog never appears (SF Mono / JetBrains Mono / system-ui etc.
// are not installed in Illustrator).
function normalizeFonts(node) {
  if (node.nodeType === 1) {
    const safe = ff => /mono/i.test(ff) ? "'Courier New', monospace" : 'Arial, sans-serif';
    const ff = node.getAttribute && node.getAttribute('font-family');
    if (ff) node.setAttribute('font-family', safe(ff));
    // also rewrite any font-family inside an inline style attribute
    const st = node.getAttribute && node.getAttribute('style');
    if (st && /font-family/i.test(st)) {
      node.setAttribute('style',
        st.replace(/font-family\s*:[^;]*/i, 'font-family:' + safe(st)));
    }
    for (const c of node.children) normalizeFonts(c);
  }
}

async function exportSvg() {
  const sad = state.manifest.sads.find(s => s.sad_id === state.currentSadId);
  if (!sad) return;

  const svg = document.getElementById('map');
  const clone = svg.cloneNode(true);

  // Strip the zoom transform so we export in unzoomed canvas coords
  const root = clone.querySelector('#map_root');
  if (root) root.removeAttribute('transform');

  // Force-clip the cropped_content group to the canvas, regardless of the live
  // view's crop mode. Exports always want roads/buildings clipped to the
  // canvas; bleeding into the legend column is a bug in the deliverable.
  try {
    const exportW = svg.clientWidth || 1100;
    const exportH = svg.clientHeight || 800;
    let cp = clone.querySelector('clipPath[id="crop-inside"]');
    if (!cp) {
      const defs = clone.querySelector('defs') ||
                   clone.insertBefore(document.createElementNS(
                     'http://www.w3.org/2000/svg', 'defs'), clone.firstChild);
      cp = document.createElementNS('http://www.w3.org/2000/svg', 'clipPath');
      cp.setAttribute('id', 'crop-inside');
      cp.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'path'));
      defs.appendChild(cp);
    }
    const cpPath = cp.querySelector('path');
    if (cpPath) {
      cpPath.setAttribute('d',
        `M0,0L${exportW},0L${exportW},${exportH}L0,${exportH}Z`);
    }
    const cropped = clone.querySelector('[id="cropped_content"]');
    if (cropped && !cropped.getAttribute('clip-path')) {
      cropped.setAttribute('clip-path', 'url(#crop-inside)');
    }
  } catch (e) { console.warn('[export] clip enforcement skipped:', e); }

  const w = svg.clientWidth || 1100;
  const h = svg.clientHeight || 800;

  clone.setAttribute('xmlns:inkscape', 'http://www.inkscape.org/namespaces/inkscape');

  // Embed the satellite image as a data URI so it travels into Illustrator
  // (an external href would import as a broken/empty link). Wrapped so any
  // failure here can never abort the whole export.
  // NOTE: '#00_satellite' is an invalid CSS selector (IDs can't start with a
  // digit) — use an [id="..."] attribute selector instead.
  try {
    const satImg = clone.querySelector('[id="00_satellite"] image');
    if (satImg) {
      const srcUrl = satImg.getAttribute('href')
        || satImg.getAttributeNS('http://www.w3.org/1999/xlink', 'href');
      if (srcUrl && srcUrl.indexOf('data:') !== 0) {
        const dataUri = await imageUrlToDataUri(srcUrl);
        if (dataUri) satImg.setAttribute('href', dataUri);
      }
    }
  } catch (e) {
    console.warn('[export] satellite embed skipped:', e);
  }

  // Build legend / scale / north chrome beside the map
  const chrome = buildExportChrome(sad, w, h);
  const totalW = w + chrome.width;
  const totalH = Math.max(h, chrome.bottom + 24);

  clone.setAttribute('width', totalW);
  clone.setAttribute('height', totalH);
  clone.setAttribute('viewBox', '0 0 ' + totalW + ' ' + totalH);

  // White page background behind everything (legend reads on white;
  // the map keeps its own dark canvas rect on top).
  const pageBg = svgEl('rect', {
    id: 'page_background', x: 0, y: 0, width: totalW, height: totalH, fill: '#ffffff',
  });
  clone.insertBefore(pageBg, clone.firstChild);

  // Metadata title
  const title = svgEl('title', null,
    (sad.sad_name || sad.sad_id) + ' - SAD viewer export');
  clone.insertBefore(title, clone.firstChild);

  // Append chrome layers as siblings of the map
  chrome.groups.forEach(g => clone.appendChild(g));

  // Data-source attribution — contained within the right-hand legend column
  // (left-anchored, one source per line) so it never overlaps the map. Stacked
  // at the bottom of the column.
  const V = state.layerVisible;
  const srcBits = ['Base map \u00A9 OpenStreetMap'];
  if (V.pois || state.heatmap.on) srcBits.push('POIs \u00A9 Overture Maps');
  if (state.satellite && state.satellite.on) {
    srcBits.push(state.satellite.provider === 'mapbox'
      ? 'Imagery \u00A9 Mapbox, Maxar' : 'Imagery \u00A9 Esri, Maxar');
  }
  const MONO_SAFE = "'Courier New', monospace";
  const attrG = svgEl('g', {
    id: 'attribution', 'inkscape:label': 'attribution', 'inkscape:groupmode': 'layer',
  });
  const lines = srcBits.map(t => ({ text: t, size: 8, fill: '#9a9a9a', sp: '0.02em' }));
  lines.push({
    text: ('Rossetti SAD Viewer  \u00B7  ' + new Date().toISOString().slice(0, 10)).toUpperCase(),
    size: 7.5, fill: '#bdbdbd', sp: '0.06em',
  });
  const lineH = 12;
  const bottomBaseline = totalH - 16;                       // sits near canvas bottom
  const topBaseline = bottomBaseline - (lines.length - 1) * lineH;
  lines.forEach((ln, i) => {
    attrG.appendChild(svgEl('text', {
      x: chrome.attrX, y: topBaseline + i * lineH,          // left-anchored in column
      'text-anchor': 'start', fill: ln.fill,
      'font-family': MONO_SAFE, 'font-size': ln.size, 'letter-spacing': ln.sp,
    }, ln.text));
  });
  clone.appendChild(attrG);

  // Normalize all fonts to Illustrator-safe families (kills the missing-font dialog)
  normalizeFonts(clone);

  // Images: Illustrator renders <image> only via `xlink:href`, not the SVG2
  // plain `href`. Collapse every image to a SINGLE literal xlink:href (no plain
  // href, no duplicate, no `crossorigin` attribute) so it embeds cleanly with no
  // broken-link placeholder. Requires the xmlns:xlink declaration below.
  const XLINK = 'http://www.w3.org/1999/xlink';
  clone.querySelectorAll('image').forEach(im => {
    const uri = im.getAttribute('href')
      || im.getAttributeNS(XLINK, 'href')
      || im.getAttribute('xlink:href');
    im.removeAttribute('href');
    im.removeAttributeNS(XLINK, 'href');
    im.removeAttribute('xlink:href');
    im.removeAttribute('crossorigin');
    if (uri) im.setAttribute('xlink:href', uri);
  });
  clone.setAttribute('xmlns:xlink', XLINK);

  // Label id'd nodes as Illustrator/Inkscape layers
  function addLabels(node) {
    if (node.nodeType === 1 && node.id && !node.hasAttribute('inkscape:label')) {
      node.setAttribute('inkscape:label', node.id);
      node.setAttribute('inkscape:groupmode', 'layer');
    }
    for (const c of node.children) addLabels(c);
  }
  addLabels(clone);

  const xml = new XMLSerializer().serializeToString(clone);
  const blob = new Blob(
    ['<?xml version="1.0" encoding="UTF-8"?>\n', xml],
    { type: 'image/svg+xml' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = sad.sad_id + '_viewer_export.svg';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ─── Resize ──────────────────────────────────────────────────────────────────

let resizeTimer = null;
window.addEventListener('resize', () => {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (!state.currentSadId) return;
    const sad = state.manifest.sads.find(s => s.sad_id === state.currentSadId);
    if (sad) { setupProjection(sad); render(); }
  }, 180);
});

// ─── Expose internals for non-invasive add-ons (viewer_modules.js) ───────────
window.sadViewerState = state;
window.sadViewerRender = render;
