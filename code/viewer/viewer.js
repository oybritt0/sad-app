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
 *   <g id="06_boundary">     (SAD boundary â€” always on top)
 *   <g id="07_chrome">       (scale bar, north arrow â€” in HTML overlay)
 */

'use strict';

// â”€â”€â”€ Constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const ROSSETTI_ORDER = [
  'retail_food_entertainment', 'office', 'sport', 'residential',
  'hotel', 'parking', 'open_space', 'other',
];

// HAZUS occupancy sub-buckets. Only RES/COM/IND expand into sub-types; other
// families (AGR/REL/EDU/GOV) have too few codes to be worth drilling into.
// occResolve() maps a full occtype like "RES1-2SWB" or "COM10" to its family
// ("RES"/"COM") and a merged sub-bucket key ("RES1"/"COM5"). The RES1-* single-
// family variants (story/basement encoded) all collapse to RES1 - that detail
// lives in Height mode, not here, to keep the legend legible.
const OCC_SUB_MAP = {
  RES1:'RES1', RES2:'RES2', RES3:'RES3', RES4:'RES4', RES5:'RES4', RES6:'RES4',
  COM1:'COM1', COM2:'COM2', COM3:'COM2', COM4:'COM4',
  COM5:'COM5', COM6:'COM5', COM7:'COM5', COM8:'COM8', COM9:'COM8', COM10:'COM5',
  IND1:'IND1', IND2:'IND2', IND3:'IND1', IND4:'IND1', IND5:'IND1', IND6:'IND1',
};
const OCC_SUB_LABELS = {
  RES1:'Single-family', RES3:'Multi-family', RES4:'Hotel / institutional', RES2:'Mobile / manuf.',
  COM1:'Retail', COM4:'Office', COM8:'Entertainment', COM2:'Wholesale / service', COM5:'Medical / civic / parking',
  IND2:'Light industrial', IND1:'Heavy / other',
};
const OCC_SUB_BUCKETS = {
  RES:['RES1','RES3','RES4','RES2'],
  COM:['COM1','COM4','COM8','COM2','COM5'],
  IND:['IND2','IND1'],
};
const OCC_SUB_COLORS = {
  RES1:'#8fd4c2', RES3:'#5fb3a1', RES4:'#3d8a7a', RES2:'#2a6359',
  COM1:'#f0a878', COM4:'#e08550', COM8:'#c46a3a', COM2:'#a65520', COM5:'#d4a437',
  IND2:'#9aa3ad', IND1:'#6e7787',
};
const OCC_EXPANDABLE = ['RES','COM','IND'];
// "RES1-2SWB" -> {family:'RES', sub:'RES1'}; "COM10" -> {family:'COM', sub:'COM5'};
// the \d+ is greedy so COM10 reads as COM10 (not COM1) before the sub-map lookup.
function occResolve(occtype) {
  if (!occtype) return { family:'OTHER', sub:null };
  const fm = String(occtype).match(/^([A-Z]+)/);
  const family = fm ? fm[1] : 'OTHER';
  const cm = String(occtype).match(/^([A-Z]+\d+)/);
  const code = cm ? cm[1] : family;
  return { family, sub: OCC_SUB_MAP[code] || null };
}

// POI surfaces use POI_CATEGORIES, NOT ROSSETTI_ORDER. Residential is excluded:
// Overture residential POIs are too sparse/inconsistent. Residential is sourced
// from the OSM `building` tag (see buildingProgram + Module 3b), so it stays in
// ROSSETTI_ORDER for the building-program legend but is removed from POI surfaces.
const POI_CATEGORIES = ROSSETTI_ORDER.filter(c => c !== 'residential');

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
  transit_routes: { label: 'Transit routes',  swatch: 'line',   color: '#5b9bd5' },
  walkshed:     { label: 'Walkshed',           swatch: 'block',  color: '#7BB661' },
};

const M_PER_FT = 0.3048;

// Heatmap gradient stops â€” KPF dark aesthetic, matches M12 amenity density
// (transparent navy at low end -> coral -> KPF yellow -> pale highlight).
const HEAT_STOPS = [
  { t: 0.00, color: 'rgba(40, 40, 120, 0.00)' },   // transparent â€” no data
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

// â”€â”€â”€ Geometry sanitization â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    return g;  // Points / LineStrings / MultiPoints â€” pass through
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

// â”€â”€â”€ State â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const state = {
  manifest: null,
  currentSadId: null,
  data: {},
  layerVisible: {
    sad_boundary: true, buildings: true, parking: true, parks: true,
    roads: true, pois: false, transit: false, transit_routes: false, walkshed: false,
  },
  programOn: Object.fromEntries(ROSSETTI_ORDER.map(k => [k, true])),
  // Building-type filter: which OSM building-tag programs are shown on the map.
  buildingProgramOn: Object.fromEntries(ROSSETTI_ORDER.map(k => [k, true])),
  crop: 'both',
  boundaryColor: '#f1c50c',
  poiSize: 2.6,
  transitSize: 7,
  walkshedColor: '#7BB661',
  walkshedOpacity: 0.22,
  buildingColorMode: 'default',  // 'default' | 'dominant' (POI program) | 'by_occupancy' (NSI HAZUS) | 'by_height' (NSI/FEMA height) | 'by_year' (NSI med_yr_blt)
  // Compound filters - applied independently of which mode is coloring the map.
  // Default values match the slider absolute bounds so a "no constraint" state
  // reads as inactive in the isXxxFilterActive() helpers.
  occupancyFilter: { RES: true, COM: true, IND: true, AGR: true, REL: true, EDU: true, GOV: true, OTHER: true },
  // Sub-bucket filter flags (only consulted when the family is expanded).
  occupancySubFilter: {
    RES1: true, RES3: true, RES4: true, RES2: true,
    COM1: true, COM4: true, COM8: true, COM2: true, COM5: true,
    IND2: true, IND1: true,
  },
  // Which expandable families are currently drilled-open in the legend.
  occExpanded: { RES: false, COM: false, IND: false },
  heightFilter: { min: 3.5, max: 110 },
  yearFilter:   { min: 1900, max: 2020 },
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

// â”€â”€â”€ Bootstrap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
  buildBuildingTypeFilter();
  // OSM building-type filter section is now hidden (the NSI occupancy mode
  // is the primary categorical view). Force all programs ON so the filter
  // logic at renderBuildings() passes every feature.
  const _btf = document.getElementById('building-type-filter');
  if (_btf) _btf.style.display = 'none';
  ['retail_fb','office','sport','residential','hotel','parking','open_space','other']
    .forEach(c => { state.buildingProgramOn = state.buildingProgramOn || {}; state.buildingProgramOn[c] = true; });
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

// â”€â”€â”€ UI population â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
  const activityLayers = ['transit', 'transit_routes'];
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
                  'Â· pois loaded:', !!state.data.pois,
                  'Â· feature count:', state.data.pois ? state.data.pois.features.length : 0);
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

// Building-type filter â€” shows buildings by their OSM `building`-tag program.
// Injected as a sidebar sub-block; reuses .category-checks / .sw-dot styling.
function buildBuildingTypeFilter() {
  let wrap = document.getElementById('building-type-filter');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'building-type-filter';
    wrap.className = 'sub-controls';
    const head = document.createElement('div');
    head.textContent = 'Building types \u00B7 OSM';
    head.style.cssText = 'font-family:var(--mono);font-size:10px;letter-spacing:0.06em;'
      + 'text-transform:uppercase;color:var(--ink-trace);margin:0 0 8px 0;';
    wrap.appendChild(head);
    const ul = document.createElement('ul');
    ul.id = 'building-type-checks';
    ul.className = 'category-checks';
    wrap.appendChild(ul);
    const bulk = document.createElement('div');
    bulk.className = 'bulk-actions';
    const all = document.createElement('span'); all.className='link'; all.textContent='all';
    const sep = document.createTextNode('  \u00B7  ');
    const none = document.createElement('span'); none.className='link'; none.textContent='none';
    all.addEventListener('click', () => {
      ROSSETTI_ORDER.forEach(c => state.buildingProgramOn[c] = true);
      ul.querySelectorAll('input').forEach(cb => cb.checked = true); render();
    });
    none.addEventListener('click', () => {
      ROSSETTI_ORDER.forEach(c => state.buildingProgramOn[c] = false);
      ul.querySelectorAll('input').forEach(cb => cb.checked = false); render();
    });
    bulk.appendChild(all); bulk.appendChild(sep); bulk.appendChild(none);
    wrap.appendChild(bulk);
    const hint = document.createElement('div');
    hint.textContent = 'Type colors show in Building use (OSM) mode';
    hint.style.cssText = 'font-size:10px;color:var(--ink-faint);margin-top:6px;';
    wrap.appendChild(hint);
    const bmode = document.querySelector('input[name="bmode"]');
    const bcolor = document.getElementById('building-color');
    const poiWrap = document.getElementById('poi-filter-wrap');
    const sidebar = document.querySelector('.sidebar');
    if (bmode && bmode.closest('.sub-controls')) {
      const host = bmode.closest('.sub-controls');
      host.parentNode.insertBefore(wrap, host.nextSibling);
    } else if (bcolor && bcolor.closest('section')) {
      bcolor.closest('section').appendChild(wrap);
    } else if (poiWrap) {
      poiWrap.parentNode.insertBefore(wrap, poiWrap);
    } else if (sidebar) {
      sidebar.appendChild(wrap);
    }
  }
  const ul = document.getElementById('building-type-checks');
  ul.innerHTML = '';
  for (const cat of ROSSETTI_ORDER) {
    const li = document.createElement('li');
    li.dataset.cat = cat;
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = 'bldg-type-' + cat;
    cb.checked = state.buildingProgramOn[cat] !== false;
    cb.addEventListener('change', () => {
      state.buildingProgramOn[cat] = cb.checked;
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

function buildPoiCategoryFilter() {
  const ul = document.getElementById('poi-category-checks');
  ul.innerHTML = '';
  for (const cat of POI_CATEGORIES) {
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
    for (const cat of POI_CATEGORIES) {
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
    POI_CATEGORIES.forEach(c => state.programOn[c] = true);
    document.querySelectorAll('#poi-category-checks input').forEach(cb => cb.checked = true);
    render();
  });
  document.getElementById('poi-none').addEventListener('click', () => {
    POI_CATEGORIES.forEach(c => state.programOn[c] = false);
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

  // Building color mode (Anchors = grey + red SAD anchors Â· Dominant POI = program colors)
function renderBuildingLegend() {
  const el = document.getElementById('building-legend');
  if (!el) return;
  const mode = state.buildingColorMode || 'default';
  if (mode === 'default') {
    el.innerHTML = '';
    el.style.display = 'none';
    return;
  }
  el.style.display = '';

  // All three filter sections always rendered. The ACTIVE mode is the one that
  // colors the map; filters from inactive modes still apply (compound filtering).
  // Each section has its own "clear" link; top-level "clear all" appears when
  // any filter is active.
  const occActive = isOccupancyFilterActive();
  const hgtActive = isHeightFilterActive();
  const yrActive  = isYearFilterActive();
  const anyActive = occActive || hgtActive || yrActive;

  const clearAllBar = anyActive
    ? '<div style="display:flex;justify-content:flex-end;margin-bottom:6px;">'
      + '<span class="link" id="bldg-clear-all" style="font-size:10px;cursor:pointer;color:var(--ink-trace);text-decoration:underline;">clear all filters</span>'
      + '</div>'
    : '';

  el.innerHTML = clearAllBar
    + renderOccupancySection(mode === 'by_occupancy', occActive)
    + renderHeightSection(mode === 'by_height', hgtActive)
    + renderYearSection(mode === 'by_year', yrActive);

  // Wire occupancy family checkboxes
  el.querySelectorAll('label[data-occ]').forEach(lbl => {
    const code = lbl.getAttribute('data-occ');
    const cb = lbl.querySelector('input');
    cb.addEventListener('change', () => {
      state.occupancyFilter[code] = cb.checked;
      renderBuildingLegend();   // re-render to refresh active-state styling
      render();
    });
  });
  // Wire occupancy sub-bucket checkboxes (visible only when family expanded)
  el.querySelectorAll('label[data-occ-sub]').forEach(lbl => {
    const sk = lbl.getAttribute('data-occ-sub');
    const cb = lbl.querySelector('input');
    cb.addEventListener('change', () => {
      state.occupancySubFilter[sk] = cb.checked;
      renderBuildingLegend();
      render();
    });
  });
  // Wire expand/collapse carets on RES/COM/IND
  el.querySelectorAll('.occ-caret[data-occ-expand]').forEach(car => {
    const fam = car.getAttribute('data-occ-expand');
    car.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      state.occExpanded[fam] = !state.occExpanded[fam];
      renderBuildingLegend();
      render();   // recolor map: expanded family shows sub-shades
    });
  });
  // Wire range sliders if they exist
  wireRangeSlider('height');
  wireRangeSlider('year');
  // Wire per-section clear links
  const occClear = document.getElementById('bldg-clear-occ');
  if (occClear) occClear.addEventListener('click', () => {
    Object.keys(state.occupancyFilter).forEach(k => state.occupancyFilter[k] = true);
    renderBuildingLegend(); render();
  });
  const hgtClear = document.getElementById('bldg-clear-hgt');
  if (hgtClear) hgtClear.addEventListener('click', () => {
    state.heightFilter.min = 3.5; state.heightFilter.max = 110;
    renderBuildingLegend(); render();
  });
  const yrClear = document.getElementById('bldg-clear-yr');
  if (yrClear) yrClear.addEventListener('click', () => {
    state.yearFilter.min = 1900; state.yearFilter.max = 2020;
    renderBuildingLegend(); render();
  });
  // Wire top-level clear-all
  const clearAll = document.getElementById('bldg-clear-all');
  if (clearAll) clearAll.addEventListener('click', () => {
    Object.keys(state.occupancyFilter).forEach(k => state.occupancyFilter[k] = true);
    state.heightFilter.min = 3.5; state.heightFilter.max = 110;
    state.yearFilter.min = 1900; state.yearFilter.max = 2020;
    renderBuildingLegend(); render();
  });

  // Expose the nested legend function globally so selectSad() can find it.
  // renderBuildingLegend was accidentally declared inside wireControls
  // (block-scoped), making it unreachable from selectSad's module scope.
  window.renderBuildingLegend = renderBuildingLegend;
}

// Status helpers -----------------------------------------------------------
function isOccupancyFilterActive() {
  const f = state.occupancyFilter || {};
  if (Object.values(f).some(v => v === false)) return true;
  // Also active if any sub-bucket is off within an expanded family.
  const sf = state.occupancySubFilter || {};
  const exp = state.occExpanded || {};
  return Object.keys(sf).some(sk => {
    if (sf[sk] !== false) return false;
    const fam = (sk.match(/^([A-Z]+)/) || [])[1];
    return fam && exp[fam];
  });
}
function isHeightFilterActive() {
  const f = state.heightFilter || {};
  return f.min > 3.5 || f.max < 110;
}
function isYearFilterActive() {
  const f = state.yearFilter || {};
  return f.min > 1900 || f.max < 2020;
}

// Section renderers --------------------------------------------------------
function sectionHeader(label, active, isMode, clearId) {
  const modeBadge = isMode
    ? '<span style="font-size:9px;background:var(--ink);color:var(--bg);padding:1px 5px;border-radius:2px;margin-left:6px;letter-spacing:0.04em;">COLORING</span>'
    : '';
  const filterBadge = active && !isMode
    ? '<span style="font-size:9px;background:var(--ink-trace);color:var(--bg);padding:1px 5px;border-radius:2px;margin-left:6px;letter-spacing:0.04em;">FILTER ON</span>'
    : '';
  const clearLink = active
    ? '<span class="link" id="' + clearId + '" style="font-size:9.5px;cursor:pointer;color:var(--ink-trace);text-decoration:underline;margin-left:auto;">clear</span>'
    : '';
  const opacity = (active || isMode) ? 1 : 0.7;
  return '<div style="display:flex;align-items:center;margin:14px 0 5px 0;font-size:10px;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;color:var(--ink);opacity:' + opacity + ';">'
    + '<span>' + label + '</span>'
    + modeBadge + filterBadge + clearLink + '</div>';
}

function renderOccupancySection(isMode, active) {
  const items = [
    ['RES',   '#5fb3a1', 'Residential'],
    ['COM',   '#e08550', 'Commercial'],
    ['IND',   '#6e7787', 'Industrial'],
    ['AGR',   '#8b9a3d', 'Agricultural'],
    ['REL',   '#9b6db3', 'Religious / non-profit'],
    ['EDU',   '#5b8cc4', 'Education'],
    ['GOV',   '#d4a437', 'Government'],
    ['OTHER', '#4a4a4a', 'Unknown / no NSI match'],
  ];
  const dim = !(isMode || active);
  const exp = state.occExpanded || {};
  const subF = state.occupancySubFilter || {};

  const familyRow = (code, c, l) => {
    const on = state.occupancyFilter[code] !== false;
    const canExpand = OCC_EXPANDABLE.indexOf(code) !== -1;
    const caret = canExpand
      ? '<span class="occ-caret" data-occ-expand="' + code + '" title="show sub-types" style="display:inline-block;width:14px;text-align:center;cursor:pointer;color:var(--ink);font-size:11px;user-select:none;">' + (exp[code] ? '\u25bc' : '\u25b6') + '</span>'
      : '<span style="display:inline-block;width:12px;"></span>';
    const row = '<div style="display:flex;align-items:center;gap:4px;margin:2px 0;">'
      + caret
      + '<label data-occ="' + code + '" style="display:flex;align-items:center;gap:6px;cursor:pointer;flex:1;opacity:' + (on ? 1 : 0.35) + ';">'
      + '<input type="checkbox" ' + (on ? 'checked' : '') + ' style="margin:0;">'
      + '<span style="display:inline-block;width:10px;height:10px;background:' + c + ';border:0.5px solid #555;"></span>'
      + '<span style="color:var(--ink);">' + l + '</span></label>'
      + '</div>';
    if (!canExpand || !exp[code]) return row;
    // Drilled-open: indented sub-bucket rows for this family.
    const subs = (OCC_SUB_BUCKETS[code] || []).map(sk => {
      const son = subF[sk] !== false;
      const sc = OCC_SUB_COLORS[sk] || c;
      const sl = OCC_SUB_LABELS[sk] || sk;
      return '<label data-occ-sub="' + sk + '" style="display:flex;align-items:center;gap:6px;margin:1px 0 1px 28px;cursor:pointer;opacity:' + (son ? 1 : 0.35) + ';">'
        + '<input type="checkbox" ' + (son ? 'checked' : '') + ' style="margin:0;">'
        + '<span style="display:inline-block;width:9px;height:9px;background:' + sc + ';border:0.5px solid #555;"></span>'
        + '<span style="color:var(--ink-trace);font-size:10px;">' + sl + '</span></label>';
    }).join('');
    return row + subs;
  };

  return sectionHeader('HAZUS occupancy', active, isMode, 'bldg-clear-occ')
    + '<div style="opacity:' + (dim ? 0.7 : 1) + ';">'
    + items.map(([code, c, l]) => familyRow(code, c, l)).join('')
    + '</div>';
}

function renderHeightSection(isMode, active) {
  const dim = !(isMode || active);
  let body = sectionHeader('Building height (m)', active, isMode, 'bldg-clear-hgt');
  body += '<div style="opacity:' + (dim ? 0.7 : 1) + ';">';
  if (isMode) {
    body += buildGradientStrip('viridis')
      + '<div style="display:flex;justify-content:space-between;margin-top:3px;font-size:9.5px;color:var(--ink-trace);">'
      + '<span>~1 story (3m)</span><span>~22+ stories (80m+)</span></div>';
  }
  body += buildRangeSlider('height', 3.5, 110, 0.5, state.heightFilter.min, state.heightFilter.max, 'm');
  body += '</div>';
  return body;
}

function renderYearSection(isMode, active) {
  const dim = !(isMode || active);
  const feats = (state.data.buildings && state.data.buildings.features) || [];
  const yearVals = new Set();
  feats.forEach(d => {
    const v = (d.properties || {}).med_yr_blt;
    if (v != null && !isNaN(v)) yearVals.add(Math.round(v));
  });
  const sortedYears = Array.from(yearVals).sort((a, b) => a - b);
  const yearListStr = sortedYears.length ? sortedYears.join(', ') : '(no NSI year data)';
  const eras = [
    ['#a85d3a', 'Pre-war (≤1945)'],
    ['#9ca96b', 'Mid-century (1946–1980)'],
    ['#4a9b8f', 'Contemporary (1981+)'],
    ['#4a4a4a', 'Unknown'],
  ];
  let body = sectionHeader('Year built · era bands', active, isMode, 'bldg-clear-yr');
  body += '<div style="opacity:' + (dim ? 0.7 : 1) + ';">';
  if (isMode) {
    body += eras.map(([c, l]) =>
        '<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">'
        + '<span style="display:inline-block;width:10px;height:10px;background:' + c + ';border:0.5px solid #555;"></span>'
        + '<span style="color:var(--ink);">' + l + '</span></div>'
      ).join('')
      + '<div style="margin-top:8px;font-size:9.5px;color:var(--ink-faint);line-height:1.4;">NSI year-built is parcel-inferred and heavily binned. Actual values present in this district: <span style="color:var(--ink-trace);">' + yearListStr + '</span></div>';
  }
  body += buildRangeSlider('year', 1900, 2020, 1, state.yearFilter.min, state.yearFilter.max, '');
  body += '</div>';
  return body;
}

// Two stacked native range inputs for min and max. Simple, zero-dependency.
function buildRangeSlider(name, absMin, absMax, step, curMin, curMax, suffix) {
  return ''
    + '<div style="margin-top:10px;font-size:10px;text-transform:uppercase;letter-spacing:0.06em;color:var(--ink-trace);">Filter range</div>'
    + '<div style="display:flex;justify-content:space-between;font-size:10.5px;color:var(--ink);margin:2px 0 4px 0;">'
    + '<span id="' + name + '-min-lbl">' + curMin + suffix + '</span>'
    + '<span id="' + name + '-max-lbl">' + curMax + suffix + '</span></div>'
    + '<input id="' + name + '-min" type="range" min="' + absMin + '" max="' + absMax + '" step="' + step + '" value="' + curMin + '" style="width:100%;display:block;margin:2px 0;">'
    + '<input id="' + name + '-max" type="range" min="' + absMin + '" max="' + absMax + '" step="' + step + '" value="' + curMax + '" style="width:100%;display:block;margin:2px 0;">';
}

function wireRangeSlider(name) {
  const minEl = document.getElementById(name + '-min');
  const maxEl = document.getElementById(name + '-max');
  const minLbl = document.getElementById(name + '-min-lbl');
  const maxLbl = document.getElementById(name + '-max-lbl');
  if (!minEl || !maxEl) return;
  const filterKey = name + 'Filter';
  const suffix = (name === 'height') ? 'm' : '';
  function update() {
    let lo = parseFloat(minEl.value);
    let hi = parseFloat(maxEl.value);
    if (lo > hi) { // clamp - never let min exceed max
      if (this === minEl) { hi = lo; maxEl.value = hi; }
      else { lo = hi; minEl.value = lo; }
    }
    state[filterKey].min = lo;
    state[filterKey].max = hi;
    minLbl.textContent = (Math.round(lo * 10) / 10) + suffix;
    maxLbl.textContent = (Math.round(hi * 10) / 10) + suffix;
    render();
  }
  minEl.addEventListener('input', update);
  maxEl.addEventListener('input', update);
}

function buildGradientStrip(scale) {
  // Render a 9-stop CSS linear-gradient inline for the legend bar.
  const interp = (scale === 'viridis') ? d3.interpolateViridis
                : (scale === 'puor_rev') ? (t => d3.interpolatePuOr(1 - t))
                : d3.interpolateViridis;
  const stops = [];
  for (let i = 0; i <= 8; i++) stops.push(interp(i / 8));
  const grad = 'linear-gradient(to right, ' + stops.join(', ') + ')';
  return '<div style="height:10px;background:' + grad + ';border:0.5px solid #555;"></div>';
}
  document.querySelectorAll('input[name="bmode"]').forEach(el => {
    el.addEventListener('change', e => {
      if (e.target.checked) {
        state.buildingColorMode = e.target.value;
        renderBuildingLegend();
        render();
      }
    });
  });
  // Initial render of the building legend (in case the current mode isn't default)
  renderBuildingLegend();
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

// â”€â”€â”€ SAD loading â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    // Heatmap is derived from POI data â€” enable it whenever POIs are loaded.
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
      const shown = state.data.pois.features.filter(f =>
        ((f.properties && f.properties.rossetti_category) || 'other') !== 'residential').length;
      poiCountEl.textContent = '(' + shown + ')';
    } else {
      poiCountEl.textContent = '(0)';
    }
  }

  document.getElementById('loading').classList.add('hide');
  // Re-render the building legend so the year-built list (and any mode-aware
  // bits) reflect the newly-loaded district's data, not the previous one.
  // Uses window. lookup because renderBuildingLegend was accidentally declared
  // inside wireControls (block-scoped) - selectSad runs in module scope and
  // can only see it via the explicit window exposure set at wireControls' end.
  if (typeof window.renderBuildingLegend === 'function') window.renderBuildingLegend();
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
    const latS = (lat >= 0 ? lat.toFixed(4) + 'Â°N' : (-lat).toFixed(4) + 'Â°S');
    const lngS = (lng >= 0 ? lng.toFixed(4) + 'Â°E' : (-lng).toFixed(4) + 'Â°W');
    indexEl.textContent = latS + '  ' + lngS;
  } else {
    indexEl.textContent = '—';
  }
}

// â”€â”€â”€ Projection setup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€â”€ Rendering â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function render() {
  const svg = d3.select('#map');
  svg.selectAll('*').remove();
  if (!state.currentSadId || !state.projection) return;

  // â”€â”€ Defs (clipPaths, masks)
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

  // â”€â”€ 01: Background
  const bgG = svg.append('g').attr('id', '01_background');
  const w = svg.node().clientWidth || 800;
  const h = svg.node().clientHeight || 600;
  bgG.append('rect').attr('x', 0).attr('y', 0)
     .attr('width', w).attr('height', h)
     .attr('fill', state.canvasBg || '#0a0a0a');

  // â”€â”€ Map root (zoom transform applies here)
  const root = svg.append('g').attr('id', 'map_root')
                  .attr('transform', state.transform);

  // â”€â”€ 00: Satellite backdrop (under everything, pans/zooms with the map)
  if (state.satellite.on && state.bbox) {
    renderSatellite(root.append('g').attr('id', '00_satellite'));
  }

  // â”€â”€ Cropped content
  const cropped = root.append('g').attr('id', 'cropped_content');
  if (state.crop === 'inside' && sadGj) {
    cropped.attr('clip-path', 'url(#crop-inside)');
  } else if (state.crop === 'outside' && sadGj) {
    cropped.attr('mask', 'url(#crop-outside)');
  }

  // â”€â”€ 02: Base map layers (parks, parking, roads)
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

  // â”€â”€ 04: Buildings (rendered before the analysis overlay)
  if (state.layerVisible.buildings && state.data.buildings) {
    const blgG = cropped.append('g').attr('id', '04_buildings');
    renderBuildings(blgG.append('g').attr('id', 'buildings'),
                     state.data.buildings);
  }

  // â”€â”€ 03: Analysis overlay (heatmap, walkshed) â€” drawn ABOVE buildings so the
  //        density core near the venues is not hidden under stadium footprints
  const anaG = cropped.append('g').attr('id', '03_analysis');
  if (state.heatmap.on && state.data.pois) {
    renderHeatmap(anaG.append('g').attr('id', 'heatmap'));
  }
  if (state.layerVisible.walkshed && state.data.walkshed) {
    renderWalkshed(anaG.append('g').attr('id', 'walkshed'),
                    state.data.walkshed);
  }

  // â”€â”€ 05: Activity layers (POIs subgrouped by category, transit)
  const actG = cropped.append('g').attr('id', '05_activity');
  if (state.layerVisible.pois && state.data.pois) {
    renderPois(actG.append('g').attr('id', 'pois'), state.data.pois);
  }
  if (state.layerVisible.transit && state.data.transit) {
    renderTransit(actG.append('g').attr('id', 'transit'),
                   state.data.transit);
  }
  if (state.layerVisible.transit_routes && state.data.transit_routes) {
    renderTransitRoutes(actG.append('g').attr('id', 'transit_routes'),
                   state.data.transit_routes);
  }

  // â”€â”€ 06: SAD boundary (always on top, NOT cropped)
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

// â”€â”€â”€ Layer renderers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function isProjectionSane(feature) {
  // Drop features whose projected bbox is >10Ã— the viewport in either
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
    // Outline mode â€” stroke with the layer's own color, no fill (shows satellite)
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

  // Labels â€” one per polygon, centered above the polygon top edge.
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
    console.warn('[walkshed] no labelable properties â€” feature[0] props:',
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

// OSM `building` tag -> Rossetti program. Single source of truth for both
// classifiers below.
const BUILDING_ROOF_TO_PROG = {
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

// Tag first, then dominant-POI fallback, then 'other'. Used by the stadium
// classifier (which wants a best-guess program for every building).
function buildingProgram(props) {
  const tag = (props.building || '').toLowerCase();
  if (BUILDING_ROOF_TO_PROG[tag]) return BUILDING_ROOF_TO_PROG[tag];
  const dom = (props.dominant_program_inside || '').toLowerCase();
  if (dom) return dom;
  return 'other';
}

// OSM tag ONLY -- no POI fallback. Returns null when the building is untagged
// or its tag isn't recognized, so 'by use' coloring/filtering can treat those
// as neutral 'other' (grey) rather than guessing from nearby POIs.
function buildingTagProgram(props) {
  return BUILDING_ROOF_TO_PROG[(props.building || '').toLowerCase()] || null;
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

// Combined classifier â€” populates state.stadiumSet once per SAD load.
//
// Requires a COMBINATION, never a single signal:
//   â€¢ Morphology prerequisite: footprint >= MIN_AREA  (excludes small gyms; parks
//     aren't in the buildings layer at all, and open_space-program buildings are
//     explicitly rejected)
//   AND one confirming sport signal:
//     â€¢ an explicit venue tag (building=stadium/arena, leisure/amenity=stadium,
//       sport=*, or pipeline program=sport), OR
//     â€¢ at least one sport-category POI located INSIDE the footprint (POI analysis)
//   â€¢ Narrow morphology-only fallback: a very large AND highly compact enclosed
//     form (dome/arena) â€” gated tightly so convention centers / warehouses
//     (large but rectangular, low compactness) and parks never qualify.
const MIN_STADIUM_AREA = 4000;     // m^2 â€” major-venue floor
const HUGE_STADIUM_AREA = 22000;   // m^2 â€” enclosed-arena fallback floor
const HUGE_COMPACTNESS = 0.55;     // Polsby-Popper for the fallback
const MASSIVE_STADIUM_AREA = 28000; // m^2 â€” at this size it's a venue regardless

function classifyStadium(building, sportInside) {
  const props = building.properties || {};
  const area = buildingAreaM2(building);
  // Morphology prerequisite
  if (area < MIN_STADIUM_AREA) return false;
  // Never flag open space / parks / parking
  const prog = buildingProgram(props);
  if (prog === 'open_space' || prog === 'parking') return false;

  // Sport signal A â€” explicit tags
  const b = (props.building || '').toLowerCase();
  const leis = (props.leisure || '').toLowerCase();
  const amen = (props.amenity || '').toLowerCase();
  const hasTag = b === 'stadium' || b === 'arena' || b === 'sports_centre'
              || b === 'sports_hall' || leis === 'stadium' || amen === 'stadium'
              || !!props.sport || prog === 'sport';
  if (hasTag) return true;                      // large + explicit sport tag

  // Sport signal B â€” POI analysis (sport POI physically inside footprint)
  if (sportInside >= 1) return true;            // large + sport POI inside

  // Morphology fallback â€” enclosed arena/dome form
  if (area > HUGE_STADIUM_AREA && compactness(building) > HUGE_COMPACTNESS) return true;

  // Massive footprint â€” at 28,000+ m^2 a non-open-space building in a SAD is a
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
  console.log('[stadia] ' + set.size + ' venues Â· tag+area: ' + byTag
              + ', POI+area: ' + byPoi + ', shape+area: ' + byShape);
}

// â”€â”€ Planar point-in-polygon (winding-agnostic, robust to wrapper-ring artifacts)
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
  if (rings.length === 0) return () => true;  // no usable boundary â†’ keep all
  return (pt) => {
    let inside = false;
    for (const ring of rings) if (pointInRing(pt, ring)) inside = !inside;
    return inside;
  };
}

// Of all detected venues, keep only those whose centroid falls inside the SAD
// boundary â€” these are the district anchors that get colored red.
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
  let feats = filterSane(gj.features);
  const sadStadia = state.sadStadiumSet || new Set();
  // Building-type filter â€” hide programs the user unchecked. Anchors are
  // sport-class, so they follow the 'sport' toggle.
  const bProgOn = state.buildingProgramOn || {};
  feats = feats.filter(d => {
    const prog = sadStadia.has(d) ? 'sport'
                 : (buildingTagProgram(d.properties || {}) || 'other');
    return bProgOn[prog] !== false;
  });

  // NSI/FEMA filters: applied INDEPENDENTLY of which mode is coloring the map.
  // This lets a user filter by occupancy AND year AND height simultaneously
  // (e.g. "pre-war commercial buildings between 10-30m"). Filters are only
  // applied if they're actively constraining (any non-default state).
  const _mode = state.buildingColorMode || 'default';
  if (_mode !== 'default') {
    const occF = state.occupancyFilter || {};
    const hf = state.heightFilter || {};
    const yf = state.yearFilter || {};
    const _subF = state.occupancySubFilter || {};
    const _exp = state.occExpanded || {};
    // Occupancy filter is active if any family is off, OR any sub-bucket is off
    // within a currently-expanded family (so sub-toggles take effect even when
    // every top-level family stays checked).
    const occActive = Object.values(occF).some(v => v === false)
      || Object.keys(_subF).some(sk => {
           if (_subF[sk] !== false) return false;
           const fam = (sk.match(/^([A-Z]+)/) || [])[1];
           return fam && _exp[fam];
         });
    const hgtActive = (hf.min != null && hf.min > 3.5) || (hf.max != null && hf.max < 110);
    const yrActive  = (yf.min != null && yf.min > 1900) || (yf.max != null && yf.max < 2020);
    // Occupancy filter
    if (occActive) {
      const subF = state.occupancySubFilter || {};
      const exp = state.occExpanded || {};
      feats = feats.filter(d => {
        const occ = (d.properties || {}).occtype;
        if (!occ) return occF.OTHER !== false;
        const { family, sub } = occResolve(occ);
        if (occF[family] === false) return false;
        // Sub-bucket gating only applies when the family is drilled-open.
        if (exp[family] && sub && subF[sub] === false) return false;
        return true;
      });
    }
    // Height filter
    if (hgtActive) {
      feats = feats.filter(d => {
        const p = d.properties || {};
        const v = (p.fema_height != null) ? p.fema_height : p.height_m_est;
        if (v == null || isNaN(v)) return false;
        return v >= hf.min && v <= hf.max;
      });
    }
    // Year filter
    if (yrActive) {
      feats = feats.filter(d => {
        const v = (d.properties || {}).med_yr_blt;
        if (v == null || isNaN(v)) return false;
        return v >= yf.min && v <= yf.max;
      });
    }
  }
  const mode = state.buildingColorMode || 'default';
  const dominant = mode === 'dominant';
  const byOccupancy = mode === 'by_occupancy';
  const byHeight = mode === 'by_height';
  const byYear = mode === 'by_year';
  const grey = state.buildingColor || '#4a4a4a';
  const outline = state.buildingStyle === 'outline';
  const fillOpac = state.buildingFillOpacity != null ? state.buildingFillOpacity : 0.92;
  const owt = state.buildingOutlineWeight != null ? state.buildingOutlineWeight : 0.6;
  const ocol = state.buildingOutlineColor || '#888888';

  // HAZUS occupancy class -> color (grouped by first letter of code:
  // RES=teal, COM=orange, IND=slate, AGR=olive, REL=purple, EDU=blue,
  // GOV=mustard, others=grey)
  const OCC_COLORS = {
    RES: '#5fb3a1', COM: '#e08550', IND: '#6e7787',
    AGR: '#8b9a3d', REL: '#9b6db3', EDU: '#5b8cc4',
    GOV: '#d4a437',
  };
  function occColor(occtype) {
    if (!occtype) return grey;
    const { family, sub } = occResolve(occtype);
    // When this family is drilled-open, color by its sub-bucket shade so the
    // map shows internal structure; otherwise use the flat family color.
    if (sub && state.occExpanded && state.occExpanded[family] && OCC_SUB_COLORS[sub]) {
      return OCC_SUB_COLORS[sub];
    }
    return OCC_COLORS[family] || grey;
  }

  // Continuous ramps for height & year (cool->warm). Domains capped at
  // values useful for downtown SADs; outliers clamp.
  const HEIGHT_DOMAIN = [3, 80];  // ~1 story to ~22 stories; clamps above
  const YEAR_DOMAIN = [1900, 2020];
  function heightColor(v) {
    if (v == null || isNaN(v)) return grey;
    const t = Math.max(0, Math.min(1, (v - HEIGHT_DOMAIN[0]) / (HEIGHT_DOMAIN[1] - HEIGHT_DOMAIN[0])));
    return d3.interpolateViridis(t);
  }
  // Year built: NSI data is heavily binned at the parcel level (often just a
  // few default years like 1938/1946/2000 per region). Use discrete era bands
  // rather than a continuous gradient to honestly represent the granularity.
  const YEAR_ERA_COLORS = {
    prewar:        '#a85d3a',   // rust - pre-1946
    midcentury:    '#9ca96b',   // sage - 1946-1980
    contemporary:  '#4a9b8f',   // teal - 1981+
  };
  function yearEraOf(v) {
    if (v == null || isNaN(v)) return null;
    if (v <= 1945) return 'prewar';
    if (v <= 1980) return 'midcentury';
    return 'contemporary';
  }
  function yearColor(v) {
    const era = yearEraOf(v);
    return era ? YEAR_ERA_COLORS[era] : grey;
  }

  function baseColor(d) {
    const props = d.properties || {};
    if (dominant) {
      const p = buildingTagProgram(props);
      return p ? PROGRAM_COLORS[p] : grey;
    }
    if (byOccupancy) return occColor(props.occtype);
    if (byHeight) return heightColor(props.fema_height != null ? props.fema_height : props.height_m_est);
    if (byYear) return yearColor(props.med_yr_blt);
    return grey;
  }

  const sel = g.selectAll('path').data(feats).join('path').attr('d', state.pathGen);

  if (outline) {
    // Outline mode â€” no fill so satellite/base shows through; anchors stay red.
    // Stroke follows the active color mode (program colors in Dominant POI).
    sel.attr('fill', 'none')
       .attr('stroke', d => {
         const props = d.properties || {};
         if (dominant) {
           const p = buildingTagProgram(props);
           return p ? PROGRAM_COLORS[p] : ocol;
         }
         if (byOccupancy) return occColor(props.occtype) || ocol;
         if (byHeight)    return heightColor(props.fema_height != null ? props.fema_height : props.height_m_est) || ocol;
         if (byYear)      return yearColor(props.med_yr_blt) || ocol;
         return ocol;
       })
       .attr('stroke-width', owt)
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
    if (cat === 'residential') continue;  // residential is building-derived, not a POI layer
    if (state.programOn[cat] === false) continue;
    (byCat[cat] = byCat[cat] || []).push(f);
  }
  for (const cat of POI_CATEGORIES) {
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
      return nm ? (nm + '  Â·  ' + PROGRAM_LABELS[cat]) : PROGRAM_LABELS[cat];
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

// â”€â”€â”€ Transit routes (GTFS shapes, colored by mode) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
  sel.append('title').text(d => {
    const p = d.properties || {};
    const nm = p.short_name || p.long_name || p.route_id || 'route';
    const md = p.mode || 'other';
    return nm + ' (' + md + ')' + (p.served ? ' - enters SAD' : '');
  });
}

// â”€â”€â”€ Heatmap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function renderHeatmap(g) {
  if (!state.data.pois) return;
  const cat = state.heatmap.category;
  const pts = (state.data.pois.features || []).filter(f => {
    if (!f.geometry || f.geometry.type !== 'Point') return false;
    const rc = (f.properties && f.properties.rossetti_category) || 'other';
    if (cat === '__all') return rc !== 'residential';  // residential excluded from POI surfaces
    return rc === cat;
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
  // hex fill + numeric fill-opacity â€” Illustrator does NOT parse rgba() in the
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

// â”€â”€â”€ Zoom + pan â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€â”€ Scale chrome â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

// â”€â”€â”€ SVG export â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs, text) {
  const n = document.createElementNS(SVGNS, tag);
  if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  return n;
}

// Base-scale pixels-per-foot (zoom independent â€” export is unzoomed)
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

  // Title block â€” wrap to the column width so long SAD names don't run off the
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
    }, subBits.join('  Â·  ').toUpperCase()));
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

  // â”€â”€ LAYERS section --------------------------------------------------------
  const V = state.layerVisible;
  sectionHeader('LAYERS');

  if (V.sad_boundary) dashRow(state.boundaryColor || '#f1c50c', 'SAD boundary');

  if (V.buildings && state.data.buildings) {
    const mode = state.buildingColorMode || 'default';

    if (mode === 'by_occupancy') {
      // HAZUS occupancy buckets (NSI) - categorical legend
      blockRow('#cccccc', 'Buildings â€” by occupancy (NSI)', { outline: outlineMode, stroke: '#bbb', strokeWidth: 0.5 });
      const occBuckets = [
        ['RES', '#5fb3a1', 'Residential'],
        ['COM', '#e08550', 'Commercial'],
        ['IND', '#6e7787', 'Industrial'],
        ['AGR', '#8b9a3d', 'Agricultural'],
        ['REL', '#9b6db3', 'Religious / non-profit'],
        ['EDU', '#5b8cc4', 'Education'],
        ['GOV', '#d4a437', 'Government'],
      ];
      occBuckets.forEach(([code, color, label]) => {
        const yc = y;
        legend.appendChild(svgEl('rect', {
          x: cx + 10, y: yc, width: SW - 4, height: SW - 4,
          fill: outlineMode ? 'none' : color,
          stroke: color, 'stroke-width': outlineMode ? 1.4 : 0,
        }));
        legend.appendChild(svgEl('text', {
          x: LBL + 6, y: yc + (SW - 4) * 0.78, 'font-family': SANS,
          'font-size': 10.5, fill: '#444',
        }, label));
        y += ROW - 4;
      });
    } else if (mode === 'by_height') {
      // Continuous gradient: height in meters
      blockRow('#cccccc', 'Buildings â€” by height (m)', { outline: outlineMode, stroke: '#bbb', strokeWidth: 0.5 });
      drawGradientBar('viridis', 3.5, 60, 'm', '~1 story', '~17+ stories');
    } else if (mode === 'by_year') {
      // Continuous gradient: year built
      blockRow('#cccccc', 'Buildings â€” by year built', { outline: outlineMode, stroke: '#bbb', strokeWidth: 0.5 });
      drawGradientBar('puor_rev', 1900, 2020, '', 'pre-1900', '2020+');
    } else {
      // Default mode (Standard) - just shows the building color
      blockRow(state.buildingColor || '#4a4a4a', 'Buildings', { outline: outlineMode });
    }
  }

  // Helper: draw a continuous gradient bar legend (for height/year modes)
  function drawGradientBar(scale, domainMin, domainMax, suffix, lowLabel, highLabel) {
    const barW = 100;
    const barH = 8;
    const barX = cx + 10;
    const barY = y + 2;
    // Build a gradient id unique per call
    const gradId = 'legend-grad-' + scale + '-' + Math.floor(Math.random() * 1e6);
    const defs = svgEl('defs', {});
    const grad = svgEl('linearGradient', { id: gradId, x1: '0%', y1: '0%', x2: '100%', y2: '0%' });
    // Sample 8 stops along the ramp
    const interp = (scale === 'viridis') ? d3.interpolateViridis :
                   (scale === 'puor_rev') ? (t => d3.interpolatePuOr(1 - t)) :
                   d3.interpolateViridis;
    for (let i = 0; i <= 8; i++) {
      const t = i / 8;
      grad.appendChild(svgEl('stop', {
        offset: (t * 100) + '%',
        'stop-color': interp(t),
      }));
    }
    defs.appendChild(grad);
    legend.appendChild(defs);
    legend.appendChild(svgEl('rect', {
      x: barX, y: barY, width: barW, height: barH,
      fill: 'url(#' + gradId + ')',
      stroke: '#888', 'stroke-width': 0.5,
    }));
    // Low + high labels under the bar
    legend.appendChild(svgEl('text', {
      x: barX, y: barY + barH + 11, 'font-family': SANS,
      'font-size': 9.5, fill: '#666',
    }, lowLabel + ' (' + domainMin + suffix + ')'));
    legend.appendChild(svgEl('text', {
      x: barX + barW, y: barY + barH + 11, 'font-family': SANS,
      'font-size': 9.5, fill: '#666', 'text-anchor': 'end',
    }, highLabel + ' (' + domainMax + suffix + ')'));
    y += ROW + 10;
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

  // â”€â”€ POI categories (only those toggled on) --------------------------------
  if (V.pois && state.data.pois) {
    const cats = POI_CATEGORIES.filter(c => state.programOn[c] !== false);
    if (cats.length) {
      y += 6; sectionHeader('POI CATEGORIES');
      cats.forEach(c => dotRow(PROGRAM_COLORS[c], PROGRAM_LABELS[c]));
    }
  }

  // â”€â”€ Heatmap note ----------------------------------------------------------
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

  // â”€â”€ divider before chrome -------------------------------------------------
  y += 10;
  legend.appendChild(svgEl('line', {
    x1: cx, y1: y, x2: x0 + LEG_W - PAD, y2: y,
    stroke: '#e0e0e0', 'stroke-width': 1,
  }));
  y += 28;

  const groups = [legend];
  const colInner = LEG_W - PAD * 2;

  // â”€â”€ Scale bar (own layer) â€” sized to fit WITHIN the column ----------------
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

  // â”€â”€ North arrow (own layer) â€” stacked below the scale bar, centered -------
  y += 30;                                  // clear gap, no collision
  const northG = svgEl('g', {
    id: 'north_arrow', 'inkscape:label': 'north_arrow', 'inkscape:groupmode': 'layer',
  });
  const ncx = x0 + LEG_W / 2;               // column center (away from scale label)
  const nTop = y;                           // "N" baseline
  const aTip = y + 8, aBot = y + 32;        // arrowhead tip â†’ base
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
// Export tuning â€” keep the exported SVG small enough for Illustrator to open.
const EXPORT_SAT_MAX_EDGE = 2400;      // px â€” cap embedded satellite long edge
const EXPORT_SAT_JPEG_QUALITY = 0.85;  // embedded satellite JPEG quality
const EXPORT_COORD_DECIMALS = 2;       // coordinate precision (pixels) in the SVG

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
        // Cap long edge + JPEG. A full-res PNG of tiled satellite imagery is
        // tens of MB of base64 and crashes Illustrator on import; a capped JPEG
        // is ~10-20x smaller and embeds cleanly (imagery is photographic).
        const nw = im.naturalWidth, nh = im.naturalHeight;
        const longEdge = Math.max(nw, nh) || 1;
        const scale = longEdge > EXPORT_SAT_MAX_EDGE ? EXPORT_SAT_MAX_EDGE / longEdge : 1;
        const w = Math.max(1, Math.round(nw * scale));
        const h = Math.max(1, Math.round(nh * scale));
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        c.getContext('2d').drawImage(im, 0, 0, w, h);
        const uri = c.toDataURL('image/jpeg', EXPORT_SAT_JPEG_QUALITY);
        console.log('[export] satellite ' + nw + 'x' + nh + ' -> ' + w + 'x' + h
                    + ' jpeg (~' + Math.round(uri.length / 1024) + ' KB base64)');
        finish(uri);
      } catch (e) { console.warn('[export] satellite embed failed:', e); finish(null); }
    };
    im.onerror = () => { clearTimeout(timer); console.warn('[export] satellite load failed'); finish(null); };
    im.src = url;
  });
}

// Reduce coordinate precision on the export clone. Full-precision floats across
// thousands of paths + POI circles balloon the file and choke Illustrator's
// parser. Rounding projected PIXEL coords to a couple decimals is sub-pixel
// (visually lossless) and shrinks the SVG ~50-70%. Mirrors viewer_export_shrink.js.
function roundSvgCoords(node) {
  const numRe = /-?\d*\.?\d+(?:e[-+]?\d+)?/gi;
  const roundNum = m => {
    const n = parseFloat(m);
    return Number.isFinite(n) ? String(+n.toFixed(EXPORT_COORD_DECIMALS)) : m;
  };
  // Geometry only â€” NOT stroke-width / font-size, so hairlines/type stay intact.
  const GEOM_ATTRS = ['d', 'points', 'transform', 'cx', 'cy', 'x', 'y',
                      'width', 'height', 'r', 'rx', 'ry', 'x1', 'y1', 'x2', 'y2'];
  (function walk(el) {
    if (el.nodeType !== 1) return;
    for (const a of GEOM_ATTRS) {
      const v = el.getAttribute(a);
      if (v) el.setAttribute(a, v.replace(numRe, roundNum));
    }
    for (const c of el.children) walk(c);
  })(node);
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
  // digit) â€” use an [id="..."] attribute selector instead.
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

  // Data-source attribution â€” contained within the right-hand legend column
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

  // Shrink coordinates so Illustrator can open the result (see roundSvgCoords).
  roundSvgCoords(clone);

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

// â”€â”€â”€ Resize â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

let resizeTimer = null;
window.addEventListener('resize', () => {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (!state.currentSadId) return;
    const sad = state.manifest.sads.find(s => s.sad_id === state.currentSadId);
    if (sad) { setupProjection(sad); render(); }
  }, 180);
});

// â”€â”€â”€ Expose internals for non-invasive add-ons (viewer_modules.js) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
window.sadViewerState = state;
window.sadViewerRender = render;






