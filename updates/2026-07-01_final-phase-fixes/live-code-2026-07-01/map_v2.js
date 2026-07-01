// Force Leaflet.draw to English regardless of browser locale
if (window.L && L.drawLocal) {
  L.drawLocal.draw.toolbar.actions = { title: 'Cancel drawing', text: 'Cancel' };
  L.drawLocal.draw.toolbar.finish = { title: 'Finish drawing', text: 'Finish' };
  L.drawLocal.draw.toolbar.undo = { title: 'Delete last point drawn', text: 'Delete last point' };
  L.drawLocal.draw.toolbar.buttons = { polyline: 'Draw a polyline', polygon: 'Draw a district', rectangle: 'Draw a rectangle', circle: 'Draw a circle', marker: 'Draw a marker', circlemarker: 'Draw a circlemarker' };
  L.drawLocal.draw.handlers.polygon = { tooltip: { start: 'Click to start drawing shape.', cont: 'Click to continue drawing shape.', end: 'Click first point to close this shape.' } };
  L.drawLocal.edit.toolbar.actions = { save: { title: 'Save changes', text: 'Save' }, cancel: { title: 'Cancel editing, discards all changes', text: 'Cancel' }, clearAll: { title: 'Clear all layers', text: 'Clear All' } };
  L.drawLocal.edit.toolbar.buttons = { edit: 'Edit layers', editDisabled: 'No layers to edit', remove: 'Delete layers', removeDisabled: 'No layers to delete' };
}
// map.js â€” Synthesis Map (unified map-first view)
// One Leaflet window: select an existing district or draw a new one; layers off
// until toggled; comparison expands in the right panel. Reuses the corpus
// manifest, the /analyze + /places endpoints, and the viewer's layer manifest.

const MATCH_API = 'http://localhost:8000';

// â€”â‚¬â€”â‚¬ color + format helpers (shared vocabulary with the field) â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
const TYPO = {
  entertainment: 0xe2674f, community: 0x5fae7e, innovation: 0x5688c4,
  'sports park': 0xd2a23f, tourism: 0xc98a3a, mixed: 0x8f78c9, default: 0x9a9088,
};
const typoKey = t => (t == null ? '' : String(t)).trim().toLowerCase();
const typoColor = t => TYPO[typoKey(t)] ?? TYPO.default;
const hex = n => '#' + (n >>> 0).toString(16).padStart(6, '0');
function fmt(v, kind) {
  if (v == null || v === '') return '\u2014';
  const n = Number(v); if (!isFinite(n)) return String(v);
  switch (kind) {
    case 'usd': return '$' + Math.round(n).toLocaleString();
    case 'pct': return n.toFixed(0) + '%';
    case 'num1': return n.toFixed(1);
    case 'int': return Math.round(n).toLocaleString();
    default: return String(v);
  }
}
const shortName = r => {
  const parts = r.sad_id.split('_');
  const drawn = /drawn/i.test(r.sad_id) || /^drawn[\s-]?district$/i.test((r.sad_name || '').trim());
  if (drawn) { const loc = (parts[2] || parts[1] || '').replace(/-/g, ' ').trim(); if (loc) return loc; }
  return (r.sad_name && r.sad_name !== r.sad_id) ? r.sad_name : (parts[1] || r.sad_id).replace(/-/g, ' ');
};
const cityOf = r => /drawn/i.test(r.sad_id) ? '' : (r.sad_id.split('_')[2] || '').replace(/-/g, ' ');

// â€”â‚¬â€”â‚¬ rose axes (same as the field) â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
const ROSE_AXES = [
  ['Income', r => r.census?.sad?.median_household_income_pop_weighted],
  ['Renter', r => r.census?.sad?.pct_renter_occupied],
  ['Educ',   r => r.census?.sad?.pct_bachelors_or_higher],
  ['Pop',    r => r.census?.sad?.estimated_population],
  ['POIs',   r => r.amenity?.total_points_in_sad ?? r.program?.total],
  ['Transit',r => r.transit?.total_stations],
];

// â€”â‚¬â€”â‚¬ layer catalog (keys match the viewer manifest) â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
// viewerKey -> existing-district layer in the viewer manifest
// extractKey -> /extract layer name for drawn areas (null = not yet for drawn)
const LAYERS = [
  { key:'buildings', label:'Buildings', color:'#3a342d', kind:'poly', viewerKey:'buildings', extractKey:'buildings',
    style:{ color:'#2a2520', weight:0.5, fillColor:'#3a342d', fillOpacity:0.42 } },
  { key:'parks', label:'Parks', color:'#5fae7e', kind:'poly', viewerKey:'parks', extractKey:'parks',
    style:{ color:'#4f9e6e', weight:0.6, fillColor:'#5fae7e', fillOpacity:0.34 } },
  { key:'parking', label:'Parking', color:'#b4a99c', kind:'poly', viewerKey:'parking', extractKey:'parking',
    style:{ color:'#9a9088', weight:0.5, fillColor:'#b4a99c', fillOpacity:0.30 } },
  { key:'highways', label:'Streets', color:'#6a635a', kind:'line', viewerKey:'roads', extractKey:'highways',
    style:{ color:'#6a635a', weight:1, opacity:0.75 } },
  { key:'pois', label:'POIs', color:'#ff5a45', kind:'point', viewerKey:'pois', extractKey:'pois',
    style:{ radius:3, color:'#ff5a45', weight:0, fillOpacity:0.9 } },
  { key:'heatmap', label:'POI heatmap', color:'#ff8a45', kind:'heat', viewerKey:'pois', extractKey:'pois' },
  { key:'walkshed', label:'Walkshed', color:'#ff5a45', kind:'poly', viewerKey:'walkshed', extractKey:'walkshed',
    style:{ color:'#ff5a45', weight:1.2, fillColor:'#ff5a45', fillOpacity:0.10 } },
  { key:'transit', label:'Transit', color:'#1b1813', kind:'point', viewerKey:'transit', extractKey:'transit',
    style:{ radius:3.2, color:'#1b1813', weight:0, fillOpacity:0.85 } },
  { key:'census', label:'Census block groups', color:'#4292c6', kind:'census', viewerKey:null, extractKey:null },
  { key:'parcels', label:'Parcels (Regrid)', color:'#7fb069', kind:'parcels', viewerKey:null, extractKey:null },
];

// â€”â‚¬â€”â‚¬ state â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
const S = {
  manifest: null, idToRec: {}, axisStats: [],
  parcelCache: {}, parcelMode: 'use',
  map: null, markers: {}, selected: null,           // sad_id or 'drawn'
  boundary: null, overlays: {}, layerOn: {},
  viewerManifest: null, geoCache: {},
  drawn: null, drawControl: null, drawGroup: null,
  compareOpen: true,
};

// â€”â‚¬â€”â‚¬ boot â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
async function boot() {
  try {
    S.manifest = await fetch('compare_manifest_geo.json').then(r => { if (!r.ok) throw 0; return r.json(); });
  } catch { document.getElementById('map').innerHTML = '<p style="padding:24px">compare_manifest.json not found â€” run build_compare_manifest.py</p>'; return; }
  S.manifest.sads = (S.manifest.sads || []);
  for (const r of S.manifest.sads) S.idToRec[r.sad_id] = r;
  S.axisStats = ROSE_AXES.map(([, g]) =>
    S.manifest.sads.map(r => Number(g(r))).filter(x => isFinite(x)).sort((a, b) => a - b));

  initMap(); buildMarkers(); buildList(); buildLegend(); buildLayerRail();
  wireDraw(); wireCompare(); wireSearch();
  await restoreState();
}

function initMap() {
  const L = window.L;
  S.map = L.map('map', { zoomControl: true }).setView([39.5, -96], 4);
  S.baseLight = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    { maxZoom: 19, attribution: 'Â© OpenStreetMap Â· Â© CARTO' });
  S.baseSat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 19, attribution: 'Imagery Â© Esri, Maxar, Earthstar Geographics' });
  // --- Wayback historical imagery (Esri World Imagery archive, global) ---
  S.waybackRelease = S.waybackRelease || 10842;
  S.baseWayback = L.tileLayer(
    'https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{rel}/{z}/{y}/{x}',
    { maxZoom: 19, rel: S.waybackRelease,
      attribution: 'Imagery (Wayback) \u00A9 Esri, Maxar, Earthstar Geographics' });
  S.baseLabels = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png',
    { maxZoom: 19, pane: 'shadowPane' });
  S.baseLight.addTo(S.map);
  S.drawGroup = new L.FeatureGroup().addTo(S.map);
  S.map.on('moveend zoomend', saveState);
  wireBasemap();
}
function setBasemap(which) {
  [S.baseLight, S.baseSat, S.baseLabels, S.baseWayback].forEach(l => { if (l && S.map.hasLayer(l)) S.map.removeLayer(l); });
  const wb = document.getElementById('wayback-ctrl');
  if (which === 'sat') { S.baseSat.addTo(S.map); S.baseLabels.addTo(S.map); if (wb) wb.style.display = 'none'; }
  else if (which === 'history') { S.baseWayback.addTo(S.map); S.baseLabels.addTo(S.map); if (wb) wb.style.display = ''; }
  else { S.baseLight.addTo(S.map); if (wb) wb.style.display = 'none'; }
}
function wireBasemap() {
  document.querySelectorAll('#base-toggle .bt').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('#base-toggle .bt').forEach(x => x.classList.toggle('on', x === b));
    setBasemap(b.dataset.base); saveState();
  }));
  wireWayback();
}
function setWaybackRelease(rel) {
  S.waybackRelease = rel;
  if (!S.baseWayback) return;
  S.baseWayback.options.rel = rel;
  if (S.map.hasLayer(S.baseWayback)) { S.baseWayback.redraw(); }
}
async function wireWayback() {
  const sel = document.getElementById('wayback-ctrl');
  if (!sel) return;
  if (sel.dataset.ready === '1') { sel.style.display = ''; return; }
  if (!document.getElementById('wb-style')) {
    const st = document.createElement('style'); st.id = 'wb-style';
    st.textContent = '.wb-date{font-size:13px;font-weight:600;margin:8px 0 4px;text-align:center}.wb-row{display:flex;align-items:center;gap:6px}.wb-row input[type=range]{flex:1}.wb-arrow{border:1px solid #ccc;background:#fff;border-radius:4px;cursor:pointer;padding:2px 6px;line-height:1}.wb-arrow:hover{background:#f0f0f0}';
    document.head.appendChild(st);
  }
  const FALLBACK = [
    {rel:23001,date:"2020-01-08"},{rel:1049,date:"2021-01-13"},{rel:42663,date:"2022-01-12"},
    {rel:11475,date:"2023-01-11"},{rel:41468,date:"2024-01-18"},{rel:36557,date:"2025-01-30"},
    {rel:22252,date:"2026-01-29"},{rel:10842,date:"2026-05-28"}
  ];
  let items = FALLBACK;
  try {
    const r = await fetch('https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json');
    const cfg = await r.json();
    const all = [];
    for (const k of Object.keys(cfg)) {
      const m = String((cfg[k]||{}).itemTitle||'').match(/(\d{4}-\d{2}-\d{2})/);
      if (m) all.push({ rel: Number(k), date: m[1] });
    }
    if (all.length) {
      all.sort((a,b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));  // oldest first
      // thin to ~2 per year so the slider stays usable
      const seen = {}; const thinned = [];
      for (const it of all) {
        const y = it.date.slice(0,4); const half = Number(it.date.slice(5,7)) <= 6 ? 'a' : 'b';
        const key = y + half;
        if (!seen[key]) { seen[key] = 1; thinned.push(it); }
      }
      if (thinned[thinned.length-1].rel !== all[all.length-1].rel) thinned.push(all[all.length-1]);
      items = thinned;
    }
  } catch (e) { /* keep FALLBACK */ }
  const last = items.length - 1;
  sel.innerHTML =
    '<div class="wb-date" id="wb-date"></div>' +
    '<div class="wb-row">' +
    '<button class="wb-arrow" id="wb-prev" title="Older">&#9664;</button>' +
    '<input type="range" id="wb-slider" min="0" max="' + last + '" value="' + last + '" step="1">' +
    '<button class="wb-arrow" id="wb-next" title="Newer">&#9654;</button>' +
    '</div>';
  const slider = sel.querySelector('#wb-slider');
  const label  = sel.querySelector('#wb-date');
  const apply = (i) => {
    i = Math.max(0, Math.min(last, i));
    slider.value = String(i);
    label.textContent = items[i].date;
    setWaybackRelease(items[i].rel);
  };
  slider.addEventListener('input', () => { label.textContent = items[Number(slider.value)].date; });
  slider.addEventListener('change', () => { apply(Number(slider.value)); saveState(); });
  sel.querySelector('#wb-prev').addEventListener('click', () => { apply(Number(slider.value) - 1); saveState(); });
  sel.querySelector('#wb-next').addEventListener('click', () => { apply(Number(slider.value) + 1); saveState(); });
  apply(last);
  sel.dataset.ready = '1';
  sel.style.display = '';
}

function buildMarkers() {
  const L = window.L;
  const pts = [];
  for (const r of S.manifest.sads) {
    if (!r.centroid && !r.bbox) continue;
    const [lon, lat] = r.centroid || [(r.bbox[0]+r.bbox[2])/2, (r.bbox[1]+r.bbox[3])/2];
    const m = L.marker([lat, lon], { icon: pin(r, false) }).addTo(S.map);
    m.on('click', () => selectDistrict(r.sad_id));
    m.bindTooltip(shortName(r), { direction: 'top', offset: [0, -8] });
    S.markers[r.sad_id] = m;
    pts.push([lat, lon]);
  }
  if (pts.length) S.map.fitBounds(pts, { padding: [40, 40] });
}
function pin(r, sel) {
  return window.L.divIcon({
    className: '', iconSize: sel ? [20, 20] : [14, 14], iconAnchor: sel ? [10, 20] : [7, 14],
    html: `<div class="marker-pin ${sel ? 'sel' : ''}" style="width:100%;height:100%;box-sizing:border-box;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.45);background:${hex(typoColor(r.typology))}"></div>`,
  });
}

function buildList(filter = '') {
  const wrap = document.getElementById('dlist'); wrap.innerHTML = '';
  const f = filter.trim().toLowerCase();
  for (const r of S.manifest.sads) {
    const label = shortName(r) + ' ' + cityOf(r);
    if (f && !label.toLowerCase().includes(f)) continue;
    const el = document.createElement('div');
    el.className = 'ditem' + (S.selected === r.sad_id ? ' sel' : '');
    el.innerHTML = `<span class="dot" style="width:100%;height:100%;box-sizing:border-box;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.45);background:${hex(typoColor(r.typology))}"></span>
      <span>${shortName(r)}<small> \u00b7 ${cityOf(r)}</small></span>`;
    el.addEventListener('click', () => selectDistrict(r.sad_id));
    wrap.appendChild(el);
  }
}
function buildLegend() {
  const seen = new Map();
  for (const r of S.manifest.sads) { const t = r.typology || 'Unclassified'; if (!seen.has(typoKey(t))) seen.set(typoKey(t), t); }
  const wrap = document.getElementById('legend'); wrap.innerHTML = '';
  for (const [, label] of seen) {
    const row = document.createElement('div'); row.className = 'legend-row';
    row.innerHTML = `<span class="dot" style="background:${hex(typoColor(label))}"></span>${label}`;
    wrap.appendChild(row);
  }
}

// â€”â‚¬â€”â‚¬ layer rail â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
function buildLayerRail() {
  const wrap = document.getElementById('layers'); wrap.innerHTML = '';
  for (const L of LAYERS) {
    const el = document.createElement('div');
    el.className = 'layer-toggle' + (S.layerOn[L.key] ? ' on' : '');
    el.innerHTML = `<span class="sw"></span><span class="sw-dot" style="background:${L.color}"></span>${L.label}`;
    el.addEventListener('click', () => toggleLayer(L.key));
    wrap.appendChild(el);
  }
  updateLayerContext();
}
function updateLayerContext() {
  const note = document.getElementById('layer-context');
  if (!S.selected) note.textContent = 'Select a district or draw one to enable layers.';
  else if (S.selected === 'drawn') note.textContent = (S.drawn && S.drawn.extentInfo
    ? 'Extent: ' + (S.drawn.extentInfo.name || S.drawn.extent) : 'Drawn area')
    + ' Â· buildings, parks, parking, streets, POIs, transit & walkshed pull live.';
  else note.textContent = shortName(S.idToRec[S.selected]) + ' Â· toggle layers on the map.';
}

async function toggleLayer(key) {
  if (!S.selected) return;
  const def = LAYERS.find(l => l.key === key);
  if (S.layerOn[key]) {                     // turn off
    removeOverlay(key); if (key === 'buildings') removeOverlay('_context'); S.layerOn[key] = false; buildLayerRail(); renderPoiFilter(); renderCensusCtrl(); renderParcelCtrl(); renderParcelCtrl(); return;
  }
  S.layerOn[key] = true; buildLayerRail();
  try {
    let layer;
    if (def.kind === 'heat') layer = await heatLayer();
    else if (def.kind === 'census') layer = await censusLayer();
    else if (def.kind === 'parcels') layer = await parcelsLayer(S.selected);
    else if (key === 'pois') layer = styledLayer(def, await filteredPoiFC());
    else if (key === 'walkshed') layer = await walkshedLayer();
    else if ((S.selected === 'drawn' || /rawn/i.test(S.selected||'')) && ['buildings','parks','parking','highways'].includes(key)) layer = await drawnContextLayer(def);
    else if (S.selected === 'drawn') layer = await drawnLayer(def);
    else layer = await existingLayer(S.selected, def);
    if (layer) S.overlays[key] = layer.addTo(S.map);
    else S.layerOn[key] = false;
    if (false && key === 'buildings' && layer) { try { const _cl = await contextLayer(); if (_cl) S.overlays['_context'] = _cl.addTo(S.map); } catch (e) {} }
  } catch (e) { S.layerOn[key] = false; }
  buildLayerRail(); renderPoiFilter(); renderCensusCtrl(); renderParcelCtrl(); renderParcelCtrl(); saveState();
}
function removeOverlay(key) { if (S.overlays[key]) { S.map.removeLayer(S.overlays[key]); delete S.overlays[key]; } }

async function existingLayer(id, def) {
  const layers = await viewerLayers(id);
  const rec = layers && layers[def.viewerKey];
  if (!rec || !rec.path) return null;
  return styledLayer(def, await loadGeo('../' + rec.path));
}

async function drawnLayer(def) {
  if (!def.extractKey) return null;          // walkshed not yet for drawn areas
  const gj = await extractDrawn(def.extractKey);
  return gj ? styledLayer(def, gj) : null;
}

function _ctxBuffer(geom,marginM){const cs=_ctxCoords(geom);if(!cs.length)return null;let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;for(const p of cs){const x=p[0],y=p[1];if(x<minx)minx=x;if(x>maxx)maxx=x;if(y<miny)miny=y;if(y>maxy)maxy=y;}const cy=(miny+maxy)/2;const mLat=marginM/111320,mLon=marginM/(111320*Math.cos(cy*Math.PI/180));return{type:'Polygon',coordinates:[[[minx-mLon,miny-mLat],[maxx+mLon,miny-mLat],[maxx+mLon,maxy+mLat],[minx-mLon,maxy+mLat],[minx-mLon,miny-mLat]]]};}
function _ctxSquare(geom,sizeM){const cs=_ctxCoords(geom);if(!cs.length)return null;let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;for(const p of cs){const x=p[0],y=p[1];if(x<minx)minx=x;if(x>maxx)maxx=x;if(y<miny)miny=y;if(y>maxy)maxy=y;}const cx=(minx+maxx)/2,cy=(miny+maxy)/2;const half=sizeM/2;const mLat=half/111320,mLon=half/(111320*Math.cos(cy*Math.PI/180));return{type:'Polygon',coordinates:[[[cx-mLon,cy-mLat],[cx+mLon,cy-mLat],[cx+mLon,cy+mLat],[cx-mLon,cy+mLat],[cx-mLon,cy-mLat]]]};}
async function drawnContextLayer(def){let geom=null;if(S.selected==='drawn'){geom=S.drawn&&S.drawn.geometry;}else{geom=S.boundaryGeom;if(!geom){const _r=S.idToRec[S.selected];if(_r&&_r.artifacts&&_r.artifacts.sad_boundary){try{const _b=await loadGeo('../'+_r.artifacts.sad_boundary);geom=(_b&&_b.type==='FeatureCollection')?((_b.features&&_b.features[0]||{}).geometry):((_b&&_b.geometry)||_b);}catch(e){}}}}if(!geom)return null;const exp=_ctxSquare(geom,2000)||geom;const resp=await fetch(MATCH_API+'/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry:exp,extent:'sad',layer:def.extractKey})}).then(r=>r.json()).catch(()=>null);if(!resp||!resp.ok||!resp.geojson||!((resp.geojson.features||[]).length))return null;return styledLayer(def,resp.geojson);}

async function walkshedLayer(){let geom=null;if(S.selected==='drawn'){geom=S.drawn&&S.drawn.geometry;}else if(S.boundaryGeom){geom=S.boundaryGeom;}else{const rec=S.idToRec[S.selected];if(rec&&rec.artifacts&&rec.artifacts.sad_boundary){try{const b=await loadGeo('../'+rec.artifacts.sad_boundary);geom=(b&&b.type==='FeatureCollection')?((b.features&&b.features[0]||{}).geometry):((b&&b.geometry)||b);}catch(e){}}}if(!geom)return null;const def=LAYERS.find(l=>l.key==='walkshed');const resp=await fetch(MATCH_API+'/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry:geom,extent:'sad',layer:'walkshed'})}).then(r=>r.json()).catch(()=>null);if(!resp||!resp.ok||!resp.geojson||!((resp.geojson.features||[]).length))return null;return styledLayer(def,resp.geojson);}

function _ctxCoords(geom){const out=[];const walk=a=>{if(typeof a[0]==='number')out.push(a);else a.forEach(walk);};if(geom&&geom.coordinates)walk(geom.coordinates);return out;}
function _ctxExpand(geom,factor,maxM){const cs=_ctxCoords(geom);if(!cs.length)return null;let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;for(const p of cs){const x=p[0],y=p[1];if(x<minx)minx=x;if(x>maxx)maxx=x;if(y<miny)miny=y;if(y>maxy)maxy=y;}const cx=(minx+maxx)/2,cy=(miny+maxy)/2;let hx=(maxx-minx)/2*(1+factor),hy=(maxy-miny)/2*(1+factor);const latCap=maxM/111320,lonCap=maxM/(111320*Math.cos(cy*Math.PI/180));hx=Math.min(hx,lonCap);hy=Math.min(hy,latCap);return{type:'Polygon',coordinates:[[[cx-hx,cy-hy],[cx+hx,cy-hy],[cx+hx,cy+hy],[cx-hx,cy+hy],[cx-hx,cy-hy]]]};}
async function contextLayer(){try{if(!S.map.getPane('contextPane')){S.map.createPane('contextPane');S.map.getPane('contextPane').style.zIndex=350;}}catch(e){}let geom=null;if(S.selected==='drawn')geom=S.drawn&&S.drawn.geometry;else{geom=S.boundaryGeom;if(!geom){const _r=S.idToRec[S.selected];if(_r&&_r.artifacts&&_r.artifacts.sad_boundary){try{const _b=await loadGeo('../'+_r.artifacts.sad_boundary);geom=(_b&&_b.type==='FeatureCollection')?((_b.features&&_b.features[0]||{}).geometry):((_b&&_b.geometry)||_b);}catch(e){}}}}if(!geom)return null;const exp=_ctxExpand(geom,0.6,800);if(!exp)return null;const resp=await fetch(MATCH_API+'/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry:exp,extent:'sad',layer:'buildings'})}).then(r=>r.json()).catch(()=>null);if(!resp||!resp.ok||!resp.geojson)return null;return window.L.geoJSON(resp.geojson,{pane:'contextPane',interactive:false,style:()=>({color:'#b8ad9d',weight:0.5,fillColor:'#cfc6b8',fillOpacity:0.7})});}

// fetch one /extract layer for the drawn area's current extent (per-extent cache)
// fetch one /extract layer for the drawn area's current extent (per-extent cache).
// When narrowing City -> SAD, crop the city pull client-side instead of re-fetching.
const CROPPABLE = new Set(['buildings', 'parks', 'parking', 'highways', 'pois', 'transit', 'census']);
async function extractDrawn(layerName) {
  S.drawn.layers = S.drawn.layers || {};
  const ext = S.drawn.extent || 'city';
  const ck = ext + ':' + layerName;
  if (ck in S.drawn.layers) return S.drawn.layers[ck];
  const cityFC = S.drawn.layers['city:' + layerName];
  if (ext === 'sad' && CROPPABLE.has(layerName) && cityFC) {     // crop, don't re-query
    S.drawn.layers[ck] = cropFC(cityFC, S.drawn.geometry);
    if (!S.drawn.extentInfo || S.drawn.extentInfo.kind !== 'sad') {
      S.drawn.extentInfo = { kind: 'sad', name: 'SAD boundary' }; updateLayerContext();
    }
    return S.drawn.layers[ck];
  }
  const resp = await fetch(MATCH_API + '/extract', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ geometry: S.drawn.geometry, extent: ext, layer: layerName })
  }).then(r => r.json()).catch(() => null);
  S.drawn.layers[ck] = (resp && resp.ok) ? resp.geojson : null;
  if (resp && resp.ok && resp.extent) { S.drawn.extentInfo = resp.extent; updateLayerContext(); }
  return S.drawn.layers[ck];
}

// â€”â‚¬â€”â‚¬ client-side crop of a FeatureCollection to a SAD polygon â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
function _ptInRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}
function _sadContains(geom, x, y) {
  const polys = geom.type === 'MultiPolygon' ? geom.coordinates : [geom.coordinates];
  for (const rings of polys) {
    if (_ptInRing(x, y, rings[0])) {
      let hole = false;
      for (let h = 1; h < rings.length; h++) if (_ptInRing(x, y, rings[h])) { hole = true; break; }
      if (!hole) return true;
    }
  }
  return false;
}
function _walk(c, out) { if (typeof c[0] === 'number') out.push(c); else for (const z of c) _walk(z, out); }
function cropFC(fc, sadGeom) {
  if (!fc || !fc.features) return fc;
  const sc = []; _walk(sadGeom.coordinates, sc);
  const sb = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of sc) { if (x < sb[0]) sb[0] = x; if (y < sb[1]) sb[1] = y; if (x > sb[2]) sb[2] = x; if (y > sb[3]) sb[3] = y; }
  const cx = (sb[0] + sb[2]) / 2, cy = (sb[1] + sb[3]) / 2;
  const keep = (f) => {
    const g = f.geometry; if (!g) return false;
    if (g.type === 'Point') return _sadContains(sadGeom, g.coordinates[0], g.coordinates[1]);
    const cs = []; _walk(g.coordinates, cs);
    let mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity;
    for (const [x, y] of cs) { if (x < mnx) mnx = x; if (y < mny) mny = y; if (x > mxx) mxx = x; if (y > mxy) mxy = y; }
    if (mxx < sb[0] || mnx > sb[2] || mxy < sb[1] || mny > sb[3]) return false;   // bbox disjoint
    for (const [x, y] of cs) if (_sadContains(sadGeom, x, y)) return true;        // a vertex inside SAD
    if (cx >= mnx && cx <= mxx && cy >= mny && cy <= mxy) return true;            // feature spans SAD
    return false;
  };
  return { type: 'FeatureCollection', features: fc.features.filter(keep) };
}

// â€”â‚¬â€”â‚¬ POIs: fetch once per selection/extent, filter by category client-side â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
// â€”â‚¬â€”â‚¬ POIs classified into the viewer's Rossetti programs â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
const ROSSETTI_ORDER = ['retail_food_entertainment', 'office', 'sport', 'residential', 'hotel', 'parking', 'open_space', 'other'];
const PROGRAM_LABELS = { sport: 'Sport', residential: 'Residential', hotel: 'Hotel',
  retail_food_entertainment: 'Retail / F&B', office: 'Office', parking: 'Parking',
  open_space: 'Open space', other: 'Other' };
const PROGRAM_COLORS = { sport: '#d62728', residential: '#2ca02c', hotel: '#9467bd',
  retail_food_entertainment: '#ff7f0e', office: '#1f77b4', parking: '#8c564b',
  open_space: '#bcbd22', other: '#7f7f7f' };
function catOf(f) { const p = f.properties || {}; return p.rossetti_category || p.program || 'other'; }
async function poiFC() {
  const srcKey = S.selected + '|' + (S.selected === 'drawn' ? (S.drawn.extent || 'city') : '');
  if (S.poi && S.poi.key === srcKey && S.poi.fc) return S.poi.fc;
  let fc = null;
  if (S.selected === 'drawn') fc = await extractDrawn('pois');
  else {
    const layers = await viewerLayers(S.selected);
    const rec = layers && layers.pois;
    if (rec && rec.path) fc = await loadGeo('../' + rec.path);
  }
  fc = fc || { type: 'FeatureCollection', features: [] };
  const cats = new Set(fc.features.map(catOf));
  S.poi = { key: srcKey, fc, cats: new Set(cats) };
  return fc;
}
async function filteredPoiFC() {
  const fc = await poiFC(), sel = S.poi.cats;
  return { type: 'FeatureCollection', features: fc.features.filter(f => sel.has(catOf(f))) };
}
async function heatLayer() {
  const fc = await filteredPoiFC();
  if (!fc.features.length || !window.L.heatLayer) return null;
  const pts = fc.features.filter(f => f.geometry && f.geometry.type === 'Point')
    .map(f => [f.geometry.coordinates[1], f.geometry.coordinates[0], 0.6]);
  return window.L.heatLayer(pts, { radius: 22, blur: 18, maxZoom: 17,
    gradient: { 0.2: '#2c7fb8', 0.5: '#fdae61', 0.8: '#f03b20', 1.0: '#bd0026' } });
}
async function refreshPoiOverlays() {
  if (S.layerOn.pois) { removeOverlay('pois'); const l = styledLayer(LAYERS.find(x => x.key === 'pois'), await filteredPoiFC()); if (l) S.overlays.pois = l.addTo(S.map); }
  if (S.layerOn.heatmap) { removeOverlay('heatmap'); const l = await heatLayer(); if (l) S.overlays.heatmap = l.addTo(S.map); }
  saveState();
}
function renderPoiFilter() {
  const host = document.getElementById('poi-filter'); if (!host) return;
  const show = (S.layerOn.pois || S.layerOn.heatmap) && S.poi && S.poi.fc && S.poi.fc.features.length;
  if (!show) { host.innerHTML = ''; return; }
  const counts = {}; for (const f of S.poi.fc.features) { const c = catOf(f); counts[c] = (counts[c] || 0) + 1; }
  const cats = ROSSETTI_ORDER.filter(c => counts[c]);
  host.innerHTML = `<div class="pf-head"><span>POI type</span>
      <span class="pf-act"><a id="pf-all">all</a> Â· <a id="pf-none">none</a></span></div>
    <div class="pf-list">` + cats.map(c => `<label class="pf-row">
      <input type="checkbox" data-cat="${c}" ${S.poi.cats.has(c) ? 'checked' : ''}>
      <span class="sw-dot" style="background:${PROGRAM_COLORS[c]}"></span>
      <span class="pf-name">${PROGRAM_LABELS[c]}</span><span class="pf-n">${counts[c]}</span></label>`).join('') + `</div>`;
  host.querySelectorAll('input[data-cat]').forEach(cb => cb.addEventListener('change', () => {
    if (cb.checked) S.poi.cats.add(cb.dataset.cat); else S.poi.cats.delete(cb.dataset.cat);
    refreshPoiOverlays();
  }));
  host.querySelector('#pf-all').addEventListener('click', () => { S.poi.cats = new Set(cats); refreshPoiOverlays(); renderPoiFilter(); });
  host.querySelector('#pf-none').addEventListener('click', () => { S.poi.cats = new Set(); refreshPoiOverlays(); renderPoiFilter(); });
}

// â€”â‚¬â€”â‚¬ census block groups (choropleth) â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
const CENSUS_PROP = { income: 'median_household_income', pop: 'total_pop', age: 'median_age',
  renter: 'pct_renter', educ: 'pct_bachelors' };
const CENSUS_METRICS = [['income', 'Median income'], ['pop', 'Population'], ['age', 'Median age'],
  ['renter', '% renter'], ['educ', "Bachelor's+ %"]];
const CENSUS_PALETTE = ['#eaf3fb', '#c6dbef', '#9ecae1', '#4292c6', '#08519c'];

async function censusGeo() {
  const key = S.selected + (S.selected === 'drawn' ? '|' + (S.drawn.extent || 'city') : '');
  if (S.census && S.census.key === key && S.census.fc) return S.census.fc;
  let fc = null;
  if (S.selected === 'drawn') fc = await extractDrawn('census');   // city BGs, croppable to SAD
  else { const rec = S.idToRec[S.selected];
    if (rec && rec.artifacts && rec.artifacts.census_geojson) { try { fc = await loadGeo('../' + rec.artifacts.census_geojson); } catch (e) {} } }
  fc = fc || { type: 'FeatureCollection', features: [] };
  S.census = { key, fc, metric: (S.census && S.census.metric) || S._censusMetric || 'income' };
  return fc;
}
function censusBreaks(fc, prop) {
  const v = fc.features.map(f => Number(f.properties && f.properties[prop])).filter(x => isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const q = p => v[Math.min(v.length - 1, Math.floor(p * v.length))];
  return [q(0.2), q(0.4), q(0.6), q(0.8)];
}
function censusColor(val, breaks) {
  if (val == null || !isFinite(val) || !breaks) return '#dddddd';
  let i = 0; while (i < breaks.length && val > breaks[i]) i++;
  return CENSUS_PALETTE[i];
}
function fmtCensus(metric, v) {
  if (v == null || !isFinite(Number(v))) return '\u2014'; const n = Number(v);
  if (metric === 'income') return '$' + Math.round(n).toLocaleString();
  if (metric === 'pop') return Math.round(n).toLocaleString();
  if (metric === 'age') return n.toFixed(1);
  return n.toFixed(0) + '%';
}
async function censusLayer() {
  const fc = await censusGeo();
  if (!fc.features.length) return null;
  const prop = CENSUS_PROP[S.census.metric] || 'median_household_income';
  const breaks = censusBreaks(fc, prop);
  return window.L.geoJSON(fc, {
    style: f => ({ fillColor: censusColor(Number(f.properties && f.properties[prop]), breaks),
      color: '#ffffff', weight: 0.4, fillOpacity: 0.62 }),
    onEachFeature: (f, l) => l.bindTooltip(fmtCensus(S.census.metric, f.properties && f.properties[prop]), { sticky: true })
  });
}
function censusLegendLabel(i, breaks) {
  const f = v => S.census.metric === 'income' ? '$' + Math.round(v / 1000) + 'k'
    : S.census.metric === 'pop' ? Math.round(v).toLocaleString()
    : S.census.metric === 'age' ? v.toFixed(0) : v.toFixed(0) + '%';
  if (i === 0) return '< ' + f(breaks[0]);
  if (i === CENSUS_PALETTE.length - 1) return 'â‰¥ ' + f(breaks[breaks.length - 1]);
  return f(breaks[i - 1]) + 'Ã¢â‚¬â€œ' + f(breaks[i]);
}
function renderCensusCtrl() {
  const host = document.getElementById('census-ctrl'); if (!host) return;
  if (!S.layerOn.census || !S.census || !S.census.fc.features.length) { host.innerHTML = ''; return; }
  const breaks = censusBreaks(S.census.fc, CENSUS_PROP[S.census.metric]);
  host.innerHTML = `<div class="pf-head"><span>Census metric</span></div>
    <select class="dsearch" id="census-metric" style="margin-bottom:6px">` +
    CENSUS_METRICS.map(([k, l]) => `<option value="${k}" ${S.census.metric === k ? 'selected' : ''}>${l}</option>`).join('') +
    `</select>` + (breaks ? `<div class="census-legend">` +
      CENSUS_PALETTE.map((c, i) => `<span class="cl-step"><i style="background:${c}"></i>${censusLegendLabel(i, breaks)}</span>`).join('') +
      `</div>` : '');
  host.querySelector('#census-metric').addEventListener('change', async e => {
    S.census.metric = e.target.value; S._censusMetric = e.target.value;
    if (S.layerOn.census) { removeOverlay('census'); const l = await censusLayer(); if (l) S.overlays.census = l.addTo(S.map); }
    renderCensusCtrl(); renderParcelCtrl(); saveState();
  });
}

function styledLayer(def, gj) {
  const L = window.L;
  if (def.kind === 'point') {
    const byProgram = def.key === 'pois';
    return L.geoJSON(gj, {
      pointToLayer: (f, ll) => {
        const st = byProgram
          ? { ...def.style, color: PROGRAM_COLORS[catOf(f)] || def.style.color, fillColor: PROGRAM_COLORS[catOf(f)] || def.style.color }
          : def.style;
        return L.circleMarker(ll, st);
      },
      onEachFeature: (f, lyr) => {
        const p = f.properties || {};
        const t = p.name || p.NAME || def.label;
        const tag = byProgram ? (PROGRAM_LABELS[catOf(f)] || '') : (p.category || '');
        const sub = tag ? `<span class="t-sub">${tag}</span>` : '';
        lyr.bindTooltip(`${t}<br>${sub}`, { sticky: true });
      }
    });
  }
  return L.geoJSON(gj, { style: () => def.style });
}

// â€”â‚¬â€”â‚¬ viewer manifest (shared source of truth for layers) â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
async function viewerLayers(id) {
  if (!S.viewerManifest) {
    try { S.viewerManifest = await loadGeo('../_ui/manifest.json'); }
    catch { S.viewerManifest = { sads: [] }; }
  }
  const e = (S.viewerManifest.sads || []).find(s => s.sad_id === id);
  return e && e.layers;
}
async function loadGeo(url) {
  if (S.geoCache[url]) return S.geoCache[url];
  const g = await fetch(url).then(r => { if (!r.ok) throw new Error(url); return r.json(); });
  S.geoCache[url] = g; return g;
}

// â€”â‚¬â€”â‚¬ selection â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
function clearLayers() {
  for (const k of Object.keys(S.overlays)) { S.map.removeLayer(S.overlays[k]); }
  S.overlays = {}; S.layerOn = {}; S.poi = null; S.census = null;
  if (S.boundary) { S.map.removeLayer(S.boundary); S.boundary = null; }
  S.boundaryGeom = null; removeOverlay('_context');
  renderPoiFilter(); renderCensusCtrl(); renderParcelCtrl();
}
async function selectDistrict(id, opts = {}) {
  const rec = S.idToRec[id]; if (!rec) return;
  clearLayers();
  S.drawGroup.clearLayers(); S.drawn = null;
  S.selected = id;
  // markers highlight
  for (const [mid, m] of Object.entries(S.markers)) m.setIcon(pin(S.idToRec[mid], mid === id));
  buildList(document.getElementById('dsearch').value);
  // fly + boundary
  if (!opts.noFly) {
    if (rec.bbox) S.map.flyToBounds([[rec.bbox[1], rec.bbox[0]], [rec.bbox[3], rec.bbox[2]]], { padding: [60, 60], maxZoom: 15 });
    else if (rec.centroid) S.map.flyTo([rec.centroid[1], rec.centroid[0]], 14);
  }
  if (rec.artifacts && rec.artifacts.sad_boundary) {
    try { const b = await loadGeo('../' + rec.artifacts.sad_boundary); S.boundaryGeom = (b && b.type === 'FeatureCollection') ? ((b.features && b.features[0] || {}).geometry) : ((b && b.geometry) || b);
      S.boundary = window.L.geoJSON(b, { style: { color: '#1b1813', weight: 2.5, fill: false, dashArray: '5 3' } }).addTo(S.map);
    } catch {}
  }
  await ensureNature(); buildLayerRail(); renderPanel(); saveState();
}

// â€”â‚¬â€”â‚¬ draw Ã¢â€ â€™ analyze â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
function wireDraw() {
  const L = window.L;
  document.getElementById('btn-draw').addEventListener('click', () => {
    new L.Draw.Polygon(S.map, { shapeOptions: { color: '#ff5a45', weight: 2 } }).enable();
  });
  S.map.on(L.Draw.Event.CREATED, async e => {
    S.drawGroup.clearLayers(); S.drawGroup.addLayer(e.layer);
    clearLayers();
    for (const [mid, m] of Object.entries(S.markers)) m.setIcon(pin(S.idToRec[mid], false));
    S.selected = 'drawn';
    S.drawn = { geometry: e.layer.toGeoJSON().geometry, analysis: null, layers: {}, extent: 'city' };
    buildLayerRail();
    renderPanelBusy('Pulling ACS for the drawn area and matchingâ€¦');
    try {
      const resp = await fetch(MATCH_API + '/analyze', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ geometry: S.drawn.geometry, name: 'Drawn district' })
      });
      S.drawn.analysis = await resp.json();
      try { const _city = await resolveCity(S.drawn.geometry); if (_city && S.drawn.analysis) { S.drawn.analysis.name = _city; S.drawn.analysis.region = _city; } } catch (e) {}
      try {
        const pr = await fetch(MATCH_API + '/analyze_program', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ geometry: S.drawn.geometry })
        });
        S.drawn.program = await pr.json();
      } catch { S.drawn.program = null; }
    } catch {
      renderPanelError('Couldn\u2019t reach the match server. Start it:<br><code>python sad_match_server.py --data-dir ..\\data</code>');
      return;
    }
    if (!S.drawn.analysis.ok) { renderPanelError(S.drawn.analysis.error || 'Analysis failed.'); return; }
    renderPanel(); saveState();
  });
}

// â€”â‚¬â€”â‚¬ rose â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
function axisPct(rec, i) {
  const v = Number(ROSE_AXES[i][1](rec)); if (!isFinite(v)) return null;
  const arr = S.axisStats[i]; if (!arr || !arr.length) return null;
  let c = 0; for (const x of arr) if (x <= v) c++; return c / arr.length;
}
function roseSVG(entries, color = '#ff5a45') {
  const N = entries.length; if (N < 3) return '';
  const W = 300, H = 270, cx = 150, cy = 135, R = 82, LR = 1.2;
  const ang = p => (-90 + p * 360 / N) * Math.PI / 180;
  const pt = (p, v) => [cx + R * v * Math.cos(ang(p)), cy + R * v * Math.sin(ang(p))];
  let s = `<svg viewBox="0 0 ${W} ${H}" class="rose">`;
  for (const f of [0.25, 0.5, 0.75, 1]) s += `<polygon class="ring" points="${entries.map((_, p) => pt(p, f).map(n => n.toFixed(1)).join(',')).join(' ')}"/>`;
  entries.forEach(([label], p) => {
    const [x, y] = pt(p, 1), [lx, ly] = pt(p, LR);
    s += `<line class="spoke" x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
    s += `<text class="axlab" x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" dominant-baseline="middle">${label}</text>`;
  });
  const pts = entries.map(([, t], p) => pt(p, t == null ? 0 : t));
  s += `<polygon class="dpoly" points="${pts.map(q => q.map(n => n.toFixed(1)).join(',')).join(' ')}" style="stroke:${color};fill:${color}"/>`;
  pts.forEach(q => s += `<circle class="dvert" cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}" r="2.1" style="fill:${color}"/>`);
  return s + `</svg>`;
}

// â€”â‚¬â€”â‚¬ right panel â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
function openPanel() { document.getElementById('panel').classList.add('open'); }
function renderPanelBusy(msg) {
  openPanel();
  document.getElementById('panel-inner').innerHTML =
    `<div class="panel-head"><div class="panel-title">Drawn district</div></div>
     <div class="sec"><div class="spinner"></div><p class="muted" style="text-align:center">${msg}</p></div>`;
}
function renderPanelError(msg) {
  openPanel();
  document.getElementById('panel-inner').innerHTML =
    `<div class="panel-head"><div class="panel-title">Drawn district</div></div>
     <div class="sec"><p class="muted">${msg}</p></div>`;
}

function scopeRow(label, demo, here) {
  if (!demo) return `<tr><td class="rl">${label}</td><td>\u2014</td><td>\u2014</td></tr>`;
  return `<tr><td class="rl ${here ? 'here' : ''}">${label}</td>
    <td class="${here ? 'here' : ''}">${fmt(demo.estimated_population, 'int')}</td>
    <td class="${here ? 'here' : ''}">${fmt(demo.median_household_income_pop_weighted, 'usd')}</td></tr>`;
}

function renderPanel() {
  openPanel();
  const inner = document.getElementById('panel-inner');
  if (S.selected === 'drawn') return renderDrawnPanel(inner);

  const r = S.idToRec[S.selected];
  const c = r.census || {};
  const muni = c.municipality ? c.municipality.namelsad || c.municipality.name : null;
  const nb = nearest(r.sad_id, 6);
  const roseEntries = ROSE_AXES.map(([label], i) => [label, axisPct(r, i)]);

  inner.innerHTML = `
    <div class="panel-head">
      <div class="panel-kicker">${r.region || 'District'}</div>
      <div class="panel-title">${shortName(r)}</div>
      <div class="panel-sub">
        <span class="typo-chip"><span class="dot" style="width:100%;height:100%;box-sizing:border-box;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.45);background:${hex(typoColor(r.typology))}"></span>${r.typology || 'Unclassified'}</span>
        ${muni ? `<span class="panel-muni">${muni}</span>` : ''}
      </div>
    </div>
    <div class="sec">
      <div class="sec-h">Census scopes</div>
      <table class="scopes"><thead><tr><th class="rl">scope</th><th>pop</th><th>income</th></tr></thead><tbody>
        ${scopeRow('District', c.sad, true)}
        ${scopeRow('City' + (muni ? ` \u00b7 ${c.municipality.name}` : ''), c.municipal, false)}
        ${scopeRow('Metro', c.metro, false)}
      </tbody></table>
    </div>
    <div class="sec">
      <div class="sec-h">Comparison <span class="toggle-x" id="cmp-x">${S.compareOpen ? 'hide' : 'show'}</span></div>
      <div id="cmp-body" style="${S.compareOpen ? '' : 'display:none'}">
        ${roseSVG(roseEntries, hex(typoColor(r.typology))) || '<div class="empty">Profile chart needs census + feature data (run the analysis modules for this district).</div>'}
        <div class="sec-h" style="margin-top:6px">Most similar districts</div>
        <div>${nb.map(x => nbRow(x)).join('') || '<div class="empty">No similarity data.</div>'}</div>
        <p class="layer-note" style="margin-left:0">By morphology + demographics. % = overall similarity (higher is closer).</p>
      </div>
    </div>
    <div class="actions">
      <a class="btn" href="../_ui/?sad=${encodeURIComponent(r.sad_id)}">Open in viewer \u2192</a>
    </div>`;
  injectNature(r); wirePanel();
}

// Typology fit: render /analyze_program's typology_fit block (percent + why).
async function resolveCity(geometry){
  try{
    let ring=geometry&&geometry.type==='Polygon'?geometry.coordinates[0]:(geometry&&geometry.type==='MultiPolygon'?geometry.coordinates[0][0]:null);
    if(!ring||!ring.length)return null;
    let sx=0,sy=0;for(const p of ring){sx+=p[0];sy+=p[1];}
    const lon=sx/ring.length,lat=sy/ring.length;
    const u='https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10&lat='+lat+'&lon='+lon;
    const j=await fetch(u,{headers:{'Accept':'application/json'}}).then(r=>r.json());
    const a=(j&&j.address)||{};
    const place=a.city||a.town||a.village||a.hamlet||a.suburb||a.municipality||a.county;
    let st='';const iso=a['ISO3166-2-lvl4'];if(iso&&iso.indexOf('-')>=0)st=iso.split('-').pop();else if(a.state)st=a.state;
    if(!place)return st||null;
    return st?(place+', '+st):place;
  }catch(e){return null;}
}
const TYPO_FIT_COLORS = { 'Entertainment':'#D85A30', 'Innovation':'#534AB7', 'Sports Park':'#1D9E75', 'Community':'#888780' };
const TYPO_DEF = { 'Entertainment':'Entertainment Destinations: event-spending driven, commercial mixed-use, serves visitors.','Community':'Community-Centered Districts: civic-value driven, year-round local use, serves residents.','Innovation':'Innovation / Employment Districts: jobs and knowledge driven, office and research, serves workers.','Sports Park':'Sports Tourism Districts: amateur tournament travel, fields and recreation (framework name: Sports Tourism).' };

function typoFitSection(prog) {
  const tf = prog && prog.ok && prog.typology_fit;
  if (!tf || !tf.percent_by_typology) return '';
  const order = (tf.ranked && tf.ranked.length) ? tf.ranked.map(r => r[0]) : Object.keys(tf.percent_by_typology);
  const rows = order.map(t => {
    const v = Math.max(0, Math.min(100, Number(tf.percent_by_typology[t]) || 0));
    const col = TYPO_FIT_COLORS[t] || '#888780';
    return `<div title="${(TYPO_DEF[t]||t)}" style="display:flex;align-items:center;gap:8px;margin:6px 0;cursor:help">
      <span style="flex:0 0 94px;font-size:11.5px;color:var(--ink-2)">${t}</span>
      <span style="flex:1 1 auto;height:8px;border-radius:100px;background:var(--line-2,#ece7df);position:relative;overflow:hidden">
        <span style="position:absolute;left:0;top:0;bottom:0;width:${v}%;background:${col};border-radius:100px"></span>
      </span>
      <span class="mono" style="flex:0 0 36px;text-align:right;font-size:11px;color:var(--ink)">${v.toFixed(0)}%</span>
    </div>`;
  }).join('');
  const top = tf.top_typology || (order[0] || '');
  const why = top ? `<p class="layer-note" style="margin-left:0">Reads closest to <b>${top}</b>${tf.why ? ': ' + tf.why : ''}.</p>` : '';
  return `<div class="sec">
      <div class="sec-h">Typology fit</div>
      <p class="layer-note" style="margin:0 0 6px">Hover a type for its meaning. Based on the framework: who the district serves and what value it creates.</p>
      ${rows}
      ${why}
    </div>
    `;
}

// One "Most similar" list, switchable by lens (program / demographics; structure later).
function simListHTML(lens, a, demoMatches) {
  if (lens === 'demographic') {
    return (demoMatches || '<div class="empty">No matches.</div>') +
      `<p class="layer-note" style="margin-left:0">By demographics (ACS). % = similarity, higher is closer. ${a && a.note ? a.note : ''}</p>`;
  }
  const p = S.drawn.program;
  const rows = (p && p.ok ? (p.matches || []).map(m => nbRow({ id: m.sad_id, name: m.sad_name, city: m.region, dist: m.distance, known: !!S.idToRec[m.sad_id] })).join('') : '') || '<div class="empty">No program match.</div>';
  return rows + `<p class="layer-note" style="margin-left:0">Nearest districts by program mix, from a live Overture POI pull. ${(p && p.n_pois) ? p.n_pois + ' places.' : ''}</p>`;
}

function renderDrawnPanel(inner) {
  const a = S.drawn.analysis, p = a.profile || {};
  const muni = a.municipality ? (a.municipality.namelsad || a.municipality.name) : null;
  const roseEntries = Object.entries(a.percentiles || {}).map(([k, v]) => [k, v]);
  const matches = (a.matches || []).map(m => nbRow({
    id: m.sad_id, name: m.sad_name, city: m.region, dist: m.distance, known: !!S.idToRec[m.sad_id]
  })).join('');
  inner.innerHTML = `
    <div class="panel-head">
      <div class="panel-kicker">Drawn area</div>
      <div class="panel-title">${a.name || 'Drawn district'}</div>
      ${muni ? `<div class="panel-sub"><span class="panel-muni">${muni}</span></div>` : ''}
    </div>
    <div class="sec">
      <div class="sec-h">Census scopes</div>
      <table class="scopes"><thead><tr><th class="rl">scope</th><th>pop</th><th>income</th></tr></thead><tbody>
        ${scopeRow('Drawn area', p, true)}
        ${scopeRow('City' + (muni ? ` \u00b7 ${a.municipality.name}` : ''), a.profile_municipal, false)}
      </tbody></table>
    </div>
    <div class="sec">
      <div class="sec-h">Acquisition extent</div>
      <div class="base-toggle" id="extent-toggle">
        <button class="bt ${(S.drawn.extent || 'city') === 'city' ? 'on' : ''}" data-extent="city">City</button>
        <button class="bt ${(S.drawn.extent || 'city') === 'sad' ? 'on' : ''}" data-extent="sad">SAD only</button>
      </div>
      <p class="layer-note" style="margin-left:0">Layers pull within this extent and cache. Switching it clears loaded layers.</p>
    </div>
    <div class="sec">
      <div class="sec-h">Profile <span class="toggle-x">percentile vs corpus</span></div>
      ${roseSVG(roseEntries) || '<div class="empty">Not enough demographic data to chart a profile here.</div>'}
    </div>
        ${typoFitSection(S.drawn.program)}
    <div class="sec">
      <div class="sec-h">Most similar</div>
      <div class="base-toggle" id="sim-lens" style="margin:2px 0 10px">
        <button class="bt ${(S.drawn.simLens || 'program') === 'program' ? 'on' : ''}" data-lens="program">Program</button>
        <button class="bt ${(S.drawn.simLens || 'program') === 'demographic' ? 'on' : ''}" data-lens="demographic">Demographics</button>
      </div>
      <div id="sim-list">${simListHTML(S.drawn.simLens || 'program', a, matches)}</div>
    </div>
    <div class="actions">
      <button class="btn" id="save-area">Save as district</button>
      <p class="layer-note" style="margin-left:0">Writes the pulled layers + boundary into a new <span class="mono">data\\</span> folder.</p>
    </div>`;
  inner.querySelectorAll('[data-go]').forEach(el => el.addEventListener('click', () => selectDistrict(el.dataset.go)));
  inner.querySelectorAll('#sim-lens .bt').forEach(b => b.addEventListener('click', () => {
    S.drawn.simLens = b.dataset.lens;
    inner.querySelectorAll('#sim-lens .bt').forEach(x => x.classList.toggle('on', x.dataset.lens === S.drawn.simLens));
    const list = inner.querySelector('#sim-list');
    if (list) list.innerHTML = simListHTML(S.drawn.simLens, a, matches);
  }));
  inner.querySelectorAll('#extent-toggle .bt').forEach(b => b.addEventListener('click', async () => {
    if (b.dataset.extent === (S.drawn.extent || 'city')) return;
    const wasOn = Object.keys(S.layerOn).filter(k => S.layerOn[k]);   // remember what's on
    const poiCats = S.poi ? new Set(S.poi.cats) : null;
    S.drawn.extent = b.dataset.extent;
    for (const k of Object.keys(S.overlays)) S.map.removeLayer(S.overlays[k]);
    S.overlays = {}; S.layerOn = {}; S.poi = null; S.drawn.extentInfo = null;  // keep per-extent layer cache
    buildLayerRail(); renderDrawnPanel(inner);
    for (const k of wasOn) { try { await toggleLayer(k); } catch (e) {} }      // re-display at new extent
    if (poiCats && S.poi) { S.poi.cats = poiCats; await refreshPoiOverlays(); }
    renderPoiFilter(); saveState();
  }));
  const sa = inner.querySelector('#save-area');
  if (sa) sa.addEventListener('click', () => saveArea(sa));
  injectNature({ sad_id: 'drawn', typology: null });
}

async function saveArea(btn) {
  btn.disabled = true; btn.textContent = 'Savingâ€¦';
  const resp = await fetch(MATCH_API + '/save_area', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ geometry: S.drawn.geometry, extent: S.drawn.extent || 'city',
      name: (S.drawn.analysis && S.drawn.analysis.name) || 'Drawn district' })
  }).then(r => r.json()).catch(() => null);
  btn.disabled = false;
  if (resp && resp.ok) {
    btn.textContent = 'Saved Â· ' + resp.sad_id;
    btn.classList.add('on');
    window.location.href = 'compare.html?focus=' + encodeURIComponent(resp.sad_id);
  } else {
    btn.textContent = 'Save failed \u2014 is the server running?';
  }
}

function nbRow(x) {
  const maxD = 4;
  const sim = Math.round(100 * Math.max(0, 1 - (x.dist || 0) / maxD));
  const w = Math.max(4, sim).toFixed(0);
  const r = S.idToRec[x.id];
  const name = x.name || (r ? shortName(r) : x.id);
  const city = x.city || (r ? cityOf(r) : '');
  const go = (x.known || r) ? `data-go="${x.id}"` : '';
  return `<div class="nb-row" ${go}>
    <div class="nb-name">${name}<small>${city}</small></div>
    <div class="nb-bar"><span style="width:${w}%"></span></div>
    <div class="nb-d mono" title="Demographic similarity">${x.dist != null ? sim + '%' : ''}</div></div>`;
}

function nearest(id, k) {
  const row = S.manifest.embedding && S.manifest.embedding.distance_matrix && S.manifest.embedding.distance_matrix[id];
  if (!row) return [];
  return Object.entries(row).filter(([o]) => o !== id && S.idToRec[o])
    .map(([o, d]) => ({ id: o, dist: Number(d), known: true })).filter(x => isFinite(x.dist))
    .sort((a, b) => a.dist - b.dist).slice(0, k);
}

function wirePanel() {
  const x = document.getElementById('cmp-x');
  if (x) x.addEventListener('click', () => { S.compareOpen = !S.compareOpen; renderPanel(); });
  document.querySelectorAll('#cmp-body [data-go]').forEach(el =>
    el.addEventListener('click', () => selectDistrict(el.dataset.go)));
}

// â€”â‚¬â€”â‚¬ toolbar â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
function wireCompare() {
  document.getElementById('btn-compare').addEventListener('click', () => {
    if (!S.selected) { document.getElementById('dsearch').focus(); return; }
    S.compareOpen = !S.compareOpen;
    document.getElementById('btn-compare').classList.toggle('on', S.compareOpen);
    renderPanel();
  });
}
function wireSearch() {
  document.getElementById('dsearch').addEventListener('input', e => buildList(e.target.value));
}

// â€”â‚¬â€”â‚¬ state persistence across Map/Field/Viewer navigation â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
const STATE_KEY = 'sotv_map_state';
function saveState() {
  if (!S.map) return;
  try {
    const c = S.map.getCenter();
    const st = {
      center: [c.lat, c.lng], zoom: S.map.getZoom(),
      basemap: S.map.hasLayer(S.baseSat) ? 'sat' : 'light',
      layers: Object.keys(S.layerOn).filter(k => S.layerOn[k]),
      poiCats: S.poi ? [...S.poi.cats] : null,
      censusMetric: S.census ? S.census.metric : (S._censusMetric || null),
      sel: S.selected === 'drawn'
        ? { kind: 'drawn', geometry: S.drawn.geometry, extent: S.drawn.extent || 'city' }
        : (S.selected ? { kind: 'existing', id: S.selected } : null),
    };
    sessionStorage.setItem(STATE_KEY, JSON.stringify(st));
  } catch (e) { /* sessionStorage unavailable */ }
}
async function restoreState() {
  let st; try { st = JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null'); } catch { st = null; }
  if (!st) return false;
  if (st.basemap === 'sat') {
    setBasemap('sat');
    document.querySelectorAll('#base-toggle .bt').forEach(b => b.classList.toggle('on', b.dataset.base === 'sat'));
  }
  try {
    if (st.sel && st.sel.kind === 'existing' && S.idToRec[st.sel.id]) await selectDistrict(st.sel.id, { noFly: true });
    else if (st.sel && st.sel.kind === 'drawn') await restoreDrawn(st.sel.geometry, st.sel.extent);
  } catch (e) { /* selection restore failed; continue */ }
  if (st.center && st.zoom != null) S.map.setView(st.center, st.zoom);
  if (st.censusMetric) S._censusMetric = st.censusMetric;
  const wantCats = st.poiCats ? new Set(st.poiCats) : null;
  for (const k of (st.layers || [])) { if (!S.layerOn[k]) { try { await toggleLayer(k); } catch (e) {} } }
  if (wantCats && S.poi) { S.poi.cats = wantCats; await refreshPoiOverlays(); renderPoiFilter(); }
  return true;
}
async function restoreDrawn(geometry, extent) {
  const L = window.L;
  clearLayers(); S.drawGroup.clearLayers();
  S.drawGroup.addLayer(L.geoJSON({ type: 'Feature', geometry }, { style: { color: '#ff5a45', weight: 2 } }));
  S.selected = 'drawn';
  S.drawn = { geometry, analysis: null, layers: {}, extent: extent || 'city' };
  for (const [mid, m] of Object.entries(S.markers)) m.setIcon(pin(S.idToRec[mid], false));
  buildLayerRail(); renderPanelBusy('Restoring drawn areaâ€¦');
  try {
    const resp = await fetch(MATCH_API + '/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ geometry, name: 'Drawn district' })
    });
    S.drawn.analysis = await resp.json();
      try { const _city = await resolveCity(S.drawn.geometry); if (_city && S.drawn.analysis) { S.drawn.analysis.name = _city; S.drawn.analysis.region = _city; } } catch (e) {}
  } catch { renderPanelError('Couldn\u2019t reach the match server to restore the drawn area.'); return; }
  if (S.drawn.analysis && S.drawn.analysis.ok) renderPanel();
  else renderPanelError('Couldn\u2019t restore the drawn area.');
}

boot();


// â€”â‚¬â€”â‚¬ Regrid parcels layer â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬â€”â‚¬
const PARCEL_PALETTE = ['#5fa8d3','#f2a154','#7fb069','#c084d3','#e4a672','#a3b18a','#d88c9a','#4a6fa5','#b56576','#6c757d','#9a8c98','#c9ada7'];
const PARCEL_MODES = {
  use:        { label: 'Use',        kind: 'cat', get: f => _ppick(f,'usedesc','lbcs_function_desc','use_description') || 'Unspecified' },
  zoning:     { label: 'Zoning',     kind: 'cat', get: f => _ppick(f,'zoning','zoning_type','zoning_description','lbcs_activity_desc') || 'Unspecified' },
  year_built: { label: 'Year built', kind: 'seq', get: f => { const y = +(_ppick(f,'yearbuilt','year_built')); return (Number.isFinite(y) && y>1700 && y<2050) ? y : null; } },
};
function _ppick(f, ...keys) {
  if (!f) return null;
  const p = f.properties || {}; const fd = p.fields || {};
  for (const k of keys) { const v = (k in p) ? p[k] : fd[k]; if (v !== null && v !== undefined && v !== '') return v; }
  return null;
}
async function parcelsGeo(sadId) {
  if (sadId in S.parcelCache) return S.parcelCache[sadId];
  try {
    const r = await fetch('../' + sadId + '/derived/parcels/parcels.geojson');
    if (!r.ok) { S.parcelCache[sadId] = null; return null; }
    const g = await r.json();
    S.parcelCache[sadId] = g;
    return g;
  } catch (e) { S.parcelCache[sadId] = null; return null; }
}
async function parcelsLayer(sadId) {
  const gj = await parcelsGeo(sadId);
  if (!gj || !gj.features || !gj.features.length) return null;
  const mode = PARCEL_MODES[S.parcelMode] || PARCEL_MODES.use;
  // build color resolver
  let resolve;
  if (mode.kind === 'cat') {
    const counts = {};
    gj.features.forEach(f => { const c = mode.get(f); counts[c] = (counts[c]||0)+1; });
    const top = Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,12).map(x=>x[0]);
    const topSet = new Set(top);
    const pal = {}; top.forEach((c,i)=>{ pal[c] = PARCEL_PALETTE[i % PARCEL_PALETTE.length]; });
    pal['Other'] = '#666'; pal['Unspecified'] = '#5a5a5a';
    resolve = f => { const c = mode.get(f); return pal[(topSet.has(c) ? c : (c ? 'Other' : 'Unspecified'))] || '#666'; };
  } else {
    let lo = Infinity, hi = -Infinity;
    gj.features.forEach(f => { const v = mode.get(f); if (v !== null) { if (v<lo) lo=v; if (v>hi) hi=v; } });
    resolve = f => {
      const v = mode.get(f); if (v === null) return '#444';
      const t = (hi > lo) ? (v - lo) / (hi - lo) : 0.5;
      return d3 ? d3.interpolateViridis(t) : '#5fa8d3';
    };
  }
  return window.L.geoJSON(gj, {
    style: f => ({ color: '#1a1a1a', weight: 0.4, fillColor: resolve(f), fillOpacity: 0.55 }),
    onEachFeature: (f, lyr) => {
      lyr.bindPopup(_parcelPopupHTML(f), { maxWidth: 320, className: 'parcel-popup' });
    }
  });
}
function _parcelPopupHTML(f) {
  const addr = _ppick(f,'address','situs_address','address1') || _ppick(f,'parcelnumb','parcelnumb_no_formatting') || '(parcel)';
  const own = _ppick(f,'owner');
  const use = PARCEL_MODES.use.get(f);
  const zoning = PARCEL_MODES.zoning.get(f);
  const yr = PARCEL_MODES.year_built.get(f);
  const ac = +(_ppick(f,'ll_gisacre','gisacre')); const acStr = Number.isFinite(ac) ? ac.toFixed(2)+' ac' : null;
  const bsq = +(_ppick(f,'bldg_sqft','ll_bldg_footprint_sqft','improvement_sqft'));
  const bsqStr = Number.isFinite(bsq) && bsq>0 ? Math.round(bsq).toLocaleString()+' sf' : null;
  const lv = +(_ppick(f,'land_value','gisland_value','ll_land_value'));
  const lvStr = Number.isFinite(lv) && lv>0 ? '$'+Math.round(lv).toLocaleString() : null;
  const path = _ppick(f,'path');
  const esc = s => String(s).replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
  const rows = [
    ['Use', use], ['Zoning', zoning === 'Unspecified' ? null : zoning], ['Year built', yr],
    ['Lot', acStr], ['Bldg', bsqStr], ['Land $', lvStr],
  ].filter(([_,v]) => v != null && v !== '' && v !== 'Unspecified');
  return `<div style="font:11.5px -apple-system,Segoe UI,Arial,sans-serif;min-width:200px;">
    <div style="font-weight:600;font-size:12.5px;margin-bottom:3px;">${esc(addr)}</div>
    ${own ? `<div style="color:#666;font-size:11px;margin-bottom:8px;">${esc(own)}</div>` : ''}
    <table style="font-size:11px;border-collapse:collapse;">${rows.map(([k,v]) => `<tr><td style="color:#777;padding-right:10px;padding-top:1px;">${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}</table>
    ${path ? `<div style="margin-top:8px;"><a href="https://app.regrid.com${esc(path)}" target="_blank" style="color:#4292c6;font-size:10.5px;">open on regrid.com Ã¢â€ â€™</a></div>` : ''}
  </div>`;
}
function renderParcelCtrl() {
  const host = document.getElementById('parcel-ctrl');
  if (!host) return;
  if (!S.layerOn.parcels || !S.selected || S.selected === 'drawn') { host.innerHTML = ''; return; }
  const opts = Object.entries(PARCEL_MODES).map(([k,v]) => `<option value="${k}"${k===S.parcelMode?' selected':''}>${v.label}</option>`).join('');
  host.innerHTML = `<div class="ctrl-row"><label>Parcels color by</label><select id="pc-mode">${opts}</select></div>`;
  const sel = host.querySelector('#pc-mode');
  if (sel) sel.addEventListener('change', async (e) => {
    S.parcelMode = e.target.value;
    if (S.layerOn.parcels && S.overlays.parcels) {
      removeOverlay('parcels');
      const lyr = await parcelsLayer(S.selected);
      if (lyr) S.overlays.parcels = lyr.addTo(S.map);
    }
    saveState();
  });
}



// ---- Nature lens (added) ----
var NATURE = null;
var NAT_LC = [
  {k:'mth_lc_builtup_pct',label:'Built',   c:'#37343a'},
  {k:'mth_lc_tree_pct',   label:'Tree',    c:'#1b5e20'},
  {k:'mth_lc_grass_pct',  label:'Grass',   c:'#7cb342'},
  {k:'mth_lc_crop_pct',   label:'Cropland',c:'#d4b483'},
  {k:'mth_lc_water_pct',  label:'Water',   c:'#2f6f9f'},
  {k:'mth_lc_wetland_pct',label:'Wetland', c:'#2e8b8b'},
  {k:'mth_lc_bare_pct',   label:'Bare',    c:'#b9a48a'}
];
async function ensureNature(){
  if(NATURE) return NATURE;
  NATURE = {byId:{},dist:{},maxDist:0.6};
  try{
    var gj = await fetch('nature_map.geojson').then(function(r){return r.json();});
    NATURE.dist = gj.distances || {};
    (gj.features||[]).forEach(function(f){ NATURE.byId[f.properties.sad_id]=f.properties; });
    var mx=0; for(var a in NATURE.dist){ var rr=NATURE.dist[a]; for(var b in rr){ if(rr[b]>mx) mx=rr[b]; } }
    if(mx>0) NATURE.maxDist=mx;
  }catch(e){}
  return NATURE;
}
function ensureNatureStyles(){
  if(document.getElementById('nat-panel-css')) return;
  var st=document.createElement('style'); st.id='nat-panel-css';
  st.textContent='.nat-tag{font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3);border:1px solid var(--line);border-radius:100px;padding:2px 7px;font-weight:400;float:right}'
    +'.nat-paved{display:flex;align-items:baseline;gap:11px;margin:4px 0 8px}'
    +'.nat-big{font-family:var(--serif);font-size:34px;font-weight:600;line-height:.9}'
    +'.nat-pavtx{font-size:11px;color:var(--ink-2);line-height:1.45}.nat-pavtx b{color:var(--ink);font-weight:600}'
    +'.nat-sub{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3);margin:16px 0 8px}'
    +'.nat-bar{display:flex;height:28px;border-radius:7px;overflow:hidden;border:1px solid var(--line);margin:0 0 10px;box-shadow:inset 0 0 0 1px rgba(255,255,255,.4)}'
    +'.nat-bar>span{height:100%;transition:opacity .12s}.nat-bar>span:hover{opacity:.78}'
    +'.nat-key{display:grid;grid-template-columns:1fr 1fr;gap:5px 14px}'
    +'.nat-key .k{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--ink-2)}'
    +'.nat-key .k i{width:10px;height:10px;border-radius:2px;flex:0 0 auto}'
    +'.nat-key .k b{margin-left:auto;color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}'
    +'.nat-note{font-size:10px;color:var(--ink-3);line-height:1.5;margin-top:8px}.nat-note b{color:var(--ink-2)}'
    +'.nat-bio2{display:flex;flex-direction:column;gap:9px}'
    +'.nat-bio2 .b{display:grid;grid-template-columns:104px 1fr 50px;gap:9px;align-items:center}'
    +'.nat-bio2 .bl{font-size:11px;color:var(--ink-2);line-height:1.1}'
    +'.nat-bio2 .bl small{display:block;color:var(--ink-3);font-size:9px;margin-top:1px}'
    +'.nat-bio2 .bt{height:7px;background:var(--line);border-radius:4px;overflow:hidden}'
    +'.nat-bio2 .bt>span{display:block;height:100%;border-radius:4px}'
    +'.nat-bio2 .bv{font-family:var(--mono);font-size:11.5px;text-align:right;color:var(--ink);font-variant-numeric:tabular-nums}'
    +'.nat-pct .r{display:grid;grid-template-columns:64px 1fr 34px;gap:8px;align-items:center;padding:3px 0}'
    +'.nat-pct .l{font-size:10.5px;color:var(--ink-2)}'
    +'.nat-pct .rail{position:relative;height:16px;border-radius:5px;background:var(--paper-3);border:1px solid var(--line-2)}'
    +'.nat-pct .rail .mk{position:absolute;top:-2px;width:3px;height:20px;border-radius:2px;background:var(--ink);transform:translateX(-1.5px)}'
    +'.nat-pct .rr{font-family:var(--mono);font-size:9.5px;color:var(--ink-3);text-align:right}';
  document.head.appendChild(st);
}
function natNearest(id,k){
  var row = NATURE && NATURE.dist && NATURE.dist[id];
  if(!row) return [];
  return Object.entries(row).filter(function(e){return e[0]!==id && S.idToRec[e[0]];})
    .map(function(e){return {id:e[0],dist:Number(e[1])};}).filter(function(x){return isFinite(x.dist);})
    .sort(function(a,b){return a.dist-b.dist;}).slice(0,k);
}
function natRow(x){
  var maxD=(NATURE && NATURE.maxDist)||0.6;
  var sim=Math.round(100*Math.max(0,1-(x.dist||0)/maxD));
  var w=Math.max(4,sim).toFixed(0);
  var r=S.idToRec[x.id];
  var name=r?shortName(r):x.id;
  var city=r?cityOf(r):'';
  var go=r?('data-go="'+x.id+'"'):'';
  return '<div class="nb-row" '+go+'><div class="nb-name">'+name+'<small>'+city+'</small></div>'
    +'<div class="nb-bar"><span style="width:'+w+'%"></span></div>'
    +'<div class="nb-d mono" title="Ecological similarity">'+(x.dist!=null?sim+'%':'')+'</div></div>';
}
function natArr(key){ return Object.keys(NATURE.byId||{}).map(function(id){return +NATURE.byId[id][key];}).filter(function(v){return isFinite(v);}); }
function natPctile(v,arr){ if(v==null||!isFinite(v)||!arr.length) return null; var s=arr.slice().sort(function(a,b){return a-b;}); var c=0; for(var i=0;i<s.length;i++) if(s[i]<=v) c++; return c/s.length; }
function natLogbar(v,arr){ var mx=arr.length?Math.max.apply(null,arr):0; var lv=Math.log(Math.max(1,v||0))/Math.LN10; var lm=Math.log(Math.max(2,mx))/Math.LN10; return Math.max(3,100*lv/lm).toFixed(0); }
function natPctRow(label,v,arr){ var p=natPctile(v,arr); var x=p==null?0:p*100; return '<div class="r"><span class="l">'+label+'</span><span class="rail"><span class="mk" style="left:'+x.toFixed(0)+'%"></span></span><span class="rr">'+(p==null?'\u2014':Math.round(x)+'%')+'</span></div>'; }
/* nature5 */
function natureSection(r){
  ensureNatureStyles();
  var p = NATURE && NATURE.byId && NATURE.byId[r.sad_id];
  if(!p) return '';
  function nv(v,d){ return (v==null||isNaN(Number(v)))?'\u2014':(d!=null?Number(v).toFixed(d):String(Math.round(Number(v)))); }
  var col = hex(typoColor(r.typology));

  // headline: green cover (0..1 share of district)
  var gc = (p.green_cover==null)?null:Number(p.green_cover);
  var gcArr = natArr('green_cover');
  var gcP = natPctile(gc, gcArr);
  var gcPct = (gc==null)?null:gc*100;

  // canopy height, gated. p.canopy_m is null when the ETH sample was too thin.
  var canM = (p.canopy_m==null)?null:Number(p.canopy_m);
  var canRaw = (p.canopy_m_raw==null)?null:Number(p.canopy_m_raw);
  var canArr = natArr('canopy_m');

  // composition bar: green vs paved remainder of the district
  var greenShare = (gc==null)?0:Math.max(0,Math.min(1,gc));
  var segs = '<span style="width:'+(greenShare*100).toFixed(2)+'%;background:#2e7d32" title="Green cover: '+nv(gcPct,1)+'%"></span>'
    + '<span style="width:'+((1-greenShare)*100).toFixed(2)+'%;background:#cfc6b8" title="Other / built"></span>';

  // nearest water (blue adjacency): smaller is closer
  var wat = (p.water_adj==null)?null:Number(p.water_adj);
  var watTxt = (wat==null)?'\u2014':(wat<=0?'adjacent':(wat<1000?Math.round(wat)+' m':(wat/1000).toFixed(1)+' km'));

  var trees = (p.trees==null)?null:Number(p.trees);
  var treesArr = natArr('trees');
  var pm = (p.pm25==null)?null:Number(p.pm25);
  var pmArr = natArr('pm25');

  var nn = natNearest(r.sad_id,5);

  return '<div class="sec">'
    +'<div class="sec-h">Nature <span class="nat-tag">eco lens</span></div>'
    +'<div class="nat-paved"><div class="nat-big" style="color:'+col+'">'+(gcPct==null?'\u2014':Math.round(gcPct)+'%')+'</div>'
      +'<div class="nat-pavtx">green cover<br><b>greener than '+(gcP==null?'\u2014':Math.round(gcP*100)+'%')+'</b> of corpus</div></div>'
    +'<div class="nat-bar">'+segs+'</div>'
    +'<div class="nat-key">'
      +'<span class="k"><i style="background:#2e7d32"></i>Green<b>'+(gcPct==null?'\u2014':Math.round(gcPct)+'%')+'</b></span>'
      +'<span class="k"><i style="background:#cfc6b8"></i>Other<b>'+(gcPct==null?'\u2014':Math.round(100-gcPct)+'%')+'</b></span>'
    +'</div>'
    +'<div class="nat-note">Green = vegetated land cover (Overture land use + canopy area), the share of the district that reads as planted rather than paved.</div>'
    +'<div class="nat-sub">Structure and surroundings</div>'
    +'<div class="nat-bio2">'
      +'<div class="b"><span class="bl">Canopy height<small>'+(canM==null?'thin sample':'ETH 10m, mean')+'</small></span>'
        +'<span class="bt"><span style="width:'+(canM==null?0:natLinbar(canM,canArr))+'%;background:#1b5e20"></span></span>'
        +'<span class="bv">'+(canM==null?(canRaw==null?'\u2014':'('+nv(canRaw,1)+')'):nv(canM,1)+' m')+'</span></div>'
      +'<div class="b"><span class="bl">Trees mapped<small>Overture points</small></span>'
        +'<span class="bt"><span style="width:'+natLogbar(trees,treesArr)+'%;background:#558b2f"></span></span>'
        +'<span class="bv">'+nv(trees)+'</span></div>'
      +'<div class="b"><span class="bl">Water<small>nearest blue space</small></span>'
        +'<span class="bt"><span style="width:'+(wat==null?0:Math.max(4,100-Math.min(100,wat/10)).toFixed(0))+'%;background:#2f6f9f"></span></span>'
        +'<span class="bv">'+watTxt+'</span></div>'
      +'<div class="b"><span class="bl">Air (PM2.5)<small>OpenAQ median</small></span>'
        +'<span class="bt"><span style="width:'+(pm==null?0:Math.max(4,100-Math.min(100,pm*2)).toFixed(0))+'%;background:#8d6e63"></span></span>'
        +'<span class="bv">'+(pm==null?'\u2014':nv(pm,1))+'</span></div>'
    +'</div>'
    +'<div class="nat-note">Canopy height shown only where the ETH sample covers enough of the district; dense cores read "thin sample." Air is a vicinity median, cleaner bar = lower PM2.5.</div>'
    +'<div class="nat-sub">Corpus rank</div>'
    +'<div class="nat-pct">'
      +natPctRow('Green',gc,gcArr)
      +natPctRow('Canopy',canM,canArr)
      +natPctRow('Trees',trees,treesArr)
      +natPctRow('Clean air',pm==null?null:-pm, pmArr.map(function(x){return -x;}))
    +'</div>'
    +'<div class="sec-h" style="margin-top:14px">Ecological nearest</div>'
    +'<div id="nat-body">'+(nn.map(natRow).join('')||'<div class="empty">No ecological data.</div>')+'</div>'
    +'<p class="layer-note" style="margin-left:0">Canopy + green + blue + air \u2014 a standalone lens, orthogonal to typology.</p></div>';
}
function natLinbar(v,arr){ var mx=arr.length?Math.max.apply(null,arr):0; if(!isFinite(v)||mx<=0) return 3; return Math.max(3,100*v/mx).toFixed(0); }

function injectNature(r){
  if(!r) return;
  var html=natureSection(r); if(!html) return;
  var inner=document.getElementById('panel-inner'); if(!inner) return;
  var act=inner.querySelector('.actions');
  if(act) act.insertAdjacentHTML('beforebegin',html); else inner.insertAdjacentHTML('beforeend',html);
  inner.querySelectorAll('#nat-body [data-go]').forEach(function(el){ el.addEventListener('click',function(){ selectDistrict(el.dataset.go); }); });
}

/* nature-draw-hook */
(function(){
  if (typeof injectNature !== 'function' || typeof NATURE === 'undefined') return;
  var _origInject = injectNature;
  var _drawnNatPending = false;

  // map the server /analyze_nature block to the flat props natureSection reads
  function _flattenNature(block){
    if(!block) return null;
    var c = block.canopy || {}, gb = block.green_blue || {}, ai = block.air || {};
    var green = (gb.green_area_share!=null) ? gb.green_area_share : null;
    var carea = (c.canopy_area_share!=null) ? c.canopy_area_share : null;
    var gcov = (green!=null || carea!=null) ? Math.max(green||0, carea||0) : null;
    return {
      sad_id: 'drawn',
      green_cover: gcov,
      canopy_m: c.height_reliable ? c.mean_canopy_m : null,
      canopy_m_raw: (c.mean_canopy_m!=null) ? c.mean_canopy_m : null,
      water_adj: (gb.nearest_water_m!=null) ? gb.nearest_water_m : null,
      trees: (gb.tree_points!=null) ? gb.tree_points : null,
      pm25: (ai.pm25!=null) ? ai.pm25 : null
    };
  }

  function _drawnGeom(){
    return (typeof S!=='undefined' && S.drawn && S.drawn.geometry) ? S.drawn.geometry : null;
  }

  // override: corpus districts unchanged; drawn district triggers a live pull
  injectNature = function(r){
    if(!r) return;
    if(r.sad_id !== 'drawn'){ return _origInject(r); }

    NATURE = NATURE || {byId:{},dist:{},maxDist:0.6};
    NATURE.byId = NATURE.byId || {};

    if(NATURE.byId['drawn']){ return _origInject(r); }  // already have it

    var geom = _drawnGeom();
    if(!geom){ return; }
    if(_drawnNatPending) return;
    _drawnNatPending = true;

    // show a lightweight placeholder using the existing panel machinery
    var inner = document.getElementById('panel-inner');
    if(inner && !document.getElementById('nat-drawn-wait')){
      var act = inner.querySelector('.actions');
      var ph = '<div class="sec" id="nat-drawn-wait"><div class="sec-h">Nature <span class="nat-tag">eco lens</span></div>'
        + '<p class="layer-note" style="margin-left:0">Pulling green, canopy and air for this boundary\u2026 first draw in a region can take a moment.</p></div>';
      if(act) act.insertAdjacentHTML('beforebegin', ph); else inner.insertAdjacentHTML('beforeend', ph);
    }

    var api = (typeof MATCH_API!=='undefined') ? MATCH_API : 'http://localhost:8000';
    fetch(api + '/analyze_nature', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ geometry: geom })
    }).then(function(resp){ return resp.json(); }).then(function(data){
      _drawnNatPending = false;
      var wait = document.getElementById('nat-drawn-wait'); if(wait) wait.remove();
      if(data && data.ok && data.nature){
        NATURE.byId['drawn'] = _flattenNature(data.nature);
        _origInject({ sad_id:'drawn', typology: (r.typology||null) });
      } else {
        var inr = document.getElementById('panel-inner');
        if(inr){ var a2 = inr.querySelector('.actions');
          var msg = '<div class="sec"><div class="sec-h">Nature <span class="nat-tag">eco lens</span></div>'
            + '<p class="layer-note" style="margin-left:0">Nature pull did not return data'
            + (data && data.error ? ' ('+String(data.error).slice(0,80)+')' : '') + '.</p></div>';
          if(a2) a2.insertAdjacentHTML('beforebegin', msg); else inr.insertAdjacentHTML('beforeend', msg);
        }
      }
    }).catch(function(){
      _drawnNatPending = false;
      var wait = document.getElementById('nat-drawn-wait'); if(wait) wait.remove();
      var inr = document.getElementById('panel-inner');
      if(inr){ var a3 = inr.querySelector('.actions');
        var em = '<div class="sec"><div class="sec-h">Nature <span class="nat-tag">eco lens</span></div>'
          + '<p class="layer-note" style="margin-left:0">Could not reach the match server for the nature pull. Start sad_match_server.py and redraw.</p></div>';
        if(a3) a3.insertAdjacentHTML('beforebegin', em); else inr.insertAdjacentHTML('beforeend', em);
      }
    });
  };
})();
